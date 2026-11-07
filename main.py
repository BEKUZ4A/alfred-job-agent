import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Dict, Any

from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import JSONResponse

from config import PORT, MIN_MATCH_SCORE
from database import init_db, is_vacancy_seen, mark_vacancy_seen
from scraper import scrape_all_sources, is_potentially_relevant
from ai_analyzer import batch_analyze_vacancies, get_last_error
from notifier import notify_user_about_vacancies
from bot_handler import process_telegram_update, run_telegram_polling

# Log sozlamalari
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("Alfred.Main")

# Global holat
is_scanning = False

async def run_vacancy_scan() -> int:
    """
    Yangi vakansiyalarni qidirish, batch orqali tahlil qilish va moslarini yuborish asosiy funksiyasi.
    Har bitta e'lon alohida yuborilmaydi; barcha yangi e'lonlar bitta umumiy batch so'rovda Gemini'ga yuboriladi.
    """
    global is_scanning
    if is_scanning:
        logger.info("Qidiruv jarayoni allaqachon bajarilmoqda...")
        return 0
        
    is_scanning = True
    matches = []
    
    try:
        logger.info("🚀 Yangi vakansiyalarni qidirish boshlandi...")
        raw_vacancies = await scrape_all_sources()
        logger.info(f"Jami {len(raw_vacancies)} ta xom e'lon yig'ildi.")
        
        # 1. Python kalit so'zlari va ilgari ko'rilmaganligi bo'yicha birlamchi filtr
        unseen_filtered = []
        for item in raw_vacancies:
            vac_id = item["id"]
            if is_vacancy_seen(vac_id):
                continue
            if not is_potentially_relevant(item["text"]):
                continue
            unseen_filtered.append(item)
            
        logger.info(f"Tahlil uchun {len(unseen_filtered)} ta yangi e'lon saralandi.")
        
        if not unseen_filtered:
            logger.info("Yangi ko'rilmagan mos keluvchi e'lonlar topilmadi.")
            return 0
            
        # 2. Batch tahlil: barcha yangi e'lonlarni bitta umumiy so'rovda (10 tadan bo'lib) yuborish
        batch_size = 10
        analysis_map = {}
        for i in range(0, len(unseen_filtered), batch_size):
            chunk = unseen_filtered[i:i + batch_size]
            results = await batch_analyze_vacancies(chunk)
            for res in results:
                res_id = str(res.get("id"))
                if res_id:
                    analysis_map[res_id] = res
                    
        # 3. Natijalarni birlashtirish va SQLite bazaga yozish (ertaga qayta yuborilmasligi uchun)
        for item in unseen_filtered:
            vac_id = item["id"]
            analysis = analysis_map.get(vac_id)
            if not analysis:
                continue
                
            score = analysis.get("match_score", 0)
            is_relevant = analysis.get("is_relevant", False)
            
            logger.info(f"Baho: {score}% | Mosmi: {is_relevant} | {item['title'][:40]}")
            
            # Bazaga ko'rilgan deb yozib qo'yamiz
            job_type = analysis.get("job_type") or item.get("job_type", "vacancy")
            item["job_type"] = job_type
            mark_vacancy_seen(
                vacancy_id=vac_id,
                title=item["title"],
                company=analysis.get("company", item.get("company", "")),
                url=item["url"],
                source=item["source"],
                match_score=score,
                ai_summary=analysis.get("short_summary", ""),
                job_type=job_type,
                raw_text=item.get("text", ""),
                salary=analysis.get("salary", "")
            )
            
            # Agar talab qilingan minimal balldan yuqori bo'lsa
            if is_relevant and score >= MIN_MATCH_SCORE:
                item["analysis"] = analysis
                matches.append(item)
                
        # 4. Mos vakansiyalar topilsa, xabar va ovozli xabar yuborish
        if matches:
            logger.info(f"🎉 {len(matches)} ta mos vakansiya topildi! Foydalanuvchi ogohlantirilmoqda...")
            await notify_user_about_vacancies(matches)
        else:
            logger.info("E'lonlar tahlil qilindi, ammo talabga mos yuqori balli vakansiya chiqmadi.")
            
        return len(matches)
        
    except Exception as e:
        logger.error(f"Skanerlashda kutilmagan xatolik: {e}")
        return 0
    finally:
        is_scanning = False

async def background_scheduler():
    """Ichki avtomatik taymer: har 15 daqiqada bir marta o'zi skanerlab turadi"""
    while True:
        try:
            await asyncio.sleep(900)  # 15 daqiqa (900 soniya)
            logger.info("⏰ Rejali avtomatik skanerlash vaqti keldi...")
            await run_vacancy_scan()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Taymer xatoligi: {e}")
            await asyncio.sleep(60)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Dastur ishga tushgandagi amallar
    init_db()
    logger.info("Alfred AI Agent ishga tushdi!")
    
    # 1. Orqa fonda Telegram Bot Pollingni ishga tushirish (xabarlarni zudlik bilan o'qish)
    bot_task = asyncio.create_task(run_telegram_polling(trigger_scan_callback=run_vacancy_scan))
    
    # 2. Orqa fonda avtomatik qidiruv taymerini yoqish
    timer_task = asyncio.create_task(background_scheduler())
    
    yield
    
    # Dastur to'xtaganda vazifalarni to'xtatish
    bot_task.cancel()
    timer_task.cancel()
    logger.info("Alfred AI Agent to'xtatildi.")

app = FastAPI(
    title="Alfred - 24/7 AI IT Recruiter",
    description="Python/Django/FastAPI/AI bo'yicha vakansiyalarni 24/7 qidiruvchi shaxsiy agent",
    version="1.2.0",
    lifespan=lifespan
)

@app.get("/")
async def root():
    return {
        "status": "online",
        "agent": "Alfred 24/7 AI IT Recruiter",
        "bot_listener": "active (Telegram Polling)",
        "gemini_status": get_last_error(),
        "endpoints": {
            "/health": "Server sog'lig'i tekshiruvi",
            "/scan": "Yangi vakansiyalarni darhol (orqa fonda) qidirish",
            "/telegram-webhook": "Telegram Bot webhook manzili (ixtiyoriy)"
        }
    }

@app.get("/health")
async def health_check():
    """cron-job.org yoki Render health tekshiruvi uchun endpoint"""
    return {
        "status": "healthy",
        "agent": "Alfred",
        "is_scanning": is_scanning,
        "gemini_last_status": get_last_error()
    }

@app.get("/scan")
async def trigger_scan(background_tasks: BackgroundTasks):
    """
    Vakansiya qidirishni ishga tushiruvchi endpoint.
    cron-job.org vaqt limiti (30 soniya) tufayli timeout bo'lmasligi uchun,
    og'ir skanerlash ishi FastAPI BackgroundTasks ga beriladi va 1 soniyada javob qaytariladi.
    """
    background_tasks.add_task(run_vacancy_scan)
    return {
        "status": "scan started",
        "message": "Vakansiyalarni qidirish orqa fonda tezkor boshlandi!"
    }

@app.post("/scan")
async def trigger_scan_post(background_tasks: BackgroundTasks):
    """POST so'rovlar uchun ham /scan"""
    background_tasks.add_task(run_vacancy_scan)
    return {
        "status": "scan started",
        "message": "Vakansiyalarni qidirish orqa fonda tezkor boshlandi!"
    }

@app.post("/telegram-webhook")
async def telegram_webhook(request: Request):
    """Telegram Webhook kelganda ham qayta ishlash imkoniyati"""
    try:
        update_data = await request.json()
        asyncio.create_task(process_telegram_update(update_data, trigger_scan_callback=run_vacancy_scan))
        return JSONResponse(content={"ok": True})
    except Exception as e:
        logger.error(f"Webhook xatosi: {e}")
        return JSONResponse(content={"ok": False, "error": str(e)}, status_code=500)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main.py:app", host="0.0.0.0", port=PORT, reload=False)

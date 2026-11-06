import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Dict, Any

from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import JSONResponse

from config import PORT, MIN_MATCH_SCORE
from database import init_db, is_vacancy_seen, mark_vacancy_seen
from scraper import scrape_all_sources
from ai_analyzer import analyze_vacancy
from notifier import notify_user_about_vacancies
from bot_handler import process_telegram_update

# Log sozlamalari
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("Alfred.Main")

# Global holat
is_scanning = False

async def run_vacancy_scan() -> int:
    """Yangi vakansiyalarni qidirish, tahlil qilish va moslarini yuborish asosiy funksiyasi"""
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
        
        for item in raw_vacancies:
            vac_id = item["id"]
            
            # 1. Ilgari yuborilganmi? (Qayta yubormaslik qoidasi)
            if is_vacancy_seen(vac_id):
                continue
                
            logger.info(f"Yangi e'lon tahlil qilinmoqda: {item['title'][:60]}")
            
            # 2. Gemini API orqali sinchkov tahlil
            analysis = await analyze_vacancy(item["text"])
            if not analysis:
                continue
                
            score = analysis.get("match_score", 0)
            is_relevant = analysis.get("is_relevant", False)
            
            logger.info(f"Baho: {score}% | Mosmi: {is_relevant} | {item['title'][:40]}")
            
            # Bazaga ko'rilgan deb yozib qo'yamiz (boshqa qayta tekshirmaslik uchun)
            mark_vacancy_seen(
                vacancy_id=vac_id,
                title=item["title"],
                company=analysis.get("company", item.get("company", "")),
                url=item["url"],
                source=item["source"],
                match_score=score,
                ai_summary=analysis.get("short_summary", "")
            )
            
            # 3. Agar talab qilingan balldan yuqori bo'lsa, mos deb olamiz
            if is_relevant and score >= MIN_MATCH_SCORE:
                item["analysis"] = analysis
                matches.append(item)
                
        # 4. Mos vakansiyalar topilsa, xabar va ovozli xabar yuborish
        if matches:
            logger.info(f"🎉 {len(matches)} ta mos vakansiya topildi! Foydalanuvchi ogohlantirilmoqda...")
            await notify_user_about_vacancies(matches)
        else:
            logger.info("Hozircha yangi mos vakansiyalar topilmadi.")
            
        return len(matches)
        
    except Exception as e:
        logger.error(f"Skanerlashda xatolik: {e}")
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
    
    # Orqa fonda avtomatik qidiruv taymerini yoqish
    timer_task = asyncio.create_task(background_scheduler())
    
    yield
    
    # Dastur to'xtaganda
    timer_task.cancel()
    logger.info("Alfred AI Agent to'xtatildi.")

app = FastAPI(
    title="Alfred - 24/7 AI IT Recruiter",
    description="Python/Django/FastAPI/AI bo'yicha vakansiyalarni 24/7 qidiruvchi shaxsiy agent",
    version="1.0.0",
    lifespan=lifespan
)

@app.get("/")
async def root():
    return {
        "status": "online",
        "agent": "Alfred 24/7 AI IT Recruiter",
        "author": "Behruz",
        "endpoints": {
            "/health": "Server sog'lig'i tekshiruvi (cron-job.org uchun)",
            "/scan": "Yangi vakansiyalarni darhol qidirish",
            "/telegram-webhook": "Telegram Bot webhook manzili"
        }
    }

@app.get("/health")
async def health_check():
    """cron-job.org orqali serverni 24/7 uyg'oq tutish uchun endpoint"""
    return {
        "status": "healthy",
        "agent": "Alfred",
        "is_scanning": is_scanning
    }

@app.get("/scan")
async def trigger_scan(background_tasks: BackgroundTasks):
    """Vakansiya qidirishni ishga tushiruvchi endpoint"""
    background_tasks.add_task(run_vacancy_scan)
    return {
        "message": "Qidiruv orqa fonda ishga tushirildi!",
        "status": "processing"
    }

@app.post("/telegram-webhook")
async def telegram_webhook(request: Request):
    """Telegram Botdan kelgan xabarlarni qabul qilish"""
    try:
        update_data = await request.json()
        await process_telegram_update(update_data, trigger_scan_callback=run_vacancy_scan)
        return JSONResponse(content={"ok": True})
    except Exception as e:
        logger.error(f"Webhook xatosi: {e}")
        return JSONResponse(content={"ok": False, "error": str(e)}, status_code=500)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main.py:app", host="0.0.0.0", port=PORT, reload=False)

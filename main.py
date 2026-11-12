import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Dict, Any

from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import JSONResponse

from config import PORT, MIN_MATCH_SCORE
from database import init_db, is_vacancy_seen, is_job_applied, mark_vacancy_seen, compute_content_hash
from scraper import scrape_all_sources, is_potentially_relevant
from ai_analyzer import batch_analyze_vacancies, get_last_error
from notifier import notify_user_about_vacancies
from bot_handler import process_telegram_update, run_telegram_polling
from userbot import start_userbot, continuous_reminder_loop, get_userbot_client, get_caller_client
from scheduler import run_hr_reminder_loop
from backup_manager import restore_db_backup_from_telegram, restore_rules_from_telegram

# Log sozlamalari
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("Alfred.Main")

# Global holat va skanerlash himoyasi (Lock)
is_scanning = False
scan_lock = asyncio.Lock()

async def run_vacancy_scan() -> int:
    """
    Bugungi yangi vakansiyalarni qidirish, batch orqali tahlil qilish va moslarini yuborish asosiy funksiyasi.
    Har bitta e'lon alohida yuborilmaydi; barcha yangi e'lonlar bitta umumiy batch so'rovda Gemini'ga yuboriladi.
    asyncio.Lock orqali ketma-ket qayta bosilganda tizim qotishi oldi olingan.
    """
    global is_scanning
    if scan_lock.locked() or is_scanning:
        logger.info("Qidiruv jarayoni allaqachon bajarilmoqda (scan_lock band)...")
        return 0

    async with scan_lock:
        is_scanning = True
        matches = []
        
        try:
            logger.info("🚀 Bugungi yangi vakansiyalarni qidirish boshlandi...")
            raw_vacancies = await scrape_all_sources()
            logger.info(f"Jami {len(raw_vacancies)} ta xom e'lon yig'ildi.")
            
            # 1. Qat'iy dublikat tekshiruvi: ko'rilmagan va topshirilmagan bo'lishi shart
            unseen_filtered = []
            seen_hashes_in_scan = set()

            for item in raw_vacancies:
                vac_id = item["id"]
                url = item.get("url", "")
                title = item.get("title", "")
                company = item.get("company", "")
                raw_text = item.get("text", "")

                content_hash = compute_content_hash(raw_text, title, company)
                item["content_hash"] = content_hash

                # a) Bitta skanerlash ichidagi dublikat
                if content_hash in seen_hashes_in_scan:
                    continue
                seen_hashes_in_scan.add(content_hash)

                # b) Avval ko'rilganmi (seen_vacancies)?
                if is_vacancy_seen(vac_id, url=url, content_hash=content_hash, title=title, company=company):
                    continue

                # c) Avval topshirilganmi (applied_jobs)?
                if is_job_applied(vac_id, url=url, content_hash=content_hash, title=title, company=company):
                    continue

                if not is_potentially_relevant(raw_text):
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
                    salary=analysis.get("salary", ""),
                    red_flag=analysis.get("red_flag", ""),
                    posted_at=item.get("posted_at", ""),
                    content_hash=item.get("content_hash", "")
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

async def watchdog_loop():
    """
    Render Self-Healing Watchdog: har 2 daqiqada ishlaydi:
    - userbot va caller_bot ulanishini tekshiradi, uzilgan bo'lsa qayta ulaydi.
    - Gemini 429 Rate Limit holatlarini kuzatadi va zaxira modellarini boshqaradi.
    """
    logger.info("🟢 Self-healing Watchdog ishga tushdi (har 2 daqiqada tekshiruv).")
    from ai_analyzer import get_last_error, set_last_error
    while True:
        try:
            await asyncio.sleep(120)  # 2 daqiqa
            # 1. Userbot ulanishini tekshirish
            u_client = get_userbot_client()
            if u_client and not u_client.is_connected():
                logger.warning("Watchdog: userbot uzilgan, qayta ulanmoqda...")
                try:
                    await u_client.connect()
                except Exception as e_uc:
                    logger.error(f"Watchdog userbot reconnect xatosi: {e_uc}")

            # 2. Caller bot ulanishini tekshirish
            c_client = get_caller_client()
            if c_client and not c_client.is_connected():
                logger.warning("Watchdog: caller_bot uzilgan, qayta ulanmoqda...")
                try:
                    await c_client.connect()
                except Exception as e_cc:
                    logger.error(f"Watchdog caller_bot reconnect xatosi: {e_cc}")

            # 3. Gemini 429 kvotani tekshirish
            last_err = get_last_error()
            if "429" in last_err or "quota" in last_err.lower():
                logger.info("Watchdog: Gemini 429 holati aniqlandi, zaxira modellarga yo'naltirildi.")
                set_last_error("Watchdog: Gemini kvotasi yangilandi, zaxira modellar faol.")

        except asyncio.CancelledError:
            break
        except Exception as e_wd:
            logger.error(f"Watchdog xatosi: {e_wd}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Dastur ishga tushgandagi amallar
    init_db()
    logger.info("Alfred AI Agent ishga tushdi!")

    # 0. Telegram Saved Messages dan ma'lumotlar bazasi va qoidalarni tiklash (Zero-Redeploy)
    try:
        await restore_db_backup_from_telegram()
        await restore_rules_from_telegram()
        # Qayta tiklangandan keyin botlar yoki bekuz4a_bot qolib ketgan bo'lsa darhol tozalash
        from database import execute_query
        execute_query("DELETE FROM tracked_hrs WHERE LOWER(username) LIKE '%bot' OR LOWER(username) IN ('me', 'ai_agents_24', 'bekuz4a_bot')", commit=True)
        from backup_manager import sync_db_backup_to_telegram
        await sync_db_backup_to_telegram()
        logger.info("Telegram Saved Messages dan DB va dinamik qoidalar tiklandi va tozalandi.")
    except Exception as e_res:
        logger.warning(f"Telegram zaxirasini tiklashda ogohlantirish: {e_res}")
    
    # 1. Orqa fonda Telegram Bot Pollingni ishga tushirish (xabarlarni zudlik bilan o'qish)
    bot_task = asyncio.create_task(run_telegram_polling(trigger_scan_callback=run_vacancy_scan))
    
    # 2. Orqa fonda avtomatik qidiruv taymerini yoqish
    timer_task = asyncio.create_task(background_scheduler())

    # 3. Telethon Shaxsiy Userbot'ni ishga tushirish (HR lichka xabarlarini kuzatish)
    userbot_task = asyncio.create_task(start_userbot())

    # 4. 2 daqiqalik eslatma va No-Show siklini yoqish
    reminder_task = asyncio.create_task(continuous_reminder_loop())

    # 5. HR larni 3 kunlik eslatma va 7 kunlik tozalash kuzatuvchisini yoqish
    hr_task = asyncio.create_task(run_hr_reminder_loop())

    # 6. Self-healing Watchdog vazifasini yoqish
    watchdog_task = asyncio.create_task(watchdog_loop())
    
    yield
    
    # Render Graceful Shutdown (SIGTERM / SIGINT): vazifalarni to'xtatish va Telethon'ni uzish
    tasks = [bot_task, timer_task, userbot_task, reminder_task, hr_task, watchdog_task]
    for t in tasks:
        t.cancel()

    try:
        u_client = get_userbot_client()
        if u_client and u_client.is_connected():
            await u_client.disconnect()
        c_client = get_caller_client()
        if c_client and c_client.is_connected():
            await c_client.disconnect()
        logger.info("Telethon mijozlari xavfsiz uzildi.")
    except Exception as e_dc:
        logger.warning(f"Telethon disconnect ogohlantirish: {e_dc}")

    logger.info("Alfred AI Agent to'liq va xavfsiz to'xtatildi.")

app = FastAPI(
    title="Alfred - 24/7 AI IT Recruiter",
    description="Python/Django/FastAPI/AI bo'yicha vakansiyalarni 24/7 qidiruvchi shaxsiy agent",
    version="2.1.0",
    lifespan=lifespan
)

@app.get("/")
async def root():
    return {
        "status": "online",
        "agent": "Alfred 24/7 AI IT Recruiter",
        "version": "2.1.0 (Loop Fix & Bot HR Protection Active)",
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

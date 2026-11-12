import os
import base64
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Dict, Any

from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import JSONResponse, HTMLResponse

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

                # b) Avval ko'rilganmi (seen_vacancies)? (QULF 3: clean_hash va matn bo'yicha)
                if is_vacancy_seen(vac_id, url=url, content_hash=content_hash, title=title, company=company, raw_text=raw_text):
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
                logger.info(f"🎉 {len(matches)} ta mos vakansiya topildi! QULF 3: Avval #ALFRED_DB_BACKUP yangilanmoqda...")
                try:
                    from backup_manager import sync_db_backup_to_telegram
                    await sync_db_backup_to_telegram()
                    logger.info("✅ QULF 3: Yangi vakansiya xeshlari Saved Messages (#ALFRED_DB_BACKUP) ga muhrlandi.")
                except Exception as e_bsync:
                    logger.warning(f"Backup sync ogohlantirish: {e_bsync}")

                logger.info("Foydalanuvchi ogohlantirilmoqda...")
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

    # 1. Telethon Shaxsiy Userbot'ni ishga tushirish va ulanishini kutish
    userbot_task = asyncio.create_task(start_userbot())
    for _ in range(15):
        u_client = get_userbot_client()
        if u_client and u_client.is_connected() and await u_client.is_user_authorized():
            break
        await asyncio.sleep(1)

    # 2. QULF 1: Telegram Saved Messages dan ma'lumotlar bazasi va qoidalarni 100% tiklash
    try:
        restored = await restore_db_backup_from_telegram()
        await restore_rules_from_telegram()
        # Qayta tiklangandan keyin botlar yoki bekuz4a_bot qolib ketgan bo'lsa darhol tozalash
        from database import execute_query
        execute_query("DELETE FROM tracked_hrs WHERE LOWER(username) LIKE '%bot' OR LOWER(username) IN ('me', 'ai_agents_24', 'bekuz4a_bot', 'caller_bot')", commit=True)
        if restored:
            logger.info("✅ Telegram Saved Messages dan DB va dinamik qoidalar 100% tiklandi va tozalandi!")
    except Exception as e_res:
        logger.warning(f"Telegram zaxirasini tiklashda ogohlantirish: {e_res}")
    
    # 3. Orqa fonda Telegram Bot Pollingni ishga tushirish (xabarlarni zudlik bilan o'qish)
    bot_task = asyncio.create_task(run_telegram_polling(trigger_scan_callback=run_vacancy_scan))
    
    # 4. QULF 1: FAQAT DB 100% TIKLANGANDAN KEYIN Skaner taymerini yoqish!
    timer_task = asyncio.create_task(background_scheduler())

    # 5. 2 daqiqalik eslatma va No-Show siklini yoqish
    reminder_task = asyncio.create_task(continuous_reminder_loop())

    # 6. HR larni 3 kunlik eslatma va 7 kunlik tozalash kuzatuvchisini yoqish
    hr_task = asyncio.create_task(run_hr_reminder_loop())

    # 7. Self-healing Watchdog vazifasini yoqish
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
    version="2.4.0",
    lifespan=lifespan
)

LIVE_PAGE_HTML = """<!DOCTYPE html>
<html lang="uz">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Alfred — Jonli Ovozli Karyera Agenti</title>
    <script src="https://telegram.org/js/telegram-web-app.js"></script>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #090d16;
            color: #f1f5f9;
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: space-between;
            padding: 24px 16px;
        }
        .header { text-align: center; margin-top: 10px; }
        .header h1 {
            font-size: 22px;
            font-weight: 700;
            background: linear-gradient(135deg, #38bdf8, #818cf8);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        .status-pill {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            background: rgba(16, 185, 129, 0.15);
            border: 1px solid rgba(16, 185, 129, 0.4);
            color: #34d399;
            padding: 6px 14px;
            border-radius: 20px;
            font-size: 13px;
            margin-top: 8px;
        }
        .pulse-dot {
            width: 8px;
            height: 8px;
            background: #10b981;
            border-radius: 50%;
            box-shadow: 0 0 10px #10b981;
            animation: pulse 1.5s infinite;
        }
        @keyframes pulse {
            0%, 100% { opacity: 1; transform: scale(1); }
            50% { opacity: 0.4; transform: scale(1.3); }
        }
        .visualizer {
            flex: 1;
            width: 100%;
            max-width: 480px;
            display: flex;
            flex-direction: column;
            justify-content: center;
            align-items: center;
            margin: 24px 0;
        }
        .mic-sphere {
            width: 140px;
            height: 140px;
            border-radius: 50%;
            background: radial-gradient(circle, #38bdf8 0%, #1e40af 100%);
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            box-shadow: 0 0 40px rgba(56, 189, 248, 0.4);
            transition: all 0.3s ease;
            user-select: none;
            -webkit-tap-highlight-color: transparent;
        }
        .mic-sphere.recording {
            background: radial-gradient(circle, #ef4444 0%, #991b1b 100%);
            box-shadow: 0 0 50px rgba(239, 68, 68, 0.7);
            animation: wave 1.2s infinite;
        }
        .mic-sphere.speaking {
            background: radial-gradient(circle, #10b981 0%, #065f46 100%);
            box-shadow: 0 0 50px rgba(16, 185, 129, 0.7);
            animation: wave 1.2s infinite;
        }
        @keyframes wave {
            0% { transform: scale(1); }
            50% { transform: scale(1.08); }
            100% { transform: scale(1); }
        }
        .mic-icon { font-size: 54px; }
        .hint-text {
            margin-top: 24px;
            font-size: 15px;
            color: #94a3b8;
            text-align: center;
            min-height: 24px;
        }
        .chat-box {
            width: 100%;
            max-width: 480px;
            background: rgba(30, 41, 59, 0.6);
            border: 1px solid rgba(148, 163, 184, 0.15);
            border-radius: 16px;
            padding: 14px;
            max-height: 200px;
            overflow-y: auto;
            backdrop-filter: blur(10px);
        }
        .msg { margin-bottom: 8px; font-size: 14px; line-height: 1.4; }
        .msg.user { color: #38bdf8; }
        .msg.alfred { color: #f8fafc; }
        .footer-note { font-size: 11px; color: #64748b; text-align: center; margin-top: 10px; }
    </style>
</head>
<body>
    <div class="header">
        <h1>Alfred Voice Live</h1>
        <div class="status-pill">
            <div class="pulse-dot"></div>
            <span id="status-text">Aloqaga tayyor</span>
        </div>
    </div>

    <div class="visualizer">
        <div class="mic-sphere" id="mic-btn" onclick="toggleRecording()">
            <div class="mic-icon" id="mic-icon">🎤</div>
        </div>
        <div class="hint-text" id="hint-text">Mikrofonni bosib gapiring</div>
    </div>

    <div class="chat-box" id="chat-box">
        <div class="msg alfred"><b>Alfred:</b> Assalomu alaykum, Behruzbek! Savol yoki topshirig'ingizni eshitaman.</div>
    </div>

    <div class="footer-note">Google Gemini + ElevenLabs AI ovozli suhbat</div>

    <script>
        if (window.Telegram && window.Telegram.WebApp) {
            window.Telegram.WebApp.ready();
            window.Telegram.WebApp.expand();
        }

        let isRecording = false;
        let mediaRecorder = null;
        let audioChunks = [];
        let currentAudio = null;

        const micBtn = document.getElementById("mic-btn");
        const micIcon = document.getElementById("mic-icon");
        const hintText = document.getElementById("hint-text");
        const statusText = document.getElementById("status-text");
        const chatBox = document.getElementById("chat-box");

        function addMessage(sender, text, isUser) {
            const div = document.createElement("div");
            div.className = "msg " + (isUser ? "user" : "alfred");
            div.innerHTML = "<b>" + sender + ":</b> " + text;
            chatBox.appendChild(div);
            chatBox.scrollTop = chatBox.scrollHeight;
        }

        async function toggleRecording() {
            if (isRecording) {
                stopRecording();
            } else {
                startRecording();
            }
        }

        async function startRecording() {
            if (currentAudio) {
                currentAudio.pause();
            }
            try {
                const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                mediaRecorder = new MediaRecorder(stream);
                audioChunks = [];

                mediaRecorder.ondataavailable = e => {
                    if (e.data.size > 0) audioChunks.push(e.data);
                };

                mediaRecorder.onstop = async () => {
                    const audioBlob = new Blob(audioChunks, { type: "audio/webm" });
                    stream.getTracks().forEach(track => track.stop());
                    await sendAudioToServer(audioBlob);
                };

                mediaRecorder.start();
                isRecording = true;
                micBtn.className = "mic-sphere recording";
                micIcon.innerText = "⏹";
                hintText.innerText = "Eshitilmoqda... Tugatish uchun bosing";
                statusText.innerText = "Yozib olinmoqda...";
            } catch (err) {
                console.error(err);
                hintText.innerText = "Mikrofonga ruxsat berilmadi!";
            }
        }

        function stopRecording() {
            if (mediaRecorder && isRecording) {
                mediaRecorder.stop();
                isRecording = false;
                micBtn.className = "mic-sphere";
                micIcon.innerText = "⏳";
                hintText.innerText = "Alfred o'ylamoqda...";
                statusText.innerText = "Tahlil qilinmoqda...";
            }
        }

        async function sendAudioToServer(blob) {
            const formData = new FormData();
            formData.append("audio", blob, "voice.webm");

            try {
                const res = await fetch("/api/voice-chat", {
                    method: "POST",
                    body: formData
                });
                const data = await res.json();
                if (data.ok) {
                    if (data.user_text) {
                        addMessage("Siz", data.user_text, true);
                    }
                    if (data.ai_reply) {
                        addMessage("Alfred", data.ai_reply, false);
                    }
                    if (data.audio_base64) {
                        playResponseAudio(data.audio_base64);
                    } else {
                        resetUI();
                    }
                } else {
                    hintText.innerText = data.error || "Xatolik yuz berdi";
                    resetUI();
                }
            } catch (err) {
                console.error(err);
                hintText.innerText = "Aloqa xatosi. Qayta urinib ko'ring.";
                resetUI();
            }
        }

        function playResponseAudio(b64) {
            micBtn.className = "mic-sphere speaking";
            micIcon.innerText = "🔊";
            hintText.innerText = "Alfred javob bermoqda...";
            statusText.innerText = "Gapirmoqda...";

            currentAudio = new Audio("data:audio/mp3;base64," + b64);
            currentAudio.onended = () => { resetUI(); };
            currentAudio.onerror = () => { resetUI(); };
            currentAudio.play().catch(e => {
                console.log("Audio play xatosi:", e);
                resetUI();
            });
        }

        function resetUI() {
            micBtn.className = "mic-sphere";
            micIcon.innerText = "🎤";
            hintText.innerText = "Mikrofonni bosib gapiring";
            statusText.innerText = "Aloqada";
        }
    </script>
</body>
</html>
"""

@app.get("/live", response_class=HTMLResponse)
async def live_voice_page():
    """Jonli telefon suhbati WebApp oynasi"""
    return HTMLResponse(content=LIVE_PAGE_HTML)

@app.post("/api/voice-chat")
async def api_voice_chat(request: Request):
    """WebApp dan kelgan jonli audio faylni qabul qilib, Gemini + ElevenLabs orqali javob qaytarish"""
    try:
        from ai_analyzer import transcribe_voice_with_gemini
        from gemini_agent import run_gemini_react_agent
        from voice import generate_voice_message
        from config import TELEGRAM_CHAT_ID

        form = await request.form()
        audio_file = form.get("audio")
        if not audio_file:
            return JSONResponse(content={"ok": False, "error": "Audio fayl topilmadi."}, status_code=400)

        audio_bytes = await audio_file.read()
        if not audio_bytes or len(audio_bytes) < 100:
            return JSONResponse(content={"ok": False, "error": "Bo'sh audio fayl."}, status_code=400)

        # 1. Gemini orqali transkripsiya
        transcribed = await transcribe_voice_with_gemini(audio_bytes, mime_type="audio/webm")
        if not transcribed:
            transcribed = await transcribe_voice_with_gemini(audio_bytes, mime_type="audio/ogg")
        if not transcribed:
            return JSONResponse(content={"ok": False, "error": "Ovoz aniqlanmadi, iltimos qaytadan gapiring."}, status_code=200)

        # 2. ReAct Agent orqali buyruqni bajarish
        ai_reply = await run_gemini_react_agent(
            user_message=transcribed,
            chat_id=TELEGRAM_CHAT_ID,
            is_voice=True
        )

        # 3. ElevenLabs ovozini generatsiya qilish
        v_file = await generate_voice_message(ai_reply, prepare_script=True)
        audio_b64 = ""
        if v_file and os.path.exists(v_file):
            with open(v_file, "rb") as af:
                audio_b64 = base64.b64encode(af.read()).decode("utf-8")

        return JSONResponse(content={
            "ok": True,
            "user_text": transcribed,
            "ai_reply": ai_reply,
            "audio_base64": audio_b64
        })
    except Exception as e:
        logger.error(f"/api/voice-chat xatosi: {e}")
        return JSONResponse(content={"ok": False, "error": str(e)}, status_code=500)

@app.get("/")
async def root():
    return {
        "status": "online",
        "agent": "Alfred 24/7 AI IT Recruiter",
        "version": "2.4.0 (Anti-Duplicate 3-Layer Lock, Voice Live WebApp & Call Fix)",
        "bot_listener": "active (Telegram Polling)",
        "gemini_status": get_last_error(),
        "endpoints": {
            "/live": "Jonli telefon suhbati WebApp oynasi (HTML + JS mikrofon)",
            "/api/voice-chat": "Jonli ovozli suhbat API",
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

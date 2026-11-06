import logging
import asyncio
import httpx
from typing import Dict, Any, Optional
from config import TELEGRAM_BOT_TOKEN
from ai_analyzer import chat_with_alfred, get_last_error
from notifier import send_telegram_message
from database import get_recent_vacancies

logger = logging.getLogger("Alfred.BotHandler")

async def process_telegram_update(update: Dict[str, Any], trigger_scan_callback=None):
    """Telegramdan kelgan yangilanishlarni (xabarlarni) qayta ishlash"""
    message = update.get("message")
    if not message:
        return
        
    chat_id = str(message.get("chat", {}).get("id"))
    text = message.get("text", "")
    voice = message.get("voice")
    
    # 1. /start buyrug'i
    if text == "/start":
        welcome_text = (
            "🤖 <b>Assalomu alaykum! Men Alfred — Sizning shaxsiy AI Vakansiya Agentingizman.</b>\n\n"
            "Men 24/7 rejimda ishlayman va siz uchun O'zbekistondagi nufuzli manbalar hamda Telegram kanallardan quyidagi yo'nalishlarda yangi ishlarni qidiraman:\n"
            "• Python (Django, FastAPI)\n"
            "• PostgreSQL, Docker, Redis, Celery\n"
            "• AI Agentlar va LLM integratsiyasi\n"
            "• Junior, Intern va Freelance o'rinlari\n\n"
            "<b>Mavjud buyruqlar:</b>\n"
            "🔍 /scan yoki 'tekshir' — Hoziroq yangi vakansiyalarni qidirish\n"
            "📊 /status — Tizim va AI holati, oxirgi xatolik va topilgan ishlar\n"
            "💬 Menga istalgan savolingizni yozishingiz mumkin (rezyume, intervyu, texnologiyalar haqida), men sizga o'zbek tilida yordam beraman!"
        )
        await send_telegram_message(welcome_text, chat_id=chat_id)
        return
        
    # 2. /scan yoki 'tekshir' buyrug'i
    if text in ["/scan", "tekshir", "Tekshir", "qidir", "Qidir"]:
        await send_telegram_message(
            "🔎 <b>Qidiruv orqa fonda tezkor boshlandi...</b>\n"
            "Barcha Telegram kanallar va HH.uz tekshirilmoqda. Barcha yangi e'lonlar birgalikda (batch) sun'iy intellekt orqali tahlil qilinadi. Mos vakansiyalar topilsa darhol xabar va ovozli xabar beraman!",
            chat_id=chat_id
        )
        if trigger_scan_callback:
            # Orqa fonda skanerni ishga tushirish
            asyncio.create_task(trigger_scan_callback())
        return
        
    # 3. /status buyrug'i (Oxirgi xatolik va AI holatini ko'rsatish)
    if text == "/status":
        recent = get_recent_vacancies(limit=5)
        last_err = get_last_error()
        status_text = (
            "🟢 <b>Alfred 24/7 Agent Holati:</b> Faol\n\n"
            f"🤖 <b>AI Tizimi (Gemini) holati:</b>\n<i>{last_err}</i>\n\n"
            f"📊 <b>Bazada saqlangan ishlar soni:</b> {len(recent)}\n"
        )
        if recent:
            status_text += "\n<b>Oxirgi topilgan 5 ta vakansiya:</b>\n"
            for r in recent:
                status_text += f"• {r.get('title')} ({r.get('match_score')}%) - <a href='{r.get('url')}'>Havola</a>\n"
        else:
            status_text += "\nHali birorta yangi vakansiya qayd etilmadi."
            
        await send_telegram_message(status_text, chat_id=chat_id)
        return
        
    # 4. Ovozli xabar kelsa
    if voice:
        await send_telegram_message(
            "🎙 Ovozli xabaringizni qabul qildim! Men sizga doim yordamga tayyorman. Menga savollaringizni matn ko'rinishida yozsangiz, darhol batafsil javob va maslahat beraman.",
            chat_id=chat_id
        )
        return

    # 5. Oddiy matnli xabar bo'lsa (Gemini orqali suhbat)
    if text:
        logger.info(f"Foydalanuvchi xabari: {text}")
        ai_reply = await chat_with_alfred(text)
        await send_telegram_message(ai_reply, chat_id=chat_id)


async def run_telegram_polling(trigger_scan_callback=None):
    """
    Telegram Bot API dan xabarlarni getUpdates (long polling) orqali fonda uzluksiz o'qib borish.
    Bu FastAPI serveri bilan parallel fonda (asyncio task) 24/7 ishlaydi.
    """
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN topilmadi, polling ishga tushmadi.")
        return

    # Avval webhook faol bo'lsa uni o'chiramiz (Conflict xatoligi bo'lmasligi uchun)
    delete_wh_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteWebhook?drop_pending_updates=false"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.get(delete_wh_url)
            logger.info("Eski Telegram webhook tozalandi, Polling rejimiga o'tildi.")
    except Exception as e:
        logger.warning(f"deleteWebhook bajarishda ogohlantirish: {e}")

    offset: Optional[int] = None
    poll_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    
    logger.info("🟢 Telegram Bot Polling (uzluksiz qabul qilish) fonda ishga tushdi!")

    async with httpx.AsyncClient(timeout=35) as client:
        while True:
            try:
                params = {"timeout": 25}
                if offset is not None:
                    params["offset"] = offset

                resp = await client.get(poll_url, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("ok"):
                        updates = data.get("result", [])
                        for update in updates:
                            offset = update["update_id"] + 1
                            # Har bir xabarni async task sifatida parallel qayta ishlash
                            asyncio.create_task(
                                process_telegram_update(update, trigger_scan_callback=trigger_scan_callback)
                            )
                elif resp.status_code == 409:
                    logger.warning("Telegram 409 Conflict: boshqa instansiya faol, 5 soniya kutamiz...")
                    await asyncio.sleep(5)
                else:
                    logger.warning(f"getUpdates status {resp.status_code}: {resp.text[:100]}")
                    await asyncio.sleep(3)
            except asyncio.CancelledError:
                logger.info("Telegram Polling to'xtatildi.")
                break
            except Exception as e:
                logger.error(f"Telegram polling loop xatoligi: {e}")
                await asyncio.sleep(3)

import logging
from typing import Dict, Any
from ai_analyzer import chat_with_alfred
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
            "🔍 /scan — Hoziroq yangi vakansiyalarni qidirish\n"
            "📊 /status — Tizim holati va oxirgi topilgan ishlar\n"
            "💬 Menga istalgan savolingizni yozishingiz mumkin (rezyume, intervyu, texnologiyalar haqida), men sizga o'zbek tilida yordam beraman!"
        )
        await send_telegram_message(welcome_text, chat_id=chat_id)
        return
        
    # 2. /scan buyrug'i
    if text == "/scan":
        await send_telegram_message("🔎 <b>Qidiruv boshlandi...</b> Barcha Telegram kanallar va HH.uz tekshirilmoqda. Yangilik bo'lsa darhol xabar beraman!", chat_id=chat_id)
        if trigger_scan_callback:
            count = await trigger_scan_callback()
            if count == 0:
                await send_telegram_message("ℹ️ Hozircha yangi mos vakansiyalar topilmadi. Qidiruvni davom ettiraman!", chat_id=chat_id)
        return
        
    # 3. /status buyrug'i
    if text == "/status":
        recent = get_recent_vacancies(limit=5)
        status_text = f"🟢 <b>Alfred 24/7 Agent Holati: Faol</b>\n"
        status_text += f"Bazada saqlangan oxirgi ishlar soni: {len(recent)}\n\n"
        if recent:
            status_text += "<b>Oxirgi topilgan 5 ta vakansiya:</b>\n"
            for r in recent:
                status_text += f"• {r.get('title')} ({r.get('match_score')}%) - <a href='{r.get('url')}'>Havola</a>\n"
        else:
            status_text += "Hali birorta yangi vakansiya qayd etilmadi."
        await send_telegram_message(status_text, chat_id=chat_id)
        return
        
    # 4. Ovozli xabar kelsa
    if voice:
        await send_telegram_message(
            "🎙 Ovozli xabaringizni qabul qildim! Men sizga doim yordamga tayyorman. Menga savollaringizni matn ko'rinishida yuborsangiz, darhol batafsil maslahat beraman.",
            chat_id=chat_id
        )
        return

    # 5. Oddiy matnli xabar bo'lsa (Gemini orqali suhbat)
    if text:
        logger.info(f"Foydalanuvchi xabari: {text}")
        ai_reply = await chat_with_alfred(text)
        await send_telegram_message(ai_reply, chat_id=chat_id)

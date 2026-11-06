import httpx
import urllib.parse
import os
import logging
from typing import Dict, Any, List
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, CALLMEBOT_USERNAME
from voice import generate_voice_message

logger = logging.getLogger("Alfred.Notifier")

async def send_callmebot_call(text: str = "Sizga mos yangi vakansiya chiqdi"):
    """CallMeBot API orqali Telegramdan qo'ng'iroq qilish"""
    if not CALLMEBOT_USERNAME:
        logger.info("CallMeBot username kiritilmagan, qo'ng'iroq o'tkazib yuborildi.")
        return
        
    username = CALLMEBOT_USERNAME.strip()
    if not username.startswith("@"):
        username = f"@{username}"
        
    encoded_text = urllib.parse.quote(text)
    url = f"http://api.callmebot.com/start.php?user={username}&text={encoded_text}&lang=ru-RU-Standard-B"
    
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            logger.info(f"CallMeBot qo'ng'iroq so'rovi yuborildi: {resp.status_code}")
    except Exception as e:
        logger.error(f"CallMeBot qo'ng'iroq qilishda xatolik: {e}")

async def send_telegram_message(text: str, chat_id: str = "") -> bool:
    """Telegram bot orqali matnli xabar yuborish"""
    target_chat_id = chat_id or TELEGRAM_CHAT_ID
    if not TELEGRAM_BOT_TOKEN or not target_chat_id:
        logger.warning("Telegram Bot Token yoki Chat ID sozlanmagan!")
        return False
        
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": target_chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload)
            return resp.status_code == 200
    except Exception as e:
        logger.error(f"Telegram matn yuborishda xatolik: {e}")
        return False

async def send_telegram_voice(file_path: str, caption: str = "", chat_id: str = "") -> bool:
    """Telegram bot orqali ovozli xabar (voice/audio) yuborish"""
    target_chat_id = chat_id or TELEGRAM_CHAT_ID
    if not TELEGRAM_BOT_TOKEN or not target_chat_id:
        logger.warning("Telegram Bot Token yoki Chat ID sozlanmagan!")
        return False
        
    if not os.path.exists(file_path):
        logger.error(f"Audio fayl topilmadi: {file_path}")
        return False
        
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVoice"
    
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            with open(file_path, "rb") as f:
                files = {"voice": (os.path.basename(file_path), f, "audio/mpeg")}
                data = {
                    "chat_id": target_chat_id,
                    "caption": caption[:1024],
                    "parse_mode": "HTML"
                }
                resp = await client.post(url, data=data, files=files)
                return resp.status_code == 200
    except Exception as e:
        logger.error(f"Telegram audio yuborishda xatolik: {e}")
        return False
    finally:
        # Temp faylni o'chirish
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            pass

def format_vacancy_card(item: Dict[str, Any]) -> str:
    """Vakansiya ma'lumotlarini chiroyli HTML formatga keltirish"""
    analysis = item.get("analysis", {})
    score = analysis.get("match_score", 0)
    title = analysis.get("role_title") or item.get("title")
    company = analysis.get("company") or item.get("company")
    level = analysis.get("experience_level", "Junior")
    salary = analysis.get("salary", "Ko'rsatilmagan")
    summary = analysis.get("short_summary", "")
    reason = analysis.get("reason", "")
    url = item.get("url", "#")
    source = item.get("source", "Internet")
    tech_stack = ", ".join(analysis.get("tech_stack", [])) or "Python"

    card = (
        f"🎯 <b>Yangi mos vakansiya!</b> (Moslik: <b>{score}%</b>)\n\n"
        f"📌 <b>Lavozim:</b> {title}\n"
        f"🏢 <b>Kompaniya:</b> {company}\n"
        f"📊 <b>Daraja:</b> {level}\n"
        f"💰 <b>Maosh:</b> {salary}\n"
        f"🛠 <b>Texnologiyalar:</b> {tech_stack}\n"
        f"🌐 <b>Manba:</b> {source}\n\n"
        f"💡 <b>Alfred tahlili:</b>\n<i>{summary}</i>\n\n"
        f"🔍 <b>Nega mos:</b> {reason}\n\n"
        f"👉 <a href='{url}'><b>Vakansiyaga o'tish / Ariza topshirish</b></a>"
    )
    return card

async def notify_user_about_vacancies(matches: List[Dict[str, Any]]):
    """
    Topilgan barcha mos vakansiyalar bo'yicha foydalanuvchini ogohlantirish:
    1. Eng yaxshi (Top 1) vakansiyani o'zbekcha ovozli xabarga aylantirib jo'natadi.
    2. CallMeBot orqali qo'ng'iroq chaqiradi.
    3. Barcha qolgan vakansiyalarni Telegram chatga chiroyli matn shaklida yuboradi.
    """
    if not matches:
        return
        
    # Ball bo'yicha saralash (eng yuqori ball birinchi)
    matches.sort(key=lambda x: x.get("analysis", {}).get("match_score", 0), reverse=True)
    
    top_vacancy = matches[0]
    top_analysis = top_vacancy.get("analysis", {})
    voice_text = top_analysis.get("voice_text") or f"Salom! Sizga yangi mos vakansiya topildi: {top_analysis.get('role_title', 'Python Dasturchi')}."
    
    # 1. Qo'ng'iroq qilish (CallMeBot)
    await send_callmebot_call(text="Sizga mos yangi vakansiya chiqdi! Alfred ovozli xabar qoldirdi.")
    
    # 2. Top-1 vakansiya uchun ovozli xabar yaratish va yuborish
    voice_file = await generate_voice_message(voice_text)
    if voice_file:
        caption = f"🎙 <b>Top-1 Vakansiya Ovozli Xulosasi:</b>\n<b>{top_analysis.get('role_title')}</b> ({top_analysis.get('match_score')}% moslik)"
        await send_telegram_voice(voice_file, caption=caption)
        
    # 3. Barcha mos vakansiyalarni matnli ko'rinishda yuborish
    for vac in matches:
        text_message = format_vacancy_card(vac)
        await send_telegram_message(text_message)

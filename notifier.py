import httpx
import urllib.parse
import os
import logging
from datetime import datetime
from typing import Dict, Any, List
from config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    CALLMEBOT_USERNAME,
    TASHKENT_TZ,
    CALL_START_HOUR,
    CALL_END_HOUR,
    MAX_MATCHES_PER_SCAN
)
from voice import generate_voice_message

logger = logging.getLogger("Alfred.Notifier")

def is_call_allowed_now() -> bool:
    """
    Qo'ng'iroq qilish vaqti (Toshkent vaqti bilan 8:00 dan 20:00 gacha) ekanligini tekshirish.
    Kechasi (20:00 dan ertalab 08:00 gacha) bezovta qilmaslik uchun qo'ng'iroq to'xtatiladi.
    """
    now = datetime.now(TASHKENT_TZ)
    current_hour = now.hour
    is_allowed = CALL_START_HOUR <= current_hour < CALL_END_HOUR
    if not is_allowed:
        logger.info(f"Hozir soat {now.strftime('%H:%M')} (ruxsat etilgan vaqt: {CALL_START_HOUR}:00 - {CALL_END_HOUR}:00). Qo'ng'iroq bekor qilindi.")
    return is_allowed

async def send_callmebot_call(text: str = "Sizga mos yangi vakansiya chiqdi"):
    """CallMeBot API orqali Telegramdan qo'ng'iroq qilish (faqat 8:00 dan 20:00 oralig'ida)"""
    if not is_call_allowed_now():
        return

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
    Topilgan mos vakansiyalar bo'yicha foydalanuvchini ogohlantirish:
    1. Maksimal 10 ta eng yaxshi vakansiya tanlab olinadi.
    2. Eng yuqori ball to'plagan 1 ta eng mos vakansiya ovozli xabarga aylantiriladi.
    3. Agar vaqt 08:00 dan 20:00 oralig'ida bo'lsa, CallMeBot orqali qo'ng'iroq qilinadi.
    4. Barcha 10 ta mos vakansiyalar Telegram chatga matn shaklida yuboriladi.
    """
    if not matches:
        return
        
    # Ball bo'yicha saralash (eng yuqori ball birinchi)
    matches.sort(key=lambda x: x.get("analysis", {}).get("match_score", 0), reverse=True)
    
    # Maksimal 10 ta yangi vakansiya bilan cheklash
    top_matches = matches[:MAX_MATCHES_PER_SCAN]
    
    # 1 TA ENG MOS VAKANSIYA
    top_vacancy = top_matches[0]
    top_analysis = top_vacancy.get("analysis", {})
    top_title = top_analysis.get('role_title', 'Python Dasturchi')
    voice_text = top_analysis.get("voice_text") or f"Salom Behruz! Sizga yangi mos vakansiya topildi: {top_title}."
    
    # 1. Qo'ng'iroq qilish (faqat 8:00 - 20:00 oralig'ida)
    await send_callmebot_call(text=f"Sizga mos yangi {top_title} vakansiyasi chiqdi!")
    
    # 2. Eng mos 1 ta vakansiya uchun o'zbekcha ovozli xabar
    voice_file = await generate_voice_message(voice_text)
    if voice_file:
        caption = f"🎙 <b>Top-1 Eng Mos Vakansiya Ovozli Xabari:</b>\n<b>{top_title}</b> ({top_analysis.get('match_score')}% moslik)"
        await send_telegram_voice(voice_file, caption=caption)
        
    # 3. Barcha qolgan (10 tagacha) mos vakansiyalarni matnli ko'rinishda yuborish
    for vac in top_matches:
        text_message = format_vacancy_card(vac)
        await send_telegram_message(text_message)

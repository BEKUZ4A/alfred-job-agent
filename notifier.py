import httpx
import urllib.parse
import os
import logging
import asyncio
from datetime import datetime, timezone
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

def format_posted_at(posted_at: str) -> str:
    """
    Vakansiya e'lon qilingan vaqtini o'qilishi oson ko'rinishda formatlash.
    ISO format (UTC) → Toshkent vaqti (UTC+5): "06-11-2026 10:30"
    HH.uz HTML scraping'dan kelgan oddiy matn bo'lsa, uni aynan qaytaradi.
    """
    if not posted_at:
        return "Noma'lum"
    try:
        # ISO format bo'lsa, Toshkent vaqtiga o'giramiz
        dt = datetime.fromisoformat(posted_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt_tash = dt.astimezone(TASHKENT_TZ)
        return dt_tash.strftime("%d-%m-%Y %H:%M")
    except (ValueError, TypeError):
        # ISO format bo'lmasa (masalan, HH.uz HTML "1 soat oldin"), aynan qaytaramiz
        return posted_at if posted_at else "Noma'lum"

async def make_call(text: str = "Sizga mos yangi vakansiya chiqdi", force: bool = False) -> bool:
    """
    CallMeBot API orqali Telegramdan qo'ng'iroq qilish (faqat 8:00 dan 20:00 oralig'ida).
    1. Agar CallMeBot FLOOD yoki xato qaytarsa, 10 soniya kutib 1 marta qayta urinsin (retry).
    2. Qo'ng'iroq muvaffaqiyatiga qarab True yoki False qaytaradi.
    """
    if not force and not is_call_allowed_now():
        logger.info("Qo'ng'iroq vaqti emas (8:00 dan 20:00 gacha ruxsat etilgan), qo'ng'iroq bekor qilindi.")
        return False

    if not CALLMEBOT_USERNAME:
        logger.info("CallMeBot username kiritilmagan, qo'ng'iroq o'tkazib yuborildi.")
        return False
        
    username = CALLMEBOT_USERNAME.strip()
    if not username.startswith("@"):
        username = f"@{username}"
        
    encoded_text = urllib.parse.quote(text)
    url = f"http://api.callmebot.com/start.php?user={username}&text={encoded_text}&lang=ru-RU-Standard-B"
    
    async def _execute_request() -> tuple[bool, str]:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url)
                body = resp.text.strip()
                lower_body = body.lower()

                if resp.status_code != 200:
                    return False, f"HTTP {resp.status_code}: {body[:150]}"

                # CallMeBot FLOOD yoki server xatosi matnini tekshirish
                if "flood" in lower_body or "too many users" in lower_body:
                    return False, f"FLOOD xatosi ({body[:150]})"

                if "error" in lower_body:
                    return False, f"Server xatosi ({body[:150]})"

                return True, body[:150]
        except Exception as e:
            return False, f"Tarmoq/ulanish xatosi: {e}"

    try:
        # 1-urinish
        logger.info(f"📞 CallMeBot orqali qo'ng'iroq qilinmoqda ({username})...")
        success, msg = await _execute_request()
        if success:
            logger.info(f"✅ CallMeBot qo'ng'irog'i muvaffaqiyatli amalga oshirildi: {msg}")
            return True

        # 1. Agar CallMeBot FLOOD yoki xato qaytarsa, 10 soniya kutib 1 marta qayta urinsin (retry)
        logger.warning(f"⚠️ CallMeBot xatolik berdi: {msg}. 10 soniya kutib qayta uriniladi (retry)...")
        await asyncio.sleep(10)

        # 2-urinish (retry)
        logger.info("📞 CallMeBot qayta urinish (retry) amalga oshirilmoqda...")
        success, msg = await _execute_request()
        if success:
            logger.info(f"✅ CallMeBot qayta urinishda muvaffaqiyatli amalga oshirildi: {msg}")
            return True
        else:
            logger.error(f"❌ CallMeBot 2-urinishda ham muvaffaqiyatsiz bo'ldi: {msg}")
            return False
    except Exception as e:
        logger.error(f"make_call da kutilmagan xatolik: {e}")
        return False

# Orqaga moslik (backward compatibility) uchun
send_callmebot_call = make_call

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
            if resp.status_code != 200:
                logger.warning(f"Telegram xabar yuborishda HTTP {resp.status_code}: {resp.text[:200]}")
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

def format_vacancy_card(item: Dict[str, Any], rank: int = 0) -> str:
    """
    Vakansiya ma'lumotlarini chiroyli HTML formatga keltirish.
    E'lon qilingan vaqt (posted_at) va tartib raqami (rank) ham ko'rsatiladi.
    """
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

    # ✅ E'lon qilingan vaqtni formatlash
    posted_at_raw = item.get("posted_at", "")
    posted_at_formatted = format_posted_at(posted_at_raw)

    # Rank belgisi (1-chi eng mos)
    rank_emoji = ""
    if rank == 1:
        rank_emoji = "🥇 "
    elif rank == 2:
        rank_emoji = "🥈 "
    elif rank == 3:
        rank_emoji = "🥉 "

    card = (
        f"{rank_emoji}🎯 <b>Yangi mos vakansiya!</b> (Moslik: <b>{score}%</b>)\n\n"
        f"📌 <b>Lavozim:</b> {title}\n"
        f"🏢 <b>Kompaniya:</b> {company}\n"
        f"📊 <b>Daraja:</b> {level}\n"
        f"💰 <b>Maosh:</b> {salary}\n"
        f"🛠 <b>Texnologiyalar:</b> {tech_stack}\n"
        f"📅 <b>E'lon qilingan:</b> {posted_at_formatted}\n"  # ✅ Yangi qator
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
    3. CallMeBot orqali qo'ng'iroq qilinadi (make_call: 10s retry bilan).
       Agar qo'ng'iroq baribir o'tmasa, dastur to'xtab qolmasdan Telegram orqali ogohlantirish yuboriladi:
       "🚨 DIQQAT: Yangi vakansiya topildi, lekin qo'ng'iroq liniyasi bandligi sababli xabar yuborildi!"
    4. Eng mos vakansiyaning ovozli va matnli xabari Telegram chatga yetkazib beriladi.
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
    top_title = top_analysis.get("role_title", "Python Dasturchi")
    top_score = top_analysis.get("match_score", 0)
    top_company = top_analysis.get("company", top_vacancy.get("company", ""))

    # ✅ E'lon qilingan vaqtni ovozli xabarga ham qo'shamiz
    top_posted_at = format_posted_at(top_vacancy.get("posted_at", ""))

    voice_text = top_analysis.get("voice_text") or (
        f"Salom Behruz! Sizga yangi mos vakansiya topildi: {top_title}. "
        f"Kompaniya: {top_company}. Moslik: {top_score} foiz. "
        f"E'lon qilingan: {top_posted_at}. Batafsil Telegram chatda."
    )
    
    # 1. Qo'ng'iroq qilish (make_call: faqat 8:00 - 20:00 oralig'ida)
    call_success = False
    call_attempted = is_call_allowed_now() and bool(CALLMEBOT_USERNAME)
    if call_attempted:
        try:
            call_success = await make_call(text=f"Sizga mos yangi {top_title} vakansiyasi chiqdi! Moslik: {top_score}%")
        except Exception as e:
            logger.error(f"make_call chaqirishda xatolik: {e}")
            call_success = False

    # 2. Agar qo'ng'iroq baribir o'tmasa, dastur to'xtab qolmasin — darhol Telegram bot orqali ogohlantirish yuborish
    if call_attempted and not call_success:
        try:
            logger.warning("Qo'ng'iroq o'tmadi (liniya band / FLOOD). Telegram orqali ogohlantirish yuborilmoqda...")
            await send_telegram_message(
                "🚨 <b>DIQQAT: Yangi vakansiya topildi, lekin qo'ng'iroq liniyasi bandligi sababli xabar yuborildi!</b>"
            )
        except Exception as e:
            logger.error(f"Ogohlantirish matnini yuborishda xatolik: {e}")
    
    # 3. Eng mos 1 ta vakansiya uchun o'zbekcha ovozli xabar
    try:
        voice_file = await generate_voice_message(voice_text)
        if voice_file:
            caption = (
                f"🎙 <b>Top-1 Eng Mos Vakansiya Ovozli Xabari:</b>\n"
                f"<b>{top_title}</b> ({top_score}% moslik)\n"
                f"📅 E'lon qilingan: {top_posted_at}"
            )
            await send_telegram_voice(voice_file, caption=caption)
    except Exception as e:
        logger.error(f"Telegram ovozli xabar yuborishda xatolik: {e}")
        
    # 4. Barcha qolgan (10 tagacha) mos vakansiyalarni matnli ko'rinishda yuborish
    for i, vac in enumerate(top_matches, start=1):
        try:
            text_message = format_vacancy_card(vac, rank=i)
            await send_telegram_message(text_message)
        except Exception as e:
            logger.error(f"Telegram matn yuborishda xatolik: {e}")

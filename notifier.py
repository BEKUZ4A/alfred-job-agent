import httpx
import urllib.parse
import os
import logging
import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
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
    """Vakansiya e'lon qilingan vaqtini Toshkent vaqtiga o'girish"""
    if not posted_at:
        return "Noma'lum"
    try:
        dt = datetime.fromisoformat(posted_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt_tash = dt.astimezone(TASHKENT_TZ)
        return dt_tash.strftime("%d-%m-%Y %H:%M")
    except (ValueError, TypeError):
        return posted_at if posted_at else "Noma'lum"

async def make_call(text: str = "Sizga mos yangi vakansiya chiqdi", force: bool = False) -> bool:
    """
    CallMeBot API orqali Telegramdan qo'ng'iroq qilish.
    Agar CallMeBot FLOOD yoki xato qaytarsa, 10 soniya kutib 1 marta qayta urinadi.
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

                if "flood" in lower_body or "too many users" in lower_body:
                    return False, f"FLOOD xatosi ({body[:150]})"

                if "error" in lower_body:
                    return False, f"Server xatosi ({body[:150]})"

                return True, body[:150]
        except Exception as e:
            return False, f"Tarmoq/ulanish xatosi: {e}"

    try:
        logger.info(f"📞 CallMeBot orqali qo'ng'iroq qilinmoqda ({username})...")
        success, msg = await _execute_request()
        if success:
            logger.info(f"✅ CallMeBot qo'ng'irog'i muvaffaqiyatli amalga oshirildi: {msg}")
            return True

        logger.warning(f"⚠️ 1-urinish o'tmadi: {msg}. 10 soniya kutib qayta urinamiz...")
        await asyncio.sleep(10)

        success2, msg2 = await _execute_request()
        if success2:
            logger.info(f"✅ 2-urinishda CallMeBot qo'ng'irog'i muvaffaqiyatli o'tdi: {msg2}")
            return True
        else:
            logger.error(f"❌ 2-urinishda ham qo'ng'iroq o'tmadi: {msg2}")
            return False
    except Exception as e:
        logger.error(f"make_call da kutilmagan xatolik: {e}")
        return False

async def send_telegram_message(text: str, reply_markup: Optional[Dict] = None, chat_id: str = "") -> bool:
    """Telegram bot orqali matnli xabar yuborish (uzun matnlarni 3900 belgidan bo'laklab yuboradi)"""
    target_chat_id = chat_id or TELEGRAM_CHAT_ID
    if not TELEGRAM_BOT_TOKEN or not target_chat_id:
        logger.warning("Telegram Bot Token yoki Chat ID sozlanmagan!")
        return False
        
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    max_chunk = 3900
    chunks = []
    if len(text) <= max_chunk:
        chunks = [text]
    else:
        lines = text.split("\n")
        cur_chunk = ""
        for line in lines:
            if len(cur_chunk) + len(line) + 1 <= max_chunk:
                cur_chunk += ("\n" if cur_chunk else "") + line
            else:
                if cur_chunk:
                    chunks.append(cur_chunk)
                cur_chunk = line
        if cur_chunk:
            chunks.append(cur_chunk)

    success = True
    async with httpx.AsyncClient(timeout=25) as client:
        for idx, chunk in enumerate(chunks):
            chunk_markup = reply_markup if idx == len(chunks) - 1 else None
            payload = {
                "chat_id": target_chat_id,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": False
            }
            if chunk_markup:
                payload["reply_markup"] = chunk_markup

            try:
                resp = await client.post(url, json=payload)
                if resp.status_code != 200:
                    payload.pop("parse_mode", None)
                    resp_retry = await client.post(url, json=payload)
                    if resp_retry.status_code != 200:
                        logger.warning(f"Telegram sendMessage HTTP {resp_retry.status_code}: {resp_retry.text[:200]}")
                        success = False
            except Exception as e:
                logger.error(f"Telegram chunk yuborishda xato: {e}")
                success = False

    return success

async def send_telegram_voice(file_path: str, caption: str = "", chat_id: str = "") -> bool:
    """Telegram bot orqali ovozli xabar (voice/audio) yuborish"""
    target_chat_id = chat_id or TELEGRAM_CHAT_ID
    if not TELEGRAM_BOT_TOKEN or not target_chat_id:
        return False
        
    if not os.path.exists(file_path):
        return False
        
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendVoice"
    
    mime_type = "audio/ogg" if file_path.endswith(".ogg") else ("audio/wav" if file_path.endswith(".wav") else "audio/mpeg")

    try:
        async with httpx.AsyncClient(timeout=35) as client:
            with open(file_path, "rb") as f:
                files = {"voice": (os.path.basename(file_path), f, mime_type)}
                data = {
                    "chat_id": target_chat_id,
                    "caption": caption[:1024],
                    "parse_mode": "HTML"
                }
                resp = await client.post(url, data=data, files=files)
                if resp.status_code == 200:
                    return True

            # sendAudio orqali zaxira
            audio_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendAudio"
            with open(file_path, "rb") as f2:
                a_files = {"audio": (os.path.basename(file_path), f2, mime_type)}
                a_resp = await client.post(audio_url, data=data, files=a_files)
                return a_resp.status_code == 200
    except Exception as e:
        logger.error(f"Telegram voice yuborishda xato: {e}")
        return False
    finally:
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            pass

async def send_telegram_document(file_path: str, caption: str = "", chat_id: str = "") -> bool:
    """Telegram bot orqali hujjat (PDF Rezyume) yuborish"""
    target_chat_id = chat_id or TELEGRAM_CHAT_ID
    if not TELEGRAM_BOT_TOKEN or not target_chat_id:
        return False

    if not os.path.exists(file_path):
        logger.error(f"Hujjat fayli topilmadi: {file_path}")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
    try:
        async with httpx.AsyncClient(timeout=40) as client:
            with open(file_path, "rb") as f:
                files = {"document": (os.path.basename(file_path), f, "application/pdf")}
                data = {
                    "chat_id": target_chat_id,
                    "caption": caption[:1024],
                    "parse_mode": "HTML"
                }
                resp = await client.post(url, data=data, files=files)
                return resp.status_code == 200
    except Exception as e:
        logger.error(f"Telegram document yuborishda xato: {e}")
        return False
    finally:
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            pass

def format_vacancy_card(item: Dict[str, Any], rank: int = 0) -> str:
    """Vakansiya yoki Frilans buyurtma kartochkasi"""
    analysis = item.get("analysis", {})
    score = analysis.get("match_score", 0)
    title = analysis.get("role_title") or item.get("title")
    company = analysis.get("company") or item.get("company")
    level = analysis.get("experience_level", "Junior / Frilans")
    salary = analysis.get("salary", "Kelishilgan narxda / Ko'rsatilmagan")
    summary = analysis.get("short_summary", "")
    reason = analysis.get("reason", "")
    url = item.get("url", "#")
    source = item.get("source", "Internet")
    tech_stack = ", ".join(analysis.get("tech_stack", [])) or "Python"
    red_flag = analysis.get("red_flag") or item.get("red_flag", "")

    job_type = analysis.get("job_type") or item.get("job_type", "vacancy")
    if "freelance" in str(job_type).lower() or "frilans" in str(job_type).lower() or "buyurtma" in str(job_type).lower():
        type_badge = "💰 <b>[FRILANS BUYURTMA]</b>"
    else:
        type_badge = "🏢 <b>[VAKANSIYA]</b>"

    src_lower = str(source).lower()
    if "hh" in src_lower:
        source_badge = "<b>[HH.UZ]</b>"
    elif "olx" in src_lower:
        source_badge = "<b>[OLX.UZ]</b>"
    else:
        source_badge = f"<b>[TELEGRAM {source}]</b>" if source.startswith("@") else "<b>[TELEGRAM]</b>"

    posted_at_formatted = format_posted_at(item.get("posted_at", ""))

    rank_emoji = ""
    if rank == 1:
        rank_emoji = "🥇 "
    elif rank == 2:
        rank_emoji = "🥈 "
    elif rank == 3:
        rank_emoji = "🥉 "

    red_flag_alert = ""
    if red_flag:
        red_flag_alert = f"⚠️ <b>Ehtiyot bo'ling (Shubhali e'lon):</b> <i>{red_flag}</i>\n\n"

    card = (
        f"{red_flag_alert}"
        f"{rank_emoji}{type_badge} {source_badge} (Moslik: <b>{score}%</b>)\n\n"
        f"📌 <b>Nomi:</b> {title}\n"
        f"🏢 <b>Buyurtmachi/Kompaniya:</b> {company}\n"
        f"📊 <b>Daraja / Turi:</b> {level}\n"
        f"💰 <b>Byudjet / Maosh:</b> {salary}\n"
        f"🛠 <b>Texnologiyalar:</b> {tech_stack}\n"
        f"📅 <b>E'lon qilingan:</b> {posted_at_formatted}\n\n"
        f"💡 <b>Alfred tahlili:</b>\n<i>{summary}</i>\n\n"
        f"🔍 <b>Nega mos:</b> {reason}\n\n"
        f"👉 <a href='{url}'><b>E'longa o'tish / Bog'lanish</b></a>"
    )
    return card

async def notify_user_about_vacancies(matches: List[Dict[str, Any]]):
    """Topilgan mos vakansiyalar bo'yicha bildirishnoma yuborish"""
    if not matches:
        return
        
    matches.sort(key=lambda x: x.get("analysis", {}).get("match_score", 0), reverse=True)
    top_matches = matches[:MAX_MATCHES_PER_SCAN]
    
    top_vacancy = top_matches[0]
    top_analysis = top_vacancy.get("analysis", {})
    top_title = top_analysis.get("role_title", "Python Dasturchi")
    top_score = top_analysis.get("match_score", 0)
    top_company = top_analysis.get("company", top_vacancy.get("company", ""))
    top_posted_at = format_posted_at(top_vacancy.get("posted_at", ""))

    voice_text = top_analysis.get("voice_text") or (
        f"Assalomu alaykum Behruzbek! Sizga juda mos yangi taklif topildi: {top_title}. "
        f"Kompaniya {top_company} sizning ko'nikmalaringizga to'liq mos keladi. "
        f"Barcha batafsil ma'lumotlarni Telegram chatimizda qoldirdim, bemalol ko'rib chiqishingiz mumkin."
    )
    
    # Agar 80%+ mos bo'lsa: shoshilinch signal yoki qo'ng'iroq
    if top_score >= 80:
        try:
            from userbot import trigger_urgent_call_or_alert
            await trigger_urgent_call_or_alert(
                reason=f"80%+ mos vakansiya topildi: {top_title} ({top_company}) - {top_score}% moslik!"
            )
        except Exception:
            pass

    # Eng mos 1 ta vakansiya uchun o'zbekcha ovozli xabar
    try:
        voice_file = await generate_voice_message(voice_text)
        if voice_file:
            caption = (
                f"🎙 <b>Top-1 Eng Mos Vakansiya / Buyurtma Ovozli Xabari:</b>\n"
                f"<b>{top_title}</b> ({top_score}% moslik)\n"
                f"📅 E'lon qilingan: {top_posted_at}"
            )
            await send_telegram_voice(voice_file, caption=caption)
    except Exception as e:
        logger.error(f"Telegram ovozli xabar yuborishda xato: {e}")
        
    # Vakansiyalarni tugmalari bilan yuborish
    for i, vac in enumerate(top_matches, start=1):
        try:
            text_message = format_vacancy_card(vac, rank=i)
            vac_id = str(vac["id"])
            vac_url = vac.get("url", "https://t.me")

            # Inline tugmalar:
            # 1-qator: 📄 PDF Rezyume | 🎯 Suhbat savollari
            # 2-qator: ✅ Topshirdim | 🔗 E'lonni ochish
            reply_markup = {
                "inline_keyboard": [
                    [
                        {"text": "📄 PDF Rezyume", "callback_data": f"pdf_resume_{vac_id}"},
                        {"text": "🎯 Suhbat savollari", "callback_data": f"interview_prep_{vac_id}"}
                    ],
                    [
                        {"text": "✅ Topshirdim", "callback_data": f"applied_{vac_id}"},
                        {"text": "🔗 E'lonni ochish", "url": vac_url}
                    ]
                ]
            }
                
            await send_telegram_message(text_message, reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Telegram matn yuborishda xato: {e}")

async def send_hr_alert_to_bot(hr_username: str, company: str, original_msg: str, ai_summary: str, suggested_reply: str):
    """HR xabar yozganda botga batafsil ma'lumot va ovozli xabarni chiqarish"""
    company_text = f" ({company})" if company else ""
    text = (
        f"🚨 <b>KUZATUVDAGI HR JAVOB YOZDI!</b>\n\n"
        f"👤 <b>HR:</b> @{hr_username}{company_text}\n"
        f"💬 <b>Xabar:</b>\n<i>\"{original_msg}\"</i>\n\n"
        f"💡 <b>AI Xulosa:</b>\n{ai_summary}\n\n"
        f"📝 <b>Tavsiya etilgan javob varianti:</b>\n"
        f"<code>{suggested_reply}</code>"
    )

    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "✅ Javobni yuborish (Telegramdan)", "callback_data": f"send_hr_reply_{hr_username}"}
            ]
        ]
    }

    await send_telegram_message(text, reply_markup=reply_markup)

    # ElevenLabs orqali qisqa ovozli xabar
    try:
        from voice import generate_elevenlabs_tts
        voice_text = f"Assalomu alaykum Behruzbek! Kuzatuvdagi HR {hr_username} sizga javob yozdi. {ai_summary}"
        voice_file = await generate_elevenlabs_tts(voice_text)
        if voice_file:
            await send_telegram_voice(voice_file, caption=f"🎙 HR @{hr_username} xabari xulosasi")
    except Exception as e:
        logger.warning(f"HR ovozli xabarida xatolik: {e}")

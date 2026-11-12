import httpx
import urllib.parse
import os
import re
import logging
import asyncio
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
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
        return "🕒 Bugun"
    try:
        clean_str = posted_at.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt_tash = dt.astimezone(TASHKENT_TZ)
        now_tash = datetime.now(TASHKENT_TZ)
        if dt_tash.date() == now_tash.date():
            return f"🕒 Bugun, {dt_tash.strftime('%H:%M')}"
        return f"📅 {dt_tash.strftime('%d-%m-%Y %H:%M')}"
    except (ValueError, TypeError):
        return posted_at if posted_at else "🕒 Bugun"

async def make_call(text: str = "Sizga mos yangi vakansiya chiqdi", force: bool = False) -> bool:
    """2-chi akkaunt (caller_bot) orqali haqiqiy Telegram audio qo'ng'iroq qilish"""
    if not force and not is_call_allowed_now():
        logger.info("Qo'ng'iroq vaqti emas (8:00 dan 20:00 gacha ruxsat etilgan), qo'ng'iroq bekor qilindi.")
        return False
    try:
        from userbot import make_telegram_call
        return await make_telegram_call(text, force=force)
    except Exception as e:
        logger.error(f"Telegram audio qo'ng'iroq xatosi: {e}")
        return False

def markdown_to_telegram_html(text: str) -> str:
    """
    Markdown sintaksisini Telegram HTML formatiga xavfsiz o'girish:
    - **qalin matn** -> <b>qalin matn</b>
    - `kod` -> <code>kod</code>
    - ```kod``` -> <pre>kod</pre>
    - [Nom](havola) -> <a href="havola">Nom</a>
    - *kursiv* -> <i>kursiv</i>
    """
    if not text:
        return ""
    try:
        # Pre code blocks: ```lang\ncode\n```
        text = re.sub(r"```(?:[a-zA-Z0-9_\-]+)?\n?(.*?)```", r"<pre>\1</pre>", text, flags=re.DOTALL)
        # Inline code: `code`
        text = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", text)
        # Bold: **bold** or __bold__
        text = re.sub(r"\*\*([^\*\n]+)\*\*", r"<b>\1</b>", text)
        text = re.sub(r"__([^_\n]+)__", r"<b>\1</b>", text)
        # Markdown headers: ### Header
        text = re.sub(r"^[ \t]*#{1,6}[ \t]+([^\n]+)", r"<b>\1</b>", text, flags=re.MULTILINE)
        # Markdown links: [Title](https://...)
        text = re.sub(r"\[([^\]]+)\]\((https?://[^\)]+)\)", r'<a href="\2">\1</a>', text)
        # Italic: *italic* (oddiy matn ichidagi kursiv)
        text = re.sub(r"(?<![\*\w])\*([^\*\n]+)\*(?![\*\w])", r"<i>\1</i>", text)
    except Exception:
        pass
    return text

async def send_telegram_message(text: str, reply_markup: Optional[Dict] = None, chat_id: str = "") -> bool:
    """Telegram bot orqali matnli xabar yuborish (uzun matnlarni 3900 belgidan bo'laklab yuboradi)"""
    target_chat_id = chat_id or TELEGRAM_CHAT_ID
    if not TELEGRAM_BOT_TOKEN or not target_chat_id:
        logger.warning("Telegram Bot Token yoki Chat ID sozlanmagan!")
        return False

    # Markdown formatlarni Telegram HTML teglariga avtomatik o'girish
    text = markdown_to_telegram_html(text)
        
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
    """Chatga golos yuborish butunlay o'chirildi - barcha ovozlar faqat jonli telefon qo'ng'irog'i ichida yangraydi"""
    logger.info("ℹ️ Chatga ovozli xabar (golos) yuborish o'chirildi (barcha ovoz faqat jonli telefon qo'ng'irog'ida yangraydi).")
    return True

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

async def edit_telegram_reply_markup(chat_id: str, message_id: int, reply_markup: Optional[Dict] = None) -> bool:
    """Telegram xabarining inline tugmalarini (reply_markup) yangilash"""
    target_chat_id = chat_id or TELEGRAM_CHAT_ID
    if not TELEGRAM_BOT_TOKEN or not target_chat_id or not message_id:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageReplyMarkup"
    payload = {
        "chat_id": target_chat_id,
        "message_id": message_id,
        "reply_markup": reply_markup or {"inline_keyboard": []}
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload)
            return resp.status_code == 200
    except Exception as e:
        logger.error(f"edit_telegram_reply_markup xatosi: {e}")
        return False

async def answer_callback_query(callback_query_id: str, text: Optional[str] = None, show_alert: bool = False) -> bool:
    """Telegram callback so'roviga javob qaytarish"""
    if not TELEGRAM_BOT_TOKEN or not callback_query_id:
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery"
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
        payload["show_alert"] = show_alert
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            return resp.status_code == 200
    except Exception as e:
        logger.debug(f"answer_callback_query xatosi: {e}")
        return False

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

    # Vakansiyalarni tugmalari bilan yuborish
    for i, vac in enumerate(top_matches, start=1):
        try:
            text_message = format_vacancy_card(vac, rank=i)
            vac_id = str(vac["id"])
            vac_url = vac.get("url", "https://t.me")
            hr_cand = vac.get("hr_username", "")
            track_hr_btn_text = f"➕ @{hr_cand} ni kuzatuvga olish" if hr_cand else "➕ HR'ni kuzatuvga olish"

            # Inline tugmalar:
            # 1-qator: 📝 Matn + 📄 PDF Rezyume | 🎯 Suhbat savollari
            # 2-qator: ✅ Topshirdim | ➕ HR'ni kuzatuvga olish
            # 3-qator: 🔗 E'lonni ochish
            reply_markup = {
                "inline_keyboard": [
                    [
                        {"text": "📝 Matn + 📄 PDF Rezyume", "callback_data": f"resume_bundle_{vac_id}"},
                        {"text": "🎯 Suhbat savollari", "callback_data": f"interview_prep_{vac_id}"}
                    ],
                    [
                        {"text": "✅ Topshirdim", "callback_data": f"applied_{vac_id}"},
                        {"text": track_hr_btn_text, "callback_data": f"track_hr_{vac_id}"}
                    ],
                    [
                        {"text": "🔗 E'lonni ochish", "url": vac_url}
                    ]
                ]
            }
                
            await send_telegram_message(text_message, reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Telegram matn yuborishda xato: {e}")

async def send_hr_alert_to_bot(hr_username: str, company: str, original_msg: str, ai_summary: str, suggested_reply: str, time_str: str = ""):
    """HR xabar yozganda botga batafsil ma'lumot va ovozli xabarni chiqarish"""
    company_text = f" ({company})" if company else ""
    time_badge = f"\n🕒 <b>Xabar vaqti:</b> {time_str}" if time_str else ""
    text = (
        f"🚨 <b>KUZATUVDAGI HR JAVOB YOZDI!</b>\n\n"
        f"👤 <b>HR:</b> @{hr_username}{company_text}"
        f"{time_badge}\n"
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



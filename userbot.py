import asyncio
import logging
import os
import re
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import User

from config import (
    TELEGRAM_API_ID,
    TELEGRAM_API_HASH,
    TELEGRAM_STRING_SESSION,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    CALLMEBOT_USERNAME,
    TASHKENT_TZ
)
from database import get_tracked_hr, get_all_tracked_hrs, update_hr_status, update_hr_last_msg, delete_tracked_hr

logger = logging.getLogger("Alfred.Userbot")

client: Optional[TelegramClient] = None
is_running = False

def get_userbot_client() -> Optional[TelegramClient]:
    global client
    if client is not None:
        return client
    if not (TELEGRAM_API_ID and TELEGRAM_API_HASH and TELEGRAM_STRING_SESSION):
        logger.warning("Telegram Userbot ma'lumotlari (API_ID/API_HASH/STRING_SESSION) to'liq emas.")
        return None
    try:
        client = TelegramClient(StringSession(TELEGRAM_STRING_SESSION), TELEGRAM_API_ID, TELEGRAM_API_HASH)
        return client
    except Exception as e:
        logger.error(f"TelegramClient yaratishda xato: {e}")
        return None

def is_userbot_connected() -> bool:
    global client, is_running
    return bool(client and client.is_connected() and is_running)

async def get_userbot_me() -> Optional[Any]:
    c = get_userbot_client()
    if not c or not c.is_connected():
        return None
    try:
        return await c.get_me()
    except Exception:
        return None

def format_message_time(msg_date) -> str:
    """Xabar vaqtini Toshkent vaqti (UTC+5) bo'yicha '🕒 Bugun, 14:30' shaklida formatlash"""
    if not msg_date:
        now_tash = datetime.now(TASHKENT_TZ)
        return f"🕒 Bugun, {now_tash.strftime('%H:%M')}"
    try:
        if getattr(msg_date, "tzinfo", None) is None:
            dt = msg_date.replace(tzinfo=timezone.utc)
        else:
            dt = msg_date
        dt_tash = dt.astimezone(TASHKENT_TZ)
        now_tash = datetime.now(TASHKENT_TZ)
        if dt_tash.date() == now_tash.date():
            return f"🕒 Bugun, {dt_tash.strftime('%H:%M')}"
        return f"📅 {dt_tash.strftime('%d-%m-%Y %H:%M')}"
    except Exception:
        return "🕒 Bugun"

# Vaqt, suhbat va uchrashuv kalit so'zlari
TIME_PATTERNS = [
    r"\b(?:soat\s*)?([01]?\d|2[0-3])[:.]([0-5]\d)\b",
    r"\b(?:soat\s*)(\d{1,2})\s*(?:da|ga|larga|larida|boshlab)?\b",
    r"\b(?:ertaga|bugun|indin|dushanba|seshanba|chorshanba|payshanba|juma|shanba|yakshanba)\b",
    r"\b(?:suhbat|intervyu|interview|sohbat|gaplash|call|meet|zoom|google\s*meet|qo'ng'iroq|qongiroq|uchrashuv|tayyormisiz|bormisiz|boshsiz|vaqtingiz|kelisha)\b",
    r"\b(?:созвон|собеседование|интервью|завтра|сегодня|время|встреча|поговорим|удобно|сможете)\b"
]

def is_time_or_meeting_proposal(text: str) -> bool:
    """Xabarda uchrashuv, vaqt yoki suhbat so'ralganligini aniqlash"""
    if not text:
        return False
    lower_t = text.lower()
    return any(re.search(p, lower_t, re.IGNORECASE) for p in TIME_PATTERNS)

def parse_meeting_time(text: str) -> tuple[Optional[datetime], str]:
    """
    Xabardan suhbat/uchrashuv vaqtini aniqlash (Toshkent vaqti UTC+5 bo'yicha).
    Qaytaradi: (datetime_obj, time_display_str)
    """
    now_tash = datetime.now(TASHKENT_TZ)
    lower_t = text.lower()
    is_tomorrow = any(w in lower_t for w in ["ertaga", "завтра", "indin"])
    base_date = (now_tash + timedelta(days=1)).date() if is_tomorrow else now_tash.date()

    hour = None
    minute = 0

    # 1. Masalan: 15:00 yoki 14.30
    m_hm = re.search(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b", text)
    if m_hm:
        hour = int(m_hm.group(1))
        minute = int(m_hm.group(2))
    else:
        # 2. Masalan: soat 12 ga yoki soat 3 da
        m_h = re.search(r"(?:soat\s*|в\s*)(\d{1,2})", text, re.IGNORECASE)
        if m_h:
            h_cand = int(m_h.group(1))
            if 0 <= h_cand <= 23:
                hour = h_cand

    if hour is not None:
        try:
            target_dt = datetime(base_date.year, base_date.month, base_date.day, hour, minute, tzinfo=TASHKENT_TZ)
            # Agar bugungi kun va soat o'tib ketgan bo'lsa, ertangi kun deb qabul qilinadi
            if not is_tomorrow and target_dt < now_tash:
                target_dt += timedelta(days=1)
            date_prefix = "Ertaga" if target_dt.date() > now_tash.date() else "Bugun"
            return target_dt, f"{date_prefix}, {target_dt.strftime('%H:%M')}"
        except Exception:
            pass

    return None, ""

async def schedule_2min_reminder(target_dt: datetime, hr_username: str, company: str, time_display: str):
    """Suhbatga roppa-rosa 2 daqiqa qolganda eslatma, qo'ng'iroq va ElevenLabs ovoz yuboruvchi taymer"""
    now_tash = datetime.now(TASHKENT_TZ)
    reminder_dt = target_dt - timedelta(minutes=2)
    delay_sec = (reminder_dt - now_tash).total_seconds()
    if delay_sec <= 0:
        delay_sec = 10

    logger.info(f"⏰ Suhbat eslatmasi (2 daqiqa oldin) rejalashtirildi: {delay_sec:.1f}s (@{hr_username}, {time_display})")

    async def _reminder_coro():
        await asyncio.sleep(delay_sec)
        try:
            from notifier import make_call, send_telegram_message, send_telegram_voice
            from voice import generate_voice_message

            logger.info(f"🚨 SUHBATGA 2 DAQIQA QOLDI! HR: @{hr_username}")
            # 1. Telefon qo'ng'irog'i
            await make_call(f"Behruzbek, HR @{hr_username} bilan suhbatingiz boshlanishiga 2 daqiqa qoldi! Tayyor turing!", force=True)

            # 2. ElevenLabs (GChwNsPvTsqD6tAAORVU) ovozli xabari
            voice_txt = "Behruzbek, suhbatingiz boshlanishiga 2 daqiqa qoldi! Tayyor turing!"
            v_file = await generate_voice_message(voice_txt, prepare_script=False, voice_id="GChwNsPvTsqD6tAAORVU")
            if v_file:
                await send_telegram_voice(v_file, caption=f"🎙 2 daqiqalik eslatma: @{hr_username}")

            # 3. Telegram xabar va [✅ "Tayyorman" deb HR'ga yuborish] tugmasi
            ready_msg = "Assalomu alaykum! Men suhbatga tayyorman, aloqadaman."
            reply_markup = {
                "inline_keyboard": [
                    [
                        {"text": "✅ \"Tayyorman\" deb HR'ga yuborish", "callback_data": f"send_ready_{hr_username}"}
                    ]
                ]
            }
            await send_telegram_message(
                f"⏰ <b>DIQQAT: HR (@{hr_username}) bilan suhbatga 2 daqiqa qoldi!</b>\n\n"
                f"🏢 <b>Kompaniya:</b> {company or 'Kompaniya'}\n"
                f"🕒 <b>Belgilangan vaqt:</b> {time_display}\n\n"
                f"🤖 <b>HR'ga hozir yuborish uchun tayyor xabar:</b>\n"
                f"<i>\"{ready_msg}\"</i>\n\n"
                f"<i>Aloqada bo'lishingizni bildirish uchun pastdagi tugmani bosing:</i>",
                reply_markup=reply_markup
            )
        except Exception as e_alarm:
            logger.error(f"2 daqiqalik eslatma yuborishda xato: {e_alarm}")

    asyncio.create_task(_reminder_coro())

hr_lock = asyncio.Lock()
handled_incoming_cache = set()

async def sync_hr_last_seen_silently(hr_username: str) -> Optional[int]:
    """
    Yangi qo'shilgan HR ning hozirgi eng oxirgi msg.id sini jim saqlab qo'yish.
    Eski xabarlar takrorlanmasligi uchun kerak.
    """
    c = get_userbot_client()
    if not c or not c.is_connected() or not await c.is_user_authorized():
        return None
    try:
        msgs = await c.get_messages(hr_username, limit=1)
        if msgs:
            latest_msg = msgs[0]
            update_hr_last_msg(hr_username, latest_msg.id, latest_msg.text or "", status="waiting")
            logger.info(f"HR @{hr_username} boshlang'ich last_seen_msg_id={latest_msg.id} ga sozlandi.")
            return latest_msg.id
    except Exception as e:
        logger.debug(f"HR @{hr_username} boshlang'ich ID ni olishda xato: {e}")
    return None

async def get_hr_chat_history(hr_username: str, limit: int = 15) -> List[Dict[str, Any]]:
    """
    Telethon orqali shu HR bilan bo'lgan oxirgi `limit` ta xabarni o'qish (ham IN, ham OUT).
    Xronologik tartibda (eskilardan yangisiga qarab) qaytaradi.
    """
    c = get_userbot_client()
    if not c or not c.is_connected() or not await c.is_user_authorized():
        return []
    try:
        username_clean = hr_username.strip().lstrip("@")
        messages = await c.get_messages(username_clean, limit=limit)
        result = []
        for m in reversed(messages):
            if not m.text:
                continue
            sender = "Behruzbek" if m.out else f"HR (@{username_clean})"
            time_str = format_message_time(m.date)
            result.append({
                "id": m.id,
                "out": bool(m.out),
                "sender": sender,
                "text": m.text,
                "date": m.date,
                "time_str": time_str
            })
        return result
    except Exception as e:
        logger.error(f"HR @{hr_username} yozishmalar tarixini olishda xato: {e}")
        return []

async def handle_hr_outgoing_message(
    hr_username: str, 
    company: str, 
    message_text: str, 
    msg_date: Any,
    chat_history: Optional[List[Dict[str, Any]]] = None
):
    """
    Behruzbek o'zi shaxsiy Telegramida HR ga xabar yozganda (msg.out == True):
    Telethon orqali oxirgi 15 ta yozishmani tahlil qilib, suhbat vaqti kelishilganini aniqlash.
    Agar kelishilgan bo'lsa, 2 daqiqa oldin taymer qo'yish va bot orqali tasdiq xabari yuborish.
    """
    from notifier import send_telegram_message
    from ai_analyzer import analyze_user_hr_dialogue

    if chat_history is None:
        chat_history = await get_hr_chat_history(hr_username, limit=15)

    analysis = await analyze_user_hr_dialogue(chat_history, hr_username, company)
    has_agreed = analysis.get("has_agreed_time", False)
    exact_time_display = analysis.get("exact_time_display", "")
    target_iso = analysis.get("target_iso", "")
    last_agreement = analysis.get("last_agreement", "")

    if has_agreed and exact_time_display:
        logger.info(f"🤝 Behruzbek va HR @{hr_username} suhbat vaqtiga kelishdi: {exact_time_display}")
        target_dt = None
        if target_iso:
            try:
                target_dt = datetime.fromisoformat(target_iso)
            except Exception:
                pass
        if not target_dt:
            target_dt, _ = parse_meeting_time(last_agreement or message_text)

        if target_dt:
            await schedule_2min_reminder(target_dt, hr_username, company, exact_time_display)

        await send_telegram_message(
            f"✅ <b>Siz HR (@{hr_username}) bilan o'zingiz gaplashib, suhbatni soat {exact_time_display} ga kelishdingiz! 2 daqiqa qolganda qo'ng'iroq qilaman.</b>\n\n"
            f"💬 <b>Oxirgi kelishuv:</b> <i>\"{last_agreement or message_text}\"</i>\n"
            f"🏢 <b>Kompaniya:</b> {company or 'Kompaniya'}"
        )

async def handle_hr_incoming_message(
    hr_username: str, 
    company: str, 
    message_text: str, 
    msg_date: Any,
    chat_history: Optional[List[Dict[str, Any]]] = None
):
    """
    Kuzatuvdagi HR dan yangi xabar kelganda FAQAT 1 TA bildirishnoma yuborish:
    1. Agar aniq suhbat soati aytilgan bo'lsa: 1 ta xabar + qo'ng'iroq + ovoz + 15 min oldingi taymer + [✅ Tasdiqlash javobini HR'ga yuborish]
    2. Agar savol yoki vaqt so'rash bo'lsa: 1 ta xabar (HR xabari + Gemini tavsiya qilgan mos javob) + [✅ Shu javobni HR'ga yuborish]
    Spam va takrorlanish (3 marta yuborish) BUTUNLAY yo'q!
    """
    time_str = format_message_time(msg_date)

    from ai_analyzer import analyze_hr_message
    from notifier import make_call, send_telegram_message, send_telegram_voice
    from voice import generate_voice_message
    from database import update_hr_pending_reply

    if chat_history is None:
        chat_history = await get_hr_chat_history(hr_username, limit=15)
    analysis = await analyze_hr_message(message_text, hr_username, company, chat_history=chat_history)
    has_exact_time = analysis.get("has_exact_time", False)
    exact_time_display = analysis.get("exact_time_display", "")
    suggested_reply = analysis.get("suggested_reply", "")
    target_iso = analysis.get("target_iso", "")

    # Pending reply ni saqlab qo'yamiz (tugma bosilganda darhol yuborish uchun)
    if suggested_reply:
        update_hr_pending_reply(hr_username, suggested_reply)

    if has_exact_time and exact_time_display:
        logger.info(f"📅 HR @{hr_username} ANIQ VAQT BELGILADI: {exact_time_display}")

        # a) DARHOL qo'ng'iroq (1 marta)
        await make_call(f"Behruzbek! HR @{hr_username} suhbat vaqtini belgiladi: {exact_time_display}", force=True)

        # b) ElevenLabs ovozli xabari (1 marta)
        try:
            voice_txt = f"Behruzbek! HR {hr_username} soat {exact_time_display} ga suhbat belgiladi. Tez Telegramga kiring!"
            v_file = await generate_voice_message(voice_txt, prepare_script=False)
            if v_file:
                await send_telegram_voice(v_file, caption=f"🎙 HR @{hr_username} suhbat vaqtini belgiladi: {exact_time_display}")
        except Exception as e_v:
            logger.warning(f"Ovozli xabarda ogohlantirish: {e_v}")

        # c) 2 daqiqa oldin eslatish taymeri
        target_dt = None
        if target_iso:
            try:
                target_dt = datetime.fromisoformat(target_iso)
            except Exception:
                pass
        if not target_dt:
            target_dt, _ = parse_meeting_time(message_text)

        if target_dt:
            await schedule_2min_reminder(target_dt, hr_username, company, exact_time_display)

        # d) FAQAT 1 TA xabar va ostida tasdiqlash tugmasi
        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "✅ Tasdiqlash javobini HR'ga yuborish", "callback_data": f"confirm_time_{hr_username}"}
                ]
            ]
        }
        await send_telegram_message(
            f"📅 <b>HR suhbat soatini belgiladi: {exact_time_display}</b>\n\n"
            f"👤 <b>HR:</b> @{hr_username} ({company or 'Kompaniya'})\n"
            f"🕒 <b>Kelgan vaqti:</b> {time_str}\n"
            f"💬 <b>HRning eng oxirgi xabari:</b>\n<i>\"{message_text}\"</i>\n\n"
            f"🤖 <b>HR'ga yuboriladigan javob:</b>\n<i>\"{suggested_reply}\"</i>\n\n"
            f"⏰ <b>Belgilangan suhbat vaqti:</b> <b>{exact_time_display}</b>\n"
            f"💡 <i>Ushbu vaqtdan 2 daqiqa oldin Alfred sizga avtomatik eslatma, javob va qo'ng'iroq qiladi.</i>\n\n"
            f"Tasdiqlash xabarini HR'ga yuborish uchun pastdagi tugmani bosing:",
            reply_markup=reply_markup
        )
    else:
        # Savol yoki vaqt so'rash — HECH QANDAY YOLG'ON TAYMER VA SPAMSIZ!
        logger.info(f"📩 HR @{hr_username} savol/xabar yozdi: {message_text[:50]}")
        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "✅ Shu javobni HR'ga yuborish", "callback_data": f"send_hr_reply_{hr_username}"}
                ]
            ]
        }
        await send_telegram_message(
            f"📩 <b>HR (@{hr_username}{f' - {company}' if company else ''}) xabar yozdi:</b>\n"
            f"🕒 <b>Vaqti:</b> {time_str}\n"
            f"💬 <b>HRning eng oxirgi xabari:</b>\n<i>\"{message_text}\"</i>\n\n"
            f"🤖 <b>HR'ga yuboriladigan javob:</b>\n<i>\"{suggested_reply}\"</i>",
            reply_markup=reply_markup
        )

async def hr_monitor_loop():
    """
    BARCHA qo'shilgan HR larni har 10 soniyada DINAMIK kuzatish fon sikli:
    - Ketma-ket kelgan bir nechta xabarlarni 1 ta umumiy bildirishnomaga birlashtiradi
    - last_seen_msg_id ni darhol eng oxirgi xabar ID siga yangilaydi
    - Bitta xabarni hech qachon 2 marta yubormaydi
    """
    logger.info("🟢 Barcha HR larni har 10 soniyada dinamik kuzatish fon sikli (hr_monitor_loop) ishga tushdi!")

    while True:
        try:
            c = get_userbot_client()
            if c and c.is_connected() and await c.is_user_authorized():
                tracked = get_all_tracked_hrs()
                for hr in tracked:
                    raw_user = hr.get("username", "")
                    if not raw_user:
                        continue
                    hr_username = raw_user.lstrip("@").lower().strip()
                    company = hr.get("company", "")
                    last_seen_id = hr.get("last_seen_msg_id") or 0

                    try:
                        # QAT'IY QOIDA: limit=1 (FAQAT eng oxirgi 1 dona xabarni olish, eski xabarlarni qo'shmaslik)
                        msgs = await c.get_messages(hr_username, limit=1)
                        if msgs:
                            msg = msgs[0]
                            if msg.id > last_seen_id:
                                async with hr_lock:
                                    hr_fresh = get_tracked_hr(hr_username)
                                    fresh_last_seen = (hr_fresh.get("last_seen_msg_id") if hr_fresh else 0) or 0
                                    if msg.id <= fresh_last_seen:
                                        continue
                                    # Xabar va ovozdan OLDIN DARHOL bazani yangilaymiz
                                    update_hr_last_msg(hr_username, msg.id, msg.text or "", status="replied")

                                if not msg.out:
                                    await handle_hr_incoming_message(hr_username, company, msg.text or "(Xabar matni yo'q)", msg.date)
                                else:
                                    await handle_hr_outgoing_message(hr_username, company, msg.text or "", msg.date)
                    except Exception as e_hr:
                        logger.debug(f"HR @{hr_username} get_messages ogohlantirish: {e_hr}")
        except asyncio.CancelledError:
            logger.info("hr_monitor_loop to'xtatildi.")
            break
        except Exception as e_loop:
            logger.error(f"hr_monitor_loop xatosi: {e_loop}")

        await asyncio.sleep(10)

async def start_userbot():
    """Telethon Userbot'ni fonda ishga tushirish va xabarlarni tinglash"""
    global is_running
    c = get_userbot_client()
    if not c:
        return

    if is_running:
        return

    try:
        await c.connect()
        if not await c.is_user_authorized():
            logger.error("Userbot sessiyasi ruxsat berilmagan (not authorized).")
            return

        me = await c.get_me()
        logger.info(f"✅ Userbot muvaffaqiyatli ishga tushdi: {me.first_name} (@{me.username})")
        is_running = True

        # Startup: Mavjud HR larning hozirgi oxirgi msg.id sini jim saqlab olish (eski xabarlarni qaytadan yubormaslik uchun)
        try:
            tracked = get_all_tracked_hrs()
            for hr in tracked:
                hr_u = hr.get("username", "").lstrip("@").lower().strip()
                cur_seen = hr.get("last_seen_msg_id") or 0
                if cur_seen == 0 and hr_u:
                    await sync_hr_last_seen_silently(hr_u)
        except Exception as e_sync:
            logger.debug(f"Userbot startup HR sync ogohlantirish: {e_sync}")

        # Jonli kuzatuv (Incoming va Outgoing private messages)
        @c.on(events.NewMessage(func=lambda e: e.is_private))
        async def private_message_handler(event):
            try:
                chat = await event.get_chat()
                username = (getattr(chat, "username", "") or "").lower().strip().lstrip("@")
                phone = (getattr(chat, "phone", "") or "").strip()

                hr_data = None
                if username:
                    hr_data = get_tracked_hr(username)
                if not hr_data and phone:
                    hr_data = get_tracked_hr(phone)

                if hr_data:
                    hr_username = hr_data["username"].lstrip("@").lower().strip()
                    company = hr_data.get("company", "")
                    last_seen_id = hr_data.get("last_seen_msg_id") or 0
                    if event.id > last_seen_id:
                        async with hr_lock:
                            hr_fresh = get_tracked_hr(hr_username)
                            fresh_last_seen = (hr_fresh.get("last_seen_msg_id") if hr_fresh else 0) or 0
                            if event.id <= fresh_last_seen:
                                return
                            # Xabar va ovozdan OLDIN DARHOL bazani yangilaymiz
                            update_hr_last_msg(hr_username, event.id, event.text or "", status="replied")

                        if not event.out:
                            await handle_hr_incoming_message(hr_username, company, event.text or "", event.date)
                        else:
                            await handle_hr_outgoing_message(hr_username, company, event.text or "", event.date)
            except Exception as e_msg:
                logger.error(f"Userbot xabarini qayta ishlashda xato: {e_msg}", exc_info=True)

        logger.info("Userbot shaxsiy xabarlarni jonli kuzatishni boshladi...")
        # Har 10 soniyada BARCHA HR larni dinamik kuzatuvchi siklni yoqish
        asyncio.create_task(hr_monitor_loop())
    except Exception as e:
        logger.error(f"Userbotni start qilishda xato: {e}", exc_info=True)

async def delete_hr_dialog(username_or_id: str) -> bool:
    """
    Telethon orqali shaxsiy Telegramdan shu HR bilan bo'lgan yozishmani
    ikkala tomon uchun butunlay o'chirib tashlash (revoke=True)
    """
    c = get_userbot_client()
    if not c or not c.is_connected():
        logger.error("Userbot ulanmagan.")
        return False
    try:
        username_or_id = username_or_id.strip().lstrip("@")
        entity = await c.get_input_entity(username_or_id)
        await c.delete_dialog(entity, revoke=True)
        logger.info(f"HR @{username_or_id} bilan dialog muvaffaqiyatli o'chirildi (revoke=True).")
        return True
    except Exception as e:
        logger.error(f"HR @{username_or_id} dialogini o'chirishda xatolik: {e}")
        return False

async def send_message_as_user(username_or_id: str, text: str) -> bool:
    """
    Behruzbekning shaxsiy Telegram akkaunti nomidan HR'ga xabar yuborish
    """
    c = get_userbot_client()
    if not c or not c.is_connected():
        logger.error("Userbot ulanmagan.")
        return False
    try:
        username_or_id = username_or_id.strip().lstrip("@")
        await c.send_message(username_or_id, text)
        logger.info(f"HR @{username_or_id} ga xabar muvaffaqiyatli yuborildi.")
        return True
    except Exception as e:
        logger.error(f"HR @{username_or_id} ga xabar yuborishda xatolik: {e}")
        return False

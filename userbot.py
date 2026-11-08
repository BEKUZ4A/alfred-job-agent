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

    return None, "Tez orada"

async def schedule_15min_reminder(target_dt: datetime, hr_username: str, company: str, time_display: str):
    """Suhbatga 15 daqiqa qolganda avtomatik qo'ng'iroq va eslatma qiluvchi taymer"""
    now_tash = datetime.now(TASHKENT_TZ)
    reminder_dt = target_dt - timedelta(minutes=15)
    delay_sec = (reminder_dt - now_tash).total_seconds()
    if delay_sec <= 0:
        logger.info(f"Suhbat vaqtigacha 15 daqiqadan kam qolgan yoki vaqt o'tgan ({delay_sec}s), eslatma taymeri o'tkazib yuborildi.")
        return

    logger.info(f"⏰ Suhbat eslatmasi rejalashtirildi: {delay_sec:.1f} soniyadan so'ng (@{hr_username}, {time_display})")

    async def _reminder_coro():
        await asyncio.sleep(delay_sec)
        try:
            from notifier import make_call, send_telegram_message, send_telegram_voice
            from voice import generate_voice_message

            logger.info(f"🚨 SUHBATGA 15 DAQIQA QOLDI! HR: @{hr_username}")
            # 1. Telefon qo'ng'irog'i
            await make_call(f"Behruzbek, HR @{hr_username} bilan suhbatga 15 daqiqa qoldi!", force=True)

            # 2. Telegram xabari
            await send_telegram_message(
                f"⏰🚨 <b>DIQQAT: SUHBATGA 15 DAQIQA QOLDI!</b>\n\n"
                f"👤 <b>HR:</b> @{hr_username} ({company or 'Kompaniya'})\n"
                f"🕒 <b>Belgilangan vaqt:</b> {time_display}\n\n"
                f"<i>Iltimos, suhbatga tayyorlaning: internet, kamera, mikrofon va rezyumengizni tayyorlab oling!</i>"
            )

            # 3. ElevenLabs ovozli xabari
            voice_txt = f"Behruzbek! HR {hr_username} bilan suhbatingizga o'n besh daqiqa qoldi. Iltimos, tayyorgarlikni ko'ring!"
            v_file = await generate_voice_message(voice_txt, prepare_script=False)
            if v_file:
                await send_telegram_voice(v_file, caption=f"🎙 15 daqiqalik eslatma: @{hr_username}")
        except Exception as e_alarm:
            logger.error(f"15 daqiqalik eslatma yuborishda xato: {e_alarm}")

    asyncio.create_task(_reminder_coro())

async def handle_hr_incoming_message(hr_username: str, company: str, msg: Any):
    """
    Kuzatuvdagi istalgan HR dan yangi xabar kelganda:
    - Agar vaqt/uchrashuv aytilgan bo'lsa: qo'ng'iroq + ElevenLabs ovoz + 3 ta signal + 15 min oldingi taymer + [✅ Tasdiqlash javobini HR'ga yuborish]
    - Boshqa har qanday xabar bo'lsa: darhol bildirishnoma + [✅ Javobni yuborish (Telegramdan)]
    """
    msg_text = msg.text or ""
    time_str = format_message_time(msg.date)
    is_meeting = is_time_or_meeting_proposal(msg_text)

    from notifier import make_call, send_telegram_message, send_telegram_voice
    from voice import generate_voice_message

    if is_meeting:
        target_dt, dt_display = parse_meeting_time(msg_text)
        logger.info(f"🚨 HR @{hr_username} VAQT BELGILADI! ({dt_display}): {msg_text[:50]}")

        # a) DARHOL qo'ng'iroq (Telegram Call / CallMeBot)
        await make_call(f"Behruzbek! HR @{hr_username} vaqt belgilayapti, tez Telegramga kiring!", force=True)

        # b) ElevenLabs ovozli xabari
        try:
            voice_text = f"Behruzbek! HR {hr_username} sizga vaqt belgilayapti, tez Telegramga kiring!"
            v_file = await generate_voice_message(voice_text, prepare_script=False)
            if v_file:
                await send_telegram_voice(v_file, caption=f"🎙 HR @{hr_username} vaqt belgiladi")
        except Exception as e_v:
            logger.warning(f"Ovozli xabarda xato: {e_v}")

        # c) Ketma-ket 3 ta shoshilinch signal
        for _ in range(3):
            await send_telegram_message(
                f"🚨🚨🚨 <b>SHOSHILINCH: HR VAQT BELGILADI!</b>\n\n"
                f"👤 <b>HR:</b> @{hr_username} ({company or 'Kompaniya'})\n"
                f"🕒 <b>Kelgan vaqti:</b> {time_str} (Belgilangan: <b>{dt_display}</b>)\n"
                f"💬 <b>Xabar:</b>\n<i>\"{msg_text}\"</i>\n\n"
                f"<i>Iltimos, zudlik bilan Telegramga kiring!</i>"
            )
            await asyncio.sleep(1)

        # d) Belgilangan vaqtga 15 daqiqa qolganda avtomatik taymer
        if target_dt:
            await schedule_15min_reminder(target_dt, hr_username, company, dt_display)

        # e) [✅ Tasdiqlash javobini HR'ga yuborish] tugmasi
        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "✅ Tasdiqlash javobini HR'ga yuborish", "callback_data": f"confirm_time_{hr_username}"}
                ]
            ]
        }
        await send_telegram_message(
            f"📅 <b>HR @{hr_username} ({company or 'Kompaniya'}) suhbat/uchrashuv vaqtini belgiladi:</b>\n\n"
            f"💬 <i>\"{msg_text}\"</i>\n\n"
            f"⏰ <b>Suhbat vaqti:</b> {dt_display}\n"
            f"💡 <i>Ushbu vaqtga 15 daqiqa qolganda Alfred sizga avtomatik eslatma va qo'ng'iroq qiladi.</i>\n\n"
            f"HR ga tasdiqlash xabarini shaxsiy Telegramingizdan yuborish uchun pastdagi tugmani bosing:",
            reply_markup=reply_markup
        )
    else:
        # Oddiy xabar (Salom, test topshiriq, savol va h.k.)
        logger.info(f"📩 HR @{hr_username} oddiy xabar yozdi: {msg_text[:40]}")
        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "✅ Javobni yuborish (Telegramdan)", "callback_data": f"send_hr_reply_{hr_username}"}
                ]
            ]
        }
        await send_telegram_message(
            f"📩 <b>HR (@{hr_username} - {company or 'Kompaniya'}) sizga yozdi!</b>\n\n"
            f"🕒 <b>Vaqti:</b> {time_str}\n"
            f"💬 <b>Xabar:</b>\n<i>\"{msg_text}\"</i>",
            reply_markup=reply_markup
        )

async def hr_monitor_loop():
    """
    BARCHA qo'shilgan HR larni har 10 soniyada DINAMIK kuzatish fon sikli:
    - Bazadagi tracked_hrs jadvalidagi HAR BIR HR ni aylanib chiqadi
    - await userbot.get_messages(hr.username, limit=3) orqali oxirgi xabarlarni tekshiradi
    - msg.id > hr.last_seen_msg_id va not msg.out bo'lsa darhol bildirishnoma beradi
    - last_seen_msg_id ni bazada yangilaydi (takrorlanmasligi uchun)
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
                        msgs = await c.get_messages(hr_username, limit=3)
                        if msgs:
                            for msg in reversed(msgs):
                                if not msg.out and msg.id > last_seen_id:
                                    logger.info(f"🔎 HR @{hr_username} yangi xabar yozdi (msg.id: {msg.id} > {last_seen_id})!")
                                    # Bazada zudlik bilan ID ni yangilaymiz
                                    update_hr_last_msg(hr_username, msg.id, msg.text or "", status="replied")
                                    last_seen_id = msg.id

                                    # Xabarni to'liq qayta ishlash
                                    await handle_hr_incoming_message(hr_username, company, msg)
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

        # Jonli kuzatuv (Incoming private messages)
        @c.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
        async def incoming_private_message_handler(event):
            try:
                sender = await event.get_sender()
                if not isinstance(sender, User):
                    return

                username = (sender.username or "").lower().strip().lstrip("@")
                phone = (sender.phone or "").strip()

                hr_data = None
                if username:
                    hr_data = get_tracked_hr(username)
                if not hr_data and phone:
                    hr_data = get_tracked_hr(phone)

                if hr_data:
                    last_seen_id = hr_data.get("last_seen_msg_id") or 0
                    if event.id > last_seen_id:
                        hr_username = hr_data["username"].lstrip("@").lower().strip()
                        company = hr_data.get("company", "")
                        update_hr_last_msg(hr_username, event.id, event.text or "", status="replied")
                        await handle_hr_incoming_message(hr_username, company, event)
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

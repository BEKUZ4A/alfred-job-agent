import asyncio
import logging
import os
import re
from datetime import datetime, timezone
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
import httpx

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

async def process_hr_incoming_message(hr_data: Dict[str, Any], msg_text: str, msg_date, msg_id: int):
    """
    Kuzatuvdagi HR dan xabar kelganda (jonli yoki tekshiruvda) uni to'liq qayta ishlash:
    1. Bazada status='replied' va last_seen_msg_id ni yangilash
    2. Toshkent vaqtini aniqlash
    3. Gemini orqali tahlil qilish
    4. Foydalanuvchiga audio chaqiruv / ketma-ket signallar berish
    5. Botga batafsil tahlil va [✅ Javobni yuborish] tugmasini chiqarish
    6. ElevenLabs audio xabari yuborish
    """
    hr_username = hr_data["username"].strip().lstrip("@").lower()
    company = hr_data.get("company", "")
    logger.info(f"🚨 KUZATUVDAGI HR JAVOB YOZDI! @{hr_username}: {msg_text[:60]}")

    # 1. Bazada yangilash (qayta-qayta takrorlanmasligi uchun)
    update_hr_last_msg(hr_username, msg_id=msg_id, message_text=msg_text, status="replied")

    # 2. Xabar kelgan vaqtini Toshkent vaqti bo'yicha formatlash
    time_display = format_message_time(msg_date)

    # 3. Gemini orqali tahlil qilish
    try:
        from ai_analyzer import analyze_hr_message
        analysis = await analyze_hr_message(msg_text, hr_username, company)
    except Exception as e_ai:
        logger.error(f"HR xabarini AI tahlil qilishda xato: {e_ai}")
        analysis = {
            "summary": "HR yangi xabar yubordi.",
            "suggested_reply": "Assalomu alaykum! Xabaringiz uchun rahmat, ko'rib chiqdim."
        }

    summary = analysis.get("summary", msg_text[:120])
    suggested_reply = analysis.get("suggested_reply", "")

    # 4. Foydalanuvchiga shoshilinch signal yoki qo'ng'iroq
    await trigger_urgent_call_or_alert(
        reason=f"HR @{hr_username} javob yozdi ({time_display}): {summary}"
    )

    # 5. Telegram botga bildirishnoma va javob tugmasini chiqarish
    try:
        from notifier import send_hr_alert_to_bot
        await send_hr_alert_to_bot(
            hr_username=hr_username,
            company=company,
            original_msg=msg_text,
            ai_summary=summary,
            suggested_reply=suggested_reply,
            time_str=time_display
        )
    except Exception as e_notif:
        logger.error(f"HR alert yuborishda xato: {e_notif}")

async def check_tracked_hrs_poll():
    """
    Majburiy tekshiruv (Har 5 daqiqada / skaner paytida):
    Telethon orqali tracked_hrs ro'yxatidagi har bir HR bilan bo'lgan oxirgi xabarni tekshirish.
    Agar oxirgi xabar HR tomonidan yozilgan bo'lsa (not msg.out) va u yangi bo'lsa (msg.id != last_seen_msg_id),
    uni darhol qabul qiladi.
    """
    c = get_userbot_client()
    if not c or not c.is_connected():
        return

    tracked = get_all_tracked_hrs()
    if not tracked:
        return

    for hr in tracked:
        status = hr.get("status", "waiting")
        if status in ["replied", "deleted", "prompted_delete"]:
            continue

        hr_user = hr.get("username", "").strip().lstrip("@").lower()
        if not hr_user:
            continue

        try:
            msgs = await c.get_messages(hr_user, limit=1)
            if msgs:
                last_m = msgs[0]
                last_seen_id = hr.get("last_seen_msg_id") or 0
                if not last_m.out and last_m.id != last_seen_id:
                    logger.info(f"🔎 Majburiy tekshiruvda HR @{hr_user} yangi xabari aniqlandi (ID: {last_m.id})")
                    await process_hr_incoming_message(hr, last_m.text or "", last_m.date, last_m.id)
        except Exception as e_poll:
            logger.debug(f"HR @{hr_user} majburiy tekshiruvida ogohlantirish: {e_poll}")

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
                message_text = event.text or ""

                # Tracked HR larni tekshiramiz
                hr_data = None
                if username:
                    hr_data = get_tracked_hr(username)
                if not hr_data and phone:
                    hr_data = get_tracked_hr(phone)

                if hr_data:
                    await process_hr_incoming_message(
                        hr_data=hr_data,
                        msg_text=message_text,
                        msg_date=event.date,
                        msg_id=event.id
                    )
            except Exception as e_msg:
                logger.error(f"Userbot xabarini qayta ishlashda xato: {e_msg}", exc_info=True)

        logger.info("Userbot shaxsiy xabarlarni jonli kuzatishni boshladi...")
        # Ishga tushganda 1 marta barcha HR larni majburiy tekshirish
        asyncio.create_task(check_tracked_hrs_poll())
    except Exception as e:
        logger.error(f"Userbotni start qilishda xato: {e}", exc_info=True)

async def trigger_urgent_call_or_alert(reason: str = "Shoshilinch xabar!"):
    """
    80%+ mos vakansiya chiqqanda yoki HR yozganda:
    Telegram audio qo'ng'iroq / ketma-ket baland signallar yuborish
    """
    logger.info(f"📢 SHOSHILINCH SIGNAL BERILMOQDA: {reason}")
    # 1. CallMeBot qo'ng'irog'i (agar sozlangan bo'lsa)
    if CALLMEBOT_USERNAME:
        try:
            from notifier import make_call
            await make_call(f"Diqqat! Behruzbek, sizga shoshilinch xabar keldi. {reason[:50]}")
        except Exception as e:
            logger.warning(f"CallMeBot chaqiruvida xato: {e}")

    # 2. Telegram Bot orqali ketma-ket 3 ta baland signal / bildirishnoma
    try:
        from notifier import send_telegram_message
        for _ in range(3):
            await send_telegram_message(
                f"🚨🚨🚨 <b>SHOSHILINCH BILDIRISHNOMA:</b>\n\n{reason}\n\n<i>Iltimos, darhol tekshiring!</i>"
            )
            await asyncio.sleep(1)
    except Exception as e_alert:
        logger.error(f"Signal yuborishda xato: {e_alert}")

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

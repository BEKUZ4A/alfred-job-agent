import asyncio
import logging
import os
import re
from typing import Optional

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import User

from config import (
    TELEGRAM_API_ID,
    TELEGRAM_API_HASH,
    TELEGRAM_STRING_SESSION,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    CALLMEBOT_USERNAME
)
from database import get_tracked_hr, update_hr_status, delete_tracked_hr
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

        @c.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
        async def incoming_private_message_handler(event):
            try:
                sender = await event.get_sender()
                if not isinstance(sender, User):
                    return

                username = (sender.username or "").lower().strip()
                phone = (sender.phone or "").strip()
                sender_id = sender.id
                message_text = event.text or ""

                # Tracked HR larni tekshiramiz
                hr_data = None
                if username:
                    hr_data = get_tracked_hr(username)
                if not hr_data and phone:
                    hr_data = get_tracked_hr(phone)

                if hr_data:
                    logger.info(f"🚨 KUZATUVDAGI HR YOZDI! @{username or phone}: {message_text[:60]}")
                    hr_username = hr_data["username"]
                    company = hr_data.get("company", "")

                    # 1. Google Calendar tadbirlarini bekor qilish (agar ulangan bo'lsa)
                    cal_3d = hr_data.get("cal_event_3d")
                    cal_7d = hr_data.get("cal_event_7d")
                    try:
                        from google_calendar import delete_calendar_event
                        if cal_3d:
                            delete_calendar_event(cal_3d)
                        if cal_7d:
                            delete_calendar_event(cal_7d)
                    except Exception as e_cal:
                        logger.warning(f"Kalendar tadbirini o'chirishda ogohlantirish: {e_cal}")

                    # 2. Statusni yangilash
                    update_hr_status(hr_username, "replied", last_message=message_text)

                    # 3. Gemini orqali tahlil qilish
                    from ai_analyzer import analyze_hr_message
                    analysis = await analyze_hr_message(message_text, hr_username, company)
                    
                    needs_reply = analysis.get("needs_reply", True)
                    summary = analysis.get("summary", message_text)
                    suggested_reply = analysis.get("suggested_reply", "")

                    # 4. Foydalanuvchiga audio qo'ng'iroq / shoshilinch signal berish
                    await trigger_urgent_call_or_alert(
                        reason=f"HR @{hr_username} sizga muhim javob yozdi: {summary}"
                    )

                    # 5. Telegram botga tafsilot va 'Javobni yuborish' tugmasini chiqarish
                    from notifier import send_hr_alert_to_bot
                    await send_hr_alert_to_bot(
                        hr_username=hr_username,
                        company=company,
                        original_msg=message_text,
                        ai_summary=summary,
                        suggested_reply=suggested_reply
                    )
            except Exception as e_msg:
                logger.error(f"Userbot xabarini qayta ishlashda xato: {e_msg}", exc_info=True)

        logger.info("Userbot shaxsiy xabarlarni kuzatishni boshladi...")
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

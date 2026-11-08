import asyncio
import logging
from datetime import datetime, timezone, timedelta
from database import get_all_tracked_hrs, update_hr_status
from ai_analyzer import generate_hr_followup
from notifier import send_telegram_message

logger = logging.getLogger("Alfred.Scheduler")

async def run_hr_reminder_loop():
    """
    Kuzatuvdagi HR larni davriy (har 5 daqiqada) tekshirish:
    1. Telethon Userbot orqali oxirgi xabarlarni majburiy tekshirish (poll)
    2. 3 kundan keyin: HR ga yuborish uchun tayyor follow-up matn chiqarish
    3. 7 kundan keyin: lichka chatidan o'chirishni so'rash (Google Calendar'siz)
    """
    logger.info("🟢 HR eslatma va tozalash kuzatuvchisi (har 5 daqiqada) ishga tushdi!")
    
    # 30 soniya kutib, userbot to'liq ulanib olishini kutamiz
    await asyncio.sleep(30)

    while True:
        try:
            # 1. Userbot orqali yangi xabarlarni majburiy tekshirish
            try:
                from userbot import check_tracked_hrs_poll
                await check_tracked_hrs_poll()
            except Exception as e_poll:
                logger.debug(f"Userbot poll tekshiruvida ogohlantirish: {e_poll}")

            # 2. Bazadagi HR larni muddatlarini tekshirish
            hrs = get_all_tracked_hrs()
            now = datetime.now(timezone.utc)
            for hr in hrs:
                status = hr.get("status", "waiting")
                username = hr.get("username", "")
                company = hr.get("company", "")
                added_at_str = hr.get("added_at")
                if not added_at_str or not username:
                    continue

                try:
                    raw_dt = str(added_at_str).replace("Z", "+00:00")
                    if "." in raw_dt:
                        raw_dt = raw_dt.split(".")[0]
                    added_at = datetime.fromisoformat(raw_dt)
                    if added_at.tzinfo is None:
                        added_at = added_at.replace(tzinfo=timezone.utc)
                except Exception:
                    continue

                diff = now - added_at

                # 7 kunlik tekshiruv (agar HR javob bermagan bo'lsa)
                if diff >= timedelta(days=7) and status in ["waiting", "reminder_sent"]:
                    update_hr_status(username, "prompted_delete")
                    text = (
                        f"⚠️ <b>HR @{username} ({company or 'Kompaniya'}) 7 kun ichida javob bermadi.</b>\n\n"
                        f"Uni shaxsiy Telegram lichkangizdan butunlay o'chirib tashlaymi?"
                    )
                    reply_markup = {
                        "inline_keyboard": [
                            [
                                {"text": "🗑 Ha, Telegramdan o'chir", "callback_data": f"delete_hr_yes_{username}"},
                                {"text": "⏳ Yo'q, qolsin", "callback_data": f"delete_hr_no_{username}"}
                            ]
                        ]
                    }
                    await send_telegram_message(text, reply_markup=reply_markup)

                # 3 kunlik tekshiruv (eslatma matni)
                elif diff >= timedelta(days=3) and status == "waiting":
                    update_hr_status(username, "reminder_sent")
                    pitch = await generate_hr_followup(username, company)
                    text = (
                        f"⏰ <b>HR @{username} ({company or 'Kompaniya'}) ga eslatma (follow-up) yozish vaqti bo'ldi (3 kun o'tdi):</b>\n\n"
                        f"<i>\"{pitch}\"</i>\n\n"
                        f"📋 <i>Ushbu xushmuomala xabarni nusxalab HR'ga yuborishingiz mumkin!</i>"
                    )
                    await send_telegram_message(text)

        except Exception as e:
            logger.error(f"Scheduler xatoligi: {e}")

        # Har 5 daqiqada (300 soniya) tekshiradi
        await asyncio.sleep(300)

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Tuple, Optional
from config import GOOGLE_SERVICE_ACCOUNT_JSON, GOOGLE_CALENDAR_ID

logger = logging.getLogger("Alfred.Calendar")

def get_calendar_service():
    if not GOOGLE_SERVICE_ACCOUNT_JSON:
        return None

    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        # JSON fayl yo'li yoki to'g'ridan-to'g'ri JSON string bo'lishi mumkin
        if os.path.isfile(GOOGLE_SERVICE_ACCOUNT_JSON):
            credentials = service_account.Credentials.from_service_account_file(
                GOOGLE_SERVICE_ACCOUNT_JSON,
                scopes=["https://www.googleapis.com/auth/calendar"]
            )
        else:
            json_data = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
            credentials = service_account.Credentials.from_service_account_info(
                json_data,
                scopes=["https://www.googleapis.com/auth/calendar"]
            )

        service = build("calendar", "v3", credentials=credentials)
        return service
    except Exception as e:
        logger.error(f"Google Calendar xizmatiga ulanishda xato: {e}")
        return None

def create_hr_calendar_events(hr_username: str, company: str = "") -> Tuple[str, str]:
    """
    /add_HR buyrug'i bosilganda Google Calendar'ga 2 ta tadbir qo'shish:
    1) 3 kundan keyin: "HR @username ga eslatma (follow-up) yozish"
    2) 7 kundan keyin: "HR @username javob bermadi — tozalash"
    Qaytaradi: (event_3d_id, event_7d_id)
    """
    service = get_calendar_service()
    if not service:
        logger.info("Google Calendar sozlanmagan, tadbirlar kalendarga kiritilmadi.")
        return ("", "")

    calendar_id = GOOGLE_CALENDAR_ID or "primary"
    now = datetime.now(timezone.utc)
    target_3d = now + timedelta(days=3)
    target_7d = now + timedelta(days=7)

    company_text = f" ({company})" if company else ""
    event_3d_id = ""
    event_7d_id = ""

    # 1. 3 kundan keyingi tadbir
    body_3d = {
        "summary": f"⏰ HR @{hr_username}{company_text} ga eslatma (follow-up) yozish",
        "description": f"Vakansiya bo'yicha @{hr_username} bilan bog'lanilganiga 3 kun bo'ldi. Eslatma yuborish tavsiya etiladi.",
        "start": {"dateTime": target_3d.isoformat()},
        "end": {"dateTime": (target_3d + timedelta(minutes=30)).isoformat()},
        "reminders": {"useDefault": True}
    }
    try:
        res_3d = service.events().insert(calendarId=calendar_id, body=body_3d).execute()
        event_3d_id = res_3d.get("id", "")
    except Exception as e:
        logger.error(f"3 kunlik tadbirni yaratishda xato: {e}")

    # 2. 7 kundan keyingi tadbir
    body_7d = {
        "summary": f"⚠️ HR @{hr_username}{company_text} javob bermadi — tozalash",
        "description": f"@{hr_username} 7 kun ichida javob bermadi. Kalendar va chatni tozalash lozim.",
        "start": {"dateTime": target_7d.isoformat()},
        "end": {"dateTime": (target_7d + timedelta(minutes=30)).isoformat()},
        "reminders": {"useDefault": True}
    }
    try:
        res_7d = service.events().insert(calendarId=calendar_id, body=body_7d).execute()
        event_7d_id = res_7d.get("id", "")
    except Exception as e:
        logger.error(f"7 kunlik tadbirni yaratishda xato: {e}")

    logger.info(f"HR @{hr_username} uchun Calendar tadbirlari yaratildi: 3d={event_3d_id}, 7d={event_7d_id}")
    return (event_3d_id, event_7d_id)

def delete_calendar_event(event_id: str) -> bool:
    """Kalendardan tadbirni o'chirish"""
    if not event_id:
        return False
    service = get_calendar_service()
    if not service:
        return False

    calendar_id = GOOGLE_CALENDAR_ID or "primary"
    try:
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        logger.info(f"Calendar tadbiri o'chirildi: {event_id}")
        return True
    except Exception as e:
        logger.warning(f"Calendar tadbirini o'chirishda xatolik ({event_id}): {e}")
        return False

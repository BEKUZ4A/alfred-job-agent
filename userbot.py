import asyncio
import logging
import os
import re
import random
import hashlib
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import User, PhoneCallProtocol, PhoneCallDiscardReasonBusy
from telethon.tl.functions.phone import RequestCallRequest, DiscardCallRequest
from telethon.tl.functions.messages import SaveDraftRequest

from config import (
    TELEGRAM_API_ID,
    TELEGRAM_API_HASH,
    TELEGRAM_STRING_SESSION,
    TELEGRAM_PHONE,
    CALLER_STRING_SESSION,
    CALLER_PHONE,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TASHKENT_TZ
)
from database import (
    get_tracked_hr, 
    get_all_tracked_hrs, 
    add_tracked_hr,
    update_hr_status, 
    update_hr_last_msg, 
    update_hr_interview_time,
    update_hr_reminded,
    update_hr_pending_reply,
    get_hr_pending_reply,
    delete_tracked_hr
)

logger = logging.getLogger("Alfred.Userbot")

client: Optional[TelegramClient] = None
caller_client: Optional[TelegramClient] = None
is_running = False

hr_lock = asyncio.Lock()
handled_incoming_cache = set()

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

def get_caller_client() -> Optional[TelegramClient]:
    """2-chi Telegram akkaunt (Caller Userbot) mijozi"""
    global caller_client
    if caller_client is not None:
        return caller_client
    if not (TELEGRAM_API_ID and TELEGRAM_API_HASH and CALLER_STRING_SESSION):
        return None
    try:
        caller_client = TelegramClient(StringSession(CALLER_STRING_SESSION), TELEGRAM_API_ID, TELEGRAM_API_HASH)
        return caller_client
    except Exception as e:
        logger.error(f"Caller TelegramClient yaratishda xato: {e}")
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

# ----------------- TELEGRAM AUDIO QO'NG'IROQ VA DRAFT -----------------

async def make_telegram_call_detailed(reason_text: str = "") -> tuple[bool, str]:
    """
    2-chi Telegram akkauntdan (caller_bot) asosiy profilga haqiqiy Telegram audio qo'ng'iroq qilish.
    - functions.messages.GetDhConfigRequest orqali to'g'ri g_a_hash generatsiya qiladi.
    - PhoneCallProtocol(min_layer=65, max_layer=92, udp_p2p=True, udp_reflector=True, library_versions=['2.4.4'])
    - Telefoningizda 35 soniya to'xtovsiz jiringlaydi.
    - Bir vaqtning o'zida botdan ElevenLabs ovozida reason_text ni ovozli xabar qilib yuboradi.
    - Qaytaradi: (success: bool, info: str - caller_tag yoki aniq Exception xatosi)
    """
    c_caller = get_caller_client()
    c_user = get_userbot_client()

    if not c_caller:
        return False, "2-chi akkaunt (caller_bot) sozlanmagan (CALLER_STRING_SESSION bo'sh)."

    if not c_caller.is_connected():
        try:
            await c_caller.connect()
        except Exception as e_con:
            return False, f"2-chi akkauntga ulanish xatosi: {e_con}"

    if not await c_caller.is_user_authorized():
        return False, "2-chi akkaunt sessiyasi avtorizatsiyadan o'tmagan."

    try:
        me_caller = await c_caller.get_me()
        caller_tag = f"@{me_caller.username}" if getattr(me_caller, "username", None) else f"ID: {me_caller.id}"

        me_primary = await c_user.get_me() if (c_user and c_user.is_connected()) else None
        target_user = (me_primary.username or me_primary.id) if me_primary else TELEGRAM_CHAT_ID

        # DH Config va Hash generatsiyasi
        try:
            await c_caller(functions.messages.GetDhConfigRequest(version=0, random_length=256))
        except Exception:
            pass
        g_a = os.urandom(256)
        g_a_hash = hashlib.sha256(g_a).digest()

        ent = await c_caller.get_input_entity(target_user)
        protocol = PhoneCallProtocol(
            min_layer=65, 
            max_layer=93, 
            udp_p2p=True, 
            udp_reflector=True,
            library_versions=['4.0.0', '3.0.0', '2.7.7', '2.4.4', '1.0.0']
        )
        rand_id = random.randint(1, 2**30)

        call_req = await c_caller(RequestCallRequest(
            user_id=ent,
            random_id=rand_id,
            g_a_hash=g_a_hash,
            protocol=protocol
        ))
        logger.info(f"📞 2-akkauntdan ({caller_tag}) haqiqiy Telegram Audio qo'ng'irog'i yo'llandi (35s ring)!")

        # 35 soniya to'xtovsiz jiringlatib, so'ng discard qilish
        async def _auto_discard():
            await asyncio.sleep(35)
            try:
                p_call = getattr(call_req, "phone_call", None)
                if p_call:
                    from telethon.tl.types import InputPhoneCall
                    await c_caller(DiscardCallRequest(
                        peer=InputPhoneCall(id=p_call.id, access_hash=p_call.access_hash),
                        duration=35,
                        reason=PhoneCallDiscardReasonBusy(),
                        connection_id=0
                    ))
            except Exception:
                pass
        asyncio.create_task(_auto_discard())

        # Bir vaqtning o'zida botdan ElevenLabs ovozida reason_text ni jo'natish
        if reason_text:
            try:
                from voice import generate_voice_message
                from notifier import send_telegram_voice
                v_file = await generate_voice_message(reason_text, prepare_script=False)
                if v_file:
                    await send_telegram_voice(v_file, caption=f"🎙 <b>{reason_text}</b>")
            except Exception as e_v:
                logger.warning(f"Ovozli eslatma xatosi: {e_v}")

        return True, caller_tag

    except Exception as e_call:
        logger.error(f"2-akkauntdan qo'ng'iroq qilishda Telethon xatoligi: {e_call}")
        return False, f"Telethon xatosi: {e_call}"

async def make_telegram_call(reason_text: str = "") -> bool:
    """Mavjud chaqiruvlar uchun boolean qaytaruvchi wrapper"""
    success, _ = await make_telegram_call_detailed(reason_text)
    return success

make_telegram_audio_call = make_telegram_call

async def save_hr_draft_message(hr_username: str, draft_text: str) -> bool:
    """
    HR bilan shaxsiy Telegram lichkasiga tayyor javobni Qoralama (Draft) qilib yozib qo'yish.
    Foydalanuvchi Telegram lichkasini ochganda xabar kiritish maydonida tayyor turadi.
    """
    c = get_userbot_client()
    if not c or not c.is_connected() or not await c.is_user_authorized():
        return False
    try:
        username_clean = hr_username.strip().lstrip("@")
        ent = await c.get_input_entity(username_clean)
        await c(SaveDraftRequest(peer=ent, message=draft_text, no_webpage=True))
        logger.info(f"📝 HR @{username_clean} lichkasiga Qoralama (Draft) muvaffaqiyatli saqlandi.")
        return True
    except Exception as e:
        logger.debug(f"HR @{hr_username} Draft saqlashda ogohlantirish: {e}")
        return False

# ----------------- PARSING VA VAQTNI ANIQLASH -----------------

TIME_PATTERNS = [
    r"\b(?:soat\s*)?([01]?\d|2[0-3])[:.]([0-5]\d)\b",
    r"\b(?:soat\s*)(\d{1,2})\s*(?:da|ga|larga|larida|boshlab)?\b",
    r"\b(?:ertaga|bugun|indin|dushanba|seshanba|chorshanba|payshanba|juma|shanba|yakshanba)\b",
    r"\b(?:suhbat|intervyu|interview|sohbat|gaplash|call|meet|zoom|google\s*meet|qo'ng'iroq|qongiroq|uchrashuv|tayyormisiz|bormisiz|boshsiz|vaqtingiz|kelisha)\b",
    r"\b(?:созвон|собеседование|интервью|завтра|сегодня|время|встреча|поговорим|удобно|сможете)\b"
]

def is_time_or_meeting_proposal(text: str) -> bool:
    if not text:
        return False
    lower_t = text.lower()
    return any(re.search(p, lower_t, re.IGNORECASE) for p in TIME_PATTERNS)

def parse_custom_call_time(text: str) -> tuple[Optional[datetime], str]:
    """
    Foydalanuvchi xabaridan qo'ng'iroq qilish vaqtini (UTC+5 Toshkent) ajratib olish:
    - '20 daqiqadan keyin', '15 minutdan keyin'
    - 'soat 18:15 da', '19:00 da', '09:00 da'
    - 'ertaga 09:00 da', 'ertaga soat 10 da'
    - '2 soatdan keyin'
    """
    if not text:
        return None, ""
    now_tash = datetime.now(TASHKENT_TZ)
    lower_t = text.lower()

    # 1. Nisbiy daqiqalar: (\d+)\s*(?:daqiqa|minut|daqiqadan|minutdan)
    m_rel_min = re.search(r"(\d+)\s*(?:daqiqa|minut|daqiqadan|minutdan)", lower_t)
    if m_rel_min:
        mins = int(m_rel_min.group(1))
        target_dt = now_tash + timedelta(minutes=mins)
        return target_dt, target_dt.strftime("%H:%M")

    # 2. Nisbiy soatlar: (\d+)\s*(?:soat|soatdan)\s*keyin
    m_rel_hr = re.search(r"(\d+)\s*(?:soat|soatdan)\s*keyin", lower_t)
    if m_rel_hr:
        hrs = int(m_rel_hr.group(1))
        target_dt = now_tash + timedelta(hours=hrs)
        return target_dt, target_dt.strftime("%H:%M")

    # 3. Aniq soat:daqiqa (masalan: 18:15 yoki soat 19:00)
    is_tomorrow = any(w in lower_t for w in ["ertaga", "indin"])
    base_date = (now_tash + timedelta(days=1)).date() if is_tomorrow else now_tash.date()

    m_hm = re.search(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b", lower_t)
    if m_hm:
        hour = int(m_hm.group(1))
        minute = int(m_hm.group(2))
        try:
            target_dt = datetime(base_date.year, base_date.month, base_date.day, hour, minute, tzinfo=TASHKENT_TZ)
            if not is_tomorrow and target_dt <= now_tash:
                target_dt += timedelta(days=1)
            time_disp = target_dt.strftime("%H:%M")
            if target_dt.date() > now_tash.date():
                time_disp = f"Ertaga, {time_disp}"
            return target_dt, time_disp
        except Exception:
            pass

    # 4. Aniq faqat soat (masalan: soat 19 da, soat 9 da)
    m_h = re.search(r"(?:soat\s*)(\d{1,2})\s*(?:da|ga|larga|larida|boshlab)?\b", lower_t)
    if m_h:
        hour = int(m_h.group(1))
        if 0 <= hour <= 23:
            try:
                target_dt = datetime(base_date.year, base_date.month, base_date.day, hour, 0, tzinfo=TASHKENT_TZ)
                if not is_tomorrow and target_dt <= now_tash:
                    target_dt += timedelta(days=1)
                time_disp = target_dt.strftime("%H:%M")
                if target_dt.date() > now_tash.date():
                    time_disp = f"Ertaga, {time_disp}"
                return target_dt, time_disp
            except Exception:
                pass

    return None, ""

def parse_meeting_time(text: str) -> tuple[Optional[datetime], str]:
    now_tash = datetime.now(TASHKENT_TZ)
    lower_t = text.lower()
    is_tomorrow = any(w in lower_t for w in ["ertaga", "завтра", "indin"])
    base_date = (now_tash + timedelta(days=1)).date() if is_tomorrow else now_tash.date()

    hour = None
    minute = 0

    m_hm = re.search(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)\b", text)
    if m_hm:
        hour = int(m_hm.group(1))
        minute = int(m_hm.group(2))
    else:
        m_h = re.search(r"(?:soat\s*|в\s*)(\d{1,2})", text, re.IGNORECASE)
        if m_h:
            h_cand = int(m_h.group(1))
            if 0 <= h_cand <= 23:
                hour = h_cand

    if hour is not None:
        try:
            target_dt = datetime(base_date.year, base_date.month, base_date.day, hour, minute, tzinfo=TASHKENT_TZ)
            if not is_tomorrow and target_dt < now_tash:
                target_dt += timedelta(days=1)
            date_prefix = "Ertaga" if target_dt.date() > now_tash.date() else "Bugun"
            return target_dt, f"{date_prefix}, {target_dt.strftime('%H:%M')}"
        except Exception:
            pass

    return None, ""

# ----------------- XABARLAR TARIXI VA SIKLLAR -----------------

async def sync_hr_last_seen_silently(hr_username: str) -> Optional[int]:
    """Yangi qo'shilgan HR ning hozirgi oxirgi msg.id sini jim saqlab qo'yish"""
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
    """Telethon orqali shu HR bilan bo'lgan oxirgi 15 ta xabarni (IN va OUT) xronologik o'qish"""
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
    """Behruzbek o'zi Telegram lichkada HR ga xabar yozganda (msg.out == True) kelishilgan vaqtni avto-aniqlash"""
    from database import is_forbidden_hr_username
    if is_forbidden_hr_username(hr_username):
        return

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
            update_hr_interview_time(hr_username, target_dt.isoformat(), reminded_2min=False)
            try:
                from backup_manager import schedule_db_backup
                schedule_db_backup()
            except Exception:
                pass
            # HR vaqt belgilaganda DARHOL 1 marta qo'ng'iroq qilib ogohlantirish
            await make_telegram_call(f"Behruzbek, HR @{hr_username} suhbat vaqtini belgiladi: {exact_time_display}!")

        comp_disp = f"\n🏢 <b>Kompaniya:</b> {company}" if (company and company.lower() != "kompaniya") else ""
        await send_telegram_message(
            f"✅ <b>Siz HR (@{hr_username}) bilan o'zingiz gaplashib, suhbatni soat {exact_time_display} ga kelishdingiz! 2 daqiqa qolganda qo'ng'iroq qilaman.</b>\n\n"
            f"💬 <b>Oxirgi kelishuv:</b> <i>\"{last_agreement or message_text}\"</i>{comp_disp}"
        )

async def handle_hr_incoming_message(
    hr_username: str, 
    company: str, 
    message_text: str, 
    msg_date: Any,
    chat_history: Optional[List[Dict[str, Any]]] = None
):
    """
    HR dan yangi xabar kelganda:
    1. Meet/Zoom havolasi bo'lsa darhol qo'ng'iroq qilish va tugmada chiqarish
    2. Draft (Qoralama) qilib HR lichkasiga yozib qo'yish
    3. Botda FAQAT 1 TA xabar va FAQAT 2 TA TUGMA chiqarish:
       [📤 Javob berish] va [✍️ O'zim javob berdim]
    """
    from database import is_forbidden_hr_username
    if is_forbidden_hr_username(hr_username):
        return

    time_str = format_message_time(msg_date)

    from ai_analyzer import analyze_hr_message
    from notifier import send_telegram_message, send_telegram_voice
    from voice import generate_voice_message

    if chat_history is None:
        chat_history = await get_hr_chat_history(hr_username, limit=15)

    analysis = await analyze_hr_message(message_text, hr_username, company, chat_history=chat_history)
    has_exact_time = analysis.get("has_exact_time", False)
    exact_time_display = analysis.get("exact_time_display", "")
    suggested_reply = analysis.get("suggested_reply", "")
    target_iso = analysis.get("target_iso", "")

    # 1. Tavsiya etilgan javobni HR lichkasiga Draft (Qoralama) qilib yozib qo'yish
    if suggested_reply:
        update_hr_pending_reply(hr_username, suggested_reply)
        await save_hr_draft_message(hr_username, suggested_reply)

    # 2. Google Meet / Zoom havolasi tekshiruvi
    has_meet = bool(re.search(r"(?:https?://)?(?:meet\.google\.com/[a-z0-9\-]+|zoom\.us/j/[0-9]+)", message_text, re.IGNORECASE))
    meet_url = ""
    if has_meet:
        m_link = re.search(r"https?://[^\s]+", message_text)
        if m_link:
            meet_url = m_link.group(0)

    # Agar Meet bo'lsa darhol gudok tashlash
    if has_meet:
        await make_telegram_audio_call(f"Behruzbek! HR @{hr_username} uchrashuv ssilkasini yubordi!")

    # 3. Agar aniq soat belgilangan bo'lsa
    if has_exact_time and exact_time_display:
        logger.info(f"📅 HR @{hr_username} ANIQ VAQT BELGILADI: {exact_time_display}")
        await make_telegram_audio_call(f"Behruzbek! HR @{hr_username} suhbat vaqtini belgiladi: {exact_time_display}")

        try:
            voice_txt = f"Behruzbek! HR {hr_username} soat {exact_time_display} ga suhbat belgiladi. Tez Telegramga kiring!"
            v_file = await generate_voice_message(voice_txt, prepare_script=False)
            if v_file:
                await send_telegram_voice(v_file, caption=f"🎙 HR @{hr_username} suhbat vaqtini belgiladi: {exact_time_display}")
        except Exception as e_v:
            logger.warning(f"Ovozli eslatmada ogohlantirish: {e_v}")

        target_dt = None
        if target_iso:
            try:
                target_dt = datetime.fromisoformat(target_iso)
            except Exception:
                pass
        if not target_dt:
            target_dt, _ = parse_meeting_time(message_text)

        if target_dt:
            update_hr_interview_time(hr_username, target_dt.isoformat(), reminded_2min=False)
            try:
                from backup_manager import schedule_db_backup
                schedule_db_backup()
            except Exception:
                pass
            # HR vaqt belgilaganda DARHOL 1 marta qo'ng'iroq qilib ogohlantirish
            await make_telegram_call(f"Behruzbek, HR @{hr_username} suhbat vaqtini belgiladi: {exact_time_display}!")

        inline_keyboard = []
        if meet_url:
            inline_keyboard.append([{"text": "🚀 Uchrashuvga kirish (Google Meet / Zoom)", "url": meet_url}])
        inline_keyboard.append([
            {"text": "📤 Javob berish", "callback_data": f"send_hr_reply_{hr_username}"},
            {"text": "✍️ O'zim javob berdim", "callback_data": f"manual_replied_{hr_username}"}
        ])

        comp_text = f"👤 <b>HR:</b> @{hr_username} ({company})\n" if (company and company.lower() != "kompaniya") else f"👤 <b>HR:</b> @{hr_username}\n"
        await send_telegram_message(
            f"📅 <b>HR suhbat soatini belgiladi: {exact_time_display}</b>\n\n"
            f"{comp_text}"
            f"🕒 <b>Kelgan vaqti:</b> {time_str}\n"
            f"💬 <b>HRning eng oxirgi xabari:</b>\n<i>\"{message_text}\"</i>\n\n"
            f"🤖 <b>HR'ga yuboriladigan javob:</b>\n<i>\"{suggested_reply}\"</i>\n\n"
            f"⏰ <b>Belgilangan suhbat vaqti:</b> <b>{exact_time_display}</b>\n"
            f"💡 <i>Ushbu vaqtdan 2 daqiqa oldin Alfred sizga avtomatik eslatma, javob va qo'ng'iroq qiladi.</i>\n"
            f"📝 <i>(Javob HR lichkangizga Qoralama / Draft qilib joylandi)</i>",
            reply_markup={"inline_keyboard": inline_keyboard}
        )
    else:
        # Oddiy savol yoki xabar
        inline_keyboard = []
        if meet_url:
            inline_keyboard.append([{"text": "🚀 Uchrashuvga kirish (Google Meet / Zoom)", "url": meet_url}])
        inline_keyboard.append([
            {"text": "📤 Javob berish", "callback_data": f"send_hr_reply_{hr_username}"},
            {"text": "✍️ O'zim javob berdim", "callback_data": f"manual_replied_{hr_username}"}
        ])

        comp_str = f" - {company}" if (company and company.lower() != "kompaniya") else ""
        await send_telegram_message(
            f"📩 <b>HR (@{hr_username}{comp_str}) xabar yozdi:</b>\n"
            f"🕒 <b>Vaqti:</b> {time_str}\n"
            f"💬 <b>HRning eng oxirgi xabari:</b>\n<i>\"{message_text}\"</i>\n\n"
            f"🤖 <b>HR'ga yuboriladigan javob:</b>\n<i>\"{suggested_reply}\"</i>\n\n"
            f"📝 <i>(Javob HR lichkangizga Qoralama / Draft qilib joylandi)</i>",
            reply_markup={"inline_keyboard": inline_keyboard}
        )

async def schedule_2min_reminder(target_dt: datetime, hr_username: str, company: str = "", time_display: str = ""):
    """HR suhbat vaqtini bazada saqlash va 2 daqiqalik eslatmani rejalashtirish"""
    try:
        from database import update_hr_interview_time, track_hr
        track_hr(hr_username, company=company, source="direct_agreement")
        update_hr_interview_time(hr_username, target_dt.isoformat(), reminded_2min=False)
        from backup_manager import schedule_db_backup
        schedule_db_backup()
        logger.info(f"✅ @{hr_username} uchun 2 daqiqalik eslatma rejalashtirildi: {target_dt}")
    except Exception as e:
        logger.warning(f"schedule_2min_reminder ogohlantirish: {e}")

# ----------------- 2 DAQIQALIK ESLATMA VA NO-SHOW SIKLI -----------------

async def continuous_reminder_loop():
    """
    Doimiy 5 soniyalik eslatma va no-show tekshiruv sikli:
    1. Suhbat vaqtiga roppa-rosa 2 daqiqa qolganda qo'ng'iroq va ElevenLabs ovoz
    2. Suhbat vaqtidan 10 daqiqa o'tsa-yu HR yozmasa, No-Show eslatmasini Draft'ga joylash
    """
    logger.info("🟢 2 daqiqalik eslatma va No-Show kuzatuv sikli faollashdi.")
    from notifier import send_telegram_message, send_telegram_voice
    from voice import generate_voice_message

    while True:
        try:
            now_tash = datetime.now(TASHKENT_TZ)

            # 0. Maxsus rejalashtirilgan qo'ng'iroq taymerlarini tekshirish
            try:
                from database import get_pending_call_timers, mark_call_timer_done
                pending_timers = get_pending_call_timers()
                for tm in pending_timers:
                    t_iso = tm.get("target_time")
                    t_id = tm.get("id")
                    t_reason = tm.get("reason") or "Behruzbek, so'rovingiz bo'yicha qo'ng'iroq qilyapman!"
                    try:
                        t_dt = datetime.fromisoformat(t_iso)
                        if now_tash >= t_dt:
                            mark_call_timer_done(t_id)
                            try:
                                from backup_manager import schedule_db_backup
                                schedule_db_backup()
                            except Exception:
                                pass
                            logger.info(f"⏰ Maxsus qo'ng'iroq taymeri yetib keldi (ID: {t_id})!")
                            await make_telegram_call(t_reason)
                    except Exception as e_tm:
                        logger.warning(f"Timer check xatosi ({t_id}): {e_tm}")
            except Exception as e_tms:
                logger.debug(f"get_pending_call_timers ogohlantirish: {e_tms}")

            tracked = get_all_tracked_hrs()

            for hr in tracked:
                hr_u = hr.get("username", "").strip().lstrip("@").lower()
                from database import is_forbidden_hr_username
                if not hr_u or is_forbidden_hr_username(hr_u):
                    continue
                int_iso = hr.get("interview_time") or ""
                reminded = bool(hr.get("reminded_2min"))
                status = hr.get("status") or "waiting"
                company = hr.get("company", "")

                if not int_iso or not hr_u:
                    continue

                try:
                    int_dt = datetime.fromisoformat(int_iso)
                except Exception:
                    continue

                diff_sec = (int_dt - now_tash).total_seconds()

                # a) 2 daqiqa qolganda eslatma va gudok (120 soniya yoki undan kam, lekin hali o'tib ketmagan bo'lsa)
                if not reminded and -60 <= diff_sec <= 130:
                    update_hr_reminded(hr_u, True)
                    try:
                        from backup_manager import schedule_db_backup
                        schedule_db_backup()
                    except Exception:
                        pass

                    logger.info(f"🚨 SUHBATGA 2 DAQIQA QOLDI! HR: @{hr_u}")
                    # 1. 2-akkauntdan Telegram audio qo'ng'iroq
                    await make_telegram_audio_call(f"Behruzbek, HR @{hr_u} bilan suhbatingizga 2 daqiqa qoldi! Tayyor turing!")

                    # 2. ElevenLabs ovozi
                    try:
                        voice_txt = "Behruzbek, suhbatingiz boshlanishiga 2 daqiqa qoldi! Tayyor turing!"
                        v_file = await generate_voice_message(voice_txt, prepare_script=False, voice_id="GChwNsPvTsqD6tAAORVU")
                        if v_file:
                            await send_telegram_voice(v_file, caption=f"🎙 2 daqiqalik eslatma: @{hr_u}")
                    except Exception as e_v:
                        logger.warning(f"2 daqiqalik ovoz xatosi: {e_v}")

                    # 3. Tayyor xabarni HR lichkasiga Draft qilib saqlash
                    ready_msg = "Assalomu alaykum! Men suhbatga tayyorman, aloqadaman."
                    await save_hr_draft_message(hr_u, ready_msg)
                    update_hr_pending_reply(hr_u, ready_msg)

                    comp_line = f"🏢 <b>Kompaniya:</b> {company}\n" if (company and company.lower() != "kompaniya") else ""
                    reply_markup = {
                        "inline_keyboard": [
                            [
                                {"text": "📤 \"Tayyorman\" deb yuborish", "callback_data": f"send_ready_{hr_u}"},
                                {"text": "✍️ O'zim yozdim", "callback_data": f"manual_ready_{hr_u}"}
                            ]
                        ]
                    }
                    time_disp = int_dt.strftime("%H:%M")
                    await send_telegram_message(
                        f"⏰ <b>DIQQAT: HR (@{hr_u}) bilan suhbatga 2 daqiqa qoldi!</b>\n\n"
                        f"{comp_line}"
                        f"🕒 <b>Belgilangan vaqt:</b> {time_disp}\n\n"
                        f"🤖 <b>HR'ga hozir yuborish uchun tayyor xabar:</b>\n"
                        f"<i>\"{ready_msg}\"</i>\n\n"
                        f"📝 <i>(Xabar HR lichkangizga Qoralama / Draft qilib joylandi)</i>",
                        reply_markup=reply_markup
                    )

                # b) No-Show tekshiruvi: suhbat vaqtidan 10 daqiqa o'tdi, lekin HR dan xabar kelmadi
                if diff_sec < -600 and status not in ["no_show_handled", "completed", "cancelled"]:
                    update_hr_status(hr_u, "no_show_handled")
                    time_disp = int_dt.strftime("%H:%M")
                    followup_msg = f"Assalomu alaykum! Soat {time_disp} ga belgilangan suhbatimiz bo'yicha aloqadaman. Hozir sizga qulaymi yoki boshqa vaqtga ko'chiramizmi?"
                    await save_hr_draft_message(hr_u, followup_msg)
                    update_hr_pending_reply(hr_u, followup_msg)

                    reply_markup = {
                        "inline_keyboard": [
                            [
                                {"text": "📤 Javob berish", "callback_data": f"send_hr_reply_{hr_u}"},
                                {"text": "✍️ O'zim javob berdim", "callback_data": f"manual_replied_{hr_u}"}
                            ]
                        ]
                    }
                    await send_telegram_message(
                        f"⚠️ <b>HR (@{hr_u}) suhbat vaqtidan 10 daqiqa o'tsa ham yozmadi (No-Show)!</b>\n\n"
                        f"🤖 <b>HR'ga yuborish uchun muloyim eslatma tayyorlandi:</b>\n"
                        f"<i>\"{followup_msg}\"</i>\n\n"
                        f"📝 <i>(Ushbu xabar HR lichkasiga Qoralama / Draft qilib joylandi)</i>",
                        reply_markup=reply_markup
                    )
        except asyncio.CancelledError:
            break
        except Exception as e_rem:
            logger.debug(f"continuous_reminder_loop xatosi: {e_rem}")

        await asyncio.sleep(5)

# ----------------- MONITORING VA STARTUP -----------------

AUTO_HR_KEYWORDS = [
    "intervyu", "interview", "suhbat", "vakansiya", "rezyume", 
    "cv", "soat ", "ish bo'yicha", "dasturchi", "python", "loyiha"
]

async def check_and_auto_track_hr(sender, text: str) -> Optional[str]:
    """Lichkaga yozgan yangi odamni Auto-HR qilib kuzatuvga olish"""
    if not isinstance(sender, User):
        return None
    # 1. Botlar va o'zimizning akkauntlarimizni aslo HR qilib qo'shmaslik (Infinite loop oldini olish)
    if getattr(sender, "bot", False) or getattr(sender, "is_self", False):
        return None
    username = (sender.username or "").lower().strip().lstrip("@")
    if not username:
        return None
    from database import is_forbidden_hr_username
    if is_forbidden_hr_username(username):
        return None
    hr_data = get_tracked_hr(username)
    if hr_data:
        return username

    lower_t = text.lower()
    if any(k in lower_t for k in AUTO_HR_KEYWORDS):
        company_cand = sender.first_name or "Yangi Aloqa"
        add_tracked_hr(username, company=company_cand)
        try:
            from backup_manager import schedule_db_backup
            schedule_db_backup()
        except Exception:
            pass
        from notifier import send_telegram_message
        await send_telegram_message(
            f"🆕 <b>Yangi HR aniqlandi va avtomatik kuzatuvga olindi!</b>\n\n"
            f"👤 <b>Foydalanuvchi:</b> @{username} ({sender.first_name or ''})\n"
            f"💬 <b>Xabar:</b> <i>\"{text[:100]}\"</i>"
        )
        return username
    return None

async def hr_monitor_loop():
    """BARCHA qo'shilgan HR larni har 10 soniyada dinamik kuzatish fon sikli"""
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
                    from database import is_forbidden_hr_username, delete_tracked_hr
                    if is_forbidden_hr_username(hr_username):
                        delete_tracked_hr(hr_username)
                        continue
                    company = hr.get("company", "")
                    last_seen_id = hr.get("last_seen_msg_id") or 0

                    try:
                        msgs = await c.get_messages(hr_username, limit=1)
                        if msgs:
                            msg = msgs[0]
                            if msg.id > last_seen_id:
                                async with hr_lock:
                                    hr_fresh = get_tracked_hr(hr_username)
                                    fresh_last_seen = (hr_fresh.get("last_seen_msg_id") if hr_fresh else 0) or 0
                                    if msg.id <= fresh_last_seen:
                                        continue
                                    update_hr_last_msg(hr_username, msg.id, msg.text or "", status="replied")

                                # Ovoz yoki video_note bo'lsa transkripsiya
                                msg_text = msg.text or ""
                                if (getattr(msg, "voice", None) or getattr(msg, "video_note", None)) and not msg_text:
                                    try:
                                        from ai_analyzer import transcribe_voice_with_gemini
                                        b = await msg.download_media(file=bytes)
                                        if b:
                                            tr = await transcribe_voice_with_gemini(b)
                                            if tr:
                                                msg_text = f"[Ovozli xabar]: {tr}"
                                    except Exception:
                                        pass

                                if not msg.out:
                                    await handle_hr_incoming_message(hr_username, company, msg_text or "(Xabar matni yo'q)", msg.date)
                                else:
                                    await handle_hr_outgoing_message(hr_username, company, msg_text, msg.date)
                    except Exception as e_hr:
                        logger.debug(f"HR @{hr_username} get_messages ogohlantirish: {e_hr}")
        except asyncio.CancelledError:
            logger.info("hr_monitor_loop to'xtatildi.")
            break
        except Exception as e_loop:
            logger.error(f"hr_monitor_loop xatosi: {e_loop}")

        await asyncio.sleep(10)

async def start_userbot():
    """Telethon Userbot va Caller Userbot'ni ishga tushirish"""
    global is_running
    c = get_userbot_client()
    if not c:
        return

    if is_running:
        return

    try:
        await c.connect()
        if not await c.is_user_authorized():
            logger.error("Userbot sessiyasi ruxsat berilmagan.")
            return

        me = await c.get_me()
        logger.info(f"✅ 1-Akkaunt (Userbot) muvaffaqiyatli ishga tushdi: {me.first_name} (@{me.username})")
        is_running = True

        # 2-Akkaunt (Caller bot) ni parallel ulash va tanishtirish
        c_caller = get_caller_client()
        if c_caller:
            try:
                await c_caller.connect()
                if await c_caller.is_user_authorized():
                    me_c = await c_caller.get_me()
                    logger.info(f"✅ 2-Akkaunt (Caller Bot) faol: {me_c.first_name} (@{me_c.username})")
                    # Privacy cheklovi qo'ng'iroqni to'smasligi uchun 1-akkauntga tanishtirish
                    try:
                        target_id = me.username or me.id
                        ent = await c_caller.get_input_entity(target_id)
                        await c_caller.send_message(ent, "Salom! Men Alfred Caller Userbotiman. Qo'ng'iroqlar uchun aloqadaman.")
                        logger.info("✅ 2-Akkaunt 1-akkaunt bilan tanishdi (Privacy cheklovi yechildi).")
                    except Exception as e_intro:
                        logger.debug(f"Akkauntlarni tanishtirishda ogohlantirish: {e_intro}")
            except Exception as e_c:
                logger.warning(f"2-Akkaunt (Caller) ulashda ogohlantirish: {e_c}")

        # Startup: Mavjud HR larning msg.id sini jim saqlab olish
        try:
            tracked = get_all_tracked_hrs()
            for hr in tracked:
                hr_u = hr.get("username", "").lstrip("@").lower().strip()
                cur_seen = hr.get("last_seen_msg_id") or 0
                if cur_seen == 0 and hr_u:
                    await sync_hr_last_seen_silently(hr_u)
        except Exception as e_sync:
            logger.debug(f"Userbot startup HR sync ogohlantirish: {e_sync}")

        # Jonli kuzatuv (Incoming va Outgoing private messages + Auto-HR)
        @c.on(events.NewMessage(func=lambda e: e.is_private))
        async def private_message_handler(event):
            try:
                chat = await event.get_chat()
                username = (getattr(chat, "username", "") or "").lower().strip().lstrip("@")
                phone = (getattr(chat, "phone", "") or "").strip()

                # Ovozli yoki dumaloq video xabar bo'lsa transkripsiya
                msg_text = event.text or ""
                if (getattr(event, "voice", None) or getattr(event, "video_note", None)) and not msg_text:
                    try:
                        from ai_analyzer import transcribe_voice_with_gemini
                        b = await event.download_media(file=bytes)
                        if b:
                            tr = await transcribe_voice_with_gemini(b)
                            if tr:
                                msg_text = f"[Ovozli xabar]: {tr}"
                    except Exception:
                        pass

                # Auto-HR tekshiruvi (agar hali ro'yxatda bo'lmasa)
                sender = await event.get_sender()
                if getattr(sender, "bot", False) or getattr(sender, "is_self", False):
                    return
                from database import is_forbidden_hr_username
                if username and is_forbidden_hr_username(username):
                    return
                if not event.out and sender:
                    auto_tracked = await check_and_auto_track_hr(sender, msg_text)
                    if auto_tracked:
                        username = auto_tracked

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
                            update_hr_last_msg(hr_username, event.id, msg_text, status="replied")

                        if not event.out:
                            await handle_hr_incoming_message(hr_username, company, msg_text, event.date)
                        else:
                            await handle_hr_outgoing_message(hr_username, company, msg_text, event.date)
            except Exception as e_msg:
                logger.error(f"Userbot xabarini qayta ishlashda xato: {e_msg}", exc_info=True)

        logger.info("Userbot shaxsiy xabarlarni jonli kuzatishni boshladi...")
        asyncio.create_task(hr_monitor_loop())
        asyncio.create_task(continuous_reminder_loop())
    except Exception as e:
        logger.error(f"Userbotni start qilishda xato: {e}", exc_info=True)

async def delete_hr_dialog(username_or_id: str) -> bool:
    """HR bilan bo'lgan dialog yozishmalarini ikkala tomon uchun butunlay o'chirish"""
    c = get_userbot_client()
    if not c or not c.is_connected():
        return False
    try:
        username_or_id = username_or_id.strip().lstrip("@")
        entity = await c.get_input_entity(username_or_id)
        await c.delete_dialog(entity, revoke=True)
        return True
    except Exception as e:
        logger.error(f"HR @{username_or_id} dialogini o'chirishda xatolik: {e}")
        return False

async def send_message_as_user(username_or_id: str, text: str, show_typing: bool = False) -> bool:
    """Behruzbek nomidan HR ga xabar yuborish (ixtiyoriy 3 soniya typing bilan)"""
    c = get_userbot_client()
    if not c or not c.is_connected():
        return False
    try:
        username_or_id = username_or_id.strip().lstrip("@")
        if show_typing:
            try:
                async with c.action(username_or_id, 'typing'):
                    await asyncio.sleep(3)
            except Exception:
                pass
        await c.send_message(username_or_id, text)
        logger.info(f"HR @{username_or_id} ga xabar muvaffaqiyatli yuborildi.")
        return True
    except Exception as e:
        logger.error(f"HR @{username_or_id} ga xabar yuborishda xatolik: {e}")
        return False

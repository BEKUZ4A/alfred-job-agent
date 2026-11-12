import asyncio
import logging
import os
import re
import random
import hashlib
import time
import io
import wave
import numpy as np
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import User, PhoneCallProtocol, PhoneCallDiscardReasonBusy, InputPhoneCall
from telethon.tl.functions.phone import RequestCallRequest, DiscardCallRequest, ConfirmCallRequest
from telethon.tl.functions.messages import SaveDraftRequest, GetDhConfigRequest

try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
except Exception:
    pass

try:
    from pytgcalls import PyTgCalls, filters
    from pytgcalls.types import (
        MediaStream,
        RecordStream,
        Direction,
        Device,
        StreamFrames,
        StreamEnded,
        ChatUpdate,
        CallConfig,
        AudioQuality
    )
    from pytgcalls.exceptions import (
        CallBusy,
        CallDeclined,
        CallDiscarded,
        TimedOutAnswer
    )
except Exception as e_ptg_imp:
    logging.getLogger("Alfred.Userbot").warning(f"PyTgCalls import ogohlantirish: {e_ptg_imp}")

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
    delete_tracked_hr,
    is_forbidden_hr_username,
    update_hr_no_show_done
)

logger = logging.getLogger("Alfred.Userbot")

client: Optional[TelegramClient] = None
caller_client: Optional[TelegramClient] = None
is_running = False

# Global Call Cooldown va Anti-Loop himoyasi (kamida 180s / 3 daqiqa kutiladi)
_last_call_timestamp: float = 0.0
CALL_COOLDOWN_SECONDS: int = 180

def clean_raw_hr_message(text: str) -> str:
    """HR xabaridan bot shablonlarini tozalash va faqat asl qisqa xabarni olish (shablon ichiga shablon tushmasligi uchun)"""
    if not text:
        return ""
    t = str(text).strip()
    for marker in ["HRning eng oxirgi xabari:", "Oxirgi kelishuv:", "xabar yozdi:"]:
        if marker in t:
            parts = t.split(marker, 1)
            if len(parts) > 1 and parts[1].strip():
                t = parts[1].strip()
                for stop_marker in ["🤖", "📝", "⏰", "💡", "👤", "🕒"]:
                    if stop_marker in t:
                        t = t.split(stop_marker)[0].strip()
                break
    t = t.strip('"\n\r ')
    lines = [line.strip() for line in t.splitlines() if line.strip() and not any(line.strip().startswith(em) for em in ["🤖", "📝", "⏰", "💡", "👤", "🕒"])]
    cleaned = " ".join(lines)
    return cleaned[:1000].strip() or str(text)[:1000].strip()

hr_lock = asyncio.Lock()
handled_incoming_cache = set()
active_call_dh: Dict[int, Dict[str, Any]] = {}
pytgcalls_client: Optional[Any] = None

active_two_way_call: Dict[str, Any] = {
    "chat_id": 0,
    "is_active": False,
    "is_playing_reply": False,
    "audio_buffer": bytearray(),
    "last_speech_time": 0.0,
    "speech_started": False,
    "processing": False
}

def pcm_to_wav(pcm_data: bytes, sample_rate: int = 48000, channels: int = 2) -> bytes:
    wav_io = io.BytesIO()
    with wave.open(wav_io, 'wb') as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_data)
    return wav_io.getvalue()

async def process_user_call_speech(chat_id: int):
    active_two_way_call["processing"] = True
    active_two_way_call["is_playing_reply"] = True
    pcm_bytes = bytes(active_two_way_call["audio_buffer"])
    active_two_way_call["audio_buffer"].clear()

    # Kamida 0.4 soniyalik ovoz bo'lishi kerak (48000 * 2 * 2 * 0.4 = 76800 bytes)
    if len(pcm_bytes) < 76800:
        active_two_way_call["processing"] = False
        active_two_way_call["is_playing_reply"] = False
        return

    try:
        wav_bytes = pcm_to_wav(pcm_bytes, sample_rate=48000, channels=2)
        from ai_analyzer import transcribe_voice_with_gemini
        user_text = await transcribe_voice_with_gemini(wav_bytes, mime_type="audio/wav")

        if not user_text or not user_text.strip():
            logger.info("Qo'ng'iroqda ovozdan aniq matn olinmadi.")
            active_two_way_call["processing"] = False
            active_two_way_call["is_playing_reply"] = False
            return

        logger.info(f"🎙 [Jonli Qo'ng'iroq] Foydalanuvchi: '{user_text}'")

        # Xayrlashish kalit so'zlari
        lower_u = user_text.lower().strip()
        farewell_phrases = ["xayr", "hayr", "qo'ng'iroqni tugat", "tugat", "rahmat", "bo'ldi", "stop", "ko'rishguncha", "tamom", "salomat bo'l"]
        if any(fp in lower_u for fp in farewell_phrases):
            farewell_txt = "Suhbat uchun katta rahmat, Behruzbek! Salomat bo'ling, xayr!"
            from voice import generate_voice_message
            farewell_file = await generate_voice_message(farewell_txt, prepare_script=False, voice_id="GChwNsPvTsqD6tAAORVU")
            if pytgcalls_client and farewell_file:
                from pytgcalls.types import MediaStream
                await pytgcalls_client.play(chat_id, MediaStream(farewell_file, video_flags=MediaStream.Flags.IGNORE))
                await asyncio.sleep(4.0)
                try:
                    await pytgcalls_client.leave_call(chat_id)
                except Exception:
                    pass
            active_two_way_call["is_active"] = False
            active_two_way_call["processing"] = False
            return

        # Gemini ReAct Agent orqali javob generatsiya qilish
        from gemini_agent import run_gemini_react_agent
        voice_prompt = (
            f"Foydalanuvchi siz bilan jonli telefon qo'ng'irog'ida suhbatlashyapti. "
            f"Foydalanuvchi so'zlari: \"{user_text}\". "
            f"Qisqa, aniq va do'stona tarzda, 1-2 gapda javob bering. Hech qanday markdown (*, #, linklar) ishlatmang, "
            f"chunki javobingiz to'g'ridan-to'g'ri ovozda o'qiladi."
        )
        ai_reply = await run_gemini_react_agent(voice_prompt, is_voice=True)
        if not ai_reply or not ai_reply.strip():
            ai_reply = "Tushundim Behruzbek, sizni tinglayapman. Yana qanday savol yoki topshiriq bor?"

        clean_reply = ai_reply.replace("*", "").replace("#", "").replace("`", "").strip()
        logger.info(f"🤖 [Jonli Qo'ng'iroq] Alfred javobi: '{clean_reply}'")

        from voice import generate_voice_message
        reply_file = await generate_voice_message(clean_reply, prepare_script=False, voice_id="GChwNsPvTsqD6tAAORVU")
        if pytgcalls_client and reply_file:
            from pytgcalls.types import MediaStream
            await pytgcalls_client.play(chat_id, MediaStream(reply_file, video_flags=MediaStream.Flags.IGNORE))
            logger.info("🔊 Alfred javobi to'g'ridan-to'g'ri qo'ng'iroqqa stream qilindi!")
        else:
            active_two_way_call["processing"] = False
            active_two_way_call["is_playing_reply"] = False

    except Exception as e_proc:
        logger.error(f"process_user_call_speech xatosi: {e_proc}")
        active_two_way_call["processing"] = False
        active_two_way_call["is_playing_reply"] = False

def _register_pytgcalls_handlers(call_py: Any):
    try:
        from pytgcalls import filters
        from pytgcalls.types import StreamEnded, StreamFrames, ChatUpdate, Direction
    except Exception as e:
        logger.warning(f"PyTgCalls filters import xatosi: {e}")
        return

    @call_py.on_update(filters.stream_end())
    async def _on_stream_end(client, update: StreamEnded):
        active_two_way_call["is_playing_reply"] = False
        active_two_way_call["audio_buffer"].clear()
        active_two_way_call["speech_started"] = False
        active_two_way_call["processing"] = False
        logger.info(f"🎤 Alfred gapirib bo'ldi (chat_id={getattr(update, 'chat_id', 0)}). Endi foydalanuvchini tinglamoqda...")

    @call_py.on_update(filters.stream_frame(directions=Direction.INCOMING))
    async def _on_incoming_frame(client, update: StreamFrames):
        if not active_two_way_call.get("is_active"):
            return
        if active_two_way_call.get("is_playing_reply") or active_two_way_call.get("processing"):
            return

        frames = getattr(update, "frames", [])
        for f in frames:
            raw = getattr(f, "frame", b"")
            if raw:
                active_two_way_call["audio_buffer"].extend(raw)
                try:
                    samples = np.frombuffer(raw, dtype=np.int16)
                    if len(samples) > 0:
                        rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))
                        if rms > 450.0:
                            active_two_way_call["speech_started"] = True
                            active_two_way_call["last_speech_time"] = time.time()
                except Exception:
                    pass

        now = time.time()
        if active_two_way_call.get("speech_started") and (now - active_two_way_call.get("last_speech_time", 0.0) >= 1.5):
            if not active_two_way_call.get("processing"):
                active_two_way_call["speech_started"] = False
                c_id = getattr(update, "chat_id", 0) or active_two_way_call.get("chat_id", 0)
                asyncio.create_task(process_user_call_speech(c_id))

    @call_py.on_update(filters.chat_update(ChatUpdate.Status.LEFT_CALL))
    async def _on_chat_update(client, update: ChatUpdate):
        c_id = getattr(update, "chat_id", 0)
        st = getattr(update, "status", None)
        logger.info(f"🔴 Telefon qo'ng'irog'i yakunlandi / go'shak qo'yildi (chat_id={c_id}, status={st})")
        active_two_way_call["is_active"] = False
        active_two_way_call["is_playing_reply"] = False
        active_two_way_call["processing"] = False
        active_two_way_call["audio_buffer"].clear()

async def ensure_pytgcalls_started(caller_client) -> Optional[Any]:
    global pytgcalls_client
    if pytgcalls_client is not None and getattr(pytgcalls_client, "_is_running", False):
        return pytgcalls_client
    try:
        from pytgcalls import PyTgCalls
        if caller_client and caller_client.is_connected():
            if pytgcalls_client is None:
                pytgcalls_client = PyTgCalls(caller_client)
                _register_pytgcalls_handlers(pytgcalls_client)

                # Monkeypatch resolve_chat_id to correctly resolve phone numbers like '+998999875186' to user ID (5064983998)
                orig_resolve = pytgcalls_client.resolve_chat_id
                async def patched_resolve_chat_id(chat_id: int | str) -> int:
                    if isinstance(chat_id, str) and chat_id.startswith("+"):
                        try:
                            peer = await pytgcalls_client._app.resolve_peer(chat_id)
                            return getattr(peer, "user_id", 5064983998)
                        except Exception:
                            return 5064983998
                    return await orig_resolve(chat_id)
                pytgcalls_client.resolve_chat_id = patched_resolve_chat_id

            if not getattr(pytgcalls_client, "_is_running", False):
                await pytgcalls_client.start()
                logger.info("✅ PyTgCalls (WebRTC Audio Engine) 2-akkaunt uchun muvaffaqiyatli ishga tushdi!")
            return pytgcalls_client
    except Exception as e_start:
        logger.warning(f"PyTgCalls ishga tushirishda xatolik: {e_start}")
    return pytgcalls_client

def get_pytgcalls():
    global pytgcalls_client
    return pytgcalls_client

TELEGRAM_CALL_PROTOCOL = PhoneCallProtocol(
    min_layer=92, 
    max_layer=92, 
    udp_p2p=True, 
    udp_reflector=True, 
    library_versions=[
        "8.0.0", "9.0.0", "12.0.0", "13.0.0", "4.0.0", "3.0.0", "2.7.7"
    ]
)

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

async def ensure_caller_userbot_synced(c_user: TelegramClient, c_caller: TelegramClient):
    """
    Userbot (+998999875186) va Caller (+998930037685) akkauntlarini bir-biriga kontakt qilib qo'shish
    hamda o'zaro blokdan (UnblockRequest) chiqarish (User is blocked xatosini 100% yo'qotish).
    """
    try:
        from telethon.tl.types import InputPhoneContact
        from telethon.tl.functions.contacts import ImportContactsRequest, UnblockRequest

        me_user = await c_user.get_me() if (c_user and c_user.is_connected()) else None
        me_caller = await c_caller.get_me() if (c_caller and c_caller.is_connected()) else None

        # 1. Asosiy akkaunt (userbot) 2-akkauntni kontaktga qo'shadi va unblock qiladi
        if c_user and c_user.is_connected():
            try:
                c_import = InputPhoneContact(
                    client_id=0,
                    phone="+998930037685",
                    first_name="ALfired",
                    last_name="Caller"
                )
                await c_user(ImportContactsRequest(contacts=[c_import]))
            except Exception as e_ci:
                logger.debug(f"Userbot import contact xatosi: {e_ci}")

            try:
                caller_ent = await c_user.get_input_entity("+998930037685")
                await c_user(UnblockRequest(id=caller_ent))
            except Exception:
                try:
                    caller_id = me_caller.id if me_caller else 8696382016
                    await c_user(UnblockRequest(id=caller_id))
                except Exception as e_un:
                    logger.debug(f"Userbot unblock xatosi: {e_un}")

        # 2. 2-akkaunt (caller) asosiy akkauntni kontaktga qo'shadi va unblock qiladi
        if c_caller and c_caller.is_connected():
            try:
                u_import = InputPhoneContact(
                    client_id=0,
                    phone="+998999875186",
                    first_name="Behruzbek",
                    last_name="Primary"
                )
                await c_caller(ImportContactsRequest(contacts=[u_import]))
            except Exception as e_ui:
                logger.debug(f"Caller import contact xatosi: {e_ui}")

            try:
                user_ent = await c_caller.get_input_entity("+998999875186")
                await c_caller(UnblockRequest(id=user_ent))
            except Exception as e_cun:
                logger.debug(f"Caller unblock xatosi: {e_cun}")

        logger.info("✅ Userbot va Caller o'zaro kontaktga qo'shildi va Unblock qilindi!")
    except Exception as e_sync:
        logger.warning(f"ensure_caller_userbot_synced ogohlantirish: {e_sync}")

async def make_telegram_call_detailed(reason_text: str = "", force: bool = False) -> tuple[bool, str]:
    """
    2-chi Telegram akkauntdan (caller_bot: +998930037685) asosiy profilga (+998999875186)
    haqiqiy WebRTC Telegram audio qo'ng'irog'ini PyTgCalls orqali amalga oshirish.
    - Ovozli xabarlar (golos) chatga yuborilmaydi.
    - Foydalanuvchi go'shakni ko'targanda qo'ng'iroq uzilmaydi.
    - Go'shak ko'tarilganda qutlov ijro etiladi va ikki tomonlama jonli ovozli muloqot boshlanadi.
    """
    global _last_call_timestamp
    now_ts = time.time()
    if not force and (now_ts - _last_call_timestamp) < CALL_COOLDOWN_SECONDS:
        rem = int(CALL_COOLDOWN_SECONDS - (now_ts - _last_call_timestamp))
        logger.info(f"⏳ Qo'ng'iroq cooldown faol ({rem}s qoldi). O'tkazib yuborildi.")
        return False, f"Qo'ng'iroqlar cooldown: kamida 3 daqiqa kutiladi ({rem}s qoldi)."

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
        # 1. Kontaktlarni va blokdan chiqarishni sinxronlash
        if c_user and c_caller:
            await ensure_caller_userbot_synced(c_user, c_caller)

        me_caller = await c_caller.get_me()
        caller_tag = f"@{me_caller.username}" if getattr(me_caller, "username", None) else f"ID: {me_caller.id}"

        # 2. Nishon: Asosiy profil (+998999875186 / 5064983998)
        user_target = 5064983998

        # 3. ElevenLabs qutlov audiosini yaratish
        greeting_text = "Assalomu alaykum Behruzbek! Go'shakni ko'targaningizni eshitdim. Men siz bilan jonli gaplashishga tayyorman!"
        audio_text = f"{greeting_text} {reason_text}".strip()
        from voice import generate_voice_message
        v_file = await generate_voice_message(audio_text, prepare_script=False, voice_id="GChwNsPvTsqD6tAAORVU")
        if not v_file or not os.path.exists(v_file):
            return False, "Ovozli qutlov fayli yaratilmadi."

        # 4. PyTgCalls mijozini olish va tayyorlash
        call_py = await ensure_pytgcalls_started(c_caller)
        if not call_py:
            return False, "PyTgCalls WebRTC dvigateli ishga tushmadi."

        _last_call_timestamp = time.time()

        # 5. Ikki tomonlama jonli qo'ng'iroq oqimini fonda (background) boshlash
        async def _run_live_call():
            try:
                active_two_way_call["chat_id"] = user_target
                active_two_way_call["is_active"] = True
                active_two_way_call["is_playing_reply"] = True
                active_two_way_call["audio_buffer"].clear()
                active_two_way_call["last_speech_time"] = 0.0
                active_two_way_call["speech_started"] = False
                active_two_way_call["processing"] = False

                logger.info(f"📞 PyTgCalls orqali {user_target} ga qo'ng'iroq boshlanmoqda...")
                from pytgcalls.types import MediaStream, RecordStream
                from pytgcalls.exceptions import CallDeclined, CallBusy, TimedOutAnswer, CallDiscarded

                # play(...) chaqiruvi P2P qo'ng'iroqni boshlaydi, go'shak ko'tarilishini kutadi va audio stream qiladi
                await call_py.play(user_target, MediaStream(v_file, video_flags=MediaStream.Flags.IGNORE))
                logger.info("🎉 Go'shak ko'tarildi! Qutlov ijro etilmoqda...")

                # Go'shak ko'tarilgach, mikrofondan yozib olish (tinglash) rejimini yoqish
                await call_py.record(user_target, RecordStream(audio=True))
                logger.info("🎙 Foydalanuvchi mikrofoni faollashdi, ikki tomonlama dialog yoqildi!")

            except (CallDeclined, CallBusy, TimedOutAnswer, CallDiscarded) as e_call_end:
                logger.info(f"Qo'ng'iroq yakunlandi yoki javob berilmadi: {e_call_end}")
                active_two_way_call["is_active"] = False
            except Exception as e_live:
                logger.warning(f"Jonli qo'ng'iroq oqimida ogohlantirish: {e_live}")
                active_two_way_call["is_active"] = False

        asyncio.create_task(_run_live_call())
        logger.info(f"📞 2-akkauntdan ({caller_tag}) PyTgCalls audio qo'ng'irog'i yo'llandi!")
        return True, caller_tag

    except Exception as e_call:
        logger.error(f"2-akkauntdan qo'ng'iroq qilishda xatolik: {e_call}")
        return False, f"Xatolik: {e_call}"

async def make_telegram_call(reason_text: str = "", force: bool = False) -> bool:
    """Mavjud chaqiruvlar uchun boolean qaytaruvchi wrapper"""
    success, _ = await make_telegram_call_detailed(reason_text, force=force)
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

    message_text = clean_raw_hr_message(message_text)
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
    from notifier import send_telegram_message
    from voice import generate_voice_message

    if chat_history is None:
        chat_history = await get_hr_chat_history(hr_username, limit=15)

    message_text = clean_raw_hr_message(message_text)
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
        from database import update_hr_interview_time, add_tracked_hr
        add_tracked_hr(hr_username, company=company)
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
    from notifier import send_telegram_message
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
                    # 1. 2-akkauntdan Telegram audio qo'ng'iroq (jonli gapiradi, chatga golos yozilmaydi)
                    await make_telegram_audio_call(f"Behruzbek, HR @{hr_u} bilan suhbatingizga 2 daqiqa qoldi! Tayyor turing!")

                    # 2. Tayyor xabarni HR lichkasiga Draft qilib saqlash
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
                no_show_done = bool(hr.get("no_show_notified"))
                if diff_sec < -600 and not no_show_done and status not in ["no_show_handled", "completed", "cancelled"]:
                    update_hr_no_show_done(hr_u)
                    try:
                        from backup_manager import schedule_db_backup
                        schedule_db_backup()
                    except Exception:
                        pass
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
                            sender = await msg.get_sender()
                            if sender and (getattr(sender, "bot", False) or getattr(sender, "is_self", False)):
                                delete_tracked_hr(hr_username)
                                continue
                            if msg.id > last_seen_id:
                                async with hr_lock:
                                    hr_fresh = get_tracked_hr(hr_username)
                                    fresh_last_seen = (hr_fresh.get("last_seen_msg_id") if hr_fresh else 0) or 0
                                    if msg.id <= fresh_last_seen:
                                        continue
                                    update_hr_last_msg(hr_username, msg.id, clean_raw_hr_message(msg.text or ""), status="replied")

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

                                clean_text = clean_raw_hr_message(msg_text) or "(Xabar matni yo'q)"
                                if not msg.out:
                                    await handle_hr_incoming_message(hr_username, company, clean_text, msg.date)
                                else:
                                    await handle_hr_outgoing_message(hr_username, company, clean_text, msg.date)
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
                    # 2 ta akkauntni o'zaro kontaktga qo'shish va Unblock qilish (Auto-Unblock & Contact Sync)
                    await ensure_caller_userbot_synced(c, c_caller)

                    # PyTgCalls (WebRTC Voice Engine) ni 2-akkaunt uchun ishga tushirish
                    await ensure_pytgcalls_started(c_caller)
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
                if not chat:
                    return
                # 1. BOT, O'ZIMIZ (ME) VA 2-CHI AKKAUNT (CALLER_BOT) NI QAT'IY E'TIBORSIZ QOLDIRISH
                if getattr(chat, "bot", False) or getattr(chat, "is_self", False):
                    return
                chat_u = (getattr(chat, "username", "") or "").lower().strip().lstrip("@")
                from database import is_forbidden_hr_username
                if chat_u and is_forbidden_hr_username(chat_u):
                    return
                c_id = getattr(chat, "id", 0)
                from config import TELEGRAM_CHAT_ID
                if c_id and str(c_id) in [str(TELEGRAM_CHAT_ID), "5064983998", "8696382016"]:
                    return

                username = chat_u
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
                if not sender:
                    return
                if getattr(sender, "bot", False) or getattr(sender, "is_self", False):
                    return
                s_id = getattr(sender, "id", 0)
                if s_id and str(s_id) in [str(TELEGRAM_CHAT_ID), "5064983998", "8696382016"]:
                    return
                raw_u = (getattr(sender, "username", "") or "").lower().strip().lstrip("@")
                if raw_u and is_forbidden_hr_username(raw_u):
                    return
                msg_text = clean_raw_hr_message(msg_text)
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

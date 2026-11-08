import os
import re
import time
import base64
import logging
import tempfile
import asyncio
from typing import Optional
import requests
import edge_tts

from config import (
    ELEVENLABS_API_KEY,
    ELEVENLABS_VOICE_ID,
    ELEVENLABS_MODEL_ID,
    GEMINI_API_KEY,
    GEMINI_TTS_MODEL,
    GEMINI_TTS_VOICE,
    EDGE_TTS_VOICE,
    EDGE_TTS_RATE,
    EDGE_TTS_PITCH
)

logger = logging.getLogger("Alfred.Voice")

# Ovoz aniq va tabiiy chiqishi uchun IT texnologiyalarini fonetik transkripsiyalash
PHONETIC_REPLACEMENTS = [
    (r"\bFastAPI\b", "Fast Ey-Pi-Ay"),
    (r"\bFastApi\b", "Fast Ey-Pi-Ay"),
    (r"\bfastapi\b", "Fast Ey-Pi-Ay"),
    (r"\bPython\b", "Payton"),
    (r"\bpython\b", "Payton"),
    (r"\bDjango\b", "Jango"),
    (r"\bdjango\b", "Jango"),
    (r"\bPostgreSQL\b", "Postgres Kyu-El"),
    (r"\bpostgresql\b", "Postgres Kyu-El"),
    (r"\bPostgres\b", "Postgres"),
    (r"\bMySQL\b", "May-Es-Kyu-El"),
    (r"\bSQLite\b", "Es-Kyu-El Layt"),
    (r"\bSQL\b", "Es-Kyu-El"),
    (r"\bDocker\b", "Doker"),
    (r"\bdocker\b", "Doker"),
    (r"\bGitHub\b", "Git-Xab"),
    (r"\bgithub\b", "Git-Xab"),
    (r"\bAPI\b", "Ey-Pi-Ay"),
    (r"\bAI\b", "Ey-Ay"),
    (r"\bLLM\b", "El-El-Em"),
    (r"\bBackend\b", "Bekend"),
    (r"\bbackend\b", "Bekend"),
    (r"\bFrontend\b", "Frontend"),
    (r"\bfrontend\b", "Frontend"),
    (r"\bJunior\b", "Jyunior"),
    (r"\bjunior\b", "Jyunior"),
    (r"\bMiddle\b", "Midl"),
    (r"\bmiddle\b", "Midl"),
    (r"\bSenior\b", "Senior"),
    (r"\bsenior\b", "Senior"),
    (r"\bCI/CD\b", "Si-Ay Si-Di"),
    (r"\bCelery\b", "Seleri"),
    (r"\bcelery\b", "Seleri"),
    (r"\bRedis\b", "Redis"),
    (r"\bPydantic\b", "Paydantik"),
    (r"\bJavaScript\b", "Java-Skript"),
    (r"\bTypeScript\b", "Tayp-Skript"),
]

def phonetic_normalize_for_tts(text: str) -> str:
    """
    Ovoz aniq va tabiiy chiqishi uchun IT texnologiyalarini fonetik transkripsiyalash.
    Masalan: Python -> Payton, Django -> Jango, FastAPI -> Fast Ey-Pi-Ay
    """
    if not text:
        return ""
    for pattern, repl in PHONETIC_REPLACEMENTS:
        text = re.sub(pattern, repl, text, flags=re.IGNORECASE)
    return text

def clean_speech_text(text: str) -> str:
    """
    Ovozli xabar uchun matnni har qanday texnik belgilar, ssilka, emoji
    va yulduzcha/chiziqcha kabi keraksiz so'zlardan tozalash.
    Hech qachon yulduzcha, chiziqcha, havola yoki emoji nomlarini o'qimasligi ta'minlanadi.
    """
    if not text:
        return ""

    # 1. Havolalar va URL larni butunlay olib tashlash
    text = re.sub(r"https?://\S+|www\.\S+|t\.me/\S+", "", text)

    # 2. Markdown va maxsus formatlash belgilarini olib tashlash (*, _, ~, `, #, >, |, [, ], (, ), {, }, =, +)
    text = re.sub(r"[*_~`#><\[\]\(\)\{\}\|=+]", " ", text)

    # 3. Chiziqcha, tire va ro'yxat nuqtalarini olib tashlash
    text = re.sub(r"[\-—–•]", " ", text)

    # 4. Emojilarni tozalash (barcha Unicode emoji diapazonlari)
    emoji_pattern = re.compile(
        r"[\U00010000-\U0010ffff]|[\u2600-\u27bf]|[\uD83C-\uDBFF\uDC00-\uDFFF]",
        flags=re.UNICODE
    )
    text = emoji_pattern.sub("", text)

    # 5. Ovozda keraksiz o'qiladigan meta-so'zlarni olib tashlash
    text = re.sub(
        r"\b(yulduzcha|chiziqcha|tire|ssilka|havola|link|emoji|smaylik|slash)\b",
        "",
        text,
        flags=re.IGNORECASE
    )

    # 6. Ortiqcha probellar va qatorlarni tozalash
    text = re.sub(r"\s+", " ", text).strip()
    return text

def save_pcm_to_audio_file(pcm_bytes: bytes, sample_rate: int = 24000) -> str:
    """
    Raw PCM baytlarini (16-bit 24kHz) soundfile orqali toza Telegram Voice (.ogg Opus) ga
    yoki standart kutubxona orqali MP3/WAV ga saqlash.
    """
    temp_dir = tempfile.gettempdir()
    unique_suffix = f"{os.getpid()}_{int(time.time() * 1000)}"
    ogg_path = os.path.join(temp_dir, f"alfred_voice_{unique_suffix}.ogg")

    try:
        import soundfile as sf
        import numpy as np
        audio_array = np.frombuffer(pcm_bytes, dtype=np.int16)
        sf.write(ogg_path, audio_array, sample_rate, format="OGG", subtype="OPUS")
        return ogg_path
    except Exception as sf_err:
        logger.warning(f"soundfile orqali OGG saqlashda xato: {sf_err}, MP3/WAV formatiga o'tilmoqda")

    # Fallback: MP3
    mp3_path = os.path.join(temp_dir, f"alfred_voice_{unique_suffix}.mp3")
    try:
        import soundfile as sf
        import numpy as np
        audio_array = np.frombuffer(pcm_bytes, dtype=np.int16)
        sf.write(mp3_path, audio_array, sample_rate, format="MP3")
        return mp3_path
    except Exception:
        pass

    # Fallback 2: Standart kutubxona wave moduli orqali WAV
    wav_path = os.path.join(temp_dir, f"alfred_voice_{unique_suffix}.wav")
    import wave
    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return wav_path

async def generate_elevenlabs_tts(clean_text: str) -> Optional[str]:
    """
    ASOSIY OVOZ: ElevenLabs Multilingual V2 API
    - Model: eleven_multilingual_v2
    - Ovoz ID: cfWx14ZbaEUT4BjIA5tH (yoki ELEVENLABS_VOICE_ID)
    - Fonetik transkripsiya bilan to'ldirilgan (Payton, Jango, Fast Ey-Pi-Ay)
    - Chiqish: Toza Telegram Voice (.ogg Opus) format
    """
    if not ELEVENLABS_API_KEY:
        logger.warning("ELEVENLABS_API_KEY topilmadi, ElevenLabs o'tkazib yuborildi.")
        return None

    # Ovoz aniq va ravon chiqishi uchun fonetik transkripsiyalash
    phonetic_text = phonetic_normalize_for_tts(clean_text)

    voice_id = ELEVENLABS_VOICE_ID or "cfWx14ZbaEUT4BjIA5tH"
    model_id = ELEVENLABS_MODEL_ID or "eleven_multilingual_v2"

    temp_dir = tempfile.gettempdir()
    unique_suffix = f"{os.getpid()}_{int(time.time() * 1000)}"
    ogg_path = os.path.join(temp_dir, f"alfred_voice_{unique_suffix}.ogg")

    def _sync_generate():
        try:
            from elevenlabs.client import ElevenLabs
            from elevenlabs import VoiceSettings
            client = ElevenLabs(api_key=ELEVENLABS_API_KEY)
            settings = VoiceSettings(stability=0.55, similarity_boost=0.80)

            pcm_bytes = None
            try:
                audio_stream = client.text_to_speech.convert(
                    voice_id=voice_id,
                    text=phonetic_text,
                    model_id=model_id,
                    voice_settings=settings,
                    output_format="pcm_24000"
                )
                pcm_bytes = b"".join(audio_stream)
            except Exception as e_voice:
                err_str = str(e_voice)
                # Agar kutubxona ovozi bo'yicha cheklov bo'lsa (masalan 402 paid_plan_required)
                if "paid_plan_required" in err_str or "library voices" in err_str or "402" in err_str:
                    logger.warning(
                        f"ElevenLabs ovoz ID ({voice_id}) pullik obuna talab qildi. "
                        f"Standart premade ovoz bilan urinilmoqda..."
                    )
                    try:
                        audio_stream = client.text_to_speech.convert(
                            voice_id="JBFqnCBsd6RMkjVDRZzb",  # George premade ovozi
                            text=phonetic_text,
                            model_id=model_id,
                            voice_settings=settings,
                            output_format="pcm_24000"
                        )
                        pcm_bytes = b"".join(audio_stream)
                    except Exception as e_premade:
                        logger.error(f"ElevenLabs premade ovozida ham xatolik: {e_premade}")
                        return None
                else:
                    logger.error(f"ElevenLabs TTS xatosi: {e_voice}")
                    return None

            if not pcm_bytes:
                return None

            # Toza Telegram Voice (.ogg Opus) formatida saqlash
            import soundfile as sf
            import numpy as np
            audio_array = np.frombuffer(pcm_bytes, dtype=np.int16)
            sf.write(ogg_path, audio_array, 24000, format="OGG", subtype="OPUS")
            return ogg_path
        except Exception as e:
            logger.error(f"ElevenLabs generatsiyasida kutilmagan xatolik: {e}")
            return None

    try:
        audio_file = await asyncio.to_thread(_sync_generate)
        if audio_file and os.path.exists(audio_file) and os.path.getsize(audio_file) > 1000:
            logger.info(f"ElevenLabs ({model_id}, ovoz: {voice_id}) muvaffaqiyatli yaratildi: {audio_file}")
            return audio_file
    except Exception as e:
        logger.error(f"ElevenLabs audio yaratishda xato: {e}")

    return None

async def generate_edge_tts(clean_text: str) -> Optional[str]:
    """
    ZAXIRA OVOZ: Sozlangan Edge-TTS (uz-UZ-SardorNeural)
    ElevenLabs balansi tugaganda yoki xato berganda ishlaydi.
    Robotdek eshitilmasligi uchun: rate='+8%' (jonliroq), pitch='-2Hz' (yoqimli chuqur tembr)
    """
    temp_dir = tempfile.gettempdir()
    unique_suffix = f"{os.getpid()}_{int(time.time() * 1000)}"
    output_path = os.path.join(temp_dir, f"alfred_edge_voice_{unique_suffix}.mp3")

    voice = EDGE_TTS_VOICE or "uz-UZ-SardorNeural"
    rate = EDGE_TTS_RATE or "+8%"
    pitch = EDGE_TTS_PITCH or "-2Hz"

    try:
        communicate = edge_tts.Communicate(
            text=clean_text,
            voice=voice,
            rate=rate,
            pitch=pitch
        )
        await communicate.save(output_path)
        if os.path.exists(output_path) and os.path.getsize(output_path) > 1000:
            logger.info(f"Edge-TTS zaxira ovozi muvaffaqiyatli yaratildi ({voice}, rate={rate}, pitch={pitch}): {output_path}")
            return output_path
    except Exception as e:
        logger.error(f"Edge-TTS zaxira ovoz yaratishda xatolik: {e}")

    return None

async def generate_gemini_tts(clean_text: str) -> Optional[str]:
    """
    Qo'shimcha zaxira: Google Gemini Native Audio TTS (gemini-2.5-flash-preview-tts)
    """
    if not GEMINI_API_KEY:
        return None

    model_name = GEMINI_TTS_MODEL or "gemini-2.5-flash-preview-tts"
    voice_name = GEMINI_TTS_VOICE or "Aoede"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}"

    prompt = (
        "Sen tajribali, muloyim va professional suhandonsan. "
        "Quyidagi matnni o'zbek tilida sof, ravon, suhandon kabi ohangda, "
        "juda tabiiy, iliq va yoqimli inson ovozida o'qib ber:\n\n"
        f"{clean_text}"
    )

    payload = {
        "contents": [{
            "role": "user",
            "parts": [{"text": prompt}]
        }],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {
                        "voiceName": voice_name
                    }
                }
            }
        }
    }

    headers = {
        "User-Agent": "AlfredJobAgent/1.0 (TTS Engine)",
        "Connection": "close"
    }

    def _sync_request():
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                candidates = data.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    for part in parts:
                        if "inlineData" in part:
                            b64_data = part["inlineData"].get("data", "")
                            if b64_data:
                                pcm_bytes = base64.b64decode(b64_data)
                                return save_pcm_to_audio_file(pcm_bytes, 24000)
        except Exception as req_err:
            logger.warning(f"Gemini TTS so'rov xatosi: {req_err}")
        return None

    try:
        audio_file = await asyncio.to_thread(_sync_request)
        if audio_file and os.path.exists(audio_file) and os.path.getsize(audio_file) > 1000:
            logger.info(f"Gemini TTS ({model_name}, ovoz: {voice_name}) muvaffaqiyatli yaratildi: {audio_file}")
            return audio_file
    except Exception as e:
        logger.error(f"Gemini TTS generatsiyasida xatolik: {e}")

    return None

async def generate_voice_message(text: str, prepare_script: bool = True) -> str:
    """
    Asosiy funksiya: Matnni tabiiy, yoqimli ovozli xabarga aylantirish.
    1. Matnni Gemini orqali do'stona 3-4 gapdan iborat toza so'zlashuv nutqiga aylantiradi.
    2. Yulduzcha, chiziqcha, havola, emojilarni tozalaydi.
    3. 1-BOSQICH (ASOSIY OVOZ): ElevenLabs API (eleven_multilingual_v2, fonetik transkripsiyalangan, toza .ogg Opus format).
    4. 2-BOSQICH (ZAXIRA OVOZ): Agar ElevenLabs balansi tugasa yoki xato bersa -> Sozlangan Edge-TTS (uz-UZ-SardorNeural).
    5. 3-BOSQICH (QO'SHIMCHA ZAXIRA): Google Gemini Native Audio TTS.
    """
    if not text:
        text = (
            "Assalomu alaykum Behruzbek! Siz uchun yangi juda mos taklif topildi. "
            "Barcha batafsil ma'lumotlarni Telegram chatimizda ko'rib chiqishingiz mumkin."
        )

    # 1. So'zlashuv nutqini tayyorlash (agar raw matn yoki e'lon bo'lsa)
    speech_text = text
    if prepare_script:
        needs_gemini_prep = (
            "http" in text.lower() or
            "*" in text or
            "#" in text or
            len(text) > 250 or
            not text.strip().lower().startswith("assalomu alaykum")
        )
        if needs_gemini_prep:
            try:
                from ai_analyzer import prepare_conversational_voice_text
                prepared = await prepare_conversational_voice_text(text)
                if prepared:
                    speech_text = prepared
            except Exception as prep_err:
                logger.warning(f"Gemini nutq tayyorlashda xatolik: {prep_err}")

    # 2. Qat'iy tozalash (yulduzcha, chiziqcha, havola, emoji va h.k. yo'q qilinadi)
    clean_text = clean_speech_text(speech_text)
    if not clean_text:
        clean_text = (
            "Assalomu alaykum Behruzbek! Sizga yangi mos vakansiya topildi. "
            "Batafsil ma'lumotlarni chatda ko'rib chiqishingiz mumkin."
        )

    # 3. 1-BOSQICH: ElevenLabs Multilingual V2 (Asosiy ovoz, toza Telegram Voice .ogg)
    eleven_audio = await generate_elevenlabs_tts(clean_text)
    if eleven_audio:
        return eleven_audio

    # 4. 2-BOSQICH (ZAXIRA): Sozlangan Edge-TTS (SardorNeural)
    logger.info("ElevenLabs ishlamadi yoki balansi tugadi. Zaxira Edge-TTS tizimiga o'tilmoqda...")
    edge_audio = await generate_edge_tts(clean_text)
    if edge_audio:
        return edge_audio

    # 5. 3-BOSQICH (QO'SHIMCHA ZAXIRA): Gemini Native Audio TTS
    logger.info("Edge-TTS zaxirasi o'rniga Gemini TTS bilan urinib ko'rilmoqda...")
    gemini_audio = await generate_gemini_tts(clean_text)
    if gemini_audio:
        return gemini_audio

    logger.error("Barcha TTS provayderlari (ElevenLabs, Edge-TTS va Gemini) xatolik berdi!")
    return ""

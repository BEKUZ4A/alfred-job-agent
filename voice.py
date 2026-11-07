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
    GEMINI_API_KEY,
    GEMINI_TTS_MODEL,
    GEMINI_TTS_VOICE,
    EDGE_TTS_VOICE,
    EDGE_TTS_RATE,
    EDGE_TTS_PITCH
)

logger = logging.getLogger("Alfred.Voice")

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
    Gemini qaytargan raw PCM baytlarini (16-bit 24kHz)
    soundfile orqali MP3/OGG ga yoki standart wave moduli orqali WAV ga saqlash.
    """
    temp_dir = tempfile.gettempdir()
    unique_suffix = f"{os.getpid()}_{int(time.time() * 1000)}"
    mp3_path = os.path.join(temp_dir, f"alfred_voice_{unique_suffix}.mp3")

    # 1-variant: soundfile yordamida MP3 yoki OGG ga yozish
    try:
        import soundfile as sf
        import numpy as np
        audio_array = np.frombuffer(pcm_bytes, dtype=np.int16)
        try:
            sf.write(mp3_path, audio_array, sample_rate, format="MP3")
            return mp3_path
        except Exception as mp3_err:
            logger.debug(f"soundfile MP3 yozishda xato: {mp3_err}, OGG formatiga o'tilmoqda")
            ogg_path = os.path.join(temp_dir, f"alfred_voice_{unique_suffix}.ogg")
            sf.write(ogg_path, audio_array, sample_rate, format="OGG", subtype="OPUS")
            return ogg_path
    except Exception as sf_err:
        logger.warning(f"soundfile orqali saqlashda xatolik: {sf_err}, WAV formatiga o'tilmoqda")

    # 2-variant (Fallback): Standart kutubxona wave moduli orqali WAV saqlash
    wav_path = os.path.join(temp_dir, f"alfred_voice_{unique_suffix}.wav")
    import wave
    with wave.open(wav_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit = 2 bytes
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return wav_path

async def generate_gemini_tts(clean_text: str) -> Optional[str]:
    """
    Asosiy ovoz: Google Gemini Native Audio TTS (gemini-2.5-flash-preview-tts)
    Aoede yoki Charon ovozi yordamida o'zbek tilida tabiiy suhandon kabi o'qiydi.
    """
    if not GEMINI_API_KEY:
        logger.warning("GEMINI_API_KEY mavjud emas, Gemini TTS o'tkazib yuborildi.")
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
        for attempt in range(2):
            try:
                resp = requests.post(url, json=payload, headers=headers, timeout=35)
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
                else:
                    logger.warning(f"Gemini TTS urinish {attempt+1} HTTP {resp.status_code}: {resp.text[:120]}")
            except Exception as req_err:
                logger.warning(f"Gemini TTS so'rov xatosi (urinish {attempt+1}): {req_err}")
            time.sleep(1)
        return None

    try:
        audio_file = await asyncio.to_thread(_sync_request)
        if audio_file and os.path.exists(audio_file) and os.path.getsize(audio_file) > 1000:
            logger.info(f"Gemini Native TTS ({model_name}, ovoz: {voice_name}) muvaffaqiyatli yaratildi: {audio_file}")
            return audio_file
    except Exception as e:
        logger.error(f"Gemini TTS generatsiyasida xatolik: {e}")

    return None

async def generate_edge_tts(clean_text: str) -> Optional[str]:
    """
    Zaxira ovoz: Sozlangan Edge-TTS (uz-UZ-SardorNeural)
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

async def generate_voice_message(text: str, prepare_script: bool = True) -> str:
    """
    Asosiy funksiya: Matnni tabiiy, yoqimli ovozli xabarga aylantirish.
    1. Matnni Gemini orqali do'stona 3-4 gapdan iborat toza so'zlashuv nutqiga aylantiradi.
    2. Yulduzcha, chiziqcha, havola, emojilarni tozalaydi.
    3. Asosiy ovoz: Gemini Native Audio TTS (Aoede/Charon) dan foydalanadi.
    4. Agar Gemini TTS da xato/limit bo'lsa, zaxiradagi sozlangan Edge-TTS (SardorNeural, rate=+8%, pitch=-2Hz) ga o'tadi.
    """
    if not text:
        text = (
            "Assalomu alaykum Behruzbek! Siz uchun yangi juda mos taklif topildi. "
            "Barcha batafsil ma'lumotlarni Telegram chatimizda ko'rib chiqishingiz mumkin."
        )

    # 1. So'zlashuv nutqini tayyorlash (agar raw matn yoki e'lon bo'lsa)
    speech_text = text
    if prepare_script:
        # Agar matnda texnik belgilar, ssilka yoki markdown bo'lsa, Gemini'dan do'stona nutq so'raymiz
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

    # 3. 1-bosqich: Gemini Native Audio TTS (Asosiy tabiiy ovoz)
    audio_path = await generate_gemini_tts(clean_text)
    if audio_path:
        return audio_path

    # 4. 2-bosqich: Sozlangan Edge-TTS (Zaxira)
    logger.info("Gemini TTS ishlamadi yoki kvota yetmadi. Sozlangan Edge-TTS zaxirasiga o'tilmoqda...")
    fallback_path = await generate_edge_tts(clean_text)
    if fallback_path:
        return fallback_path

    logger.error("Barcha TTS provayderlari (Gemini va Edge-TTS) xatolik berdi!")
    return ""

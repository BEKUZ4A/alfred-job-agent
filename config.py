import os
from datetime import timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

# Toshkent vaqt mintaqasi (UTC+5)
TASHKENT_TZ = timezone(timedelta(hours=5))

# Qo'ng'iroq qilish vaqti chegarasi (Ertalab 8:00 dan kechki 20:00 gacha)
CALL_START_HOUR = int(os.getenv("CALL_START_HOUR", "8"))
CALL_END_HOUR = int(os.getenv("CALL_END_HOUR", "20"))

# Bitta qidiruvda jo'natiladigan maksimal yangi mos vakansiyalar soni
MAX_MATCHES_PER_SCAN = int(os.getenv("MAX_MATCHES_PER_SCAN", "10"))

# Telegram Bot sozlamalari
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Telegram Userbot (Telethon) sozlamalari
TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", "0")) if os.getenv("TELEGRAM_API_ID") else None
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")
TELEGRAM_STRING_SESSION = os.getenv("TELEGRAM_STRING_SESSION", "")
TELEGRAM_PHONE = os.getenv("TELEGRAM_PHONE", "")

# Ma'lumotlar bazasi (PostgreSQL / Neon.tech yoki SQLite)
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

# Google Calendar sozlamalari
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary").strip()

# Google Gemini API kaliti
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# CallMeBot qo'ng'iroq sozlamasi (masalan: @username)
CALLMEBOT_USERNAME = os.getenv("CALLMEBOT_USERNAME", "")

# Minimal moslik bali (0-100 oralig'ida)
MIN_MATCH_SCORE = int(os.getenv("MIN_MATCH_SCORE", "65"))

# Server porti (Render.com avtomatik PORT o'zgaruvchisini beradi)
PORT = int(os.getenv("PORT", "8000"))

# ElevenLabs Ovoz sozlamalari (Asosiy premium ovoz)
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "sk_d770ede4c9647d52c5c4282b536d6e62b86ef156e3a54a93").strip().strip('"').strip("'")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "GChwNsPvTsqD6tAAORVU").strip().strip('"').strip("'")
ELEVENLABS_MODEL_ID = os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2").strip().strip('"').strip("'")

# Gemini Native Audio TTS sozlamalari (Zaxira tabiiy ovoz)
GEMINI_TTS_MODEL = os.getenv("GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts")
GEMINI_TTS_VOICE = os.getenv("GEMINI_TTS_VOICE", "Aoede")  # "Aoede" (yoqimli, tabiiy) yoki "Charon" (jiddiy erkak ovozi)

# Zaxira ovoz sozlamalari (Sozlangan Edge-TTS)
EDGE_TTS_VOICE = os.getenv("EDGE_TTS_VOICE", "uz-UZ-SardorNeural")  # erkak ovozi
EDGE_TTS_RATE = os.getenv("EDGE_TTS_RATE", "+8%")  # biroz jonliroq tezlik
EDGE_TTS_PITCH = os.getenv("EDGE_TTS_PITCH", "-2Hz")  # biroz chuqurroq, yoqimli tembr

# Kuzatiladigan 9 ta ommabop Telegram kanallar ro'yxati
TELEGRAM_CHANNELS = [
    "joblinkuz",
    "rizqimuz",
    "ish_kerak_toshkent_bormi_ishlar",
    "jobforjunior",
    "freelance_uzb",
    "young_june",
    "young_intern",
    "UstozShogird",
    "it_jobs_uz"
]

# Nomzodning profili (AI tahlili uchun)
CANDIDATE_PROFILE = """
- Kasb: Python Backend Dasturchi / AI Assistant Developer
- Daraja: Junior, Intern, AI-assisted developer, Freelance
- Asosiy texnologiyalar: Python, Django, FastAPI, PostgreSQL, Docker, Redis, Celery
- Qo'shimcha qiziqishlar: AI Agentlar, Gemini API, LLM integratsiyasi
- Qat'iy talab: Senior, Team Lead yoki 4-5 yildan ko'p tajriba so'ralgan ishlarni, shuningdek Python bilan aloqasi yo'q ishlarni rad etish.
"""

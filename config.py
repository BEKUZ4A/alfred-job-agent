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

# Standart Hardcode Zaxira sozlamalari (Render Environment bo'sh bo'lganda ham 100% ishlashi uchun)
DEFAULT_CALLER_SESSION = "1ApWapzMBuxQkD8duazRqHaglmuNTOOhhBpXfUat8haM6ryl_w5eo1C8twjxr0PCqtJ3mOIw-JSycj0yeCVilGtgTSoYKUKxTxgwGmxoOulNqOlm4lR2RqWJYr2M9NcQEFJqwJ3PH5Ue4z_T60h65tQqzgkCPblXDew7C2ZbFr2istHHkwg87XuFQ_0nEkEkXkQeCxDYnyRWCoUbG6ciYCh7H6YtwYbJJnW65cn7nTgpFxmuKZN1ubNNSViKCR_2XCkGjr7dwKnkbPEMLERexT6quypfDl7WBVrHq6PdSLvYKKEkVppZcKXo3LC9dbWuLOmSmnMWMqqi7opnhJUDFnBU-04jj7uY="
DEFAULT_CALLER_PHONE = "+998930037685"

# Telegram Bot sozlamalari
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip().strip("'\"")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip().strip("'\"")

# Telegram Userbot (Telethon 1-akkaunt: HR kuzatuvi va asosiy profil)
_raw_api_id = (os.getenv("TELEGRAM_API_ID") or "").strip().strip("'\"")
TELEGRAM_API_ID = int(_raw_api_id) if _raw_api_id.isdigit() else 32420165
TELEGRAM_API_HASH = (os.getenv("TELEGRAM_API_HASH") or "").strip().strip("'\"") or "721ad5ccdbf90d377435798b3ddf8b46"
TELEGRAM_STRING_SESSION = (os.getenv("TELEGRAM_STRING_SESSION") or "").strip().strip("'\"")
TELEGRAM_PHONE = (os.getenv("TELEGRAM_PHONE") or "").strip().strip("'\"") or "+998999875186"

# Telegram Caller Userbot (Telethon 2-akkaunt: Audio qo'ng'iroq / Gudok beruvchi)
CALLER_STRING_SESSION = (os.getenv("CALLER_STRING_SESSION") or "").strip().strip("'\"") or DEFAULT_CALLER_SESSION
CALLER_PHONE = (os.getenv("CALLER_PHONE") or "").strip().strip("'\"") or DEFAULT_CALLER_PHONE

# Ma'lumotlar bazasi (PostgreSQL / Neon.tech yoki SQLite)
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

# Google Calendar sozlamalari
GOOGLE_SERVICE_ACCOUNT_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
GOOGLE_CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary").strip()

# Google Gemini API kaliti
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()


# Minimal moslik bali (0-100 oralig'ida)
MIN_MATCH_SCORE = int(os.getenv("MIN_MATCH_SCORE", "65"))

# Server porti (Render.com avtomatik PORT o'zgaruvchisini beradi)
PORT = int(os.getenv("PORT", "8000"))

# ElevenLabs Ovoz sozlamalari (Asosiy premium ovoz)
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "").strip().strip('"').strip("'")
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
- Daraja: Tajribasiz (No experience / Без опыта), Intern, Stajyor, Junior, AI-assisted developer, Freelance
- Tajriba talabi bo'yicha: Tajribasiz (0 yil tajriba / tajriba talab qilinmaydi), amaliyot, stajirovka va 1 yilgacha bo'lgan boshlang'ich takliflar hamda frilans buyurtmalar.
- Asosiy texnologiyalar: Python, Django, FastAPI, PostgreSQL, Docker, Redis, Celery
- Qo'shimcha qiziqishlar: AI Agentlar, Gemini API, LLM integratsiyasi
- Qat'iy talab: Tajribasiz va stajyorlik vakansiyalariga birinchi darajali ustunlik berish. Senior, Team Lead yoki 3-4+ yillik tajriba talab qilgan ishlarni rad etish.
"""

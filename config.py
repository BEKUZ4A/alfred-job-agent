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

# Google Gemini API kaliti
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

# CallMeBot qo'ng'iroq sozlamasi (masalan: @username)
CALLMEBOT_USERNAME = os.getenv("CALLMEBOT_USERNAME", "")

# Minimal moslik bali (0-100 oralig'ida)
MIN_MATCH_SCORE = int(os.getenv("MIN_MATCH_SCORE", "65"))

# Server porti (Render.com avtomatik PORT o'zgaruvchisini beradi)
PORT = int(os.getenv("PORT", "8000"))

# O'zbek ovozi
EDGE_TTS_VOICE = os.getenv("EDGE_TTS_VOICE", "uz-UZ-MadinaNeural")

# Kuzatiladigan Telegram kanallar ro'yxati
TELEGRAM_CHANNELS = [
    "joblinkuz",
    "rizqimuz",
    "ish_kerak_toshkent_bormi_ishlar",
    "jobforjunior",
    "freelance_uzb",
    "young_june",
    "young_intern"
]

# Nomzodning profili (AI tahlili uchun)
CANDIDATE_PROFILE = """
- Kasb: Python Backend Dasturchi / AI Assistant Developer
- Daraja: Junior, Intern, AI-assisted developer, Freelance
- Asosiy texnologiyalar: Python, Django, FastAPI, PostgreSQL, Docker, Redis, Celery
- Qo'shimcha qiziqishlar: AI Agentlar, Gemini API, LLM integratsiyasi
- Qat'iy talab: Senior, Team Lead yoki 4-5 yildan ko'p tajriba so'ralgan ishlarni, shuningdek Python bilan aloqasi yo'q ishlarni rad etish.
"""

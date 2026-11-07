import httpx
import json
import logging
import base64
from typing import Dict, Any, List, Optional
from config import GEMINI_API_KEY, CANDIDATE_PROFILE

logger = logging.getLogger("Alfred.AIAnalyzer")

# Modellarni ketma-ket tekshirish zanjiri (200 OK qaytargan eng tezkor va barqaror bepul modellar)
AVAILABLE_MODELS = [
    "gemini-3.5-flash-lite",   # 1. Tezkor, barqaror va bepul kvotali (200 OK)
    "gemini-3.1-flash-lite",   # 2. Barqaror zaxira flash-lite (200 OK)
    "gemini-flash-lite-latest",# 3. Flash-lite latest (200 OK)
    "gemini-3.8-flash",        # 4. Yangi avlod Gemini 3.8
    "gemini-3.5-flash",        # 5. Gemini 3.5 flash
    "gemini-flash-latest",     # 6. Flash latest
    "gemma-4-26b-a4b-it",      # 7. Gemma modeli (200 OK)
]

# Tizimdagi oxirgi xatolik holatini saqlash (foydalanuvchi /status buyrug'ida ko'rishi uchun)
last_error_info: str = "Tizim ishga tushdi, xatolik qayd etilmadi."

def get_last_error() -> str:
    """Telegram /status buyrug'iga oxirgi AI holati yoki xatolik matnini qaytarish"""
    global last_error_info
    return last_error_info

def set_last_error(msg: str):
    """Oxirgi xatolikni yangilash"""
    global last_error_info
    last_error_info = msg

BATCH_SYSTEM_INSTRUCTION = f"""
Sen "Alfred" - professional IT-rekruter va shaxsiy karyera tahlilchisisan.
Vazifang berilgan ish e'lonlari va frilans buyurtmalarni quyidagi nomzod profiliga qanchalik mos kelishini sinchkovlik bilan tahlil qilish:

NOMZODNING PROFILI:
{CANDIDATE_PROFILE}

BAHOLASH VA FILTRLASH QOIDALARI:
1. BUYURTMACHI VA DASTURCHI REKLAMASINI AJRATISH (JUDA MUHIM):
   - Agar e'lon beruvchi o'z xizmatlarini reklama qilayotgan boshqa dasturchi/agentlik bo'lsa (masalan: "Bot yasab beramiz", "Sayt ochamiz", "Bot sotiladi", "Услуги создания сайтов", "Разработка ботов на заказ" va h.k.):
     -> BUNI QAT'IY RAD ET (`is_relevant: false`, `match_score: 0`). Bizning foydalanuvchimiz dasturchi, unga boshqa dasturchining xizmati emas, BUYURTMACHI yoki ISH BERUVCHI kerak!
   - Faqat haqiqiy ISH BERUVCHI (vakansiya: kompaniyaga xodim kerak) yoki BUYURTMACHI (frilans loyiha: "Bot yasatmoqchiman", "Sayt kerak", "Dasturchi izlayapman", "Нужен бот/сайт/парсер") yozgan e'lonlarni qabul qil!

2. E'LON TURI (job_type):
   - "vacancy": To'liq yoki yarim stavkali uzoq muddatli ish, ofis/masofaviy kompaniya vakansiyasi.
   - "freelance": Bir martalik buyurtma, frilans loyiha, bot/sayt/skript yasatishga mijoz izlayotgan buyurtma.

3. Moslik bali (0 dan 100 gacha):
   - 80-100: Junior/Intern/Freelance Python, Django, FastAPI, PostgreSQL, Docker, Telegram bot, Web scraping, AI Agentlar talab qilingan ajoyib ish yoki frilans buyurtma.
   - 60-79: Boshlang'ich yoki 1 yilgacha tajribali yaxshi taklif.
   - 0-59: Senior, Team Lead, 3+ yil tajriba talab qilgan, yoki Python/Backend bilan aloqasi yo'q ishlar.

4. `voice_text` maydoni Telegram ovozli xabarga aylantiriladi. U o'zbek tilida, do'stona, qisqa va aniq (15-20 soniyalik) bo'lsin.
Masalan: "Salom Behruz! Sizga mos yangi [vakansiya / frilans buyurtma] topildi: [Nomi]. Byudjet/Maosh: [Narxi]. Batafsil Telegram chatda."

JAVOBNI FAQAT QUYIDAGI JSON MASSIV (ARRAY) FORMATIDA QAYTAR (boshqa hech qanday kirish yoki tushuntirish so'zlarisiz):
[
  {{
    "id": "e'lon_id",
    "is_relevant": true,
    "job_type": "vacancy",
    "match_score": 85,
    "role_title": "Junior Python Developer / Telegram Bot Loyihasi",
    "company": "Kompaniya yoki Buyurtmachi",
    "experience_level": "Junior / Frilans",
    "tech_stack": ["Python", "FastAPI", "Telegram Bot"],
    "salary": "400$ - 700$ yoki Kelishilgan narxda",
    "short_summary": "O'zbek tilida 1-2 gapda e'lon haqida xulosa",
    "voice_text": "Ovozli xabar uchun o'zbekcha 2-3 jumlali matn",
    "reason": "Nima uchun ushbu ish nomzodga mos kelishining sababi"
  }}
]
"""

async def call_gemini_api(prompt: str, system_instruction: str = "") -> Optional[str]:
    """
    Gemini API ga bir nechta model bo'yicha zaxira (fallback) bilan so'rov yuborish.
    Xatolik yuz berganda uning aniq texnik sababi (404, 429, API Key) qayd etiladi.
    """
    if not GEMINI_API_KEY:
        err_msg = "❌ Xatolik: GEMINI_API_KEY topilmadi yoki o'rnatilmagan!"
        logger.error(err_msg)
        set_last_error(err_msg)
        return None

    attempts_log = []

    for model_name in AVAILABLE_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}"
        
        contents = []
        if system_instruction:
            contents.append({"role": "user", "parts": [{"text": f"SYSTEM INSTRUCTION: {system_instruction}"}]})
            contents.append({"role": "model", "parts": [{"text": "Tushundim, barcha ko'rsatmalarga qat'iy amal qilaman."}]})
            
        contents.append({"role": "user", "parts": [{"text": prompt}]})
        
        payload = {
            "contents": contents,
            "generationConfig": {
                "temperature": 0.2,
                "topP": 0.8
            }
        }
        
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        text_part = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                        set_last_error(f"✅ Barchasi soz. Faol model: {model_name}")
                        return text_part
                elif resp.status_code == 429:
                    err_detail = "429 Rate Limit (So'rovlar limiti oshib ketdi)"
                    logger.warning(f"Model {model_name} -> {err_detail}")
                    attempts_log.append(f"{model_name}: {err_detail}")
                elif resp.status_code == 404:
                    err_detail = "404 Not Found (Model mavjud emas yoki eskirgan)"
                    logger.warning(f"Model {model_name} -> {err_detail}")
                    attempts_log.append(f"{model_name}: {err_detail}")
                else:
                    err_detail = f"HTTP {resp.status_code}: {resp.text[:100]}"
                    logger.warning(f"Model {model_name} -> {err_detail}")
                    attempts_log.append(f"{model_name}: {err_detail}")
        except Exception as e:
            err_detail = f"Ulanish xatosi: {str(e)[:80]}"
            logger.warning(f"Model {model_name} bilan ulanishda xato: {e}")
            attempts_log.append(f"{model_name}: {err_detail}")
            
    summary_error = "❌ Barcha Gemini modellari rad etildi: " + " | ".join(attempts_log[-3:])
    logger.error(summary_error)
    set_last_error(summary_error)
    return None

async def batch_analyze_vacancies(vacancies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Barcha yangi e'lonlarni bitta umumiy so'rovda (batch prompt) Gemini'ga yuborib tahlil qilish.
    Bu 15 RPM Rate Limit (429 xatosi) oldini oladi va juda tez ishlaydi.
    """
    if not vacancies:
        return []

    # E'lonlarni ixcham JSON formatga keltiramiz
    items_to_send = []
    for v in vacancies:
        items_to_send.append({
            "id": v["id"],
            "title": v.get("title", ""),
            "company": v.get("company", ""),
            "source": v.get("source", ""),
            "posted_at": v.get("posted_at", ""),
            "text": v.get("text", "")[:1200]
        })

    prompt = (
        f"Quyidagi {len(items_to_send)} ta yangi vakansiyani tahlil qil. "
        "Har biri uchun qat'iy ravishda ko'rsatilgan JSON massiv ko'rinishida javob qaytar:\n\n"
        + json.dumps(items_to_send, ensure_ascii=False)
    )

    response_text = await call_gemini_api(prompt, BATCH_SYSTEM_INSTRUCTION)
    if not response_text:
        return []

    try:
        clean_json = response_text.strip()
        if clean_json.startswith("```json"):
            clean_json = clean_json[7:]
        if clean_json.startswith("```"):
            clean_json = clean_json[3:]
        if clean_json.endswith("```"):
            clean_json = clean_json[:-3]
        clean_json = clean_json.strip()

        results = json.loads(clean_json)
        if isinstance(results, list):
            return results
        elif isinstance(results, dict):
            # Agar bitta obyekt qaytarsa massivga solamiz
            return [results]
        return []
    except Exception as e:
        logger.error(f"Batch tahlil javobini JSON ga o'girishda xatolik: {e}. Xom javob: {response_text[:200]}")
        set_last_error(f"⚠️ JSON Parse xatoligi: {str(e)[:80]}")
        return []

async def chat_with_alfred(user_message: str) -> str:
    """Foydalanuvchi botga yozganda Alfred sifatida o'zbek tilida maslahat berish"""
    system_prompt = f"""
    Sen "Alfred" - foydalanuvchining shaxsiy AI IT-rekruteri va do'stonasansan.
    Foydalanuvchi dasturlashni o'rganayotgan va Junior/Intern Python, AI, Django, FastAPI bo'yicha ish izlayotgan yosh dasturchi.
    U bilan o'zbek tilida, juda samimiy, sodda va tushunarli tilda gaplash.
    Unga rezyume, suhbatlarga tayyorlanish, portfolioga loyihalar qo'shish va vakansiyalar bo'yicha amaliy maslahatlar ber.
    Javoblaring londa, professional va dalda beruvchi bo'lsin.
    """
    
    answer = await call_gemini_api(user_message, system_prompt)
    if answer:
        return answer
        
    # Agar xatolik bo'lsa, aniq texnik sababini chiqarish
    last_err = get_last_error()
    return (
        "Uzr, hozirda sun'iy intellekt xizmati bilan vaqtinchalik ulanishda uzilish bo'ldi.\n\n"
        f"🔍 <b>Texnik sabab:</b>\n<code>{last_err}</code>\n\n"
        "Birozdan so'ng qayta urinib ko'ring yoki /status buyrug'i orqali tizim holatini tekshiring."
    )

async def transcribe_voice_with_gemini(audio_bytes: bytes, mime_type: str = "audio/ogg") -> Optional[str]:
    """
    Telegramdan kelgan ovozli xabarni (audio bytes) Gemini orqali eshitib, matnga aylantirish (transkripsiya qilish).
    O'zbek va rus tillaridagi ovozlarni yuqori aniqlikda taniydi.
    """
    if not GEMINI_API_KEY:
        logger.error("GEMINI_API_KEY topilmadi!")
        return None
        
    b64_audio = base64.b64encode(audio_bytes).decode("utf-8")
    
    prompt = (
        "Ushbu audio fayldagi ovozni diqqat bilan eshitib, aytilgan gapni so'zma-so'z matnga aylantir (transkripsiya qil). "
        "Faqat eshitilgan so'zlarni qaytar, boshqa hech qanday izoh, tarjima yoki kirish so'z qo'shma."
    )
    
    for model_name in AVAILABLE_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": [{
                "role": "user",
                "parts": [
                    {
                        "inlineData": {
                            "mimeType": mime_type,
                            "data": b64_audio
                        }
                    },
                    {
                        "text": prompt
                    }
                ]
            }],
            "generationConfig": {
                "temperature": 0.1
            }
        }
        
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts:
                            text = parts[0].get("text", "").strip()
                            if text:
                                logger.info(f"Ovozli xabar transkripsiyasi muvaffaqiyatli ({model_name}): {text}")
                                return text
                else:
                    logger.warning(f"Ovoz transkripsiyasida {model_name} HTTP {resp.status_code}: {resp.text[:100]}")
        except Exception as e:
            logger.warning(f"Ovoz transkripsiyasida {model_name} xatoligi: {e}")
            
    return None

async def generate_freelance_pitch(title: str, summary: str, source: str = "") -> str:
    """
    Frilans buyurtmasi uchun mijozga (buyurtmachiga) yuborishga mo'ljallangan
    qisqa, ishonarli, xushmuomala va professional tijoriy taklif (Cover Letter / Pitch) yozish.
    """
    prompt = f"""
    Sen "Alfred" professional AI karyera agentisan. Foydalanuvchi Behruz (GitHub: https://github.com/BEKUZ4A).
    U tajribali Python, Django, FastAPI, AI Agentlar va Telegram botlar yaratuvchi dasturchi.
    
    Quyidagi frilans buyurtma / ish e'loni bo'yicha mijozga yuboriladigan qisqa, nihoyatda ishonarli va professional tijoriy taklif (Pitch / Cover Letter) yoz:
    
    BUYURTMA MA'LUMOTLARI:
    - Nomi: {title}
    - Mazmuni: {summary}
    - Manba: {source}
    
    TAKRIF TALABLARI:
    1. Til: Agar e'lon rus tilida bo'lsa rus tilida, o'zbek tilida bo'lsa o'zbek tilida yoz.
    2. Mazmuni: "Assalomu alaykum! Men bu bot/sayt/loyihani zamonaviy AI agentlar va Python/Django/FastAPI yordamida tez, xavfsiz va yuqori sifatli qilib bera olaman."
    3. Albatta GitHub profil havolasini qo'sh: https://github.com/BEKUZ4A
    4. Qisqa va londa (3-4 jumla), ortiqcha gaplarsiz, mijoz darhol bog'lanishi uchun qulay va ishonchli shaklda bo'lsin.
    
    Faqat mijozga yuboriladigan taklif matnini qaytar.
    """
    pitch = await call_gemini_api(prompt)
    if pitch:
        return pitch.strip()
    return (
        "Assalomu alaykum! Ushbu bot/sayt loyihasini zamonaviy AI agentlar va Python/FastAPI/Django yordamida "
        "tez va yuqori sifatda qilib bera olaman. Loyihalarim va portfolio bilan GitHub profilimda tanishishingiz mumkin: "
        "https://github.com/BEKUZ4A . Aloqaga chiqishingizni kutaman!"
    )

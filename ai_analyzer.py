import httpx
import json
import logging
from typing import Dict, Any, List, Optional
from config import GEMINI_API_KEY, CANDIDATE_PROFILE

logger = logging.getLogger("Alfred.AIAnalyzer")

# Modellarni ketma-ket tekshirish zanjiri:
# 1. Foydalanuvchi talab qilgan asosiy model: gemini-2.5-flash
# 2. Fallback: gemini-2.0-flash
# 3. 100% bepul, kvotasi mavjud va barqaror ishlaydigan zaxira modellar
AVAILABLE_MODELS = [
    "gemini-2.5-flash",       # 1. Asosiy model (foydalanuvchi tanlovi)
    "gemini-2.0-flash",       # 2. Fallback model (foydalanuvchi tanlovi)
    "gemini-3.5-flash-lite",  # 3. Barqaror va tezkor bepul flash-lite (200 OK)
    "gemini-3.1-flash-lite",  # 4. Zaxira flash-lite
    "gemini-flash-latest",    # 5. Eng yangi barqaror flash
    "gemini-3.5-flash",       # 6. Flash 3.5
    "gemini-3-flash-preview", # 7. Flash 3 preview
    "gemma-4-26b-a4b-it",     # 8. Gemma modeli
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
Vazifang berilgan bir nechta ish e'lonlarini (vakansiyalarni) quyidagi nomzod profiliga qanchalik mos kelishini sinchkovlik bilan tahlil qilish:

NOMZODNING PROFILI:
{CANDIDATE_PROFILE}

BAHOLASH QOIDALARI:
1. Moslik bali (0 dan 100 gacha):
   - 80-100: Junior/Intern/Freelance Python, Django, FastAPI, PostgreSQL, Docker, AI Agentlar talab qilingan ajoyib vakansiya.
   - 60-79: Python backend bo'yicha boshlang'ich yoki 1 yilgacha tajribali yaxshi vakansiya.
   - 0-59: Senior, Team Lead, 3+ yil tajriba talab qilgan, yoki Python bilan aloqasi yo'q (PHP, Java, Node.js va h.k.) ishlar.
2. Agar ish Senior/Lead bo'lsa yoki Python bo'lmasa -> `is_relevant: false`, `match_score` 40 dan past bo'lishi shart.
3. `voice_text` maydoni Telegram ovozli xabarga aylantiriladi. U o'zbek tilida, do'stona, qisqa va aniq (15-20 soniyalik) bo'lsin.
Masalan: "Salom Behruz! Sizga mos yangi Junior Python vakansiyasi topildi. Kompaniya: [Nomi]. Asosiy talablar: FastAPI va PostgreSQL. Batafsil Telegram chatda."

JAVOBNI FAQAT QUYIDAGI JSON MASSIV (ARRAY) FORMATIDA QAYTAR (boshqa hech qanday kirish yoki tushuntirish so'zlarisiz):
[
  {{
    "id": "e'lon_id",
    "is_relevant": true,
    "match_score": 85,
    "role_title": "Junior Python Developer",
    "company": "Kompaniya nomi",
    "experience_level": "Junior / Intern",
    "tech_stack": ["Python", "FastAPI", "PostgreSQL"],
    "salary": "400$ - 700$ yoki Ko'rsatilmagan",
    "short_summary": "O'zbek tilida 1-2 gapda vakansiya haqida xulosa",
    "voice_text": "Ovozli xabar uchun o'zbekcha 2-3 jumlali matn",
    "reason": "Nima uchun ushbu vakansiya nomzodga mos kelishining sababi"
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

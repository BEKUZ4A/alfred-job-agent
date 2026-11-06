import httpx
import json
import logging
from typing import Dict, Any, Optional
from config import GEMINI_API_KEY, CANDIDATE_PROFILE

logger = logging.getLogger("Alfred.AIAnalyzer")

AVAILABLE_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.5-flash",
    "gemini-3-flash-preview"
]

ANALYSIS_SYSTEM_INSTRUCTION = f"""
Sen "Alfred" - professional IT-rekruter va shaxsiy karyera tahlilchisisan.
Vazifang berilgan ish e'lonini (vakansiyani) quyidagi nomzod profiliga qanchalik mos kelishini sinchkovlik bilan tahlil qilish:

NOMZODNING PROFILI:
{CANDIDATE_PROFILE}

BAHOLASH QOIDALARI:
1. Moslik bali (0 dan 100 gacha):
   - 80-100: Junior/Intern/Freelance Python, Django, FastAPI, PostgreSQL, Docker, AI Agentlar talab qilingan ajoyib vakansiya.
   - 60-79: Python backend bo'yicha boshlang'ich yoki 1 yilgacha tajribali yaxshi vakansiya.
   - 0-59: Senior, Team Lead, 3+ yil tajriba talab qilgan, yoki Python bilan aloqasi yo'q (PHP, Java, Node.js va h.k.) ishlar.
2. Agar ish Senior/Lead bo'lsa yoki Python bo'lmasa -> `is_relevant: false`, `match_score` 40 dan past bo'lishi shart.
3. `voice_text` maydoni Telegram ovozli xabarga aylantiriladi. U o'zbek tilida, do'stona, qisqa va aniq (15-20 soniyalik) bo'lsin.
Masalan: "Salom Behruz! Sizga mos yangi Junior Python vakansiyasi topildi. Kompaniya: [Nomi]. Asosiy talablar: FastAPI va PostgreSQL. Batafsil chatda."

JAVOBNI FAQAT QUYIDAGI JSON FORMATIDA QAYTAR (boshqa hech qanday so'z yoki belgisiz):
{{
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
"""

async def call_gemini_api(prompt: str, system_instruction: str = "") -> Optional[str]:
    """Gemini API ga bir nechta model bo'yicha zaxira (fallback) bilan so'rov yuborish"""
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
            async with httpx.AsyncClient(timeout=25) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        text_part = candidates[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                        return text_part
                else:
                    logger.warning(f"Model {model_name} HTTP {resp.status_code} qaytardi: {resp.text[:100]}")
        except Exception as e:
            logger.warning(f"Model {model_name} bilan ulanishda xato: {e}")
            
    logger.error("Barcha Gemini modellari javob bermadi.")
    return None

async def analyze_vacancy(vacancy_text: str) -> Optional[Dict[str, Any]]:
    """Vakansiya matnini tahlil qilib, JSON shaklda baho olish"""
    prompt = f"Quyidagi vakansiya e'lonini tahlil qilib ber:\n\n{vacancy_text}"
    response_text = await call_gemini_api(prompt, ANALYSIS_SYSTEM_INSTRUCTION)
    
    if not response_text:
        return None
        
    try:
        # Markdown ```json teglarni tozalash
        clean_json = response_text.strip()
        if clean_json.startswith("```json"):
            clean_json = clean_json[7:]
        if clean_json.startswith("```"):
            clean_json = clean_json[3:]
        if clean_json.endswith("```"):
            clean_json = clean_json[:-3]
        clean_json = clean_json.strip()
        
        result = json.loads(clean_json)
        return result
    except Exception as e:
        logger.error(f"Gemini javobini JSON ga o'girishda xatolik: {e}. Xom javob: {response_text[:200]}")
        return None

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
    return "Uzr, hozirda sun'iy intellekt xizmati bilan vaqtinchalik ulanishda uzilish bo'ldi. Birozdan so'ng qayta yozing."

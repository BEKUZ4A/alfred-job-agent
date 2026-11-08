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
   - 80-100: Tajribasiz (No experience / Без опыта / tajriba talab qilinmaydi), Stajyor/Intern, Junior, Freelance Python, Django, FastAPI, PostgreSQL, Docker, Telegram bot, Web scraping, AI Agentlar talab qilingan ajoyib ish yoki frilans buyurtma. Ayniqsa "tajribasiz" yoki "stajyor" so'zlari bo'lgan Python/Backend e'lonlariga eng yuqori moslik bali (85-100) berilsin!
   - 60-79: Boshlang'ich yoki 1 yilgacha tajribali yaxshi taklif.
   - 0-59: Senior, Team Lead, 3+ yil tajriba talab qilgan, yoki Python/Backend bilan aloqasi yo'q ishlar.

4. `voice_text` maydoni Telegram ovozli xabarga aylantiriladi:
   - Xuddi yaqin do'stiga yangilik aytayotgandek juda samimiy, do'stona va silliq 3-4 gapdan iborat bo'lsin.
   - Boshlanishi: "Assalomu alaykum Behruzbek! Sizga juda mos [vakansiya / frilans buyurtma] chiqdi..."
   - Unda HECH QACHON yulduzcha (*), panjara (#), chiziqcha (-), havolalar (http/link), emoji nomlari bo'lmasin. Faqat og'zaki nutqda ravon o'qiladigan toza jumlalar bo'lsin.
   - Masalan: "Assalomu alaykum Behruzbek! Sizga mos yangi vakansiya chiqdi: [Nomi]. Kompaniya [Nomi] sizning FastAPI va Python bo'yicha ko'nikmalaringizga to'liq mos keladi. Barcha ma'lumotlarni chatda qoldirdim."

5. SHUBHALI VA SOXTA E'LONLARNI ANIQLASH (Red-Flag Detector):
   - Agar e'londa "3 oy bepul stajirovka", "kurs sotish / o'qish uchun pul to'lash", "tarmoq marketingi / piramida", "depozit / garov kiritish" kabi shubhali yoki adolatsiz shartlar bo'lsa:
     "red_flag": "⚠️ Ehtiyot bo'ling: Ushbu e'londa shubhali shartlar mavjud (kurs sotish / uzoq bepul stajirovka / moliyaviy talab)."
   - Agar hech qanday shubhali holat bo'lmasa: "red_flag": ""

JAVOBNI FAQAT QUYIDAGI JSON MASSIV (ARRAY) FORMATIDA QAYTAR (boshqa hech qanday kirish yoki tushuntirish so'zlarisiz):
[
  {{
    "id": "e'lon_id",
    "is_relevant": true,
    "job_type": "vacancy",
    "match_score": 85,
    "role_title": "Junior Python Developer / Telegram Bot Loyihasi",
    "company": "Kompaniya yoki Buyurtmachi",
    "experience_level": "Tajribasiz / Junior / Frilans",
    "tech_stack": ["Python", "FastAPI", "Telegram Bot"],
    "salary": "400$ - 700$ yoki Kelishilgan narxda",
    "short_summary": "O'zbek tilida 1-2 gapda e'lon haqida xulosa",
    "voice_text": "Assalomu alaykum Behruzbek! Siz uchun ajoyib yangi e'lon chiqdi...",
    "red_flag": "",
    "reason": "Nima uchun ushbu ish nomzodga mos kelishining sababi"
  }}
]
"""

async def call_gemini_api(
    prompt: str, 
    system_instruction: str = "", 
    history: Optional[List[Dict[str, str]]] = None
) -> Optional[str]:
    """
    Gemini API ga bir nechta model bo'yicha zaxira (fallback) bilan so'rov yuborish.
    Suhbat tarixi (history) qo'llab-quvvatlanadi (user/model tartibida navbatma-navbat ulanadi).
    Xatolik yuz berganda uning aniq texnik sababi (404, 429, API Key) qayd etiladi.
    """
    if not GEMINI_API_KEY:
        err_msg = "❌ Xatolik: GEMINI_API_KEY topilmadi yoki o'rnatilmagan!"
        logger.error(err_msg)
        set_last_error(err_msg)
        return None

    attempts_log = []

    # Gemini API uchun tartibli user/model zanjirini qurish
    merged_turns = []
    if system_instruction:
        merged_turns.append({"role": "user", "text": f"SYSTEM INSTRUCTION: {system_instruction}"})
        merged_turns.append({"role": "model", "text": "Tushundim. Barcha ko'rsatma va qoidalarga qat'iy amal qilaman."})

    if history:
        for item in history:
            h_role = "user" if item.get("role") == "user" else "model"
            h_text = (item.get("text") or "").strip()
            if not h_text:
                continue
            # Ketma-ket bir xil rolni birlashtirib borish (Gemini 400 xatosini oldini olish uchun)
            if merged_turns and merged_turns[-1]["role"] == h_role:
                merged_turns[-1]["text"] += "\n" + h_text
            else:
                merged_turns.append({"role": h_role, "text": h_text})

    if merged_turns and merged_turns[-1]["role"] == "user":
        merged_turns[-1]["text"] += "\n" + prompt
    else:
        merged_turns.append({"role": "user", "text": prompt})

    contents = [{"role": t["role"], "parts": [{"text": t["text"]}]} for t in merged_turns]

    for model_name in AVAILABLE_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}"
        payload = {
            "contents": contents,
            "generationConfig": {
                "temperature": 0.2,
                "topP": 0.8
            }
        }
        
        try:
            headers = {"User-Agent": "AlfredJobAgent/1.0", "Connection": "close"}
            async with httpx.AsyncClient(timeout=30, headers=headers) as client:
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

async def chat_with_alfred(
    user_message: str, 
    vacancies_context: str = "", 
    wants_voice: bool = False,
    history: Optional[List[Dict[str, str]]] = None
) -> str:
    """
    Foydalanuvchi botga yozganda yoki gapirganda Alfred sifatida professional o'zbek tilida javob berish.
    Suhbat tarixi (history) va bazadagi real vakansiyalar (vacancies_context) asosida ishlaydi.
    Yolg'on vakansiya to'qish qat'iyan taqiqlangan!
    """
    system_prompt = f"""
Sen "Alfred" - Behruzbek Bohodirovning shaxsiy professional IT-rekruteri va AI karyera maslahatchisisan.
Nomzod haqida: Behruzbek Bohodirov, Python Backend va AI Developer (Python, Django, FastAPI, PostgreSQL, Docker, Redis, Celery, AI Agents).
Darajasi va tajribasi: Tajribasiz (No experience / Без опыта), Stajyor/Intern, Junior hamda Frilans loyihalar. Foydalanuvchi "tajribasiz" yoki "stajyorlik" vakansiyalarini so'rasa, aynan shu yo'nalishdagi takliflarni birinchi navbatda ko'rsat.
GitHub: https://github.com/BEKUZ4A

MULOQOT VA FORMATLASH QOIDALARI:
1. Til: Aniq, tartibli, professional va samimiy o'zbek tili.
2. QAT'IY TAQIQ: "Behruzbekim", "jonim", "og'ayni", "qalaydigan" kabi bachkana, erkalovchi yoki buzilgan so'zlarni UMUMAN ISHLATMA! Nomzodga faqat "Behruzbek" yoki "Siz" deb murojaat qil.
3. HAR SAFAR QAYTA SALOMLASHMA: Agar suhbat allaqachon ketayotgan bo'lsa (tarixda avvalgi xabarlar mavjud bo'lsa), qaytadan "Assalomu alaykum", "Salom" deb boshlama! To'g'ridan-to'g'ri foydalanuvchi savoliga lo'nda va professional javob ber.
4. Raqamlarni oddiy yoz: 400$, 800$, 1 yil, 2026 kabi. Raqamlarni so'z bilan yozishga urinma.
5. Markdown formatlashdan (qalin matn **nomi**, ro'yxatlar, ssilka havolalari) chiroyli va qulay foydalan.

YOLG'ON VAKANSIYA TO'QISHNI QAT'IY TAQIQLASH (ANTI-HALLUCINATION):
- HECH QACHON o'zingdan vakansiya, kompaniya, maosh yoki buyurtma to'qib chiqarma!
- Faqat va faqat quyidagi BAZADAGI REAL VAKANSIYALAR bo'limida berilgan haqiqiy ma'lumotlar haqida gapir.
- Agar foydalanuvchi vakansiyalar haqida so'rasa, lekin berilgan kontekstda vakansiyalar bo'lmasa yoki bo'sh bo'lsa:
  Ochiqchasiga: "Hozircha bazada yangi mos vakansiya topilmadi, /scan bosib yangi e'lonlarni qidirib ko'ring." deb ayt!
- Agar real vakansiya haqida gapirayotgan bo'lsang, xabar ichida ALBATTA:
  1. Kompaniya yoki Kanal nomi
  2. Lavozim va asosiy talablar
  3. Maosh / Byudjet
  4. Haqiqiy manba havolasi (URL: https://...)
  aniq ko'rsatilsin!

SUHBAT TARIXI VA KONTEKSTNI TUSHUNISH:
- Foydalanuvchining oldingi xabarlarini va o'zingning oldingi javoblaringni diqqat bilan inobatga ol.
- Agar foydalanuvchi "qaysi", "ssilkasini ber", "batafsil ayt", "shunga rezyume yoz" deb qisqa yozsa:
  Darhol oldingi xabarda qaysi vakansiya haqida gap borganini aniqla va o'sha vakansiyaning to'liq ssilkasi va ma'lumotlarini taqdim et!
"""

    if vacancies_context:
        system_prompt += f"""
BAZADAGI REAL VAKANSIYALAR RO'YXATI (HAQIQIY MA'LUMOTLAR):
{vacancies_context}

KO'RSATMA: Foydalanuvchiga yuqoridagi real vakansiyalardan eng mosini (kompaniya, lavozim, maosh, talablar va uning haqiqiy havolasini) ko'rsatib javob ber.
"""
    else:
        system_prompt += """
BAZADAGI REAL VAKANSIYALAR: Hozircha bazada yangi vakansiya mavjud emas. Agar ish so'ralsa, /scan buyrug'ini bosishni tavsiya qil.
"""

    answer = await call_gemini_api(user_message, system_prompt, history=history)
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
            headers = {"User-Agent": "AlfredJobAgent/1.0", "Connection": "close"}
            async with httpx.AsyncClient(timeout=30, headers=headers) as client:
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

async def prepare_conversational_voice_text(raw_or_summary: str) -> str:
    """
    Ovozli xabar uchun matnni xuddi do'stiga yangilik aytayotgandek silliq,
    samimiy 3-4 gapdan iborat toza so'zlashuv nutqiga aylantiradi.
    Yulduzcha, chiziqcha, havola yoki emoji nomlarini o'z ichiga olmaydi.
    """
    prompt = f"""
    Sen "Alfred" - foydalanuvchi Behruzbekning shaxsiy AI assistenti va yaqin do'stisan.
    Quyidagi ish yoki xabar ma'lumotini diqqat bilan o'rganib, Telegram ovozli xabari (audio) uchun o'zbek tilida juda samimiy, do'stona, silliq 3-4 gapdan iborat jonli so'zlashuv nutqini tayyorla:

    MA'LUMOT:
    {raw_or_summary}

    QAT'IY QOIDALAR:
    1. Nutq albatta: "Assalomu alaykum Behruzbek! ..." deb boshlansin.
    2. Xuddi yaqin do'stingizga quvonchli yangilik aytayotgandek samimiy, jonli va professional suhandon ohangida bo'lsin.
    3. Maksimal 3-4 gapdan oshmasin (15-20 soniyada erkin o'qiladigan bo'lsin).
    4. HECH QACHON "yulduzcha", "chiziqcha", "https", "ssilka", "link", "havola", "emoji", belgilar (*, -, #, /) yoki emojilar nomini ishlatma!
    5. Faqat toza, ovoz chiqarib o'qiladigan matnni qaytar (boshqa hech qanday izoh, sarlavha yoki qo'shimcha so'zlarsiz).
    """
    script = await call_gemini_api(prompt)
    if script:
        return script.strip()
    return (
        "Assalomu alaykum Behruzbek! Siz uchun ajoyib yangi taklif topildi. "
        "Barcha batafsil ma'lumotlarni Telegram chatimizda qoldirdim, bemalol ko'rib chiqishingiz mumkin."
    )

async def generate_tailored_resume_and_pitch(post_text: str, user_instruction: str = "") -> str:
    """
    Aniq bitta vakansiya/e'lon matni asosida Behruzbek Bohodirovning
    haqiqiy ma'lumotlari bo'yicha maxsus moslashtirilgan Rezyume,
    Telegram Cover Letter va HR kontaktini tayyorlab berish.
    """
    prompt = f"""
    Sen "Alfred" - yuqori darajadagi IT karyera maslahatchisi va shaxsiy AI assistentsan.
    Foydalanuvchi Behruzbek Bohodirov quyidagi ANIQ ISH E'LONI bo'yicha murojaat qilmoqchi:

    ISH E'LONI TO'LIQ MATNI:
    {post_text}

    QO'SHIMCHA KO'RSATMA: {user_instruction}

    NOMZODNING HAQIQIY MA'LUMOTLARI:
    - Ism-familiya: Behruzbek Bohodirov
    - GitHub profil: https://github.com/BEKUZ4A
    - Asosiy texnologiyalar (Stek): Python, Django, DRF (Django REST Framework), FastAPI, PostgreSQL, Docker, Redis, Celery, AI Agents (Gemini API, Claude, Cursor), Frontend asoslari (HTML, CSS, JavaScript, React.js)
    - Asosiy Loyihalar:
      1. Risk Radar: AI biznes-analitika platformasi (DRF, Docker, AWS, PostgreSQL, ma'lumotlar tahlili).
      2. Uzum AI Shop: Gemini API bilan integratsiyalashgan aqlli e-commerce platformasi.
      3. Alfred AI Agent: 24/7 ishlaydigan aqlli karyera va vakansiyalar monitoring agenti (Python, FastAPI, Gemini API, ElevenLabs TTS).
    - Sertifikat va Yutuqlar:
      * Codify Tech Backend Certificate (CID-2026-000026)
      * Turon University AI Engineering Hackathon finalchisi

    VAZIFA:
    Ushbu e'londagi har bir talabni chuqur tahlil qil va Behruzbekning tajribasi asosida quyidagi 3 qismdan iborat tayyor to'plamni tuzib ber:

    1. 👤 HR / ISH BERUVCHI KONTAKTI VA MASLAHAT:
       - E'londan aniqlangan kontaktlar (Telegram username, telefon, mas'ul shaxs).
       - Qachon va qanday murojaat qilish bo'yicha qisqa amaliy tavsiya.

    2. 📨 TELEGRAM COVER LETTER (HR ga yuborish uchun tayyor murojaat xati):
       - To'g'ridan-to'g'ri nusxalab Telegramdan yuborishga qulay, samimiy va professional.
       - Aynan e'londa so'ralgan texnologiyalar va vazifalarga (masalan backend dasturlash, mentorlik yoki botlar) urg'u berilgan.
       - GitHub havolasi (https://github.com/BEKUZ4A) kiritilgan.

    3. 📄 VAKANSIYAGA MOSLASHTIRILGAN REZYUME (CV):
       - Vakansiya talablariga mos unvon va qisqa xulosa.
       - Texnologik stek (e'longa moslari oldinda).
       - Tegishli loyihalar va ularning amaliy natijalari.
       - Sertifikat va yutuqlar (Codify Tech Backend Certificate CID-2026-000026, Turon Hackathon).
       - Aloqa va GitHub.

    Telegramda qulay o'qilishi uchun chiroyli sarlavhalar va formatlashdan foydalan.
    """
    res = await call_gemini_api(prompt)
    if res:
        return res.strip()
    return (
        "❌ <i>Kechirasiz, sun'iy intellekt orqali rezyume va murojaat xatini shakllantirishda xatolik yuz berdi. "
        "Iltimos, qayta urinib ko'ring yoki /status buyrug'ini tekshiring.</i>"
    )

async def generate_interview_prep(vacancy_text: str) -> str:
    """
    Suhbatga 5 daqiqada tayyorlash ('🎯 Suhbat savollari' tugmasi):
    Vakansiyada so'ralgan texnologiyalar bo'yicha 7 ta eng muhim texnik savol
    va ularga suhbatda qanday qilib sodda, ishonchli javob berish kerakligini generatsiya qilish.
    """
    prompt = f"""
    Sen tajribali Senior Python Dasturchi va Texnik Suhbat oluvchisisan (Tech Lead).
    Nomzod Behruzbek Bohodirov quyidagi vakansiya bo'yicha texnik suhbatga tayyorlanmoqda.

    VAKANSIYA MATNI:
    {vacancy_text}

    VAZIFA:
    Aynan shu vakansiyada so'raladigan 7 ta eng muhim va dolzarb texnik savolni va ularga qanday qilib o'zbek tilida sodda, aniq, professional javob berish kerakligini tuzib ber.

    Har bir savol uchun:
    - ❓ **Savol N**: (Masalan: Django ORM da N+1 muammosi qanday hal qilinadi?)
    - 💡 **Soddalashtirilgan javob**: (Intervyuda qanday aytish kerak, kalit atamalar bilan)
    - 🎯 **Maslahat**: (Intervyuer aynan nimani eshitmoqchi)

    Formatlash: Telegramda qulay o'qilishi uchun chiroyli emojilar va aniq paragraflardan foydalan.
    """
    res = await call_gemini_api(prompt)
    if res:
        return res.strip()
    return "❌ <i>Suhbat savollarini generatsiya qilishda xatolik yuz berdi. Qayta urinib ko'ring.</i>"

async def analyze_hr_message(message_text: str, hr_username: str, company: str = "") -> Dict[str, Any]:
    """
    HR yozgan xabarni tahlil qilish:
    - Javob berish shartmi (intervyu, savol, test topshiriq)?
    - Xabarning qisqa mazmuni
    - HR'ga yuborish uchun professional javob varianti
    """
    prompt = f"""
    Sen Alfred - Behruzbek Bohodirovning shaxsiy rekrutment assistentisan.
    Kuzatuvdagi HR (@{hr_username}, {company}) quyidagi xabarni yozdi:

    HR XABARI:
    "{message_text}"

    NOMZOD HAQIDA:
    - Behruzbek Bohodirov (Python Backend, Django, FastAPI, AI Agents)
    - GitHub: https://github.com/BEKUZ4A
    - Telegram: @BBCnew_2026

    Quyidagi JSON formatda javob qaytar:
    {{
      "needs_reply": true/false,
      "summary": "O'zbek tilida 1 gapda xabarning mohiyati (masalan: Ertaga soat 15:00 da texnik intervyuga taklif qildi)",
      "suggested_reply": "HR ga yuborish uchun juda professional, samimiy va to'g'ri o'zbekcha yoki ruscha (HR qaysi tilda yozgan bo'lsa shu tilda) tayyor javob matni"
    }}
    Faqat toza JSON qaytar.
    """
    res = await call_gemini_api(prompt)
    if res:
        try:
            # Markdown json tozalash
            clean = res.replace("```json", "").replace("```", "").strip()
            return json.loads(clean)
        except Exception:
            pass
    return {
        "needs_reply": True,
        "summary": "HR xabar yubordi: " + message_text[:100],
        "suggested_reply": "Assalomu alaykum! Xabaringiz uchun rahmat. Taklifingizni mamnuniyat bilan qabul qilaman."
    }

async def generate_hr_followup(hr_username: str, company: str = "") -> str:
    """
    3 kundan keyin HR ga yuboriladigan xushmuomala eslatma (follow-up) matni
    """
    prompt = f"""
    Behruzbek Bohodirov nomidan HR @{hr_username} ga ({company} kompaniyasi bo'yicha) 
    yuborish uchun juda xushmuomala, professional va qisqa 3 kunlik eslatma (follow-up) xabari yozib ber.
    Xatda: Ilgari yuborilgan rezyumeni ko'rib chiqishga vaqtlari bo'ldimi-yo'qligi, savollari bo'lsa javob berishga tayyorligi bildirilsin.
    GitHub profili: https://github.com/BEKUZ4A
    Faqat yuborishga tayyor matnni chiqar.
    """
    res = await call_gemini_api(prompt)
    if res:
        return res.strip()
    return f"Assalomu alaykum! @{hr_username}, avvalgi xabarim yuzasidan bezovta qilayotgan edim. Rezyumemni ko'rib chiqishga imkon bo'ldimi? Savollaringiz bo'lsa, suhbatlashishdan mamnun bo'lardim. Hurmat bilan, Behruzbek Bohodirov (https://github.com/BEKUZ4A)"




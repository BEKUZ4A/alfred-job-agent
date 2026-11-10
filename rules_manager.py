import json
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger("Alfred.RulesManager")

DEFAULT_RULES = {
    "candidate": {
        "name": "Behruzbek Bohodirov",
        "role": "Python Backend & AI Developer",
        "status": "Hozirda bo'shman va to'liq stavkada yangi ishga kirishga tayyorman",
        "experience_target": "Tajribasiz (No experience / Без опыта), Intern, Stajyor, Junior, Freelance",
        "github": "https://github.com/BEKUZ4A",
        "telegram": "@BBCnew_2026",
        "phone": "+998 99 987 51 86",
        "tech_stack": [
            "Python 3.11+", "Django", "Django REST Framework", "FastAPI", 
            "PostgreSQL", "Redis", "Celery", "Docker", "Docker Compose", 
            "Telethon MTProto", "Gemini API", "AI Agents"
        ],
        "projects": [
            "Risk Radar AI — AWS EC2 da joylashtirilgan AI Biznes-analitika va xavf tahlili platformasi (DRF, PostgreSQL, Celery, Redis, Docker)",
            "Uzum AI Shop — Intellektual Elektron Savdo Yordamchisi (FastAPI, Gemini API, PostgreSQL)",
            "Alfred AI Agent — Avtonom Ish Qidirish va Telegram Userbot Tizimi (Telethon MTProto, ElevenLabs, FastAPI)",
            "Earth Weather — Global Ob-havo Analitika Xizmati (AsyncIO, API Caching, Matplotlib)",
            "Konstitutsiya AI — Qonunchilik bo'yicha NLP Vector Qidiruv Tizimi"
        ],
        "certificates": [
            "Pro Programming Backend Certificate (Verified ID: CID-2026-000026, tekshirish: certid.uz)",
            "Turon University AI Engineering Hackathon 2-bosqich finalisti"
        ]
    },
    "search_rules": {
        "blocked_keywords": [
            "senior", "5+ years", "5+ yil", "5 лет", "php", "1c", "1с", 
            "java", "c#", ".net", "flutter", "ios", "swift", 
            "marketing", "tarmoqli", "oriflame", "faberlic"
        ],
        "target_experience": [
            "tajribasiz", "tajribasi yo'q", "tajriba talab qilinmaydi", 
            "без опыта", "bez opita", "no experience", "no-experience",
            "intern", "stajyor", "amaliyot", "junior", "0 yil", "boshlang'ich"
        ],
        "min_match_score": 65
    },
    "hr_communication_rules": {
        "never_push_resume_unless_asked": True,
        "never_lie_about_currently_working": True,
        "work_status_answer": "Yo'q, hozirda bo'shman va bor e'tiborimni ishga qaratib, to'liq stavkada ishlashga tayyorman",
        "match_language": True,
        "manual_reply_only": True
    },
    "custom_rules": [
        "HR suhbat yoki uchrashuv vaqtini belgilaganda darhol 2 daqiqa oldin eslatma taymerini o'rnatish",
        "Suhbatga 2 daqiqa qolganda 2-akkauntdan Telegram audio qo'ng'iroq va ElevenLabs ovozli xabarini yuborish",
        "Kompaniya nomi noma'lum bo'lsa 'Kompaniya: Kompaniya' deb yozmaslik, HR ning ismi yoki bio'sidan foydalanish"
    ]
}

# Xotiradagi jonli qoidalar
_current_rules: Dict[str, Any] = json.loads(json.dumps(DEFAULT_RULES))

def get_rules() -> Dict[str, Any]:
    """Jonli qoidalarni olish"""
    global _current_rules
    return _current_rules

def get_rules_prompt() -> str:
    """Gemini system instruction uchun qoidalarni matn ko'rinishida chiqarish"""
    rules = get_rules()
    cand = rules.get("candidate", {})
    s_rules = rules.get("search_rules", {})
    hr_rules = rules.get("hr_communication_rules", {})
    c_rules = rules.get("custom_rules", [])

    prompt_lines = [
        "=== DINAMIK QOIDALAR (#ALFRED_RULES) ===",
        f"1. NOMZOD: {cand.get('name')} ({cand.get('role')})",
        f"   - Holati: {cand.get('status')}",
        f"   - Asosiy ko'nikmalar: {', '.join(cand.get('tech_stack', []))}",
        f"   - GitHub: {cand.get('github')}, Telegram: {cand.get('telegram')}",
        f"   - Loyihalar: {'; '.join(cand.get('projects', []))}",
        f"   - Sertifikatlar: {'; '.join(cand.get('certificates', []))}",
        "",
        "2. HR BILAN MULOQOT QOIDALARI:",
        f"   - Suhbat o'rtasida HR so'ramasa, HECH QACHON rezyume, GitHub havolasi yoki texnologiyalar ro'yxatini tiqishtirma!",
        f"   - HECH QACHON 'Hozir ishlayapman' deb yolg'on gapirma! Savol berilsa: \"{hr_rules.get('work_status_answer')}\" deb javob ber.",
        f"   - Til: HR ruscha yozsa rus tilida, o'zbekcha yozsa o'zbek tilida tabiiy so'zlashuv ohangida javob yoz.",
        f"   - Hech qachon HR'ga avtomatik xabar yuborma! Faqat Qoralama (Draft) qilib saqla va foydalanuvchiga 2 ta tugma ([📤 Javob berish] va [✍️ O'zim javob berdim]) chiqar.",
        "",
        "3. SARALASH VA BLOKLASH QOIDALARI:",
        f"   - Bloklanadigan kalit so'zlar: {', '.join(s_rules.get('blocked_keywords', []))}",
        f"   - Qidiriladigan tajriba: {', '.join(s_rules.get('target_experience', []))}",
        f"   - Minimal moslik: {s_rules.get('min_match_score', 65)}%",
        "",
        "4. FOYDALANUVCHI QO'SHGAN MAXSUS QOIDALAR:"
    ]
    for idx, cr in enumerate(c_rules, 1):
        prompt_lines.append(f"   {idx}. {cr}")

    prompt_lines.append("=========================================")
    return "\n".join(prompt_lines)

def add_custom_rule(rule_text: str) -> bool:
    """Foydalanuvchi chatda aytgan yangi qoidani qo'shish"""
    global _current_rules
    clean_rule = rule_text.strip()
    if not clean_rule:
        return False
    custom_list = _current_rules.setdefault("custom_rules", [])
    if clean_rule not in custom_list:
        custom_list.append(clean_rule)
        logger.info(f"✅ Yangi dinamik qoida qo'shildi: {clean_rule}")
        # Telegram Saved Messages ga sinxronizatsiya qilish
        try:
            from backup_manager import schedule_rules_backup
            schedule_rules_backup()
        except Exception as e:
            logger.debug(f"Rules backup chaqiruvida ogohlantirish: {e}")
        return True
    return False

def remove_custom_rule(rule_text: str) -> bool:
    """Foydalanuvchi ko'rsatgan qoidani o'chirish"""
    global _current_rules
    clean_rule = rule_text.strip().lower()
    if not clean_rule:
        return False
    custom_list = _current_rules.setdefault("custom_rules", [])
    initial_len = len(custom_list)
    _current_rules["custom_rules"] = [r for r in custom_list if clean_rule not in r.lower()]
    if len(_current_rules["custom_rules"]) < initial_len:
        logger.info(f"🗑 Dinamik qoida o'chirildi: {rule_text}")
        try:
            from backup_manager import schedule_rules_backup
            schedule_rules_backup()
        except Exception as e:
            logger.debug(f"Rules backup chaqiruvida ogohlantirish: {e}")
        return True
    return False

def export_rules_json() -> str:
    """Qoidalarni JSON formatida eksport qilish"""
    global _current_rules
    return json.dumps(_current_rules, ensure_ascii=False, indent=2)

def import_rules_json(json_str: str) -> bool:
    """Qoidalarni JSON dan import qilish"""
    global _current_rules
    try:
        data = json.loads(json_str)
        if isinstance(data, dict):
            _current_rules = data
            logger.info("✅ Dinamik qoidalar muvaffaqiyatli yangilandi (import_rules_json).")
            return True
    except Exception as e:
        logger.error(f"import_rules_json xatosi: {e}")
    return False

def reset_to_defaults():
    """Qoidalarni boshlang'ich holatga qaytarish"""
    global _current_rules
    _current_rules = json.loads(json.dumps(DEFAULT_RULES))

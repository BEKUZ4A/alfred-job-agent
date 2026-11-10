import httpx
import json
import logging
import re
import asyncio
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple

from config import GEMINI_API_KEY, CANDIDATE_PROFILE, TASHKENT_TZ
from ai_analyzer import (
    AVAILABLE_MODELS, 
    set_last_error, 
    generate_tailored_resume_and_pitch
)
from database import (
    get_all_tracked_hrs,
    get_tracked_hr,
    add_tracked_hr,
    delete_tracked_hr,
    update_hr_interview_time,
    update_hr_pending_reply,
    get_today_vacancies,
    get_recent_vacancies,
    compute_content_hash,
    mark_vacancy_seen
)
from rules_manager import (
    get_rules,
    get_rules_prompt,
    add_custom_rule,
    remove_custom_rule
)
from backup_manager import (
    schedule_db_backup,
    schedule_rules_backup
)
from notifier import (
    send_telegram_message,
    send_telegram_document,
    send_telegram_voice,
    markdown_to_telegram_html
)
from pdf_generator import generate_pdf_resume
from userbot import (
    get_userbot_client,
    get_hr_chat_history,
    save_hr_draft_message,
    make_telegram_audio_call,
    parse_meeting_time,
    send_message_as_user,
    sync_hr_last_seen_silently
)
from voice import generate_voice_message

logger = logging.getLogger("Alfred.GeminiAgent")

# ----------------- 7 TA ASOSIY REACT TOOLS -----------------

async def tool_1_inspect_telegram_entity(username_or_link: str, limit: int = 15) -> str:
    """
    @username yoki t.me havolasini tekshirish.
    Inson/HR bo'lsa shaxsiy lichkadagi oxirgi xabarlarni o'qiydi.
    Kanal yoki guruh bo'lsa kanal postlarini tekshiradi.
    """
    clean_target = (
        username_or_link.strip()
        .lstrip("@")
        .replace("https://t.me/s/", "")
        .replace("https://t.me/", "")
        .replace("t.me/s/", "")
        .replace("t.me/", "")
        .strip("/")
    )

    logger.info(f"Tool 1: inspecting entity '{clean_target}' (limit={limit})")
    client = get_userbot_client()

    # 1. Telethon orqali Entity turini aniqlash
    if client and client.is_connected():
        try:
            from telethon.tl.types import User, Channel, Chat
            entity = await client.get_entity(clean_target)
            if isinstance(entity, User):
                # Bu shaxsiy inson / HR! Oxirgi yozishmalarni o'qiymiz
                history = await get_hr_chat_history(clean_target, limit=limit)
                if not history:
                    return f"HR @{clean_target} lichkasida xabarlar topilmadi (suhbat bo'sh)."
                
                dialogue_lines = []
                for h in history:
                    role_tag = "OUT (Men)" if h.get("is_outgoing") else f"IN (HR @{clean_target})"
                    t_str = h.get("date_str", "")
                    dialogue_lines.append(f"[{role_tag} {t_str}]: {h.get('text', '')}")

                res = f"HR @{clean_target} bilan oxirgi {len(history)} ta yozishma tarixi:\n" + "\n".join(dialogue_lines)
                return res
            elif isinstance(entity, (Channel, Chat)):
                # Bu kanal yoki guruh! Kanal skanerini ishlatamiz
                from scraper import scrape_telegram_channel
                vacs = await scrape_telegram_channel(clean_target, today_only=False)
                if not vacs:
                    return f"Kanal @{clean_target} dan vakansiyalar topilmadi yoki kanal yopiq."
                summary_posts = [f"• {v.get('title')} ({v.get('url')})" for v in vacs[:5]]
                return f"Kanal @{clean_target} dan olingan so'nggi postlar:\n" + "\n".join(summary_posts)
        except Exception as e:
            logger.warning(f"Telethon get_entity xatoligi ({clean_target}): {e}")

    # Fallback: Agar bazada kuzatuvdagi HR bo'lsa
    hr = get_tracked_hr(clean_target)
    if hr:
        history = await get_hr_chat_history(clean_target, limit=limit)
        if history:
            dialogue_lines = []
            for h in history:
                role_tag = "OUT (Men)" if h.get("is_outgoing") else f"IN (HR @{clean_target})"
                dialogue_lines.append(f"[{role_tag}]: {h.get('text', '')}")
            return f"HR @{clean_target} yozishmalari:\n" + "\n".join(dialogue_lines)
        return f"HR @{clean_target} kuzatuvda. Oxirgi xabar: '{hr.get('last_message', '')}', Holati: {hr.get('status')}"

    # Kanal skaneri zaxirasi
    try:
        from scraper import scrape_telegram_channel
        vacs = await scrape_telegram_channel(clean_target, today_only=False)
        if vacs:
            summary_posts = [f"• {v.get('title')} ({v.get('url')})" for v in vacs[:5]]
            return f"Telegram @{clean_target} ochiq manbasi postlari:\n" + "\n".join(summary_posts)
    except Exception:
        pass

    return f"@{clean_target} bo'yicha ma'lumot aniqlanmadi (HR lichkasi yoki ochiq kanal topilmadi)."


async def tool_2_propose_manual_hr_reply(
    hr_username: str, 
    hr_last_msg: str, 
    suggested_reply: str, 
    detected_interview_time: Optional[str] = None
) -> str:
    """
    HR'ga HECH QACHON avtomatik xabar yubormaydi!
    1. Javobni Telegram shaxsiy lichkasiga 'Qoralama' (Draft / Черновик) qilib saqlaydi.
    2. Botda FAQAT 1 DONA xabar va FAQAT 2 TA TUGMA chiqaradi:
       [📤 Javob berish] va [✍️ O'zim javob berdim].
    """
    clean_hr = hr_username.strip().lstrip("@").lower()
    logger.info(f"Tool 2: propose reply for HR @{clean_hr}")

    # 1. Draft qilib saqlash
    await save_hr_draft_message(clean_hr, suggested_reply)
    update_hr_pending_reply(clean_hr, suggested_reply)

    hr = get_tracked_hr(clean_hr)
    company = hr.get("company", "") if hr else ""

    target_dt = None
    if detected_interview_time:
        try:
            target_dt = datetime.fromisoformat(detected_interview_time)
        except Exception:
            target_dt, _ = parse_meeting_time(detected_interview_time)
        if target_dt:
            update_hr_interview_time(clean_hr, target_dt.isoformat(), reminded_2min=False)
            schedule_db_backup()

    time_disp = target_dt.strftime("%H:%M") if target_dt else ""
    comp_line = f"🏢 <b>Kompaniya:</b> {company}\n" if (company and company.lower() != "kompaniya") else ""

    inline_keyboard = [
        [
            {"text": "📤 Javob berish", "callback_data": f"send_hr_reply_{clean_hr}"},
            {"text": "✍️ O'zim javob berdim", "callback_data": f"manual_replied_{clean_hr}"}
        ]
    ]

    msg_text = (
        f"📩 <b>HR (@{clean_hr}) bilan muloqot taklifi:</b>\n\n"
        f"{comp_line}"
        f"💬 <b>HR xabari:</b>\n<i>\"{hr_last_msg}\"</i>\n\n"
        f"🤖 <b>Tavsiya etilgan javob:</b>\n<i>\"{suggested_reply}\"</i>\n\n"
    )
    if time_disp:
        msg_text += f"⏰ <b>Aniqlangan suhbat vaqti:</b> <b>{time_disp}</b> (2 daqiqa oldin eslatma qo'yildi)\n\n"

    msg_text += "📝 <i>(Javob shaxsiy Telegramingizdagi HR lichkasiga Qoralama / Draft qilib yozib qo'yildi)</i>"

    await send_telegram_message(msg_text, reply_markup={"inline_keyboard": inline_keyboard})
    return f"HR @{clean_hr} uchun qoralama saqlandi va foydalanuvchiga 2 tugmali tasdiq xabari yuborildi."


async def tool_3_schedule_interview_timer(
    hr_username: str, 
    interview_iso_time: str, 
    company: str = ""
) -> str:
    """
    Suhbat vaqtini rejalashtirish (2 daqiqa oldingi eslatma va gudok uchun).
    """
    clean_hr = hr_username.strip().lstrip("@").lower()
    logger.info(f"Tool 3: schedule timer for @{clean_hr} at {interview_iso_time}")

    target_dt = None
    try:
        target_dt = datetime.fromisoformat(interview_iso_time)
    except Exception:
        target_dt, _ = parse_meeting_time(interview_iso_time)

    if not target_dt:
        return f"Vaqt formatini aniqlab bo'lmadi: {interview_iso_time}"

    iso_str = target_dt.isoformat()
    update_hr_interview_time(clean_hr, iso_str, reminded_2min=False)
    if company:
        add_tracked_hr(clean_hr, company=company)
    schedule_db_backup()

    time_disp = target_dt.strftime("%H:%M")
    await send_telegram_message(
        f"✅ <b>HR (@{clean_hr}) bilan suhbat rejalashtirildi!</b>\n"
        f"⏰ <b>Vaqti:</b> {time_disp}\n"
        f"🔔 <i>Suhbat boshlanishiga 2 daqiqa qolganda qo'ng'iroq va ovozli eslatma yuboraman.</i>"
    )
    return f"Suhbat {time_disp} ga rejalashtirildi. 2 daqiqa oldin qo'ng'iroq va eslatma yoqiladi."


async def tool_4_make_urgent_call_and_voice(hr_username: str, voice_text: str) -> str:
    """
    2-akkauntdan foydalanuvchiga shoshilinch Telegram qo'ng'iroq va ElevenLabs ovozli xabar berish.
    """
    clean_hr = hr_username.strip().lstrip("@").lower()
    logger.info(f"Tool 4: urgent call and voice for @{clean_hr}")

    # 1. 2-akkauntdan qo'ng'iroq
    call_res = await make_telegram_audio_call(voice_text or f"HR @{clean_hr} bo'yicha shoshilinch xabar!")

    # 2. ElevenLabs ovoz
    if voice_text:
        try:
            v_file = await generate_voice_message(voice_text, prepare_script=False)
            if v_file:
                await send_telegram_voice(v_file, caption=f"🎙 Shoshilinch xabarnoma: @{clean_hr}")
        except Exception as e:
            logger.warning(f"Tool 4 voice xatosi: {e}")

    return f"Qo'ng'iroq holati: {call_res}. Ovozli eslatma yuborildi."


async def tool_5_generate_tailored_cv(company: str, role: str, job_text: str, lang: str = "uz") -> str:
    """
    Vakansiya talablariga mos Cover Letter va 1-sahifali PDF Rezyume generatsiya qilish.
    """
    logger.info(f"Tool 5: generating tailored CV for {company} - {role}")
    await send_telegram_message(
        f"⏳ <b>{role}</b> ({company}) uchun maxsus Cover Letter va PDF Rezyume tayyorlanmoqda..."
    )

    # 1. Matnli Cover Letter va Pitch
    docs = await generate_tailored_resume_and_pitch(job_text, user_instruction=f"{company} {role}")
    await send_telegram_message(docs)

    # 2. PDF Rezyume
    try:
        pdf_path = generate_pdf_resume(
            tailored_summary=f"Python Backend & AI Developer. {role} ({company}) vakansiyasi talablariga to'liq moslashtirilgan.",
            target_job_title=role,
            target_company=company
        )
        await send_telegram_document(
            pdf_path,
            caption=f"📄 <b>Behruzbek_Bohodirov_CV.pdf</b>\n<i>{role} ({company}) uchun maxsus tayyorlandi.</i>"
        )
    except Exception as e_pdf:
        logger.error(f"PDF tayyorlashda xatolik: {e_pdf}")

    return f"{company} ({role}) uchun maxsus CV va PDF tayyorlandi va yuborildi."


async def tool_6_manage_hr_and_rules(action: str, target: str, value: str = "") -> str:
    """
    HR ro'yxatini va #ALFRED_RULES dinamik qoidalarini boshqarish.
    action: add_hr | remove_hr | list_hr | add_rule | remove_rule | get_rules
    """
    logger.info(f"Tool 6: action='{action}', target='{target}', value='{value}'")
    act = action.lower().strip()

    if act == "add_hr":
        clean_target = target.strip().lstrip("@").lower()
        add_tracked_hr(clean_target, company=value)
        asyncio.create_task(sync_hr_last_seen_silently(clean_target))
        schedule_db_backup()
        return f"HR @{clean_target} ({value or 'Kompaniya'}) kuzatuvga olindi va zaxiraga saqlandi."

    elif act == "remove_hr":
        clean_target = target.strip().lstrip("@").lower()
        delete_tracked_hr(clean_target)
        schedule_db_backup()
        return f"HR @{clean_target} kuzatuvdan o'chirildi."

    elif act == "list_hr":
        tracked = get_all_tracked_hrs()
        if not tracked:
            return "Hozircha kuzatuvda hech qanday HR yo'q."
        lines = []
        for h in tracked:
            lines.append(f"• @{h.get('username')} ({h.get('company') or 'Kompaniya'}) - Status: {h.get('status')}, Suhbat: {h.get('interview_time') or 'belgilanmagan'}")
        return "Kuzatuvdagi HR lar:\n" + "\n".join(lines)

    elif act in ["add_rule", "update_rule"]:
        rule_text = target if target else value
        add_custom_rule(rule_text)
        schedule_rules_backup()
        return f"Yangi qoida muvaffaqiyatli saqlandi: '{rule_text}'. Endi doimiy qo'llanadi."

    elif act == "remove_rule":
        remove_custom_rule(target)
        schedule_rules_backup()
        return f"Qoida o'chirildi: '{target}'."

    elif act == "get_rules":
        rules = get_rules()
        return "Joriy dinamik qoidalar (#ALFRED_RULES):\n" + json.dumps(rules, ensure_ascii=False, indent=2)

    return f"Noma'lum amal: {action}"


async def tool_7_scan_vacancies(query: str = "") -> str:
    """
    Vakansiyalarni skanerlash yoki bazadagi eng so'nggi yangi vakansiyalarni tahlil qilish.
    """
    logger.info(f"Tool 7: scan vacancies (query='{query}')")
    recent = get_today_vacancies(limit=5)
    if not recent:
        recent = get_recent_vacancies(limit=5)

    if not recent:
        return "Bugungi yangi vakansiyalar bazada topilmadi. Skanerlash ishga tushirilishi mumkin."

    lines = []
    for r in recent:
        lines.append(f"• {r.get('title')} ({r.get('company')}) - {r.get('match_score')}% mos | {r.get('url')}")
    return "So'nggi mos vakansiyalar:\n" + "\n".join(lines)


# ----------------- GEMINI TOOL SCHEMAS -----------------

GEMINI_TOOLS_DECLARATION = [
    {
        "name": "tool_1_inspect_telegram_entity",
        "description": "Telegram username yoki havola bo'yicha tekshirish. Inson/HR bo'lsa oxirgi 15 ta shaxsiy yozishmalarni o'qiydi. Kanal bo'lsa so'nggi postlarni qaytaradi.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "username_or_link": {"type": "STRING", "description": "@username yoki t.me ssilkasi"},
                "limit": {"type": "INTEGER", "description": "O'qiladigan xabarlar soni (standart: 15)"}
            },
            "required": ["username_or_link"]
        }
    },
    {
        "name": "tool_2_propose_manual_hr_reply",
        "description": "HR uchun taklif javobini Telegram lichkasiga Qoralama (Draft) qilib yozadi va foydalanuvchiga FAQAT 2 ta tugmali ([📤 Javob berish] va [✍️ O'zim javob berdim]) tasdiq yuboradi. HECH QACHON HR ga avtomatik yubormaydi!",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "hr_username": {"type": "STRING", "description": "HR username'i"},
                "hr_last_msg": {"type": "STRING", "description": "HR ning oxirgi xabari"},
                "suggested_reply": {"type": "STRING", "description": "HR ga tavsiya etiladigan muloyim va professional javob"},
                "detected_interview_time": {"type": "STRING", "description": "Kelishilgan suhbat vaqti (agar bo'lsa)"}
            },
            "required": ["hr_username", "hr_last_msg", "suggested_reply"]
        }
    },
    {
        "name": "tool_3_schedule_interview_timer",
        "description": "HR bilan kelishilgan suhbat vaqtiga 2 daqiqa oldin eslatma va qo'ng'iroq taymerini qo'yish.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "hr_username": {"type": "STRING", "description": "HR username'i"},
                "interview_iso_time": {"type": "STRING", "description": "Suhbat vaqti (ISO format yoki masalan 15:00)"},
                "company": {"type": "STRING", "description": "Kompaniya nomi (agar ma'lum bo'lsa)"}
            },
            "required": ["hr_username", "interview_iso_time"]
        }
    },
    {
        "name": "tool_4_make_urgent_call_and_voice",
        "description": "2-akkauntdan foydalanuvchiga shoshilinch Telegram qo'ng'iroq va ovozli eslatma jo'natish.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "hr_username": {"type": "STRING", "description": "Qaysi HR bo'yicha qo'ng'iroq qilinayotgani"},
                "voice_text": {"type": "STRING", "description": "Ovozli qilib aytiladigan eslatma matni"}
            },
            "required": ["hr_username", "voice_text"]
        }
    },
    {
        "name": "tool_5_generate_tailored_cv",
        "description": "Vakansiya talablariga mos Cover Letter va 1-sahifali professional PDF Rezyume generatsiya qilish.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "company": {"type": "STRING", "description": "Kompaniya nomi"},
                "role": {"type": "STRING", "description": "Lavozim nomi"},
                "job_text": {"type": "STRING", "description": "Vakansiya yoki postning to'liq matni"},
                "lang": {"type": "STRING", "description": "Til (standart: 'uz')"}
            },
            "required": ["company", "role", "job_text"]
        }
    },
    {
        "name": "tool_6_manage_hr_and_rules",
        "description": "HR kuzatuvini (add_hr, remove_hr, list_hr) yoki #ALFRED_RULES dinamik qoidalarini (add_rule, remove_rule, get_rules) boshqarish.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING", 
                    "description": "Amal: add_hr | remove_hr | list_hr | add_rule | remove_rule | get_rules"
                },
                "target": {"type": "STRING", "description": "HR username yoki yangi qoida matni"},
                "value": {"type": "STRING", "description": "Kompaniya nomi yoki qo'shimcha qiymat"}
            },
            "required": ["action", "target"]
        }
    },
    {
        "name": "tool_7_scan_vacancies",
        "description": "Yangi vakansiyalarni qidirish yoki bazadagi eng so'nggi vakansiyalar ro'yxatini olish.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {"type": "STRING", "description": "Qidiruv so'zi yoki kanal nomi"}
            }
        }
    }
]


# ----------------- REACT AGENT LOOP -----------------

async def execute_tool(tool_name: str, args: Dict[str, Any]) -> str:
    """Asbob nomiga qarab tegishli python funksiyasini chaqirish"""
    try:
        if tool_name == "tool_1_inspect_telegram_entity":
            return await tool_1_inspect_telegram_entity(
                username_or_link=args.get("username_or_link", ""),
                limit=int(args.get("limit", 15))
            )
        elif tool_name == "tool_2_propose_manual_hr_reply":
            return await tool_2_propose_manual_hr_reply(
                hr_username=args.get("hr_username", ""),
                hr_last_msg=args.get("hr_last_msg", ""),
                suggested_reply=args.get("suggested_reply", ""),
                detected_interview_time=args.get("detected_interview_time")
            )
        elif tool_name == "tool_3_schedule_interview_timer":
            return await tool_3_schedule_interview_timer(
                hr_username=args.get("hr_username", ""),
                interview_iso_time=args.get("interview_iso_time", ""),
                company=args.get("company", "")
            )
        elif tool_name == "tool_4_make_urgent_call_and_voice":
            return await tool_4_make_urgent_call_and_voice(
                hr_username=args.get("hr_username", ""),
                voice_text=args.get("voice_text", "")
            )
        elif tool_name == "tool_5_generate_tailored_cv":
            return await tool_5_generate_tailored_cv(
                company=args.get("company", ""),
                role=args.get("role", ""),
                job_text=args.get("job_text", ""),
                lang=args.get("lang", "uz")
            )
        elif tool_name == "tool_6_manage_hr_and_rules":
            return await tool_6_manage_hr_and_rules(
                action=args.get("action", ""),
                target=args.get("target", ""),
                value=args.get("value", "")
            )
        elif tool_name == "tool_7_scan_vacancies":
            return await tool_7_scan_vacancies(query=args.get("query", ""))
        else:
            return f"Noma'lum asbob: {tool_name}"
    except Exception as e:
        logger.error(f"Asbobni ({tool_name}) bajarishda xatolik: {e}")
        return f"Xatolik yuz berdi: {str(e)}"


def build_system_prompt() -> str:
    """Nomzod profili va #ALFRED_RULES asosida dinamik system instruction yaratish"""
    rules_block = get_rules_prompt()
    
    prompt = f"""
Sen "Alfred" - Behruzbek Bohodirovning shaxsiy Universal ReAct AI Agentisan.
Sening vazifang - Telegramdagi barcha buyruqlar, xabarlar, ovozli xabarlar va HR muloqotlarini professional darajada boshqarish.

NOMZODNING ASOSIY PROFILI:
{CANDIDATE_PROFILE}

{rules_block}

ENG MUHIM VA QAT'IY QOIDALAR (BUZILMASIN):
1. HR MULOQOTIDA REZYUME TIQISHTIRMASLIK:
   - Suhbat davomida HR rezyume yoki portfolio so'ramasa, HECH QACHON javob ichiga texnologiyalar ro'yxatini (Python, Django, FastAPI...) va GitHub ssilkasini tiqishtirma!
   - HECH QACHON "Hozirda ishlayapman" deb yolg'on gapirma! Agar HR "Ishlaysizmi?" yoki "O'qiysizmi?" deb so'rasa: "Yo'q, hozirda bo'shman va bor e'tiborimni ishga qaratib, to'liq stavkada ishlashga tayyorman" deb tabiiy va muloyim javob yoz!

2. 100% QO'LDA BOSHQARUV:
   - HR'ga HECH QACHON to'g'ridan-to'g'ri xabar yuborma!
   - Har doim `tool_2_propose_manual_hr_reply` orqali Draft qilib saqla va foydalanuvchiga 2 ta tugma ([📤 Javob berish] va [✍️ O'zim javob berdim]) ko'rinishida taqdim et.

3. TEZKOR ASBOB ISHLATISH:
   - Agar foydalanuvchi biror @username yoki havola aytgan bo'lsa -> avval `tool_1_inspect_telegram_entity` orqali tekshir.
   - Agar foydalanuvchi "Yangi qoida: ..." desa -> darhol `tool_6_manage_hr_and_rules` bilan qoidani saqla.
   - Agar suhbat vaqti kelishilgan bo'lsa -> `tool_3_schedule_interview_timer` bilan taymer qo'y.

4. FORMATLASH:
   - Javoblarda yulduzchalar (**) chiqib qolmasin. Telegram HTML formatiga mos javob ber.
   - Muloyim, professional va qisqa o'zbek tilida muloqot qil.
"""
    return prompt


async def run_gemini_react_agent(
    user_message: str, 
    chat_id: str = "", 
    history: Optional[List[Dict[str, str]]] = None,
    is_voice: bool = False
) -> str:
    """
    Universal ReAct Tool-Calling Agent.
    Gemini API ga tools bilan so'rov yuboradi, model asboblarni tanlaydi,
    natijalarni olib kerakli harakatlarni bajaradi.
    """
    if not GEMINI_API_KEY:
        err = "❌ Xatolik: GEMINI_API_KEY topilmadi."
        set_last_error(err)
        return err

    # 1. Tezkor deterministik ushlash (Zero-latency shortcuts)
    lower_msg = user_message.strip().lower()

    # a) Yangi qoida qo'shish so'rovi
    if lower_msg.startswith("yangi qoida:") or lower_msg.startswith("qoidani o'zgartir:"):
        rule_content = user_message.split(":", 1)[1].strip()
        add_custom_rule(rule_content)
        schedule_rules_backup()
        return (
            f"✅ <b>Yangi qoida qabul qilindi va #ALFRED_RULES xotirasiga saqlandi:</b>\n\n"
            f"<i>\"{rule_content}\"</i>\n\n"
            f"💡 Keyingi soniyadan boshlab ushbu yangi qoida bo'yicha ishlayman!"
        )

    # c) Qo'ng'iroq qilish so'rovi (Darhol yoki Aytilgan vaqtda)
    call_triggers = [
        "menga telefon qil", "telefon qil", "telefon qilib", "qo'ng'iroq qilvor", 
        "qongiroq qilvor", "qo'ng'iroq qil", "qongiroq qil", "gudok tashla", 
        "gudok ber", "uyg'ot"
    ]
    if any(ct in lower_msg for ct in call_triggers):
        from userbot import parse_custom_call_time, make_telegram_call
        from database import add_custom_call_timer
        target_dt, time_disp = parse_custom_call_time(user_message)
        if target_dt:
            add_custom_call_timer(target_dt.isoformat(), reason=user_message)
            schedule_db_backup()
            return (
                f"⏰ <b>Qo'ng'iroq taymeri o'rnatildi!</b>\n\n"
                f"📞 Sizga soat <b>{time_disp}</b> da 2-akkaunt orqali Telegramdan qo'ng'iroq qilaman."
            )
        else:
            from userbot import make_telegram_call_detailed
            call_ok, c_info = await make_telegram_call_detailed("Behruzbek, so'rovingiz bo'yicha qo'ng'iroq qilyapman!")
            if call_ok:
                return (
                    f"📲 <b>2-chi akkauntdan ({c_info}) sizning Telegramingizga audio qo'ng'iroq yuborildi!</b>\n"
                    "Qo'ng'iroq 35 soniya davomida jiringlab turadi."
                )
            else:
                return f"❌ <b>Qo'ng'iroq qilishda xatolik yuz berdi:</b>\n<code>{c_info}</code>"

    # b) "bu HR yozmadi" yoki No-Show holati
    if "bu hr yozmadi" in lower_msg or "yozmadi" in lower_msg or "javob bermadi" in lower_msg:
        hr_match = re.search(r"@([a-zA-Z0-9_]{4,32})", user_message)
        clean_hr = hr_match.group(1).lower() if hr_match else ""
        if not clean_hr:
            tracked = get_all_tracked_hrs()
            if tracked:
                clean_hr = tracked[-1].get("username", "")

        if clean_hr:
            followup_msg = (
                "Assalomu alaykum! Belgilangan suhbat vaqti bo'yicha aloqadaman. "
                "Hozir sizga qulaymi yoki boshqa vaqtga ko'chirishimiz kerakmi? "
                "Xabar bersangiz mamnun bo'lardim."
            )
            await save_hr_draft_message(clean_hr, followup_msg)
            update_hr_pending_reply(clean_hr, followup_msg)

            reply_markup = {
                "inline_keyboard": [
                    [
                        {"text": "📤 Javob berish", "callback_data": f"send_hr_reply_{clean_hr}"},
                        {"text": "✍️ O'zim javob berdim", "callback_data": f"manual_replied_{clean_hr}"}
                    ]
                ]
            }
            await send_telegram_message(
                f"⚠️ <b>HR (@{clean_hr}) ga eslatma (Follow-up) tayyorlandi:</b>\n\n"
                f"🤖 <b>Tavsiya etilgan matn:</b>\n<i>\"{followup_msg}\"</i>\n\n"
                f"📝 <i>(Javob HR lichkasiga Qoralama / Draft qilib joylandi)</i>",
                reply_markup=reply_markup
            )
            return f"HR @{clean_hr} uchun nozik eslatma Draft'ga saqlandi va 2 tugmali boshqaruv chiqarildi."

    # 2. ReAct muloqot tuzilmasi
    system_inst = build_system_prompt()
    contents = []

    if history:
        for item in history:
            h_role = "user" if item.get("role") == "user" else "model"
            h_text = (item.get("text") or "").strip()
            if h_text:
                contents.append({"role": h_role, "parts": [{"text": h_text}]})

    contents.append({"role": "user", "parts": [{"text": user_message}]})

    max_steps = 4
    current_step = 0

    for model_name in AVAILABLE_MODELS:
        try:
            while current_step < max_steps:
                current_step += 1
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}"
                payload = {
                    "contents": contents,
                    "systemInstruction": {"parts": [{"text": system_inst}]},
                    "tools": [{"functionDeclarations": GEMINI_TOOLS_DECLARATION}],
                    "generationConfig": {
                        "temperature": 0.2,
                        "topP": 0.8
                    }
                }

                headers = {"User-Agent": "AlfredJobAgent/1.0", "Connection": "close"}
                async with httpx.AsyncClient(timeout=30, headers=headers) as client:
                    resp = await client.post(url, json=payload)

                    if resp.status_code == 200:
                        data = resp.json()
                        candidates = data.get("candidates", [])
                        if not candidates:
                            break

                        candidate = candidates[0]
                        parts = candidate.get("content", {}).get("parts", [])
                        if not parts:
                            break

                        # Asbob chaqiruvlari bormi?
                        function_calls = [p.get("functionCall") for p in parts if "functionCall" in p]

                        if not function_calls:
                            # Final matnli javob keldi!
                            final_text = parts[0].get("text", "")
                            set_last_error(f"✅ ReAct muvaffaqiyatli. Faol model: {model_name}")
                            return markdown_to_telegram_html(final_text)

                        # Model tool call qildi!
                        # 1. Model javobini contents'ga qo'shamiz
                        contents.append(candidate.get("content"))

                        # 2. Barcha asboblarni bajaramiz va functionResponse larni yig'amiz
                        tool_responses_parts = []
                        for fc in function_calls:
                            fn_name = fc.get("name")
                            fn_args = fc.get("args") or {}
                            tool_result = await execute_tool(fn_name, fn_args)
                            tool_responses_parts.append({
                                "functionResponse": {
                                    "name": fn_name,
                                    "response": {"result": tool_result}
                                }
                            })

                        contents.append({
                            "role": "function",
                            "parts": tool_responses_parts
                        })

                        # Sikl davom etadi, keyingi qadamda Gemini tool_responses_parts ni tahlil qilib javob beradi
                        continue

                    elif resp.status_code == 429:
                        logger.warning(f"Model {model_name} -> 429 Rate Limit, keyingisiga o'tiladi.")
                        break
                    else:
                        logger.warning(f"Model {model_name} -> HTTP {resp.status_code}: {resp.text[:100]}")
                        break

        except Exception as e:
            logger.warning(f"ReAct Model {model_name} xatosi: {e}")
            continue

    # Fallback: Agar barcha modellar rad etilsa yoki tool chaqirilmasa
    return (
        "🤖 <b>Xabaringiz qabul qilindi.</b> "
        "Server sozlamalari va ma'lumotlar bazasi faol. "
        "HR muloqoti yoki vakansiyalarni ko'rish uchun /hr_list yoki /scan buyruqlaridan foydalanishingiz mumkin."
    )

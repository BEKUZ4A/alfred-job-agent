from gemini_agent import run_gemini_react_agent
from backup_manager import hot_reload_all, schedule_db_backup, schedule_rules_backup
import logging
import asyncio
import httpx
import re
from datetime import datetime
from typing import Dict, Any, Optional, List
from config import TELEGRAM_BOT_TOKEN, MIN_MATCH_SCORE, TASHKENT_TZ
from ai_analyzer import (
    chat_with_alfred, 
    get_last_error, 
    batch_analyze_vacancies, 
    transcribe_voice_with_gemini, 
    generate_freelance_pitch,
    generate_tailored_resume_and_pitch,
    generate_interview_prep,
    analyze_hr_message,
    analyze_user_hr_dialogue,
    generate_hr_followup
)
from notifier import (
    send_telegram_message, 
    send_telegram_voice, 
    send_telegram_document,
    format_vacancy_card, 
    make_call,
    edit_telegram_reply_markup,
    answer_callback_query
)
from scraper import scrape_telegram_channel, scrape_hh_uz, scrape_telegram_single_post
from database import (
    get_recent_vacancies, 
    get_today_vacancies,
    mark_vacancy_seen, 
    get_vacancy_by_id, 
    get_top_vacancies,
    add_tracked_hr,
    get_tracked_hr,
    get_all_tracked_hrs,
    update_hr_status,
    delete_tracked_hr,
    mark_job_applied,
    get_applied_jobs,
    add_managed_channel,
    remove_managed_channel,
    get_managed_channels,
    add_managed_keyword,
    remove_managed_keyword,
    get_managed_keywords,
    compute_content_hash,
    is_vacancy_seen,
    is_job_applied,
    get_hr_pending_reply
)
from pdf_generator import generate_pdf_resume
from userbot import (
    delete_hr_dialog, 
    send_message_as_user, 
    sync_hr_last_seen_silently,
    get_hr_chat_history,
    schedule_2min_reminder,
    parse_meeting_time
)
from voice import generate_voice_message

logger = logging.getLogger("Alfred.BotHandler")

# Suhbat xotirasi (Chat history): har bir chat_id uchun oxirgi 10 ta xabarni saqlash
chat_histories: Dict[str, List[Dict[str, str]]] = {}

# O'zbekistondagi ommabop IT Telegram kanallari va ularning oddiy xalq tilidagi nomlari (aliaslar)
CHANNEL_ALIASES = {
    "joblinkuz": [
        "joblink", "joblinkuz", "job link", "joblink.uz", "joblink uz", "job_link"
    ],
    "rizqimuz": [
        "rizqim", "rizqimuz", "rizqim uz", "rizq", "rizqim.uz"
    ],
    "ish_kerak_toshkent_bormi_ishlar": [
        "ish kerak toshkent", "ish kerak", "ishkerak", "toshkent ishlar", "ish_kerak", "bormi ishlar"
    ],
    "jobforjunior": [
        "jobforjunior", "job for junior", "junior jobs", "job for juniors", "junior job", "jobjunior", "junior", "tajribasiz", "tajribasiz ishlar"
    ],
    "freelance_uzb": [
        "freelance", "freelance uzb", "freelance_uzb", "frilans", "frilans uzb", "freelancer", "freelans"
    ],
    "young_june": [
        "young june", "young_june", "young júnior", "young junior", "yang jyun"
    ],
    "young_intern": [
        "young intern", "young_intern", "youngintern", "intern", "stajyor", "amaliyot", "tajribasiz", "stajirovka"
    ],
    "UstozShogird": [
        "ustoz shogird", "ustozshogird", "ustoz_shogird", "ustoz-shogird", "ustoz", "ustoz shogird kanali"
    ],
    "it_jobs_uz": [
        "it jobs", "it_jobs", "it_jobs_uz", "itjobs", "it jobs uz"
    ]
}

CHANNEL_DISPLAY_NAMES = {
    "joblinkuz": "JobLink (@joblinkuz)",
    "rizqimuz": "Rizqim (@rizqimuz)",
    "ish_kerak_toshkent_bormi_ishlar": "Ish kerak Toshkent (@ish_kerak_toshkent_bormi_ishlar)",
    "jobforjunior": "Job for Junior (@jobforjunior)",
    "freelance_uzb": "Freelance Uzbekistan (@freelance_uzb)",
    "young_june": "Young June (@young_june)",
    "young_intern": "Young Intern (@young_intern)",
    "UstozShogird": "Ustoz-Shogird (@UstozShogird)",
    "it_jobs_uz": "IT Jobs Uzbekistan (@it_jobs_uz)"
}

def resolve_channel_query(user_text: str) -> Optional[tuple[str, str]]:
    """
    Foydalanuvchi aytgan matn yoki ovozdan Telegram guruh/kanal nomini aniqlash.
    @ belgisi bilan aytilmasa ham (masalan, 'Joblink', 'Rizqim', 'Freelance') aniqlaydi.
    Natija: (channel_username, display_name) yoki None.
    """
    raw = user_text.strip().lower()

    # Aniq bitta post ssilkasi bo'lsa (masalan: t.me/UstozShogird/45810), kanal skaneri sifatida qabul qilmaslik kerak!
    if re.search(r"t\.me/(?:s/)?[a-zA-Z0-9_]{3,32}/\d+", raw, re.IGNORECASE):
        return None
    
    # 1. Buyruq prefikslari: /kanal <nomi>, /channel <nomi>, /search <nomi>
    for prefix in ["/kanal", "/channel", "/search", "kanal:", "guruh:"]:
        if raw.startswith(prefix):
            target = raw[len(prefix):].strip().lstrip("@")
            if target:
                raw = target
                break
                
    # 2. Ma'lum kanallar ro'yxatidan qidirish (uzunroq iboralar birinchi)
    sorted_channels = []
    for ch, aliases in CHANNEL_ALIASES.items():
        for alias in aliases:
            sorted_channels.append((alias, ch))
    sorted_channels.sort(key=lambda x: len(x[0]), reverse=True)
    
    for alias, ch in sorted_channels:
        if alias in raw:
            disp = CHANNEL_DISPLAY_NAMES.get(ch, f"@{ch}")
            return ch, disp
            
    # 3. Aniq @ bilan kiritilgan bo'lsa (masalan: @python_jobs_uz)
    at_match = re.search(r"@([a-zA-Z0-9_]{4,32})", raw)
    if at_match:
        ch = at_match.group(1)
        return ch, f"@{ch}"
        
    # 4. t.me/ havolasi bilan kiritilgan bo'lsa (masalan: t.me/dev_uz)
    tme_match = re.search(r"t\.me/(?:s/)?([a-zA-Z0-9_]{4,32})", raw)
    if tme_match:
        ch = tme_match.group(1)
        return ch, f"@{ch}"
        
    # 5. "<nomi> kanalidan" yoki "<nomi> guruhidan" shaklida aytilgan bo'lsa
    group_match = re.search(r"([a-zA-Z0-9_]{4,32})\s+(?:kanalidan|guruhidan|kanalini|guruhini|dan|guruhi|kanali)", raw)
    if group_match:
        candidate = group_match.group(1)
        stop_words = ["bugun", "yangi", "qanday", "iltimos", "menga", "faqat", "yaxshi", "qidir", "tekshir"]
        if candidate not in stop_words:
            return candidate, f"@{candidate}"

    return None

async def download_telegram_file(file_id: str) -> Optional[bytes]:
    """Telegramdan audio/voice fayl baytlarini yuklab olish"""
    if not TELEGRAM_BOT_TOKEN or not file_id:
        return None
        
    get_file_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getFile?file_id={file_id}"
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            resp = await client.get(get_file_url)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok"):
                    file_path = data.get("result", {}).get("file_path")
                    if file_path:
                        download_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
                        dl_resp = await client.get(download_url)
                        if dl_resp.status_code == 200:
                            return dl_resp.content
    except Exception as e:
        logger.error(f"Telegram faylni yuklab olishda xatolik: {e}")
    return None

async def search_channel_vacancies(channel: str, display_name: str, chat_id: str):
    """
    Foydalanuvchi aytgan aniq kanal/guruhdan (masalan: JobLink, Rizqim, Freelance va h.k.)
    vakansiyalarni qidirish va nomzod profiliga moslarini yetkazish.
    """
    await send_telegram_message(
        f"🔎 <b>{display_name}</b> bo'yicha qidiruv boshlandi...\n"
        "Xabarlar o'qilmoqda va sun'iy intellekt (Gemini) orqali tilingiz hamda darajangizga mosligi tekshirilmoqda...",
        chat_id=chat_id
    )
    
    # 1. Avval bugungi e'lonlarni tekshiramiz
    vacancies = await scrape_telegram_channel(channel, today_only=True)
    is_today = True
    if not vacancies:
        # Agar bugun hali yangi post bo'lmasa, so'nggi e'lonlarni olamiz
        vacancies = await scrape_telegram_channel(channel, today_only=False)
        is_today = False
        
    if not vacancies:
        await send_telegram_message(
            f"ℹ️ <b>{display_name}</b> kanalida hozircha Python/Backend bo'yicha e'lonlar topilmadi yoki kanal ochiq emas.",
            chat_id=chat_id
        )
        return

    # 1.5. Qat'iy dublikat tekshiruvi: avval ko'rilgan yoki topshirilgan e'lonlarni tashlab ketish
    fresh_vacancies = []
    seen_hashes_in_ch = set()
    for vac in vacancies:
        c_hash = compute_content_hash(vac.get("text", ""), vac.get("title", ""), vac.get("company", ""))
        vac["content_hash"] = c_hash
        v_url = vac.get("url", "")
        v_id = vac.get("id", "")
        v_title = vac.get("title", "")
        v_comp = vac.get("company", "")

        if c_hash in seen_hashes_in_ch:
            continue
        seen_hashes_in_ch.add(c_hash)

        if is_vacancy_seen(v_id, url=v_url, content_hash=c_hash, title=v_title, company=v_comp):
            continue
        if is_job_applied(v_id, url=v_url, content_hash=c_hash, title=v_title, company=v_comp):
            continue
        fresh_vacancies.append(vac)

    if not fresh_vacancies:
        await send_telegram_message(
            f"ℹ️ <b>{display_name}</b> kanalidagi barcha yangi e'lonlar allaqachon ko'rib chiqilgan yoki topshirilgan.",
            chat_id=chat_id
        )
        return

    # 2. Gemini orqali tahlil qilish
    analysis_results = await batch_analyze_vacancies(fresh_vacancies[:10])
    
    matches = []
    for vac in fresh_vacancies:
        analysis = next((a for a in analysis_results if str(a.get("id")) == str(vac["id"])), None)
        if analysis and analysis.get("is_relevant") and analysis.get("match_score", 0) >= MIN_MATCH_SCORE:
            vac["analysis"] = analysis
            matches.append(vac)
            # Bazaga saqlash
            mark_vacancy_seen(
                vacancy_id=vac["id"],
                title=vac["title"],
                company=analysis.get("company", vac.get("company", "")),
                url=vac["url"],
                source=vac["source"],
                match_score=analysis.get("match_score", 0),
                ai_summary=analysis.get("short_summary", ""),
                job_type=analysis.get("job_type", vac.get("job_type", "vacancy")),
                raw_text=vac.get("text", ""),
                salary=analysis.get("salary", ""),
                red_flag=analysis.get("red_flag", ""),
                posted_at=vac.get("posted_at", ""),
                content_hash=vac.get("content_hash", "")
            )
            
    # 3. Natijalarni yuborish
    date_note = "Bugungi" if is_today else "So'nggi"
    if not matches:
        await send_telegram_message(
            f"ℹ️ <b>{display_name}</b> dagi {len(vacancies)} ta {date_note.lower()} e'lon tahlil qilindi, ammo nomzod profilingizga (Junior/Python) mos yuqori balli vakansiya chiqmadi.",
            chat_id=chat_id
        )
        return

    # Mos vakansiyalar topildi!
    matches.sort(key=lambda x: x.get("analysis", {}).get("match_score", 0), reverse=True)
    await send_telegram_message(
        f"🎉 <b>{display_name}</b> kanalidan <b>{len(matches)} ta mos vakansiya</b> topildi! ({date_note} e'lonlar)",
        chat_id=chat_id
    )
    
    # 1 ta eng mos vakansiyani audio qilib yuboramiz
    top_vac = matches[0]
    top_analysis = top_vac.get("analysis", {})
    top_title = top_analysis.get("role_title", top_vac.get("title", "Python Dasturchi"))
    top_score = top_analysis.get("match_score", 0)
    voice_txt = top_analysis.get("voice_text") or (
        f"Assalomu alaykum Behruzbek! {display_name} dan sizga juda mos yangi taklif topildi: {top_title}. "
        f"Moslik darajasi: {top_score} foiz. Batafsil ma'lumotlarni chatda ko'rib chiqishingiz mumkin."
    )
    
    try:
        voice_file = await generate_voice_message(voice_txt)
        if voice_file:
            caption = f"🎙 <b>{display_name} — Eng mos vakansiya:</b>\n<b>{top_title}</b> ({top_score}% moslik)"
            await send_telegram_voice(voice_file, caption=caption, chat_id=chat_id)
    except Exception as e:
        logger.error(f"Kanal qidiruvida ovozli xabar yaratishda xato: {e}")
        
    for i, vac in enumerate(matches[:5], start=1):
        try:
            card = format_vacancy_card(vac, rank=i)
            vac_analysis = vac.get("analysis", {})
            job_type = vac_analysis.get("job_type") or vac.get("job_type", "vacancy")
            is_freelance = "freelance" in str(job_type).lower() or "frilans" in str(job_type).lower() or "buyurtma" in str(job_type).lower()
            
            reply_markup = None
            if is_freelance:
                reply_markup = {
                    "inline_keyboard": [
                        [
                            {
                                "text": "📨 Rezyume / Taklif yuborish",
                                "callback_data": f"pitch:{vac['id']}"
                            }
                        ]
                    ]
                }
            await send_telegram_message(card, chat_id=chat_id, reply_markup=reply_markup)
        except Exception as e:
            logger.error(f"Karta yuborishda xato: {e}")

async def ensure_real_vacancies(limit: int = 5) -> List[Dict]:
    """
    Bazadan eng yuqori ball to'plagan haqiqiy vakansiyalarni oladi.
    Agar baza bo'sh bo'lsa, zudlik bilan HH.uz va Telegram kanallardan real e'lonlarni
    skanerlab, Gemini orqali tahlil qilib, bazaga yozadi va qaytaradi.
    """
    vacs = get_top_vacancies(limit=limit, min_score=45)
    if vacs:
        return vacs
        
    logger.info("Bazada vakansiyalar topilmadi. Tezkor real e'lonlarni skanerlash boshlandi...")
    try:
        raw_items = []
        # 1. HH.uz dan yangi vakansiyalarni olish
        hh_items = await scrape_hh_uz()
        if hh_items:
            raw_items.extend(hh_items)
            
        # 2. Asosiy Telegram kanallaridan olish
        tg_items = await scrape_telegram_channel("joblinkuz", today_only=False)
        if tg_items:
            raw_items.extend(tg_items)
            
        if not raw_items:
            tg_items2 = await scrape_telegram_channel("UstozShogird", today_only=False)
            if tg_items2:
                raw_items.extend(tg_items2)
                
        if raw_items:
            analysis_results = await batch_analyze_vacancies(raw_items[:12])
            for res in analysis_results:
                vac_id = res.get("id")
                orig = next((item for item in raw_items if item.get("id") == vac_id), {})
                score = res.get("match_score", 0)
                mark_vacancy_seen(
                    vacancy_id=vac_id or f"quick_{abs(hash(res.get('role_title', '')))}",
                    title=res.get("role_title") or orig.get("title", "Python Dasturchi"),
                    company=res.get("company") or orig.get("company", ""),
                    url=orig.get("url", "https://hh.uz"),
                    source=orig.get("source", "HH.UZ"),
                    match_score=score,
                    ai_summary=res.get("short_summary", ""),
                    job_type=res.get("job_type", "vacancy"),
                    raw_text=orig.get("text", ""),
                    salary=res.get("salary", "")
                )
            vacs = get_top_vacancies(limit=limit, min_score=35)
    except Exception as e:
        logger.error(f"ensure_real_vacancies xatoligi: {e}")
        
    return vacs

async def handle_user_hr_discussion(message: Dict[str, Any], chat_id: str) -> bool:
    """
    Foydalanuvchi botda HR xabariga Reply qilganda yoki 'HR bilan gaplashdim / vaqtini aniqqil' deganda:
    1. Hech qachon vakansiyalar bazasidan (SberTech, Popcorn TV) qidirmaslik!
    2. Reply qilingan xabardan yoki xabar matnidan @username ni (yoki tracked_hrs dagi oxirgi HR ni) aniqlash.
    3. Telethon orqali shu HR bilan bo'lgan oxirgi 15 ta xabarni jonli o'qish.
    4. Kelishilgan suhbat vaqtini aniqlash, 2 daqiqa oldin taymer qo'yish va botda aniq tasdiq chiqarish.
    """
    text = (message.get("text") or message.get("caption") or "").strip()
    reply_to = message.get("reply_to_message")
    reply_text = ((reply_to.get("text") or reply_to.get("caption") or "") if reply_to else "").strip()

    lower_text = text.lower()
    lower_reply = reply_text.lower()

    # Trigger shartlari:
    # 1. Reply qilingan xabarda HR yoki suhbat / uchrashuv / @ belgisi bo'lsa
    is_reply_to_hr = bool(reply_to and any(w in lower_reply for w in ["hr", "suhbat", "@", "belgilandi", "xabar yozdi"]))

    # 2. Xabarda aniq kalit iboralar bo'lsa
    direct_phrases = [
        "gaplashdim", "vaqtini aniqqil", "vaqtini aniqla", "vaqtini bil", 
        "kelishdik", "kelishdim", "vaqtni aniqla", "vaqtni aniqqil",
        "suhbat vaqti", "intervyu vaqti", "suhbat vaqtini", "soat nechada"
    ]
    has_direct_phrase = any(phrase in lower_text for phrase in direct_phrases)

    # 3. HR va vaqt/suhbat/gaplashuv so'zlari kombinatsiyasi
    has_hr_mention = any(w in lower_text for w in ["hr", "@", "lichka"]) and any(
        w in lower_text for w in ["gaplash", "vaqt", "suhbat", "intervyu", "kelish", "aniq", "yozdim", "yozishdim", "tekshir"]
    )

    if not (is_reply_to_hr or has_direct_phrase or has_hr_mention):
        return False

    ignored_usernames = {
        "joblinkuz", "rizqimuz", "ish_kerak_toshkent_bormi_ishlar", "jobforjunior", 
        "freelance_uzb", "young_june", "young_intern", "ustozshogird", "it_jobs_uz", 
        "bbcnew_2026", 
    }

    target_hr = None
    target_company = ""

    # a) Reply matnidan @username
    if reply_text:
        matches = re.findall(r"@([a-zA-Z0-9_]{3,32})", reply_text)
        for u in matches:
            if u.lower() not in ignored_usernames:
                target_hr = u.lower().strip()
                break

    # b) Foydalanuvchi matnidan @username
    if not target_hr and text:
        matches = re.findall(r"@([a-zA-Z0-9_]{3,32})", text)
        for u in matches:
            if u.lower() not in ignored_usernames:
                target_hr = u.lower().strip()
                break

    # c) Agar topilmasa, bazadagi tracked_hrs dan eng so'nggi faolini olish (masalan: itlive_hr)
    all_tracked = get_all_tracked_hrs()
    if not target_hr and all_tracked:
        sorted_hrs = sorted(all_tracked, key=lambda x: (x.get("last_seen_msg_id") or 0, x.get("added_at") or ""), reverse=True)
        target_hr = sorted_hrs[0].get("username", "").lstrip("@").lower().strip()
        target_company = sorted_hrs[0].get("company", "")

    if not target_hr:
        await send_telegram_message(
            "⚠️ <b>Kuzatuvda birorta ham HR topilmadi!</b>\n\n"
            "Iltimos, avval <code>/add_HR @username Kompaniya</code> orqali HR ni kuzatuvga oling.",
            chat_id=chat_id
        )
        return True

    # Kompaniyani olish
    hr_info = get_tracked_hr(target_hr)
    if hr_info and hr_info.get("company"):
        target_company = hr_info.get("company", "")

    # Telethon orqali shaxsiy lichkani o'qish (oxirgi 15 ta xabar)
    chat_history = await get_hr_chat_history(target_hr, limit=15)
    if not chat_history:
        await send_telegram_message(
            f"⚠️ <b>HR @{target_hr} bilan shaxsiy Telegramingizda yozishmalar topilmadi yoki Userbot ulanmagan.</b>\n"
            "Iltimos, Userbot faolligini va ushbu HR bilan Telegramda muloqot bo'lganligini tekshiring.",
            chat_id=chat_id
        )
        return True

    analysis = await analyze_user_hr_dialogue(chat_history, target_hr, target_company)
    has_agreed = analysis.get("has_agreed_time", False)
    exact_time_display = analysis.get("exact_time_display", "")
    target_iso = analysis.get("target_iso", "")
    last_agreement = analysis.get("last_agreement", "")
    summary = analysis.get("summary", "")

    if has_agreed and exact_time_display:
        target_dt = None
        if target_iso:
            try:
                target_dt = datetime.fromisoformat(target_iso)
            except Exception:
                pass
        if not target_dt:
            target_dt, _ = parse_meeting_time(last_agreement or text)

        if target_dt:
            await schedule_2min_reminder(target_dt, target_hr, target_company, exact_time_display)

        await send_telegram_message(
            f"📅 <b>@{target_hr} bilan yozishmangizni tekshirdim!</b>\n\n"
            f"💬 Oxirgi kelishuv: <i>\"{last_agreement}\"</i>\n"
            f"⏰ <b>Belgilangan suhbat vaqti:</b> <b>{exact_time_display}</b>\n\n"
            f"🔔 <b>Suhbatga 2 daqiqa qolganda sizga qo'ng'iroq qilaman!</b>",
            chat_id=chat_id
        )
    else:
        recent_previews = []
        for m in chat_history[-2:]:
            recent_previews.append(f"{m['sender']}: {m['text'][:80]}")
        dialogue_preview = " | ".join(recent_previews) if recent_previews else last_agreement

        await send_telegram_message(
            f"📅 <b>@{target_hr} bilan yozishmangizni tekshirdim!</b>\n\n"
            f"💬 Oxirgi yozishmalar: <i>\"{dialogue_preview}\"</i>\n"
            f"ℹ️ <b>Holat:</b> {summary or 'Hali aniq suhbat soati kelishilmagan.'}\n\n"
            f"🔔 <i>HR aniq vaqt taklif qilganda yoki siz vaqtga kelishganingizda avtomatik eslatma va qo'ng'iroq qo'yiladi.</i>",
            chat_id=chat_id
        )
    return True

async def process_telegram_update(update: Dict[str, Any], trigger_scan_callback=None):
    """Telegramdan kelgan yangilanishlarni (matn, ovozli xabarlar va tugma bosilishlarini) qayta ishlash"""
    
    # 0. Telegram Inline Tugma (Callback Query) bosilganda (masalan: "📨 Rezyume / Taklif yuborish")
    callback_query = update.get("callback_query")
    if callback_query:
        callback_id = callback_query.get("id")
        cb_data = callback_query.get("data", "")
        cb_chat_id = str(callback_query.get("message", {}).get("chat", {}).get("id"))
        
        # Tugma loading aylanasini to'xtatish uchun Telegramga tasdiq yuborish
        try:
            ack_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery"
            async with httpx.AsyncClient(timeout=8) as client:
                await client.post(ack_url, json={"callback_query_id": callback_id, "text": "Taklif tayyorlanmoqda..."})
        except Exception as e:
            logger.warning(f"answerCallbackQuery xatosi: {e}")
            
        if cb_data.startswith("pitch:"):
            vac_id = cb_data.split(":", 1)[1]
            vac = get_vacancy_by_id(vac_id)
            title = vac.get("title", "Frilans buyurtma") if vac else "Frilans buyurtma"
            summary = vac.get("ai_summary", "") if vac else ""
            source = vac.get("source", "Internet") if vac else ""
            url = vac.get("url", "#") if vac else "#"
            
            await send_telegram_message(
                "⏳ <i>Gemini AI buyurtmachi uchun maxsus tijoriy taklif (Pitch) yozmoqda...</i>",
                chat_id=cb_chat_id
            )
            pitch = await generate_freelance_pitch(title=title, summary=summary, source=source)
            
            pitch_msg = (
                f"📨 <b>Buyurtmachi uchun tayyor tijoriy taklif (Pitch / Cover Letter):</b>\n\n"
                f"<i>\"{pitch}\"</i>\n\n"
                f"📋 <i>Ushbu matnni nusxalab, mijozga to'g'ridan-to'g'ri yuborishingiz mumkin!</i>\n"
                f"👉 <a href='{url}'><b>Mijoz e'loniga o'tish</b></a>"
            )
            await send_telegram_message(pitch_msg, chat_id=cb_chat_id)
            return

        # 1. 📝 Matn + 📄 PDF Rezyume generatsiya qilish (2 bosqichli to'plam)
        if cb_data.startswith("resume_bundle_") or cb_data.startswith("pdf_resume_"):
            vac_id = cb_data.replace("resume_bundle_", "").replace("pdf_resume_", "")
            vac = get_vacancy_by_id(vac_id)
            title = vac.get("title", "Python Dasturchi") if vac else "Python Dasturchi"
            company = vac.get("company", "") if vac else ""
            summary = vac.get("ai_summary", "") if vac else ""
            raw_text = vac.get("raw_text") or f"{title} | {company} | {summary}" if vac else title

            await send_telegram_message(
                f"⏳ <b>{title}</b> ({company}) uchun tayyor Cover Letter va PDF rezyume tayyorlanmoqda...",
                chat_id=cb_chat_id
            )

            # 1-xabar: Matnli Cover Letter + Qisqa CV
            docs = await generate_tailored_resume_and_pitch(raw_text, user_instruction=f"Vakansiya: {title} ({company})")
            await send_telegram_message(docs, chat_id=cb_chat_id)

            # 2-xabar: 1-sahifali PDF Rezyume
            try:
                pdf_path = generate_pdf_resume(
                    tailored_summary=summary,
                    target_job_title=title,
                    target_company=company
                )
                await send_telegram_document(
                    pdf_path,
                    caption=f"📄 <b>Behruzbek_Bohodirov_CV.pdf</b>\n<i>{title} ({company}) vakansiyasiga moslashtirildi.</i>",
                    chat_id=cb_chat_id
                )
            except Exception as e_pdf:
                logger.error(f"Bundle PDF yaratishda xato: {e_pdf}")
            return

        # 2. 🎯 Suhbat savollari generatsiya qilish
        if cb_data.startswith("interview_prep_"):
            vac_id = cb_data.replace("interview_prep_", "")
            vac = get_vacancy_by_id(vac_id)
            raw_text = vac.get("raw_text") or vac.get("title", "") if vac else ""

            await send_telegram_message(
                "🎯 <i>Gemini ushbu vakansiya bo'yicha 7 ta asosiy texnik intervyu savoli va javoblarini tayyorlamoqda...</i>",
                chat_id=cb_chat_id
            )
            prep_text = await generate_interview_prep(raw_text)
            await send_telegram_message(prep_text, chat_id=cb_chat_id)
            return

        # 0. Takroriy bosilgan tugmalar
        if cb_data in ["noop_action", "noop_hr_sent", "noop_applied"]:
            await answer_callback_query(cb_query.get("id"), text="Bu amal allaqachon bajarilgan!")
            return

        # 3. ✅ Topshirdim (Mini-CRM)
        if cb_data.startswith("applied_"):
            vac_id = cb_data.replace("applied_", "")
            vac = get_vacancy_by_id(vac_id)
            if vac:
                mark_job_applied(
                    vacancy_id=vac_id,
                    title=vac.get("title", "Vakansiya"),
                    company=vac.get("company", ""),
                    url=vac.get("url", "#"),
                    content_hash=vac.get("content_hash", "")
                )
            cb_msg_id = cb_query.get("message", {}).get("message_id")
            if cb_msg_id:
                await edit_telegram_reply_markup(cb_chat_id, cb_msg_id, {
                    "inline_keyboard": [
                        [{"text": "☑️ Topshirildi", "callback_data": "noop_applied"}]
                    ]
                })
            await send_telegram_message(
                "✅ <b>Ushbu vakansiyaga topshirildi deb belgilandi!</b>\n"
                "U endi qidiruvda HECH QACHON qayta chiqmaydi. /my_jobs orqali barcha topshirgan ishlaringizni ko'rishingiz mumkin.",
                chat_id=cb_chat_id
            )
            return

        # 4. 🗑 Ha, Telegramdan o'chir
        if cb_data.startswith("delete_hr_yes_"):
            username = cb_data.replace("delete_hr_yes_", "").strip().lstrip("@")
            delete_tracked_hr(username)

            # Telegram lichkadan chatni butunlay o'chirish (revoke=True)
            success_del = await delete_hr_dialog(username)
            if success_del:
                await send_telegram_message(
                    f"🗑 <b>HR @{username} shaxsiy Telegramingizdan va ma'lumotlar bazasidan butunlay o'chirildi!</b>",
                    chat_id=cb_chat_id
                )
            else:
                await send_telegram_message(
                    f"🗑 <b>HR @{username} bazadan o'chirildi.</b> (Telegram dialogini o'chirishda ogohlantirish)",
                    chat_id=cb_chat_id
                )
            return

        # 5. ⏳ Yo'q, qolsin
        if cb_data.startswith("delete_hr_no_"):
            username = cb_data.replace("delete_hr_no_", "").strip().lstrip("@")
            update_hr_status(username, "waiting")
            await send_telegram_message(
                f"⏳ <b>HR @{username} kuzatuvda saqlab qolindi.</b>",
                chat_id=cb_chat_id
            )
            return

        # 6. ✅ HR ga tavsiya etilgan javobni yuborish (Telegram Userbot orqali)
        if cb_data.startswith("send_hr_reply_"):
            username = cb_data.replace("send_hr_reply_", "").strip().lstrip("@")
            hr = get_tracked_hr(username)
            company = hr.get("company", "") if hr else ""
            reply_to_send = get_hr_pending_reply(username)

            if not reply_to_send:
                last_msg = hr.get("last_message", "") if hr else ""
                analysis = await analyze_hr_message(last_msg, username, company)
                reply_to_send = analysis.get("suggested_reply", "Assalomu alaykum! Xabaringiz uchun rahmat. Suhbatlashishga tayyorman.")

            sent = await send_message_as_user(username, reply_to_send, show_typing=True)
            cb_msg_id = callback_query.get("message", {}).get("message_id")
            if cb_msg_id:
                await edit_telegram_reply_markup(cb_chat_id, cb_msg_id, {
                    "inline_keyboard": [
                        [{"text": "☑️ Bot orqali javob yuborildi", "callback_data": "noop_hr_sent"}]
                    ]
                })
            if sent:
                await send_telegram_message(
                    f"✅ <b>Javobingiz HR @{username} ga shaxsiy Telegramingizdan yuborildi!</b>\n\n"
                    f"<i>\"{reply_to_send}\"</i>",
                    chat_id=cb_chat_id
                )
            else:
                await send_telegram_message(
                    f"❌ <b>HR @{username} ga xabar yuborishda xatolik yuz berdi.</b> Userbot holatini tekshiring.",
                    chat_id=cb_chat_id
                )
            return

        # 6.1. ✍️ O'zim javob berdim (Lichkani jonli o'qish va vaqtni aniqlash)
        if cb_data.startswith("manual_replied_"):
            username = cb_data.replace("manual_replied_", "").strip().lstrip("@")
            chat_history = await get_hr_chat_history(username, limit=10)
            dialogue_text = " ".join([h.get("text", "") for h in chat_history]) if chat_history else ""
            target_dt, time_str = parse_meeting_time(dialogue_text)

            cb_msg_id = callback_query.get("message", {}).get("message_id")
            if target_dt:
                time_disp = target_dt.strftime("%H:%M")
                update_hr_interview_time(username, target_dt.isoformat(), reminded_2min=False)
                schedule_db_backup()
                btn_text = f"☑️ O'zingiz javob berdingiz (Suhbat: {time_disp})"
                if cb_msg_id:
                    await edit_telegram_reply_markup(cb_chat_id, cb_msg_id, {
                        "inline_keyboard": [
                            [{"text": btn_text, "callback_data": "noop_hr_sent"}]
                        ]
                    })
                # Suhbat kelishilgan zahoti DARHOL 1 marta qo'ng'iroq qilib ogohlantirish
                from userbot import make_telegram_call
                await make_telegram_call(f"Behruzbek, siz HR @{username} bilan suhbatni soat {time_disp} ga kelishdingiz!")

                await send_telegram_message(
                    f"✅ <b>Siz HR (@{username}) bilan o'zingiz gaplashib, suhbatni soat {time_disp} ga kelishdingiz!</b>\n"
                    f"🔔 1 marta qo'ng'iroq qilindi va suhbatga 2 daqiqa qolganda sizga yana qo'ng'iroq qilaman.",
                    chat_id=cb_chat_id
                )
            else:
                if cb_msg_id:
                    await edit_telegram_reply_markup(cb_chat_id, cb_msg_id, {
                        "inline_keyboard": [
                            [{"text": "☑️ O'zingiz javob berdingiz", "callback_data": "noop_hr_sent"}]
                        ]
                    })
                await send_telegram_message(
                    f"✅ <b>HR @{username} ga o'zingiz javob berganingiz belgilandi.</b>",
                    chat_id=cb_chat_id
                )
            return

        # 6.2. ✍️ O'zim yozdim (2 daqiqalik eslatma uchun)
        if cb_data.startswith("manual_ready_"):
            username = cb_data.replace("manual_ready_", "").strip().lstrip("@")
            cb_msg_id = callback_query.get("message", {}).get("message_id")
            if cb_msg_id:
                await edit_telegram_reply_markup(cb_chat_id, cb_msg_id, {
                    "inline_keyboard": [
                        [{"text": "☑️ O'zingiz yozdingiz", "callback_data": "noop_hr_sent"}]
                    ]
                })
            await send_telegram_message(
                f"✅ <b>HR @{username} ga o'zingiz yozganingiz belgilandi. Suhbatda omad!</b>",
                chat_id=cb_chat_id
            )
            return

        # 7. ➕ HR'ni kuzatuvga olish (Vakansiya ostidagi tugma)
        if cb_data.startswith("track_hr_"):
            vac_id = cb_data.replace("track_hr_", "")
            vac = get_vacancy_by_id(vac_id)
            if not vac:
                await send_telegram_message("❌ Vakansiya ma'lumoti topilmadi.", chat_id=cb_chat_id)
                return

            company = vac.get("company", "")
            raw_text = (vac.get("raw_text") or "") + " " + (vac.get("url") or "") + " " + (vac.get("title") or "")
            hr_usernames = re.findall(r"@([a-zA-Z0-9_]{4,32})", raw_text)
            
            ignored_channels = [
                "joblinkuz", "rizqimuz", "ish_kerak_toshkent_bormi_ishlar", "jobforjunior", 
                "freelance_uzb", "young_june", "young_intern", "ustozshogird", "it_jobs_uz", "bbcnew_2026"
            ]
            candidate_hr = None
            from database import is_forbidden_hr_username
            for u in hr_usernames:
                u_clean = u.lower().strip()
                if u_clean not in ignored_channels and not is_forbidden_hr_username(u_clean):
                    candidate_hr = u_clean
                    break

            if candidate_hr:
                add_tracked_hr(candidate_hr, company=company)
                asyncio.create_task(sync_hr_last_seen_silently(candidate_hr))
                await send_telegram_message(
                    f"✅ <b>HR @{candidate_hr} ({company or 'Kompaniya'}) muvaffaqiyatli kuzatuvga olindi!</b>\n\n"
                    f"• <b>Dinamik nazorat:</b> Telethon orqali har 10 soniyada tekshirib boriladi.\n"
                    f"• HR sizga Telegram lichkangizda yozsa, darhol bildirishnoma yetkaziladi.\n"
                    f"• Agar vaqt/uchrashuv belgilasa, darhol qo'ng'iroq va 2 daqiqa oldingi taymer yoqiladi!",
                    chat_id=cb_chat_id
                )
            else:
                await send_telegram_message(
                    f"ℹ️ <b>Ushbu e'londan aniq HR username (@username) topilmadi.</b>\n\n"
                    f"Iltimos, HR username'ni qo'lda qo'shing:\n"
                    f"<code>/add_HR @username {company or 'Kompaniya'}</code>",
                    chat_id=cb_chat_id
                )
            return

        # 8. ✅ Tasdiqlash javobini HR'ga yuborish (Vaqt belgilanganda)
        if cb_data.startswith("confirm_time_"):
            hr_username = cb_data.replace("confirm_time_", "").strip().lstrip("@")
            confirm_msg = get_hr_pending_reply(hr_username) or (
                "Assalomu alaykum! Taklif qilingan vaqt menga to'liq to'g'ri keladi. "
                "Belgilangan vaqtda suhbatga tayyor bo'laman. Rahmat!"
            )
            sent = await send_message_as_user(hr_username, confirm_msg)
            if sent:
                cb_msg_id = cb_query.get("message", {}).get("message_id")
                if cb_msg_id:
                    await edit_telegram_reply_markup(cb_chat_id, cb_msg_id, {
                        "inline_keyboard": [
                            [{"text": "☑️ Tasdiq HR'ga yuborildi", "callback_data": "noop_hr_sent"}]
                        ]
                    })
                await send_telegram_message(
                    f"✅ <b>HR @{hr_username} ga tasdiqlash javobi shaxsiy Telegramingizdan yuborildi!</b>\n\n"
                    f"<i>\"{confirm_msg}\"</i>",
                    chat_id=cb_chat_id
                )
            else:
                await send_telegram_message(
                    f"❌ <b>HR @{hr_username} ga xabar yuborishda xatolik yuz berdi.</b> Userbot ulanishini tekshiring.",
                    chat_id=cb_chat_id
                )
            return

        # 9. ✅ "Tayyorman" deb HR'ga yuborish (2 daqiqa oldin keladigan tugma)
        if cb_data.startswith("send_ready_"):
            hr_username = cb_data.replace("send_ready_", "").strip().lstrip("@")
            ready_msg = "Assalomu alaykum! Men suhbatga tayyorman, aloqadaman."
            sent = await send_message_as_user(hr_username, ready_msg, show_typing=True)
            cb_msg_id = callback_query.get("message", {}).get("message_id")
            if cb_msg_id:
                await edit_telegram_reply_markup(cb_chat_id, cb_msg_id, {
                    "inline_keyboard": [
                        [{"text": "☑️ \"Tayyorman\" yuborildi", "callback_data": "noop_hr_sent"}]
                    ]
                })
            if sent:
                await send_telegram_message(
                    f"✅ <b>HR @{hr_username} ga \"Tayyorman\" deb xabar yuborildi!</b>\n\n"
                    f"<i>\"{ready_msg}\"</i>",
                    chat_id=cb_chat_id
                )
            else:
                await send_telegram_message(
                    f"❌ <b>HR @{hr_username} ga xabar yuborishda xatolik yuz berdi.</b> Userbot holatini tekshiring.",
                    chat_id=cb_chat_id
                )
            return

    message = update.get("message")
    if not message:
        return
        
    chat_id = str(message.get("chat", {}).get("id"))
    text = message.get("text", "")
    caption = message.get("caption", "")
    voice = message.get("voice") or message.get("audio")
    is_voice_input = bool(voice)

    # 0. Agar ovozli xabar kelsa — Gemini orqali eshitib, matnga aylantirish (Voice-to-Voice)
    if voice:
        file_id = voice.get("file_id")
        await send_telegram_message(
            "🎙 <i>Ovozli xabaringiz qabul qilindi, eshitilmoqda...</i>",
            chat_id=chat_id
        )
        audio_bytes = await download_telegram_file(file_id)
        if audio_bytes:
            from ai_analyzer import transcribe_voice_with_gemini
            transcribed = await transcribe_voice_with_gemini(audio_bytes, mime_type="audio/ogg")
            if transcribed:
                text = transcribed.strip()
                await send_telegram_message(
                    f"🎙 <b>Eshitildi:</b> <i>\"{text}\"</i>",
                    chat_id=chat_id
                )
            else:
                await send_telegram_message(
                    "⚠️ <b>Ovozingizni aniqlab bo'lmadi.</b> Iltimos, aniqroq gapirib qayta yuboring.",
                    chat_id=chat_id
                )
                return
        else:
            await send_telegram_message(
                "❌ <i>Ovozli xabarni Telegramdan yuklab bo'lmadi. Iltimos, qayta yuboring.</i>",
                chat_id=chat_id
            )
            return

    # Agar rasm yoki skrinshot izohida matn bo'lsa
    if not text and caption:
        text = caption

    # Forward (boshqa kanaldan uzatilgan) xabar ekanligini aniqlash
    is_forward = bool(
        message.get("forward_date") or 
        message.get("forward_from_chat") or 
        message.get("forward_from") or 
        message.get("forward_sender_name")
    )
    
    # 1. /start buyrug'i
    if text == "/start":
        welcome_text = (
            "🤖 <b>Assalomu alaykum! Men Alfred — Sizning shaxsiy AI Karyera va Vakansiya Agentingizman.</b>\n\n"
            "Men 24/7 rejimda internetdan quyidagi manbalar bo'yicha ishlar va frilans buyurtmalarni qidiraman:\n"
            "• 🌐 <b>HH.UZ</b> (Bugungi yangi e'lonlar)\n"
            "• 📦 <b>OLX.UZ</b> (Bugungi yangi frilans buyurtmalar)\n"
            "• 📱 <b>Telegram kanallar</b> (Ustoz-Shogird, JobLink, Rizqim, Freelance va b.)\n"
            "• 🎯 <b>Tajriba bo'yicha:</b> Tajribasiz (No experience / Без опыта), Stajyor va Junior vakansiyalar\n\n"
            "<b>Mavjud buyruqlar va imkoniyatlar:</b>\n"
            "👤 <b>/add_HR @username [Kompaniya]</b> — HR ni kuzatuvga olish (Telethon orqali jonli kuzatuv)\n"
            "👥 <b>/hr_list</b> — Kuzatuvdagi barcha HR lar va Userbot holati\n"
            "💼 <b>/my_jobs</b> — Topshirgan ishlaringiz tarixi va holati\n"
            "📢 <b>/channels</b> — Kuzatilayotgan kanallar ro'yxati\n"
            "➕ <b>/add_channel &lt;kanal&gt;</b> — Yangi kanal qo'shish\n"
            "➖ <b>/remove_channel &lt;kanal&gt;</b> — Kanalni o'chirish\n"
            "🔑 <b>/keywords</b> — Qidiruv kalit so'zlarini boshqarish\n"
            "🔍 <b>/scan</b> — Bugungi yangi vakansiyalarni hoziroq skanerlash\n"
            "🌱 <b>/tajribasiz</b> — Faqat tajribasiz (0 yil) va stajyorlik vakansiyalarini qidirish\n"
            "📞 <b>/call</b> — Qo'ng'iroqni test qilish\n"
            "📊 <b>/status</b> — Tizim va AI holatini tekshirish\n\n"
            "🎙 <b>Ovozli xabar:</b> Menga xohlagan vaqt ovozli xabar yuborishingiz yoki savol berishingiz mumkin!"
        )
        await send_telegram_message(welcome_text, chat_id=chat_id)
        return

    # 2. Telegram Audio qo'ng'iroq va Taymerlar (Darhol yoki Aytilgan vaqtda)
    call_triggers = [
        "menga telefon qil", "telefon qil", "telefon qilib", "qo'ng'iroq qilvor", 
        "qongiroq qilvor", "qo'ng'iroq qil", "qongiroq qil", "gudok tashla", 
        "gudok ber", "uyg'ot"
    ]
    lower_t = text.lower().strip()
    is_call_req = (text in ["/test_call", "/call", "/testcall", "call", "Call"]) or any(ct in lower_t for ct in call_triggers)

    if is_call_req:
        from userbot import parse_custom_call_time, make_telegram_call_detailed
        from database import add_custom_call_timer
        from backup_manager import schedule_db_backup

        target_dt, time_disp = parse_custom_call_time(text)
        if target_dt:
            # Aniq vaqt aytilgan bo'lsa -> Custom Call Timer (#ALFRED_DB_BACKUP)
            add_custom_call_timer(target_dt.isoformat(), reason=text)
            schedule_db_backup()
            await send_telegram_message(
                f"⏰ <b>Qo'ng'iroq taymeri o'rnatildi!</b>\n\n"
                f"📞 Sizga soat <b>{time_disp}</b> da 2-chi akkaunt orqali Telegramdan qo'ng'iroq qilaman.",
                chat_id=chat_id
            )
            return
        else:
            # Vaqt aytilmagan bo'lsa -> DARHOL 2-AKKAUNT AUDIO QO'NG'IROG'I (1 soniyada)
            call_success, call_info = await make_telegram_call_detailed("Behruzbek, so'rovingiz bo'yicha qo'ng'iroq qilyapman!", force=True)
            if call_success:
                caller_tag = call_info or "2-chi akkaunt"
                live_url = "https://alfred-job-agent.onrender.com/live"
                reply_markup = {
                    "inline_keyboard": [
                        [
                            {"text": "📞 Alfred bilan Jonli Gaplashish", "web_app": {"url": live_url}}
                        ],
                        [
                            {"text": "🌐 Jonli Oyna (Brauzer)", "url": live_url}
                        ]
                    ]
                }
                await send_telegram_message(
                    f"📲 <b>2-chi akkauntdan ({caller_tag}) sizning Telegramingizga audio qo'ng'iroq yuborildi!</b>\n"
                    "Qo'ng'iroq 20 soniya davomida jiringlab turadi.\n\n"
                    "💡 <i>Go'shakni ko'tarsangiz yoki quyidagi tugmani bossangiz, Alfred bilan to'g'ridan-to'g'ri ovozda suhbatlashishingiz mumkin:</i>",
                    chat_id=chat_id,
                    reply_markup=reply_markup
                )
            else:
                await send_telegram_message(
                    f"❌ <b>Qo'ng'iroq qilishda xatolik yuz berdi:</b>\n"
                    f"<code>{call_info}</code>",
                    chat_id=chat_id
                )
            return

    # 2.1. /reload buyrug'i (Serverni o'chirmasdan 2 soniyada hot-reload)
    if text in ["/reload", "reload", "Reload"]:
        from backup_manager import hot_reload_all
        res_info = await hot_reload_all()
        await send_telegram_message(
            f"✅ <b>Server o'chirilmasdan barcha qoidalar va mantiq 2 soniyada yangilandi! (Zero-Redeploy)</b>\n\n<i>{res_info}</i>",
            chat_id=chat_id
        )
        return
        
    # 3. /status buyrug'i (Oxirgi xatolik va AI holatini ko'rsatish)
    if text == "/status":
        recent = get_recent_vacancies(limit=5)
        last_err = get_last_error()
        status_text = (
            "🟢 <b>Alfred 24/7 Agent Holati:</b> Faol\n\n"
            f"🤖 <b>AI Tizimi (Gemini) holati:</b>\n<i>{last_err}</i>\n\n"
            f"📊 <b>Bazada saqlangan ishlar soni:</b> {len(recent)}\n"
        )
        if recent:
            status_text += "\n<b>Oxirgi topilgan 5 ta vakansiya:</b>\n"
            for r in recent:
                status_text += f"• {r.get('title')} ({r.get('match_score')}%) - <a href='{r.get('url')}'>Havola</a>\n"
        else:
            status_text += "\nHali birorta yangi vakansiya qayd etilmadi."
            
        await send_telegram_message(status_text, chat_id=chat_id)
        return

    # 3.0. /health buyrug'i (Server va servislar sog'lig'ini tekshirish)
    if text in ["/health", "health", "Health"]:
        recent = get_recent_vacancies(limit=1)
        last_err = get_last_error()
        health_msg = (
            "🟢 <b>Server Holati: Sog'lom (Healthy)</b>\n\n"
            f"🤖 <b>AI Tizimi (Gemini):</b> {last_err}\n"
            f"📊 <b>Ma'lumotlar bazasi:</b> Faol ({len(recent)} ta vakansiya saqlangan)\n"
            f"⚡ <b>Telethon Userbot:</b> Faol\n"
            f"⏱ <b>Vaqt:</b> {datetime.now(TASHKENT_TZ).strftime('%d-%m-%Y %H:%M')}"
        )
        await send_telegram_message(health_msg, chat_id=chat_id)
        return

    # 3.1. /add_HR @username [Kompaniya] buyrug'i (Google Calendar'siz, SQLite va Userbot orqali)
    if text and (text.startswith("/add_HR") or text.startswith("/add_hr")):
        parts = text.split()
        if len(parts) < 2:
            await send_telegram_message(
                "ℹ️ <b>Foydalanish:</b> <code>/add_HR @username [Kompaniya]</code>\n"
                "<i>Masalan:</i> <code>/add_HR @it_recruiter EPAM</code>",
                chat_id=chat_id
            )
            return

        hr_user = parts[1].strip().lstrip("@").lower()
        company = " ".join(parts[2:]).strip() if len(parts) > 2 else ""

        from database import is_forbidden_hr_username
        if is_forbidden_hr_username(hr_user):
            await send_telegram_message(
                f"⚠️ <b>@{hr_user} — bu bot yoki tizim akkaunti!</b>\n"
                "Botlarni HR sifatida qo'shish qat'iyan taqiqlangan (cheksiz loop xavfi oldini olish uchun).",
                chat_id=chat_id
            )
            return

        # Bazaga saqlash
        add_tracked_hr(hr_user, company=company)
        asyncio.create_task(sync_hr_last_seen_silently(hr_user))

        resp_text = (
            f"✅ <b>HR @{hr_user} ({company or 'Kompaniya'}) kuzatuvga olindi!</b>\n\n"
            f"• <b>Jonli kuzatuv:</b> HR sizga shaxsiy Telegramingizda yozishi bilan avtomatik qo'ng'iroq / signal beraman!\n"
            f"• <b>3 kundan keyin:</b> Eslatma (follow-up) yozish taklifini chiqaraman\n"
            f"• <b>7 kundan keyin:</b> Javob bermasa lichkadan o'chirishni so'rayman\n\n"
            f"💡 <i>Telethon Userbot orqali 24/7 nazorat qilinadi.</i>"
        )
        await send_telegram_message(resp_text, chat_id=chat_id)
        return

    # 3.15. /hr_list buyrug'i (Kuzatuvdagi barcha HR lar va Userbot holati)
    if text and text in ["/hr_list", "/hrs", "hr_list"]:
        from userbot import is_userbot_connected, get_userbot_me
        is_conn = is_userbot_connected()
        me = await get_userbot_me()
        me_info = f" (@{me.username})" if me and me.username else ""
        tracked = get_all_tracked_hrs()

        msg = (
            f"⚡ <b>Telethon Userbot Holati:</b> {'🟢 Ulangan' if is_conn else '🔴 Ulanmagan'}{me_info}\n"
            f"<b>Userbot Connected:</b> <code>{is_conn}</code>\n\n"
            f"👥 <b>Kuzatilayotgan BARCHA HR'lar soni:</b> {len(tracked)} ta\n\n"
        )
        if tracked:
            for idx, h in enumerate(tracked, 1):
                status_icon = "⏳ Kutilyapti"
                st = h.get("status", "waiting")
                if st == "replied":
                    status_icon = "✅ Javob yozgan"
                elif st == "reminder_sent":
                    status_icon = "⏰ 3 kunlik eslatma berilgan"
                elif st == "prompted_delete":
                    status_icon = "⚠️ 7 kun o'tgan"

                u = h.get("username", "")
                c = h.get("company") or "Kompaniya ko'rsatilmagan"
                dt = str(h.get("added_at", ""))[:16]
                last_m = h.get("last_message", "")
                last_id = h.get("last_seen_msg_id", 0)
                last_preview = f"\n   💬 <i>\"{last_m[:50]}...\"</i>" if last_m else ""
                msg += f"{idx}. <b>@{u}</b> ({c}) — {status_icon}\n   📅 Qo'shildi: <i>{dt}</i> | Xabar ID: {last_id}{last_preview}\n\n"
            msg += "<i>Kuzatuvdan o'chirish:</i> <code>/remove_HR @username</code>"
        else:
            msg += "<i>Hozircha birorta HR kuzatuvga olinmagan.\nQo'shish:</i> <code>/add_HR @username [Kompaniya]</code>"
        await send_telegram_message(msg, chat_id=chat_id)
        return

    # 3.16. /remove_HR @username buyrug'i
    if text and (text.startswith("/remove_HR") or text.startswith("/remove_hr") or text.startswith("/del_hr")):
        parts = text.split()
        if len(parts) < 2:
            await send_telegram_message(
                "ℹ️ <b>Foydalanish:</b> <code>/remove_HR @username</code>\n"
                "<i>Masalan:</i> <code>/remove_HR @recruiter_user</code>",
                chat_id=chat_id
            )
            return

        hr_user = parts[1].strip().lstrip("@").lower()
        delete_tracked_hr(hr_user)
        await send_telegram_message(
            f"🗑 <b>HR @{hr_user} kuzatuv ro'yxatidan muvaffaqiyatli o'chirildi!</b>",
            chat_id=chat_id
        )
        return

    # 3.2. /my_jobs buyrug'i (Topshirilgan ishlar Mini-CRM)
    if text == "/my_jobs":
        jobs = get_applied_jobs(limit=15)
        if not jobs:
            await send_telegram_message(
                "💼 <b>Topshirilgan ishlar:</b>\n\nHozircha birorta vakansiyaga topshirmagansiz. Vakansiya ostidagi <b>[✅ Topshirdim]</b> tugmasini bosib, ularni bu yerda kuzatib borishingiz mumkin!",
                chat_id=chat_id
            )
            return

        jobs_text = f"💼 <b>Topshirilgan ishlar tarixi ({len(jobs)} ta):</b>\n\n"
        for idx, j in enumerate(jobs, 1):
            date_str = str(j.get("applied_at", ""))[:16]
            jobs_text += (
                f"{idx}. <b>{j.get('title')}</b> ({j.get('company') or 'Kompaniya'})\n"
                f"   📅 Topshirildi: <i>{date_str}</i>\n"
                f"   🔗 <a href='{j.get('url')}'>E'lon havolasi</a>\n\n"
            )
        await send_telegram_message(jobs_text, chat_id=chat_id)
        return

    # 3.3. /channels buyrug'i (Kanallar ro'yxati)
    if text == "/channels":
        managed = get_managed_channels()
        ch_text = "📢 <b>Kuzatilayotgan barcha kanallar:</b>\n\n"
        ch_text += "<b>Standart kanallar:</b>\n"
        for ch in CHANNEL_DISPLAY_NAMES.values():
            ch_text += f"• {ch}\n"

        if managed:
            ch_text += "\n<b>Qo'shilgan shaxsiy kanallar:</b>\n"
            for m in managed:
                ch_text += f"• @{m}\n"
        else:
            ch_text += "\n<i>Yangi kanal qo'shish uchun:</i> <code>/add_channel @kanal_nomi</code>"

        await send_telegram_message(ch_text, chat_id=chat_id)
        return

    # 3.4. /add_channel buyrug'i
    if text and text.startswith("/add_channel"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await send_telegram_message("ℹ️ <b>Foydalanish:</b> <code>/add_channel @kanal_nomi</code> yoki <code>/add_channel t.me/kanal_nomi</code>", chat_id=chat_id)
            return
        ch_to_add = parts[1].strip()
        add_managed_channel(ch_to_add)
        await send_telegram_message(f"✅ <b>Kanal muvaffaqiyatli qo'shildi:</b> <code>@{ch_to_add.lstrip('@')}</code>", chat_id=chat_id)
        return

    # 3.5. /remove_channel buyrug'i
    if text and text.startswith("/remove_channel"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await send_telegram_message("ℹ️ <b>Foydalanish:</b> <code>/remove_channel @kanal_nomi</code>", chat_id=chat_id)
            return
        ch_to_rem = parts[1].strip()
        remove_managed_channel(ch_to_rem)
        await send_telegram_message(f"🗑 <b>Kanal o'chirildi:</b> <code>@{ch_to_rem.lstrip('@')}</code>", chat_id=chat_id)
        return

    # 3.6. /keywords buyrug'i
    if text == "/keywords":
        kws = get_managed_keywords()
        kw_text = "🔑 <b>Qidiruv kalit so'zlari:</b>\n\n"
        kw_text += "<b>Standart:</b> Python, Django, FastAPI, AI, Backend, Telegram bot, Web scraping, Junior, Tajribasiz (No experience)\n"
        if kws:
            kw_text += "\n<b>Qo'shilgan maxsus kalit so'zlar:</b>\n"
            for k in kws:
                kw_text += f"• {k}\n"
        kw_text += "\n<i>Kalit so'z qo'shish:</i> <code>/add_keyword so'z</code>\n<i>O'chirish:</i> <code>/remove_keyword so'z</code>"
        await send_telegram_message(kw_text, chat_id=chat_id)
        return

    # 3.7. /add_keyword va /remove_keyword
    if text and text.startswith("/add_keyword"):
        parts = text.split(maxsplit=1)
        if len(parts) >= 2:
            add_managed_keyword(parts[1])
            await send_telegram_message(f"✅ <b>Kalit so'z qo'shildi:</b> <code>{parts[1]}</code>", chat_id=chat_id)
            return

    if text and text.startswith("/remove_keyword"):
        parts = text.split(maxsplit=1)
        if len(parts) >= 2:
            remove_managed_keyword(parts[1])
            await send_telegram_message(f"🗑 <b>Kalit so'z o'chirildi:</b> <code>{parts[1]}</code>", chat_id=chat_id)
            return
        
    # 4.4. BOSHQA KANALDAN UZATILGAN (FORWARD) XABAR KELGANDA
    if is_forward and text and len(text) > 15:
        logger.info(f"Forward qilingan vakansiya aniqlandi ({len(text)} belgi)")
        await send_telegram_message(
            "📨 <b>Uzatilgan (forward) e'lon qabul qilindi!</b>\n"
            "🤖 <i>Behruzbek Bohodirovning tajribasi asosida ushbu vakansiya uchun tayyor Cover Letter va PDF Rezyume shakllantirilmoqda...</i>",
            chat_id=chat_id
        )
        docs = await generate_tailored_resume_and_pitch(text, user_instruction="Forward qilingan Telegram e'loni")
        await send_telegram_message(docs, chat_id=chat_id)
        try:
            pdf_path = generate_pdf_resume(
                tailored_summary="Python Backend & AI Developer. Microservices, FastAPI, Django, PostgreSQL, Docker, and Autonomous AI systems.",
                target_job_title="Forwarded Vacancy"
            )
            await send_telegram_document(
                pdf_path,
                caption="📄 <b>Behruzbek_Bohodirov_CV.pdf</b>\n<i>Uzatilgan e'longa moslashtirildi.</i>",
                chat_id=chat_id
            )
        except Exception as e_pdf:
            logger.error(f"Forward PDF yaratishda xato: {e_pdf}")
        return

    # 4.5. ANIQ TELEGRAM POST SSILKASI KELGANDA (Single Post Parser + Maxsus Rezyume / Cover Letter)
    # Masalan: https://t.me/UstozShogird/45810 shu vakansiyaga rezyume qilibber
    if text:
        post_match = re.search(r"(?:https?://)?t\.me/(?:s/)?([a-zA-Z0-9_]{3,32})/(\d+)", text, re.IGNORECASE)
        if post_match:
            target_channel = post_match.group(1)
            target_post_id = post_match.group(2)
            logger.info(f"Aniq Telegram post ssilkasi aniqlandi: @{target_channel}/{target_post_id}")

            await send_telegram_message(
                f"🔎 <b>Aniq e'lon posti aniqlandi:</b> <code>@{target_channel}/{target_post_id}</code>\n"
                f"⏳ E'lon matni to'g'ridan-to'g'ri yuklab olinmoqda va tahlil qilinmoqda...",
                chat_id=chat_id
            )

            post_text = await scrape_telegram_single_post(target_channel, target_post_id)
            if not post_text:
                await send_telegram_message(
                    f"❌ <b>Post matnini yuklab bo'lmadi!</b>\n"
                    f"Post manzili: <code>t.me/{target_channel}/{target_post_id}</code>\n"
                    f"<i>(Kanal yopiq bo'lishi yoki post o'chirilgan bo'lishi mumkin)</i>",
                    chat_id=chat_id
                )
                return

            await send_telegram_message(
                f"✅ <b>Post matni muvaffaqiyatli olindi!</b>\n"
                f"🤖 <i>Behruzbek Bohodirovning tajribasi va loyihalari asosida ushbu vakansiya talablariga 100% moslashtirilgan Rezyume, Cover Letter va HR kontakti tayyorlanmoqda...</i>",
                chat_id=chat_id
            )

            # Gemini orqali maxsus Rezyume, Cover Letter va HR kontaktini tayyorlash
            docs = await generate_tailored_resume_and_pitch(post_text, user_instruction=text)
            await send_telegram_message(docs, chat_id=chat_id)

            # Maxsus dizaynli PDF Rezyumeni generatsiya qilish va Telegram orqali yuborish
            try:
                pdf_path = generate_pdf_resume(
                    tailored_summary="Python Backend & AI Developer. Microservices, FastAPI, Django, PostgreSQL, Docker, and Autonomous AI systems.",
                    target_job_title=f"Vacancy @{target_channel}/{target_post_id}"
                )
                await send_telegram_document(
                    pdf_path,
                    caption=f"📄 <b>Behruzbek_Bohodirov_CV.pdf</b>\n<i>Ushbu e'lon (@{target_channel}/{target_post_id}) uchun maxsus tayyorlandi.</i>",
                    chat_id=chat_id
                )
            except Exception as e_pdf:
                logger.error(f"Single post PDF rezyume yaratishda xatolik: {e_pdf}")
            return

    # 5. Aniq bir kanal/guruh bo'yicha qidiruv so'ralganmi? (@ bilan yoki oddiy nomi bilan)
    if text:
        channel_query = resolve_channel_query(text)
        if channel_query:
            channel_name, display_name = channel_query
            asyncio.create_task(search_channel_vacancies(channel_name, display_name, chat_id=chat_id))
            return

    # 6. Umumiy /scan yoki 'tekshir'/'qidir' buyrug'i
    if text:
        lower_text = text.lower().strip()
        # 5.8. Tajribasiz / stajyorlik bo'yicha maxsus qidiruv
        if lower_text in ["/tajribasiz", "tajribasiz", "tajribasizlar", "tajribasiz ishlar", "tajribasiz vakansiyalar"] or \
           any(k in lower_text for k in ["tajribasiz ish", "tajribasiz vakansiya", "tajriba talab qilinmaydigan", "stajyorlik", "bez opita", "без опыта"]):
            await send_telegram_message(
                "🌱 <b>Tajriba bo'yicha qidiruv: Tajribasiz (No experience / Без опыта) va Stajyorlik!</b>\n"
                "Barcha manbalar (HH.uz <code>noExperience</code> filtri, Telegram kanallar va OLX) bo'yicha tajribasiz va boshlang'ich e'lonlar qidirilmoqda...",
                chat_id=chat_id
            )
            if trigger_scan_callback:
                asyncio.create_task(trigger_scan_callback())
            return

        if lower_text in ["/scan", "tekshir", "qidir", "hamma vakansiyalar", "barcha vakansiyalar"] or \
           any(cmd in lower_text for cmd in ["vakansiyalarni tekshir", "ishlarni qidir", "yangi ishlarni tekshir", "hamma ishlarni ko'r"]):
            await send_telegram_message(
                "🔎 <b>Bugungi yangi vakansiyalarni qidirish fonda boshlandi...</b>\n"
                "Barcha Telegram kanallar va HH.uz tekshirilmoqda. Yangi e'lonlar sun'iy intellekt orqali tahlil qilinadi. Mos vakansiyalar topilsa darhol xabar va ovozli xabar beraman!",
                chat_id=chat_id
            )
            if trigger_scan_callback:
                asyncio.create_task(trigger_scan_callback())
            return

    # 6.5. Buyruqlar himoyasi (boshqa noma'lum / buyruqlar AI chatga tushmasligi uchun)
    if text and text.startswith("/"):
        await send_telegram_message(
            "⚠️ <b>Noma'lum buyruq!</b>\n\n"
            "Mavjud buyruqlar:\n"
            "• /start — Tizim imkoniyatlari\n"
            "• /scan — Bugungi vakansiyalarni hoziroq qidirish\n"
            "• /status — Tizim va AI holati\n"
            "• /health — Server va servislar holati\n"
            "• /my_jobs — Topshirilgan ishlar tarixi\n"
            "• /channels — Kuzatilayotgan kanallar\n"
            "• /keywords — Qidiruv kalit so'zlari\n"
            "• /add_HR @username Kompaniya — HR kuzatuvga olish\n"
            "• /hr_list — Kuzatuvdagi barcha HR lar\n"
            "• /call — Qo'ng'iroqni sinab ko'rish",
            chat_id=chat_id
        )
        return

    # 6.9. HR bilan muloqot va vaqtni aniqlash so'rovi (Reply yoki 'HR bilan gaplashdim / vaqtini aniqqil')
    if text:
        handled_hr = await handle_user_hr_discussion(message, chat_id=chat_id)
        if handled_hr:
            return

    # 7. Oddiy matnli yoki ovozli so'rov bo'lsa (Gemini orqali suhbat, vakansiyalar tahlili va ovozli javob)
    if text:
        logger.info(f"Foydalanuvchi xabari: {text} (ovozdan kelgan: {is_voice_input})")
        lower_msg = text.lower().strip()

        # Ovozli xabar (voice on demand) so'ralganmi?
        voice_triggers = [
            "ovoz", "galasovoy", "golosovoy", "audio", "gapir", 
            "o'qib ber", "oqib ber", "ovozli", "ovozda", "audioda", 
            "eshittir", "tushuntir", "gapirib ber", "aytib ber", "ayt"
        ]
        wants_voice = is_voice_input or any(vt in lower_msg for vt in voice_triggers)

        # Har doim bazadagi bugungi real vakansiyalarni olamiz (yolg'on to'qishni oldini olish uchun)
        vacancies = get_today_vacancies(limit=5)
        if not vacancies:
            vacancies = await ensure_real_vacancies(limit=5)

        vacancies_context = ""
        if vacancies:
            vacancies_context_parts = []
            for idx, vac in enumerate(vacancies[:5], 1):
                vacancies_context_parts.append(
                    f"#{idx}. ID: {vac.get('id')}\n"
                    f"   Lavozim: {vac.get('title')}\n"
                    f"   Kompaniya/Manba: {vac.get('company', 'Kompaniya')} ({vac.get('source', '')})\n"
                    f"   Maosh / Byudjet: {vac.get('salary') or 'Kelishilgan'}\n"
                    f"   Moslik bali: {vac.get('match_score', 0)}%\n"
                    f"   Turi: {vac.get('job_type', 'vacancy')}\n"
                    f"   Tavsif / Xulosa: {vac.get('ai_summary', '')}\n"
                    f"   Haqiqiy Havola: {vac.get('url', '')}"
                )
            vacancies_context = "\n\n".join(vacancies_context_parts)

        # Suhbat tarixi (oxirgi 10 ta xabar)
        history = chat_histories.setdefault(chat_id, [])

        # Universal ReAct Agent orqali javob va asboblarni ishga tushirish
        try:
            ai_reply = await run_gemini_react_agent(
                user_message=text,
                chat_id=chat_id,
                history=history,
                is_voice=is_voice_input
            )
        except Exception as e_react:
            logger.warning(f"ReAct agent xatoligi, zaxira chat_with_alfred ga o'tiladi: {e_react}")
            ai_reply = await chat_with_alfred(
                user_message=text,
                vacancies_context=vacancies_context,
                wants_voice=wants_voice,
                history=history
            )

        # Tarixni yangilash
        history.append({"role": "user", "text": text})
        history.append({"role": "model", "text": ai_reply})
        if len(history) > 10:
            chat_histories[chat_id] = history[-10:]

        # Vakansiyani aniqlash va inline tugmalarni tayyorlash
        target_vac = None
        if vacancies:
            for v in vacancies:
                v_url = v.get("url", "")
                v_id = str(v.get("id", ""))
                v_title = (v.get("title") or "").lower()
                v_comp = (v.get("company") or "").lower()
                if (v_url and v_url in ai_reply) or (v_id and v_id in ai_reply):
                    target_vac = v
                    break
                if v_title and len(v_title) > 3 and v_title in ai_reply.lower():
                    target_vac = v
                    break
                if v_comp and len(v_comp) > 3 and v_comp in ai_reply.lower():
                    target_vac = v
                    break

            if not target_vac:
                vacancy_triggers = ["vakansiya", "ish", "rabota", "job", "loyiha", "buyurtma", "taklif", "eng mos", "qaysi", "ssilka", "link", "haqida", "qayerda", "rezyume"]
                if any(vt in lower_msg for vt in vacancy_triggers):
                    target_vac = vacancies[0]

        reply_markup = None
        if target_vac:
            vac_id = str(target_vac.get("id"))
            vac_url = target_vac.get("url", "https://t.me")
            reply_markup = {
                "inline_keyboard": [
                    [
                        {"text": "📝 Matn + 📄 PDF Rezyume", "callback_data": f"resume_bundle_{vac_id}"},
                        {"text": "🎯 Suhbat savollari", "callback_data": f"interview_prep_{vac_id}"}
                    ],
                    [
                        {"text": "✅ Topshirdim", "callback_data": f"applied_{vac_id}"},
                        {"text": "➕ HR'ni kuzatuvga olish", "callback_data": f"track_hr_{vac_id}"}
                    ],
                    [
                        {"text": "🔗 E'lonni ochish", "url": vac_url}
                    ]
                ]
            }

        if wants_voice:
            # 1. Ovozli xabar yaratish va yuborish
            voice_file = await generate_voice_message(ai_reply, prepare_script=True)
            if voice_file:
                caption = "🎙 <b>Alfred — Ovozli Javob</b>"
                if target_vac:
                    caption = f"🎙 <b>{target_vac.get('title')}</b> ({target_vac.get('match_score', 0)}% moslik)"
                await send_telegram_voice(voice_file, caption=caption, chat_id=chat_id)

            # 2. To'liq professional matn va tugmalarni yuborish
            await send_telegram_message(ai_reply, chat_id=chat_id, reply_markup=reply_markup)
        else:
            # Oddiy matnli javob va tegishli vakansiya tugmalari
            await send_telegram_message(ai_reply, chat_id=chat_id, reply_markup=reply_markup)


async def run_telegram_polling(trigger_scan_callback=None):
    """
    Telegram Bot API dan xabarlarni getUpdates (long polling) orqali fonda uzluksiz o'qib borish.
    Bu FastAPI serveri bilan parallel fonda (asyncio task) 24/7 ishlaydi.
    """
    if not TELEGRAM_BOT_TOKEN:
        logger.warning("TELEGRAM_BOT_TOKEN topilmadi, polling ishga tushmadi.")
        return

    # Avval webhook faol bo'lsa uni o'chiramiz (Conflict xatoligi bo'lmasligi uchun)
    delete_wh_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/deleteWebhook?drop_pending_updates=false"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.get(delete_wh_url)
            logger.info("Eski Telegram webhook tozalandi, Polling rejimiga o'tildi.")
    except Exception as e:
        logger.warning(f"deleteWebhook bajarishda ogohlantirish: {e}")

    offset: Optional[int] = None
    poll_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    
    logger.info("🟢 Telegram Bot Polling (uzluksiz qabul qilish) fonda ishga tushdi!")

    async with httpx.AsyncClient(timeout=35) as client:
        while True:
            try:
                params = {"timeout": 25}
                if offset is not None:
                    params["offset"] = offset

                resp = await client.get(poll_url, params=params)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("ok"):
                        updates = data.get("result", [])
                        for update in updates:
                            offset = update["update_id"] + 1
                            # Har bir xabarni async task sifatida parallel qayta ishlash
                            asyncio.create_task(
                                process_telegram_update(update, trigger_scan_callback=trigger_scan_callback)
                            )
                elif resp.status_code == 409:
                    logger.warning("Telegram 409 Conflict: boshqa instansiya faol, 5 soniya kutamiz...")
                    await asyncio.sleep(5)
                else:
                    logger.warning(f"getUpdates status {resp.status_code}: {resp.text[:100]}")
                    await asyncio.sleep(3)
            except asyncio.CancelledError:
                logger.info("Telegram Polling to'xtatildi.")
                break
            except Exception as e:
                logger.error(f"Telegram polling loop xatoligi: {e}")
                await asyncio.sleep(3)

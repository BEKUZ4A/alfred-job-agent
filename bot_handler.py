import logging
import asyncio
import httpx
import re
from typing import Dict, Any, Optional, List
from config import TELEGRAM_BOT_TOKEN, MIN_MATCH_SCORE
from ai_analyzer import chat_with_alfred, get_last_error, batch_analyze_vacancies, transcribe_voice_with_gemini, generate_freelance_pitch
from notifier import send_telegram_message, send_telegram_voice, format_vacancy_card, make_call
from scraper import scrape_telegram_channel, scrape_hh_uz
from database import get_recent_vacancies, mark_vacancy_seen, get_vacancy_by_id, get_top_vacancies
from voice import generate_voice_message

logger = logging.getLogger("Alfred.BotHandler")

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
        "jobforjunior", "job for junior", "junior jobs", "job for juniors", "junior job", "jobjunior", "junior"
    ],
    "freelance_uzb": [
        "freelance", "freelance uzb", "freelance_uzb", "frilans", "frilans uzb", "freelancer", "freelans"
    ],
    "young_june": [
        "young june", "young_june", "young júnior", "young junior", "yang jyun"
    ],
    "young_intern": [
        "young intern", "young_intern", "youngintern", "intern", "stajyor", "amaliyot"
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

    # 2. Gemini orqali tahlil qilish
    analysis_results = await batch_analyze_vacancies(vacancies[:10])
    
    matches = []
    for vac in vacancies:
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
                ai_summary=analysis.get("short_summary", "")
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

    message = update.get("message")
    if not message:
        return
        
    chat_id = str(message.get("chat", {}).get("id"))
    text = message.get("text", "")
    voice = message.get("voice") or message.get("audio")
    is_voice_input = bool(voice)
    
    # 1. /start buyrug'i
    if text == "/start":
        welcome_text = (
            "🤖 <b>Assalomu alaykum! Men Alfred — Sizning shaxsiy AI Vakansiya va Frilans Agentingizman.</b>\n\n"
            "Men 24/7 rejimda internetdan quyidagi manbalar bo'yicha ishlar va frilans buyurtmalarni qidiraman:\n"
            "• 🌐 <b>HH.UZ</b> (Rasmiy API orqali eng so'nggi vakansiyalar)\n"
            "• 📦 <b>OLX.UZ</b> (Yangi frilans buyurtmalar va ishlar)\n"
            "• 📱 <b>9 ta Telegram kanal</b> (Ustoz-Shogird, JobLink, Rizqim, Freelance va b.)\n\n"
            "<b>Mavjud imkoniyatlar:</b>\n"
            "🎙 <b>Ovozli xabar:</b> Menga ovozli xabar yuboring! Men uni diqqat bilan eshitib, tushunib javob beraman.\n"
            "🏢 <b>Guruh nomi bo'yicha qidirish:</b> Istalgan guruh nomini @ belgisisiz ham aytsangiz bo'ladi (masalan: <i>'JobLink'</i>, <i>'Ustoz Shogird'</i>, <i>'Rizqim'</i>, <i>'Freelance'</i>)\n"
            "📨 <b>Tijoriy taklif (Pitch):</b> Frilans buyurtma chiqsa, bir tugma bilan mijozga yuboriladigan taklif matnini tuzib beraman!\n"
            "🔍 /scan yoki 'tekshir' — Barcha manbalarni (HH + OLX + 9 kanal) parallel tekshirish\n"
            "📞 /call — CallMeBot qo'ng'irog'ini test qilish (10s retry va FLOOD himoyasi bilan)\n"
            "📊 /status — Tizim va AI holati\n"
            "💬 Menga istalgan savolingizni yozishingiz yoki gapirishingiz mumkin!"
        )
        await send_telegram_message(welcome_text, chat_id=chat_id)
        return

    # 2. /call yoki /testcall buyrug'i (CallMeBot qo'ng'irog'ini test qilish)
    if text in ["/call", "/testcall", "call", "Call"]:
        await send_telegram_message(
            "📞 <b>CallMeBot orqali qo'ng'iroq testi boshlandi...</b>\n"
            "Serverga so'rov yuborilmoqda. Agar server band bo'lsa (FLOOD), 10 soniyadan so'ng 1 marta qayta uriniladi (retry).",
            chat_id=chat_id
        )
        call_success = await make_call("Salom Behruz! Bu Alfred AI Agentidan test qo'ng'irog'i.", force=True)
        if call_success:
            await send_telegram_message(
                "✅ <b>Qo'ng'iroq muvaffaqiyatli amalga oshirildi!</b> Telegramingizga qo'ng'iroq borgan bo'lishi kerak.",
                chat_id=chat_id
            )
        else:
            await send_telegram_message(
                "🚨 <b>DIQQAT: Yangi vakansiya topildi, lekin qo'ng'iroq liniyasi bandligi sababli xabar yuborildi!</b>\n\n"
                "<i>(CallMeBot bepul serveri FLOOD xatosi berdi yoki liniya band. 10 soniya kutib qayta urinildi, ammo server javob bermadi)</i>",
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
        
    # 4. Ovozli xabar kelsa — Gemini orqali eshitib, matnga aylantirish (transkripsiya)
    if voice:
        file_id = voice.get("file_id")
        await send_telegram_message(
            "🎙 <i>Ovozli xabaringiz qabul qilindi, eshitilmoqda...</i>",
            chat_id=chat_id
        )
        audio_bytes = await download_telegram_file(file_id)
        if not audio_bytes:
            await send_telegram_message(
                "❌ <i>Ovozli xabarni Telegramdan yuklab bo'lmadi. Iltimos, qayta yuboring.</i>",
                chat_id=chat_id
            )
            return

        transcribed = await transcribe_voice_with_gemini(audio_bytes, mime_type="audio/ogg")
        if not transcribed:
            await send_telegram_message(
                "⚠️ <b>Ovozingizni aniqlab bo'lmadi.</b> Iltimos, aniqroq gapirib qayta yuboring yoki matn ko'rinishida yozing.",
                chat_id=chat_id
            )
            return

        await send_telegram_message(
            f"🎙 <b>Eshitildi:</b> <i>\"{transcribed}\"</i>",
            chat_id=chat_id
        )
        text = transcribed.strip()

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
        if lower_text in ["/scan", "tekshir", "qidir", "hamma vakansiyalar", "barcha vakansiyalar"] or \
           any(cmd in lower_text for cmd in ["vakansiyalarni tekshir", "ishlarni qidir", "yangi ishlarni tekshir", "hamma ishlarni ko'r"]):
            await send_telegram_message(
                "🔎 <b>Qidiruv orqa fonda tezkor boshlandi...</b>\n"
                "Barcha Telegram kanallar va HH.uz tekshirilmoqda. Barcha yangi e'lonlar birgalikda (batch) sun'iy intellekt orqali tahlil qilinadi. Mos vakansiyalar topilsa darhol xabar va ovozli xabar beraman!",
                chat_id=chat_id
            )
            if trigger_scan_callback:
                asyncio.create_task(trigger_scan_callback())
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

        # Real vakansiyalar haqida so'ralganmi?
        vacancy_triggers = [
            "vakansiya", "ish", "rabota", "job", "loyiha", "buyurtma", 
            "taklif", "eng mos", "mos keladigan", "qanday ishlar", 
            "qanaqa ishlar", "qaysi ish", "topilgan", "bor ishlar"
        ]
        is_vacancy_query = any(vt in lower_msg for vt in vacancy_triggers)

        vacancies_context = ""
        vacancies = []
        if is_vacancy_query:
            vacancies = await ensure_real_vacancies(limit=5)
            if vacancies:
                vacancies_context_parts = []
                for idx, vac in enumerate(vacancies[:5], 1):
                    vacancies_context_parts.append(
                        f"#{idx}. Lavozim: {vac.get('title')}\n"
                        f"   Kompaniya: {vac.get('company', 'Kompaniya')}\n"
                        f"   Maosh / Byudjet: {vac.get('salary') or 'Kelishilgan'}\n"
                        f"   Moslik bali: {vac.get('match_score', 0)}%\n"
                        f"   Turi: {vac.get('job_type', 'vacancy')}\n"
                        f"   Qisqa ma'lumot: {vac.get('ai_summary', '')}\n"
                        f"   Havola: {vac.get('url', '')}"
                    )
                vacancies_context = "\n\n".join(vacancies_context_parts)

        # Gemini javobini olish
        ai_reply = await chat_with_alfred(
            user_message=text,
            vacancies_context=vacancies_context,
            wants_voice=wants_voice
        )

        if wants_voice:
            # 1. Gemini javobini TTS orqali haqiqiy ovozli xabarga aylantirib yuborish
            voice_file = await generate_voice_message(ai_reply, prepare_script=False)
            if voice_file:
                caption = "🎙 <b>Alfred — Ovozli Javob</b>"
                if vacancies:
                    top_v = vacancies[0]
                    caption = f"🎙 <b>Eng mos taklif: {top_v.get('title')}</b> ({top_v.get('match_score', 0)}% moslik)"
                await send_telegram_voice(voice_file, caption=caption, chat_id=chat_id)
            else:
                # Agar TTS da kutilmagan uzilish bo'lsa, xabar yo'qolmasligi uchun matn qilib yuboramiz
                await send_telegram_message(ai_reply, chat_id=chat_id)

            # 2. Agar vakansiya so'ralgan bo'lsa, pastidan havolani matn ko'rinishida tashlash
            if vacancies:
                top_v = vacancies[0]
                source_badge = f"[{top_v.get('source', 'HH.UZ').upper()}]"
                job_type_str = str(top_v.get("job_type", "")).lower()
                type_badge = "[💰 FRILANS BUYURTMA]" if ("freelance" in job_type_str or "frilans" in job_type_str or "buyurtma" in job_type_str) else "[🏢 VAKANSIYA]"

                link_card = (
                    f"🔗 <b>Tavsiya etilgan eng mos vakansiya havolasi:</b>\n\n"
                    f"{type_badge} {source_badge} <b>{top_v.get('title')}</b>\n"
                    f"🏢 <b>Kompaniya / Buyurtmachi:</b> {top_v.get('company', 'Kompaniya')}\n"
                )
                if top_v.get("salary"):
                    link_card += f"💰 <b>Maosh / Byudjet:</b> {top_v.get('salary')}\n"
                link_card += (
                    f"🎯 <b>Moslik darajasi:</b> {top_v.get('match_score', 0)}%\n"
                    f"🌐 <b>Batafsil ko'rish / Topshirish:</b> <a href='{top_v.get('url')}'>{top_v.get('url')}</a>"
                )

                reply_markup = None
                if "freelance" in job_type_str or "frilans" in job_type_str or "buyurtma" in job_type_str:
                    reply_markup = {
                        "inline_keyboard": [
                            [{"text": "📨 Rezyume / Taklif yuborish", "callback_data": f"pitch:{top_v.get('id')}"}]
                        ]
                    }

                await send_telegram_message(link_card, chat_id=chat_id, reply_markup=reply_markup)
        else:
            # Oddiy matnli javob
            await send_telegram_message(ai_reply, chat_id=chat_id)

            # Agar vakansiya so'ralgan bo'lsa va javob ichida havola bo'lmasa, pastidan havolani qo'shib qo'yamiz
            if vacancies and "http" not in ai_reply:
                top_v = vacancies[0]
                link_card = (
                    f"\n🔗 <b>Eng mos ish havolasi:</b> <a href='{top_v.get('url')}'>{top_v.get('title')} ({top_v.get('company', 'Kompaniya')})</a>"
                )
                await send_telegram_message(link_card, chat_id=chat_id)


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

import httpx
import bs4
import hashlib
import logging
import asyncio
import re
import urllib.parse
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional
from curl_cffi import requests as cffi_requests
from config import TELEGRAM_CHANNELS, TASHKENT_TZ

logger = logging.getLogger("Alfred.Scraper")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json,*/*;q=0.8",
    "Accept-Language": "uz,ru;q=0.9,en;q=0.8"
}

HH_API_HEADERS = {
    "User-Agent": "AlfredJobAgent/1.0 (contact@alfredbot.uz)",
    "Accept": "application/json"
}

def clean_text(raw_text: str) -> str:
    """Matnni ortiqcha bo'shliq va teglardan tozalash"""
    if not raw_text:
        return ""
    return " ".join(raw_text.split())

def make_hash_id(source: str, identifier: str) -> str:
    """Har bir vakansiyaga yagona ID generatsiya qilish"""
    return hashlib.md5(f"{source}:{identifier}".encode("utf-8")).hexdigest()

def is_published_today(dt_str: str) -> bool:
    """
    QAT'IY TALAB: E'lon chiqqan sana aynan BUGUNGI KUN (Toshkent vaqti UTC+5)
    bilan mos kelishini tekshirish. Agar kechagi yoki oldingi kunlardagi post bo'lsa,
    darhol False qaytaradi va tashlab ketiladi.
    """
    if not dt_str:
        return False
    try:
        clean_str = dt_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt_tash = dt.astimezone(TASHKENT_TZ)
        now_tash = datetime.now(TASHKENT_TZ)
        return dt_tash.date() == now_tash.date()
    except Exception as e:
        logger.debug(f"Vaqtni tekshirishda xatolik ({dt_str}): {e}")
        return False

def is_within_last_24_hours(dt_str: str) -> bool:
    """Orqaga moslik uchun bugungi kun tekshiruvi"""
    return is_published_today(dt_str)

def format_posted_at(dt_str: str) -> str:
    """Vaqtni Toshkent vaqti (UTC+5) bo'yicha '🕒 Bugun, 14:30' shaklida qaytarish"""
    if not dt_str:
        return "Noma'lum"
    try:
        clean_str = dt_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt_tash = dt.astimezone(TASHKENT_TZ)
        now_tash = datetime.now(TASHKENT_TZ)
        if dt_tash.date() == now_tash.date():
            return f"🕒 Bugun, {dt_tash.strftime('%H:%M')}"
        return f"📅 {dt_tash.strftime('%d-%m-%Y %H:%M')}"
    except Exception:
        return dt_str if dt_str else "Noma'lum"

def is_seller_advertisement(text: str) -> bool:
    """
    Dasturchilarning o'z xizmatlarini reklama qilayotgan e'lonlarini filtrlab tashlash.
    Faqat haqiqiy ish beruvchi yoki mijoz yozgan e'lonlarni qoldiradi.
    """
    t = text.lower()
    
    # Ish beruvchi yoki buyurtmachi belgilari
    buyer_indicators = [
        "kerak", "qidirilmoqda", "qidiryapmiz", "izlayapmiz", "izlayapman",
        "yasatmoqchiman", "yozdirmoqchiman", "ishga taklif", "vakansiya", "hodim kerak",
        "нужен", "требуется", "ищем", "заказчик", "требуются", "#loyiha", "#hodim", "#ishbori", "#ishjoyi"
    ]
    if any(b in t for b in buyer_indicators):
        # Agar "kerak" yoki "izlayapmiz" bo'lsa, bu mijoz / ish beruvchi bo'lishi ehtimoli yuqori
        return False
        
    # Sotuvchi / Dasturchi o'z xizmatini reklama qilish belgilari
    seller_indicators = [
        "bot yasab beramiz", "bot yasab beraman", "sayt ochib beramiz", "sayt ochib beraman",
        "xizmatlarimiz", "xizmat korsatish", "xizmat ko'rsatamiz", "xizmat ko'rsatish",
        "sotiladi", "kino bot sotiladi", "arzon narxda",
        "услуги создания", "делаем на заказ", "разработка под ключ", "готовый бот"
    ]
    return any(s in t for s in seller_indicators)

def is_potentially_relevant(text: str) -> bool:
    """
    Tezkor kalit so'zlar bo'yicha dastlabki filtr (Python, Backend, Bot, Sayt, AI).
    """
    t = text.lower()
    
    python_keywords = [
        "python", "django", "fastapi", "flask", 
        "backend", "bekend", "py", "ai", "llm", 
        "tajribasiz", "tajribasizlar", "tajriba talab qilinmaydi", "tajribasi yo'q",
        "без опыта", "bez opita", "no experience", "no-experience",
        "junior", "intern", "amaliyot", "stajyor", "stajirovka", "стажер", "стажировка", "trainee",
        "dasturchi", "developer", "разработчик",
        "telegram bot", "телеграм бот", "bot kerak", "бот нужен", "парсер", "parser",
        "sayt", "сайт", "web dasturchi"
    ]
    has_tech = any(k in t for k in python_keywords)
    if not has_tech:
        try:
            from database import get_managed_keywords
            custom_kws = get_managed_keywords()
            if any(k in t for k in custom_kws):
                has_tech = True
        except Exception:
            pass
    
    is_entry_exempt = any(w in t for w in [
        "junior", "intern", "staj", "amaliyot", "tajribasiz", 
        "без опыта", "bez opita", "no experience"
    ])
    if "lead" in t and not is_entry_exempt:
        return False
    if "senior" in t and not is_entry_exempt:
        return False
    if "buxgalter" in t or "sotuvchi" in t or "ofitsiant" in t or "haydovchi" in t:
        return False
        
    return has_tech

def _fetch_hh_uz_sync() -> List[Dict]:
    """HH.uz dan rasmiy API va curl_cffi zaxirasi orqali oxirgi 24 soatlik ishlarni yig'ish"""
    vacancies = []
    seen_ids = set()
    
    # 1. Rasmiy HH.ru API orqali so'rov yuborish
    try:
        api_url = "https://api.hh.ru/vacancies"
        # Tajriba bo'yicha qidiruv: noExperience (Tajribasiz / Без опыта) va between1And3 (1-3 yilgacha / Junior)
        params = [
            ("area", "97"),
            ("text", "Python OR Django OR FastAPI OR AI OR Backend OR Telegram bot OR Парсер"),
            ("period", "1"),
            ("per_page", "25"),
            ("experience", "noExperience"),
            ("experience", "between1And3")
        ]
        resp = httpx.get(api_url, params=params, headers=HH_API_HEADERS, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            for item in data.get("items", []):
                v_id = str(item.get("id"))
                if v_id in seen_ids:
                    continue
                seen_ids.add(v_id)
                
                title = item.get("name", "Noma'lum")
                company = item.get("employer", {}).get("name", "Noma'lum kompaniya")
                url = item.get("alternate_url", f"https://hh.uz/vacancy/{v_id}")
                pub_at = item.get("published_at", "")
                if not is_published_today(pub_at):
                    continue
                
                # To'liq talablarni alohida https://api.hh.ru/vacancies/{id} dan olish
                full_desc = ""
                try:
                    full_resp = httpx.get(f"https://api.hh.ru/vacancies/{v_id}", headers=HH_API_HEADERS, timeout=8)
                    if full_resp.status_code == 200:
                        f_data = full_resp.json()
                        raw_desc = f_data.get("description", "")
                        soup = bs4.BeautifulSoup(raw_desc, "html.parser")
                        full_desc = soup.get_text(separator="\n", strip=True)
                        skills = [s.get("name") for s in f_data.get("key_skills", []) if s.get("name")]
                        if skills:
                            full_desc += "\nKo'nikmalar: " + ", ".join(skills)
                except Exception:
                    pass
                    
                if not full_desc:
                    snippet = item.get("snippet", {})
                    full_desc = f"{snippet.get('requirement', '')} {snippet.get('responsibility', '')}"
                    
                vacancies.append({
                    "id": make_hash_id("hh_uz", v_id),
                    "title": title,
                    "company": company,
                    "url": url,
                    "text": clean_text(f"{title} | {company} | {full_desc}")[:2500],
                    "source": "hh.uz",
                    "job_type": "vacancy",
                    "posted_at": pub_at
                })
                
            if vacancies:
                logger.info(f"HH.ru API orqali {len(vacancies)} ta vakansiya olindi.")
                return vacancies
    except Exception as e:
        logger.warning(f"HH API so'rovida ogohlantirish: {e}")

    # 2. Zaxira (Fallback): Cloudflare va bloklashlarni aylanib o'tish uchun curl_cffi orqali hh.uz
    try:
        encoded_query = urllib.parse.quote("Python OR Django OR FastAPI OR AI OR Backend OR Telegram bot OR Парсер")
        html_url = f"https://hh.uz/search/vacancy?area=97&text={encoded_query}&search_period=1&order_by=publication_time&experience=noExperience&experience=between1And3"
        resp = cffi_requests.get(html_url, impersonate="chrome", timeout=15)
        if resp.status_code == 200:
            soup = bs4.BeautifulSoup(resp.text, "html.parser")
            cards = soup.find_all("div", attrs={"data-qa": "vacancy-serp__vacancy"})
            if not cards:
                cards = soup.find_all("div", class_=lambda x: x and "vacancy-card" in x)
                
            for card in cards[:20]:
                title_elem = card.find("a", attrs={"data-qa": "serp-item__title"})
                if not title_elem:
                    continue
                title = title_elem.get_text(strip=True)
                item_url = title_elem.get("href", "")
                if "?" in item_url:
                    item_url = item_url.split("?")[0]
                    
                m = re.search(r"/vacancy/(\d+)", item_url)
                v_id = m.group(1) if m else item_url
                if v_id in seen_ids:
                    continue
                seen_ids.add(v_id)
                
                comp_elem = card.find("a", attrs={"data-qa": "vacancy-serp__vacancy-employer"})
                company = comp_elem.get_text(strip=True) if comp_elem else "Kompaniya ko'rsatilmagan"
                
                desc_elem = card.find("div", attrs={"data-qa": "vacancy-serp__vacancy_snippet_requirement"})
                desc = desc_elem.get_text(strip=True) if desc_elem else ""
                
                date_elem = card.find("span", attrs={"data-qa": "vacancy-serp__vacancy-date"})
                posted_at_raw = date_elem.get_text(strip=True) if date_elem else ""
                
                vacancies.append({
                    "id": make_hash_id("hh_uz", v_id),
                    "title": title,
                    "company": company,
                    "url": item_url,
                    "text": clean_text(f"{title} | {company} | {desc}"),
                    "source": "hh.uz",
                    "job_type": "vacancy",
                    "posted_at": posted_at_raw
                })
                
            logger.info(f"HH.uz HTML zaxirasi orqali {len(vacancies)} ta vakansiya olindi.")
    except Exception as e:
        logger.error(f"HH zaxira scraping xatoligi: {e}")
        
    return vacancies

async def scrape_hh_uz() -> List[Dict]:
    """FastAPI orqa fonda bloklanmasligi uchun alohida oqimda ishga tushirish"""
    return await asyncio.to_thread(_fetch_hh_uz_sync)

def _fetch_olx_uz_sync() -> List[Dict]:
    """OLX.uz API dan Cloudflare himoyasini aylanib o'tib (curl_cffi) oxirgi 24 soatlik buyurtmalarni yig'ish"""
    queries = [
        "python", "dasturchi", "программист", 
        "telegram bot", "телеграм бот", 
        "sayt yaratish", "сайт под ключ", 
        "ai", "backend",
        "python tajribasiz", "стажер python", "junior python", "dasturchi stajyor"
    ]
    vacancies = []
    seen_urls = set()

    for q in queries:
        encoded_q = urllib.parse.quote(q)
        url = f"https://www.olx.uz/api/v1/offers/?offset=0&limit=20&query={encoded_q}"
        try:
            resp = cffi_requests.get(url, impersonate="chrome", timeout=12)
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                for item in data:
                    item_url = item.get("url")
                    if not item_url or item_url in seen_urls:
                        continue
                    seen_urls.add(item_url)

                    time_val = item.get("last_refresh_time") or item.get("created_time")
                    if not is_within_last_24_hours(time_val):
                        continue

                    title = item.get("title", "")
                    created_time = item.get("created_time", "")
                    if created_time and not is_published_today(created_time):
                        continue

                    desc = item.get("description", "")
                    # HTML teglarni tozalash
                    soup = bs4.BeautifulSoup(desc, "html.parser")
                    clean_desc = soup.get_text(separator=" ", strip=True)
                    full_text = f"{title}\n{clean_desc}"

                    # Dasturchi reklamalarini (Bot yasab beramiz, sotiladi) rad etish
                    if is_seller_advertisement(full_text):
                        continue
                        
                    if not is_potentially_relevant(full_text):
                        continue

                    # E'lon turini aniqlash (vakansiya yoki frilans buyurtma)
                    is_freelance = any(w in full_text.lower() for w in ["bot", "sayt", "loyiha", "buyurtma", "yasatish", "proyekt", "qildirmoqchi", "freelance"])
                    job_type = "freelance" if is_freelance else "vacancy"

                    vac_id = make_hash_id("olx_uz", item_url)
                    vacancies.append({
                        "id": vac_id,
                        "title": title,
                        "company": "OLX.UZ Buyurtmachi",
                        "url": item_url,
                        "text": clean_text(full_text)[:2200],
                        "source": "olx.uz",
                        "job_type": job_type,
                        "posted_at": time_val or ""
                    })
        except Exception as e:
            logger.warning(f"OLX qidiruvida ogohlantirish ({q}): {e}")

    logger.info(f"OLX.uz dan {len(vacancies)} ta yangi 24 soatlik buyurtma/vakansiya topildi.")
    return vacancies

async def scrape_olx_uz() -> List[Dict]:
    """OLX qidiruvini alohida fonda ishlatish"""
    return await asyncio.to_thread(_fetch_olx_uz_sync)

async def scrape_telegram_channel(channel: str, today_only: bool = True) -> List[Dict]:
    """Ochiq Telegram kanalini t.me/s/{channel} orqali skanerlash"""
    vacancies = []
    clean_channel = channel.strip().lstrip("@").replace("https://t.me/s/", "").replace("https://t.me/", "").replace("t.me/s/", "").replace("t.me/", "").strip("/")
    url = f"https://t.me/s/{clean_channel}"
    
    try:
        async with httpx.AsyncClient(timeout=14, headers=HEADERS, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                logger.warning(f"Kanalni yuklab bo'lmadi: {clean_channel} (Status: {resp.status_code})")
                return []
                
            soup = bs4.BeautifulSoup(resp.text, "html.parser")
            messages = soup.find_all("div", class_="tgme_widget_message_wrap")
            
            # Oxirgi 25 ta xabarni tekshiramiz
            for msg in messages[-25:]:
                time_elem = msg.find("time")
                if not time_elem or not time_elem.get("datetime"):
                    continue
                dt_str = time_elem.get("datetime")
                # QAT'IY SHART: Faqat BUGUNGI SANA (UTC+5 Toshkent) e'lonlarini olish, eskilarini tashlab ketish
                if today_only and not is_published_today(dt_str):
                    continue
                        
                text_elem = msg.find("div", class_="tgme_widget_message_text")
                if not text_elem:
                    continue
                    
                raw_text = text_elem.get_text(separator="\n", strip=True)
                
                # Sotuvchi reklamalarini olib tashlash
                if is_seller_advertisement(raw_text):
                    continue
                    
                if not is_potentially_relevant(raw_text):
                    continue
                    
                # Post havolasi va ID sini aniqlash
                date_elem = msg.find("a", class_="tgme_widget_message_date")
                post_url = date_elem.get("href") if date_elem else f"https://t.me/{clean_channel}"
                
                # Sarlavhani birinchi qatordan olish
                lines = [line.strip() for line in raw_text.split("\n") if line.strip()]
                title = lines[0][:80] if lines else "Telegram e'lon"
                
                # Frilans buyurtma yoki vakansiya ekanligini taxminiy ajratish
                is_freelance = any(w in raw_text.lower() for w in ["#loyiha", "loyiha", "buyurtma", "zakaz", "bot yasatish", "sayt qilish", "freelance", "frilans"])
                job_type = "freelance" if is_freelance else "vacancy"
                
                vac_id = make_hash_id(clean_channel, post_url)
                
                vacancies.append({
                    "id": vac_id,
                    "title": title,
                    "company": f"Telegram @{clean_channel}",
                    "url": post_url,
                    "text": raw_text,
                    "source": f"@{clean_channel}",
                    "job_type": job_type,
                    "posted_at": dt_str
                })
    except Exception as e:
        logger.error(f"Telegram {clean_channel} kanalini o'qishda xatolik: {e}")
        
    return vacancies

async def scrape_all_sources() -> List[Dict]:
    """
    Barcha manbalarni (9 ta Telegram kanal + hh.uz + olx.uz)
    asyncio.gather orqali PARALLEL va juda tez skaner qilish.
    """
    tasks = []
    
    # 1. HH.UZ
    tasks.append(scrape_hh_uz())
    
    # 2. OLX.UZ
    tasks.append(scrape_olx_uz())
    
    # 3. Telegram kanallar (Standart 9 ta + Foydalanuvchi qo'shgan shaxsiy kanallar)
    try:
        from database import get_managed_channels
        managed_ch = get_managed_channels()
    except Exception:
        managed_ch = []

    all_channels = list(dict.fromkeys(TELEGRAM_CHANNELS + managed_ch))
    for ch in all_channels:
        tasks.append(scrape_telegram_channel(ch, today_only=True))
        
    logger.info(f"🚀 Barcha {len(tasks)} ta manba bo'yicha parallel skanerlash boshlandi...")
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    all_vacancies = []
    seen_ids = set()
    
    for res in results:
        if isinstance(res, list):
            for item in res:
                v_id = item.get("id")
                if v_id and v_id not in seen_ids:
                    seen_ids.add(v_id)
                    all_vacancies.append(item)
        elif isinstance(res, Exception):
            logger.error(f"Manbani skanerlashda parallel vazifa xatoligi: {res}")
            
    logger.info(f"Oxirgi 24 soatga oid jami {len(all_vacancies)} ta noyob vakansiya va frilans buyurtma yig'ildi.")
    return all_vacancies

async def scrape_telegram_single_post(channel: str, post_id: str) -> Optional[str]:
    """
    Aniq bitta Telegram postining to'liq matnini t.me/<channel>/<post_id>?embed=1
    yoki t.me/s/<channel>/<post_id> orqali yuklab oladi.
    Filtrlar (60% ball, 24 soat va b.) qo'llanmaydi, post matni to'g'riligicha olinadi.
    """
    clean_channel = channel.strip().lstrip("@").replace("https://t.me/s/", "").replace("https://t.me/", "").replace("t.me/s/", "").replace("t.me/", "").strip("/")
    clean_post_id = str(post_id).strip()
    
    urls = [
        f"https://t.me/{clean_channel}/{clean_post_id}?embed=1",
        f"https://t.me/s/{clean_channel}/{clean_post_id}",
        f"https://t.me/{clean_channel}/{clean_post_id}"
    ]
    
    # 1. httpx orqali sinab ko'rish
    try:
        async with httpx.AsyncClient(timeout=15, headers=HEADERS, follow_redirects=True) as client:
            for url in urls:
                try:
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        soup = bs4.BeautifulSoup(resp.text, "html.parser")
                        text_el = soup.find(class_="tgme_widget_message_text") or soup.find(class_="js-message_text")
                        if text_el:
                            full_text = text_el.get_text("\n", strip=True)
                            if full_text:
                                logger.info(f"Telegram post (@{clean_channel}/{clean_post_id}) matni olindi ({len(full_text)} belgi)")
                                return full_text
                except Exception as req_err:
                    logger.debug(f"httpx bilan {url} yuklashda xato: {req_err}")
    except Exception as e:
        logger.warning(f"httpx orqali post yuklashda umumiy xato: {e}")

    # 2. Zaxira: curl_cffi orqali brauzer barmoq izi bilan urinish
    try:
        def _fetch_cffi():
            for url in urls:
                try:
                    resp = cffi_requests.get(url, headers=HEADERS, impersonate="chrome", timeout=15)
                    if resp.status_code == 200:
                        soup = bs4.BeautifulSoup(resp.text, "html.parser")
                        text_el = soup.find(class_="tgme_widget_message_text") or soup.find(class_="js-message_text")
                        if text_el:
                            full_text = text_el.get_text("\n", strip=True)
                            if full_text:
                                return full_text
                except Exception:
                    pass
            return None

        cffi_result = await asyncio.to_thread(_fetch_cffi)
        if cffi_result:
            return cffi_result
    except Exception as cffi_err:
        logger.warning(f"curl_cffi bilan post yuklashda xato: {cffi_err}")

    return None


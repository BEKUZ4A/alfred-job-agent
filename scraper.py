import httpx
import bs4
import hashlib
import logging
from datetime import datetime, timezone
from typing import List, Dict
from config import TELEGRAM_CHANNELS, TASHKENT_TZ

logger = logging.getLogger("Alfred.Scraper")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json,*/*;q=0.8",
    "Accept-Language": "uz,ru;q=0.9,en;q=0.8"
}

def clean_text(raw_text: str) -> str:
    """Matnni ortiqcha bo'shliqlardan tozalash"""
    if not raw_text:
        return ""
    return " ".join(raw_text.split())

def make_hash_id(source: str, identifier: str) -> str:
    """Har bir vakansiyaga yagona ID generatsiya qilish"""
    return hashlib.md5(f"{source}:{identifier}".encode("utf-8")).hexdigest()

def is_published_today(dt_str: str) -> bool:
    """
    Xabar bugungi sanada e'lon qilinganligini tekshirish (Toshkent vaqti UTC+5 bo'yicha).
    Kecha yoki undan oldin chiqqan eski xabarlar qat'iy o'tkazib yuboriladi.
    """
    if not dt_str:
        return False
    try:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        post_date = dt.astimezone(TASHKENT_TZ).date()
        today_date = datetime.now(TASHKENT_TZ).date()
        return post_date >= today_date
    except Exception as e:
        logger.warning(f"Sana tekshirishda ogohlantirish ({dt_str}): {e}")
        return False

def format_posted_at(dt_str: str) -> str:
    """
    UTC ISO vaqt satrini Toshkent vaqtiga (UTC+5) o'tkazib, o'qilishi oson formatda qaytarish.
    Masalan: "06-11-2026 10:30 (Toshkent)"
    """
    if not dt_str:
        return "Noma'lum"
    try:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt_tash = dt.astimezone(TASHKENT_TZ)
        return dt_tash.strftime("%d-%m-%Y %H:%M")
    except Exception:
        return "Noma'lum"

def is_potentially_relevant(text: str) -> bool:
    """
    Tezkor kalit so'zlar bo'yicha dastlabki filtr (Gemini API kvotasini tejash uchun).
    Foydalanuvchi profili: Python, Django, FastAPI, Backend, AI, Junior, Intern, Dasturchi.
    """
    t = text.lower()
    
    # Python yoki dasturlashga aloqador kalit so'zlar
    python_keywords = [
        "python", "django", "fastapi", "flask", 
        "backend", "bekend", "py", "ai", "llm", 
        "junior", "intern", "amaliyot", "stajyor", "stajirovka",
        "dasturchi", "developer", "разработчик"
    ]
    has_tech = any(k in t for k in python_keywords)
    
    # Aniq Senior/Lead yoki mutlaqo boshqa sohalar (agar junior/intern bo'lmasa)
    if "lead" in t and "junior" not in t and "intern" not in t:
        return False
    if "senior" in t and "junior" not in t and "intern" not in t:
        return False
    if "buxgalter" in t or "sotuvchi" in t or "ofitsiant" in t or "haydovchi" in t:
        return False
        
    return has_tech

async def scrape_hh_uz() -> List[Dict]:
    """HH.uz saytidan bugungi Python vakansiyalarini yig'ish"""
    vacancies = []
    
    # 1-usul: Rasmiy HH.ru API
    try:
        api_url = "https://api.hh.ru/vacancies?area=97&text=Python&per_page=15&order_by=publication_time"
        async with httpx.AsyncClient(timeout=10, headers={"User-Agent": "AlfredJobBot/1.0 (behruz@jobhunter.uz)"}) as client:
            resp = await client.get(api_url)
            if resp.status_code == 200:
                data = resp.json()
                for item in data.get("items", []):
                    # Faqat bugungi e'lonlarni tekshiramiz
                    pub_at = item.get("published_at", "")
                    if pub_at and not is_published_today(pub_at):
                        continue
                        
                    vac_id = make_hash_id("hh_uz", str(item.get("id")))
                    title = item.get("name", "Noma'lum")
                    company = item.get("employer", {}).get("name", "Noma'lum kompaniya")
                    url = item.get("alternate_url", f"https://hh.uz/vacancy/{item.get('id')}")
                    
                    snippet = item.get("snippet", {})
                    req = snippet.get("requirement") or ""
                    resp_desc = snippet.get("responsibility") or ""
                    salary = item.get("salary")
                    salary_str = ""
                    if salary:
                        from_s = salary.get("from")
                        to_s = salary.get("to")
                        cur = salary.get("currency", "")
                        salary_str = f"{from_s or ''} - {to_s or ''} {cur}".strip()
                    
                    full_text = f"Sarlavha: {title}\nKompaniya: {company}\nMaosh: {salary_str}\nTalablar: {req}\nVazifalar: {resp_desc}"
                    
                    vacancies.append({
                        "id": vac_id,
                        "title": title,
                        "company": company,
                        "url": url,
                        "text": clean_text(full_text),
                        "source": "hh.uz",
                        "posted_at": pub_at  # ✅ E'lon qilingan vaqt saqlandi
                    })
                return vacancies
    except Exception as e:
        logger.warning(f"HH API orqali olishda xatolik: {e}, HTML sahifaga o'tilmoqda...")

    # 2-usul: HH.uz qidiruv HTML sahifasini parse qilish
    try:
        html_url = "https://hh.uz/search/vacancy?area=97&text=Python&order_by=publication_time"
        async with httpx.AsyncClient(timeout=15, headers=HEADERS, follow_redirects=True) as client:
            resp = await client.get(html_url)
            if resp.status_code == 200:
                soup = bs4.BeautifulSoup(resp.text, "html.parser")
                cards = soup.find_all("div", attrs={"data-qa": "vacancy-serp__vacancy"})
                if not cards:
                    cards = soup.find_all("div", class_=lambda x: x and "vacancy-card" in x)
                
                for card in cards[:15]:
                    title_elem = card.find("a", attrs={"data-qa": "serp-item__title"})
                    if not title_elem:
                        continue
                    title = title_elem.get_text(strip=True)
                    url = title_elem.get("href", "")
                    if "?" in url:
                        url = url.split("?")[0]
                    
                    comp_elem = card.find("a", attrs={"data-qa": "vacancy-serp__vacancy-employer"})
                    company = comp_elem.get_text(strip=True) if comp_elem else "Kompaniya ko'rsatilmagan"
                    
                    desc_elem = card.find("div", attrs={"data-qa": "vacancy-serp__vacancy_snippet_requirement"})
                    desc = desc_elem.get_text(strip=True) if desc_elem else ""

                    # HH.uz HTML da sana tegi: <span data-qa="vacancy-serp__vacancy-date">
                    date_elem = card.find("span", attrs={"data-qa": "vacancy-serp__vacancy-date"})
                    posted_at_raw = date_elem.get_text(strip=True) if date_elem else ""
                    
                    vac_id = make_hash_id("hh_uz", url)
                    vacancies.append({
                        "id": vac_id,
                        "title": title,
                        "company": company,
                        "url": url,
                        "text": clean_text(f"{title} | {company} | {desc}"),
                        "source": "hh.uz",
                        "posted_at": posted_at_raw  # ✅ HTML scraping dan ham vaqt olinadi
                    })
    except Exception as e:
        logger.error(f"HH HTML scraping xatoligi: {e}")
        
    return vacancies

async def scrape_telegram_channel(channel: str, today_only: bool = True) -> List[Dict]:
    """Ochiq Telegram kanalini t.me/s/{channel} orqali skanerlash. today_only=False bo'lsa oxirgi e'lonlarni ham oladi"""
    vacancies = []
    clean_channel = channel.strip().lstrip("@").replace("https://t.me/s/", "").replace("https://t.me/", "").replace("t.me/s/", "").replace("t.me/", "").strip("/")
    url = f"https://t.me/s/{clean_channel}"
    
    try:
        async with httpx.AsyncClient(timeout=12, headers=HEADERS, follow_redirects=True) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                logger.warning(f"Kanalni yuklab bo'lmadi: {clean_channel} (Status: {resp.status_code})")
                return []
                
            soup = bs4.BeautifulSoup(resp.text, "html.parser")
            messages = soup.find_all("div", class_="tgme_widget_message_wrap")
            
            # Oxirgi 25 ta xabarni tekshiramiz
            for msg in messages[-25:]:
                # 1. Sana tekshiruvi (agar today_only yoqilgan bo'lsa)
                time_elem = msg.find("time")
                dt_str = ""
                if time_elem and time_elem.get("datetime"):
                    dt_str = time_elem.get("datetime")
                    if today_only and not is_published_today(dt_str):
                        # Bugungi kunga tegishli emas, o'tkazib yuboramiz
                        continue
                        
                text_elem = msg.find("div", class_="tgme_widget_message_text")
                if not text_elem:
                    continue
                    
                raw_text = text_elem.get_text(separator="\n", strip=True)
                if not is_potentially_relevant(raw_text):
                    continue
                    
                # Post havolasi va ID sini aniqlash
                date_elem = msg.find("a", class_="tgme_widget_message_date")
                post_url = date_elem.get("href") if date_elem else f"https://t.me/{clean_channel}"
                
                # Sarlavhani birinchi qatordan olish
                lines = [line.strip() for line in raw_text.split("\n") if line.strip()]
                title = lines[0][:80] if lines else "Telegram vakansiya"
                
                vac_id = make_hash_id(clean_channel, post_url)
                
                vacancies.append({
                    "id": vac_id,
                    "title": title,
                    "company": f"Telegram @{clean_channel}",
                    "url": post_url,
                    "text": raw_text,
                    "source": f"@{clean_channel}",
                    "posted_at": dt_str  # ✅ Telegram post vaqti (ISO format, UTC)
                })
    except Exception as e:
        logger.error(f"Telegram {clean_channel} kanalini o'qishda xatolik: {e}")
        
    return vacancies

async def scrape_all_sources() -> List[Dict]:
    """Barcha belgilangan manbalardan faqat bugungi yangi vakansiyalarni to'plash"""
    all_vacancies = []
    
    # 1. HH.uz
    hh_vacancies = await scrape_hh_uz()
    all_vacancies.extend(hh_vacancies)
    
    # 2. Telegram kanallar
    for ch in TELEGRAM_CHANNELS:
        ch_vacancies = await scrape_telegram_channel(ch)
        all_vacancies.extend(ch_vacancies)
        
    logger.info(f"Bugungi sanaga oid jami {len(all_vacancies)} ta xom vakansiya topildi.")
    return all_vacancies

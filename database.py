import hashlib
import re
import os
import sqlite3
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any
from config import DATABASE_URL, TASHKENT_TZ

DB_FILE = os.path.join(os.path.dirname(__file__), "seen_vacancies.db")

def is_postgres() -> bool:
    return bool(DATABASE_URL and ("postgresql://" in DATABASE_URL or "postgres://" in DATABASE_URL))

def get_connection():
    if is_postgres():
        import psycopg2
        from psycopg2.extras import RealDictCursor
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
        return conn
    else:
        conn = sqlite3.connect(DB_FILE)
        conn.row_factory = sqlite3.Row
        return conn

def execute_query(query: str, params: tuple = (), fetch_one: bool = False, fetch_all: bool = False, commit: bool = False) -> Any:
    """Postgres va SQLite uchun umumiy so'rov bajaruvchi"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        if is_postgres():
            # ? ni %s ga o'zgartirish
            pg_query = query.replace("?", "%s")
            cursor.execute(pg_query, params)
        else:
            cursor.execute(query, params)
            
        result = None
        if fetch_one:
            row = cursor.fetchone()
            result = dict(row) if row else None
        elif fetch_all:
            rows = cursor.fetchall()
            result = [dict(row) for row in rows]
            
        if commit:
            conn.commit()
            
        return result
    finally:
        conn.close()

def init_db():
    """Barcha kerakli jadvallarni yaratish va tayyorlash"""
    conn = get_connection()
    cursor = conn.cursor()
    
    if is_postgres():
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS seen_vacancies (
            id TEXT PRIMARY KEY,
            title TEXT,
            company TEXT,
            url TEXT,
            source TEXT,
            match_score INTEGER,
            ai_summary TEXT,
            job_type TEXT DEFAULT 'vacancy',
            raw_text TEXT DEFAULT '',
            salary TEXT DEFAULT '',
            red_flag TEXT DEFAULT '',
            posted_at TEXT DEFAULT '',
            content_hash TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS tracked_hrs (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE,
            company TEXT DEFAULT '',
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'waiting',
            cal_event_3d TEXT DEFAULT '',
            cal_event_7d TEXT DEFAULT '',
            last_message TEXT DEFAULT '',
            last_seen_msg_id BIGINT DEFAULT 0,
            pending_reply TEXT DEFAULT '',
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS applied_jobs (
            id SERIAL PRIMARY KEY,
            vacancy_id TEXT UNIQUE,
            title TEXT,
            company TEXT,
            url TEXT,
            content_hash TEXT DEFAULT '',
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'applied'
        );
        CREATE TABLE IF NOT EXISTS managed_channels (
            channel_id TEXT PRIMARY KEY,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS managed_keywords (
            keyword TEXT PRIMARY KEY,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)
    else:
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS seen_vacancies (
            id TEXT PRIMARY KEY,
            title TEXT,
            company TEXT,
            url TEXT,
            source TEXT,
            match_score INTEGER,
            ai_summary TEXT,
            job_type TEXT DEFAULT 'vacancy',
            raw_text TEXT DEFAULT '',
            salary TEXT DEFAULT '',
            red_flag TEXT DEFAULT '',
            posted_at TEXT DEFAULT '',
            content_hash TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS tracked_hrs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            company TEXT DEFAULT '',
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'waiting',
            cal_event_3d TEXT DEFAULT '',
            cal_event_7d TEXT DEFAULT '',
            last_message TEXT DEFAULT '',
            last_seen_msg_id INTEGER DEFAULT 0,
            pending_reply TEXT DEFAULT '',
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS applied_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vacancy_id TEXT UNIQUE,
            title TEXT,
            company TEXT,
            url TEXT,
            content_hash TEXT DEFAULT '',
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status TEXT DEFAULT 'applied'
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS managed_channels (
            channel_id TEXT PRIMARY KEY,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS custom_call_timers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_time TEXT,
            reason TEXT DEFAULT '',
            is_done INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS managed_keywords (
            keyword TEXT PRIMARY KEY,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        
        # Ustunlar mavjudligini tekshirish
        for col, col_type in [
            ("job_type", "TEXT DEFAULT 'vacancy'"), 
            ("raw_text", "TEXT DEFAULT ''"), 
            ("salary", "TEXT DEFAULT ''"), 
            ("red_flag", "TEXT DEFAULT ''"),
            ("posted_at", "TEXT DEFAULT ''"),
            ("content_hash", "TEXT DEFAULT ''")
        ]:
            try:
                cursor.execute(f"ALTER TABLE seen_vacancies ADD COLUMN {col} {col_type}")
            except sqlite3.OperationalError:
                pass

        for col, col_type in [
            ("last_seen_msg_id", "INTEGER DEFAULT 0"),
            ("pending_reply", "TEXT DEFAULT ''"),
            ("interview_time", "TEXT DEFAULT ''"),
            ("reminded_2min", "INTEGER DEFAULT 0")
        ]:
            try:
                cursor.execute(f"ALTER TABLE tracked_hrs ADD COLUMN {col} {col_type}")
            except Exception:
                pass

        for col, col_type in [
            ("content_hash", "TEXT DEFAULT ''")
        ]:
            try:
                cursor.execute(f"ALTER TABLE applied_jobs ADD COLUMN {col} {col_type}")
            except sqlite3.OperationalError:
                pass

        # Botlar va o'zimizning botimizni HR bazasidan 100% tozalash (Infinite loop himoyasi)
        try:
            cursor.execute("DELETE FROM tracked_hrs WHERE LOWER(username) LIKE '%bot' OR LOWER(username) IN ('me', 'ai_agents_24', 'bekuz4a_bot')")
        except Exception:
            pass

    conn.commit()
    conn.close()

# ----------------- VAKANSIYALAR FUNKSIYALARI -----------------

def compute_content_hash(text: str = "", title: str = "", company: str = "") -> str:
    """
    Vakansiya takrorlanmasligi (dublikat) uchun unikal xesh.
    Matnning birinchi 150 ta harf va raqamlarini olib, sarlavha va kompaniya bilan xeshlaydi.
    """
    clean_title = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁo'g'shch]+", "", (title or "").lower())
    clean_comp = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁo'g'shch]+", "", (company or "").lower())
    clean_body = re.sub(r"[^a-zA-Z0-9а-яА-ЯёЁo'g'shch]+", "", (text or "").lower())[:150]
    base_str = f"{clean_title}:{clean_comp}:{clean_body}"
    return hashlib.sha256(base_str.encode("utf-8")).hexdigest()

def is_vacancy_seen(
    vacancy_id: str, 
    url: Optional[str] = None, 
    content_hash: Optional[str] = None,
    title: Optional[str] = None,
    company: Optional[str] = None
) -> bool:
    """
    Vakansiya ilgari ko'rilganligini tekshirish:
    1. vacancy_id bo'yicha
    2. url (parametrlarisiz toza havola) bo'yicha
    3. content_hash bo'yicha
    4. title + company birikmasi bo'yicha
    """
    if vacancy_id:
        row = execute_query("SELECT 1 FROM seen_vacancies WHERE id = ?", (vacancy_id,), fetch_one=True)
        if row:
            return True
            
    if url and url != "#":
        clean_url = url.split("?")[0].strip()
        row = execute_query("SELECT 1 FROM seen_vacancies WHERE url LIKE ? OR url = ?", (f"{clean_url}%", url), fetch_one=True)
        if row:
            return True
            
    if content_hash:
        row = execute_query("SELECT 1 FROM seen_vacancies WHERE content_hash = ?", (content_hash,), fetch_one=True)
        if row:
            return True

    if title and company and len(title.strip()) > 5:
        row = execute_query(
            "SELECT 1 FROM seen_vacancies WHERE LOWER(title) = ? AND LOWER(company) = ? AND company != ''", 
            (title.strip().lower(), company.strip().lower()), 
            fetch_one=True
        )
        if row:
            return True

    return False

def is_job_applied(
    vacancy_id: Optional[str] = None, 
    url: Optional[str] = None, 
    content_hash: Optional[str] = None,
    title: Optional[str] = None,
    company: Optional[str] = None
) -> bool:
    """
    Foydalanuvchi bu vakansiyaga oldin 'Topshirdim' deb belgilaganligini tekshirish:
    Agar topshirilgan bo'lsa, uni HECH QACHON qayta chiqarmaslik shart!
    """
    if vacancy_id:
        row = execute_query("SELECT 1 FROM applied_jobs WHERE vacancy_id = ?", (vacancy_id,), fetch_one=True)
        if row:
            return True
            
    if url and url != "#":
        clean_url = url.split("?")[0].strip()
        row = execute_query("SELECT 1 FROM applied_jobs WHERE url LIKE ? OR url = ?", (f"{clean_url}%", url), fetch_one=True)
        if row:
            return True
            
    if content_hash:
        row = execute_query("SELECT 1 FROM applied_jobs WHERE content_hash = ?", (content_hash,), fetch_one=True)
        if row:
            return True

    if title and company and len(title.strip()) > 5:
        row = execute_query(
            "SELECT 1 FROM applied_jobs WHERE LOWER(title) = ? AND LOWER(company) = ? AND company != ''", 
            (title.strip().lower(), company.strip().lower()), 
            fetch_one=True
        )
        if row:
            return True

    return False

def mark_vacancy_seen(
    vacancy_id: str, 
    title: str, 
    company: str, 
    url: str, 
    source: str, 
    match_score: int, 
    ai_summary: str,
    job_type: str = "vacancy",
    raw_text: str = "",
    salary: str = "",
    red_flag: str = "",
    posted_at: str = "",
    content_hash: str = ""
):
    if not content_hash:
        content_hash = compute_content_hash(raw_text or ai_summary, title, company)

    if is_postgres():
        execute_query("""
        INSERT INTO seen_vacancies (id, title, company, url, source, match_score, ai_summary, job_type, raw_text, salary, red_flag, posted_at, content_hash, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT (id) DO UPDATE SET
            title = EXCLUDED.title,
            company = EXCLUDED.company,
            url = EXCLUDED.url,
            match_score = EXCLUDED.match_score,
            ai_summary = EXCLUDED.ai_summary,
            job_type = EXCLUDED.job_type,
            raw_text = EXCLUDED.raw_text,
            salary = EXCLUDED.salary,
            red_flag = EXCLUDED.red_flag,
            posted_at = EXCLUDED.posted_at,
            content_hash = EXCLUDED.content_hash
        """, (vacancy_id, title, company, url, source, match_score, ai_summary, job_type, raw_text, salary, red_flag, posted_at, content_hash), commit=True)
    else:
        execute_query("""
        INSERT OR REPLACE INTO seen_vacancies (id, title, company, url, source, match_score, ai_summary, job_type, raw_text, salary, red_flag, posted_at, content_hash, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (vacancy_id, title, company, url, source, match_score, ai_summary, job_type, raw_text, salary, red_flag, posted_at, content_hash), commit=True)

def get_vacancy_by_id(vacancy_id: str) -> Optional[Dict]:
    return execute_query("SELECT * FROM seen_vacancies WHERE id = ?", (vacancy_id,), fetch_one=True)

def get_recent_vacancies(limit: int = 10) -> List[Dict]:
    return execute_query("SELECT * FROM seen_vacancies ORDER BY created_at DESC LIMIT ?", (limit,), fetch_all=True) or []

def get_today_vacancies(limit: int = 10) -> List[Dict]:
    """Bugungi (Toshkent vaqti UTC+5) haqiqiy vakansiyalarni qaytarish"""
    today_str = datetime.now(TASHKENT_TZ).strftime("%Y-%m-%d")
    res = execute_query("""
    SELECT * FROM seen_vacancies 
    WHERE created_at LIKE ? OR posted_at LIKE ?
    ORDER BY match_score DESC, created_at DESC 
    LIMIT ?
    """, (f"%{today_str}%", f"%{today_str}%", limit), fetch_all=True)
    if not res:
        res = execute_query("SELECT * FROM seen_vacancies ORDER BY created_at DESC LIMIT ?", (limit,), fetch_all=True)
    return res or []

def get_top_vacancies(limit: int = 5, min_score: int = 50) -> List[Dict]:
    res = execute_query("""
    SELECT * FROM seen_vacancies 
    WHERE match_score >= ? 
    ORDER BY match_score DESC, created_at DESC 
    LIMIT ?
    """, (min_score, limit), fetch_all=True)
    if not res:
        res = execute_query("SELECT * FROM seen_vacancies ORDER BY match_score DESC, created_at DESC LIMIT ?", (limit,), fetch_all=True)
    return res or []

# ----------------- HR KUZATUV VA MINI-CRM -----------------

def is_forbidden_hr_username(username: str) -> bool:
    """Botlar va shaxsiy akkauntlarning HR bo'lib qo'shilishini va cheksiz loopga tushishini 100% to'sish"""
    if not username:
        return True
    u = str(username).strip().lstrip("@").lower()
    if not u:
        return True
    # Telegram botlari har doim 'bot' bilan tugaydi
    if u.endswith("bot") or u.endswith("_bot") or u == "bekuz4a_bot":
        return True
    # Shaxsiy akkauntlar va tizim nomlari
    if u in ["me", "ai_agents_24", "self", "channel", "group", "admin"]:
        return True
    return False

def add_tracked_hr(username: str, company: str = "", cal_event_3d: str = "", cal_event_7d: str = ""):
    username = username.strip().lstrip("@").lower()
    if is_forbidden_hr_username(username):
        logger.warning(f"Taqiqlangan username (@{username}) HR sifatida qo'shilmadi (bot yoki tizim akkaunti).")
        return
    existing = get_tracked_hr(username)
    if existing:
        if is_postgres():
            execute_query("""
            UPDATE tracked_hrs 
            SET company = COALESCE(NULLIF(?, ''), company),
                status = 'waiting',
                last_updated = CURRENT_TIMESTAMP
            WHERE LOWER(username) = ?
            """, (company, username), commit=True)
        else:
            execute_query("""
            UPDATE tracked_hrs 
            SET company = CASE WHEN ? != '' THEN ? ELSE company END,
                status = 'waiting',
                last_updated = CURRENT_TIMESTAMP
            WHERE LOWER(username) = ?
            """, (company, company, username), commit=True)
    else:
        if is_postgres():
            execute_query("""
            INSERT INTO tracked_hrs (username, company, cal_event_3d, cal_event_7d, status, last_seen_msg_id, last_updated)
            VALUES (?, ?, ?, ?, 'waiting', 0, CURRENT_TIMESTAMP)
            ON CONFLICT (username) DO UPDATE SET
                company = EXCLUDED.company,
                status = 'waiting',
                last_updated = CURRENT_TIMESTAMP
            """, (username, company, cal_event_3d, cal_event_7d), commit=True)
        else:
            execute_query("""
            INSERT INTO tracked_hrs (username, company, cal_event_3d, cal_event_7d, status, last_seen_msg_id, last_updated)
            VALUES (?, ?, ?, ?, 'waiting', 0, CURRENT_TIMESTAMP)
            """, (username, company, cal_event_3d, cal_event_7d), commit=True)

def update_hr_last_msg(username: str, msg_id: int, message_text: str = "", status: str = "replied"):
    """Telethon orqali kelgan yangi xabar ID si va matnini yangilash"""
    username = username.strip().lstrip("@").lower()
    execute_query("""
    UPDATE tracked_hrs 
    SET last_seen_msg_id = ?, last_message = ?, status = ?, last_updated = CURRENT_TIMESTAMP 
    WHERE LOWER(username) = ?
    """, (msg_id, message_text, status, username), commit=True)

def get_tracked_hr(username: str) -> Optional[Dict]:
    username = username.strip().lstrip("@").lower()
    return execute_query("SELECT * FROM tracked_hrs WHERE LOWER(username) = ?", (username,), fetch_one=True)

def get_all_tracked_hrs() -> List[Dict]:
    rows = execute_query("SELECT * FROM tracked_hrs ORDER BY added_at DESC", fetch_all=True) or []
    return [r for r in rows if not is_forbidden_hr_username(r.get("username", ""))]

def update_hr_status(username: str, status: str, last_message: str = ""):
    username = username.strip().lstrip("@").lower()
    if last_message:
        execute_query("""
        UPDATE tracked_hrs 
        SET status = ?, last_message = ?, last_updated = CURRENT_TIMESTAMP 
        WHERE LOWER(username) = ?
        """, (status, last_message, username), commit=True)
    else:
        execute_query("""
        UPDATE tracked_hrs 
        SET status = ?, last_updated = CURRENT_TIMESTAMP 
        WHERE LOWER(username) = ?
        """, (status, username), commit=True)

def update_hr_pending_reply(username: str, pending_reply: str):
    """HR uchun tavsiya qilingan javobni bazada saqlash"""
    username = username.strip().lstrip("@").lower()
    execute_query("UPDATE tracked_hrs SET pending_reply = ? WHERE LOWER(username) = ?", (pending_reply, username), commit=True)

def get_hr_pending_reply(username: str) -> str:
    """HR uchun tavsiya qilingan oxirgi javobni olish"""
    username = username.strip().lstrip("@").lower()
    row = execute_query("SELECT pending_reply FROM tracked_hrs WHERE LOWER(username) = ?", (username,), fetch_one=True)
    return row.get("pending_reply", "") if row else ""

def delete_tracked_hr(username: str):
    username = username.strip().lstrip("@").lower()
    execute_query("DELETE FROM tracked_hrs WHERE LOWER(username) = ?", (username,), commit=True)

# ----------------- TOPSHIRILGAN ISHLAR (APPLIED JOBS) -----------------

def mark_job_applied(vacancy_id: str, title: str, company: str, url: str, content_hash: str = "") -> bool:
    if not content_hash:
        content_hash = compute_content_hash("", title, company)
    if is_postgres():
        execute_query("""
        INSERT INTO applied_jobs (vacancy_id, title, company, url, content_hash, status, applied_at)
        VALUES (?, ?, ?, ?, ?, 'applied', CURRENT_TIMESTAMP)
        ON CONFLICT (vacancy_id) DO UPDATE SET
            content_hash = COALESCE(NULLIF(EXCLUDED.content_hash, ''), applied_jobs.content_hash)
        """, (vacancy_id, title, company, url, content_hash), commit=True)
    else:
        execute_query("""
        INSERT OR REPLACE INTO applied_jobs (vacancy_id, title, company, url, content_hash, status, applied_at)
        VALUES (?, ?, ?, ?, ?, 'applied', CURRENT_TIMESTAMP)
        """, (vacancy_id, title, company, url, content_hash), commit=True)
    return True

def get_applied_jobs(limit: int = 15) -> List[Dict]:
    return execute_query("SELECT * FROM applied_jobs ORDER BY applied_at DESC LIMIT ?", (limit,), fetch_all=True) or []

# ----------------- KANALLAR VA KALIT SO'ZLARNI BOSHQARISH -----------------

def add_managed_channel(channel_id: str) -> bool:
    channel_id = channel_id.strip().lstrip("@").replace("https://t.me/", "")
    if is_postgres():
        execute_query("INSERT INTO managed_channels (channel_id) VALUES (?) ON CONFLICT (channel_id) DO NOTHING", (channel_id,), commit=True)
    else:
        execute_query("INSERT OR IGNORE INTO managed_channels (channel_id) VALUES (?)", (channel_id,), commit=True)
    return True

def remove_managed_channel(channel_id: str) -> bool:
    channel_id = channel_id.strip().lstrip("@").replace("https://t.me/", "")
    execute_query("DELETE FROM managed_channels WHERE channel_id = ?", (channel_id,), commit=True)
    return True

def get_managed_channels() -> List[str]:
    rows = execute_query("SELECT channel_id FROM managed_channels", fetch_all=True) or []
    return [r["channel_id"] for r in rows]

def add_managed_keyword(keyword: str) -> bool:
    keyword = keyword.strip().lower()
    if is_postgres():
        execute_query("INSERT INTO managed_keywords (keyword) VALUES (?) ON CONFLICT (keyword) DO NOTHING", (keyword,), commit=True)
    else:
        execute_query("INSERT OR IGNORE INTO managed_keywords (keyword) VALUES (?)", (keyword,), commit=True)
    return True

def remove_managed_keyword(keyword: str) -> bool:
    keyword = keyword.strip().lower()
    execute_query("DELETE FROM managed_keywords WHERE keyword = ?", (keyword,), commit=True)
    return True

def get_managed_keywords() -> List[str]:
    rows = execute_query("SELECT keyword FROM managed_keywords", fetch_all=True) or []
    return [r["keyword"] for r in rows]

# Bazani ishga tushirish
init_db()

# Standart qidiruv kalit so'zlarini (shu jumladan tajribasiz / stajyor) bazaga kiritish
try:
    for _k in ["python", "django", "fastapi", "tajribasiz", "stajyor", "junior", "intern", "backend", "telegram bot"]:
        add_managed_keyword(_k)
except Exception:
    pass


def update_hr_interview_time(username: str, interview_iso: str, reminded_2min: bool = False):
    """HR bilan kelishilgan suhbat vaqtini bazada saqlash"""
    username = username.strip().lstrip("@").lower()
    execute_query(
        "UPDATE tracked_hrs SET interview_time = ?, reminded_2min = ?, last_updated = CURRENT_TIMESTAMP WHERE LOWER(username) = ?",
        (interview_iso, 1 if reminded_2min else 0, username),
        commit=True
    )

def update_hr_reminded(username: str, reminded_2min: bool = True):
    """2 daqiqalik eslatma berilganligini belgilash"""
    username = username.strip().lstrip("@").lower()
    execute_query(
        "UPDATE tracked_hrs SET reminded_2min = ?, last_updated = CURRENT_TIMESTAMP WHERE LOWER(username) = ?",
        (1 if reminded_2min else 0, username),
        commit=True
    )

def get_backup_data() -> Dict[str, Any]:
    """#ALFRED_DB_BACKUP uchun to'liq ma'lumotlar tuzilmasini yig'ish"""
    hrs = get_all_tracked_hrs()
    tracked_hrs_dict = {}
    for h in hrs:
        u = h.get("username", "").strip().lstrip("@").lower()
        if u and not is_forbidden_hr_username(u):
            tracked_hrs_dict[u] = {
                "company": h.get("company", ""),
                "last_seen_msg_id": h.get("last_seen_msg_id") or 0,
                "interview_time": h.get("interview_time") or "",
                "reminded_2min": bool(h.get("reminded_2min")),
                "status": h.get("status") or "waiting"
            }
    # Seen job hashes
    rows = execute_query("SELECT content_hash FROM seen_vacancies WHERE content_hash != '' ORDER BY created_at DESC LIMIT 500", fetch_all=True) or []
    seen_hashes = list({r["content_hash"] for r in rows if r.get("content_hash")})
    # Applied jobs
    applied_rows = execute_query("SELECT vacancy_id, title, company, url, content_hash FROM applied_jobs ORDER BY applied_at DESC LIMIT 100", fetch_all=True) or []
    
    from datetime import datetime
    from config import TASHKENT_TZ
    return {
        "tracked_hrs": tracked_hrs_dict,
        "seen_job_hashes": seen_hashes,
        "applied_jobs": applied_rows,
        "custom_call_timers": get_pending_call_timers(),
        "updated_at": datetime.now(TASHKENT_TZ).isoformat()
    }

def restore_from_backup_data(data: Dict[str, Any]) -> bool:
    """Telegram #ALFRED_DB_BACKUP dan olingan JSON ni SQLite bazaga 100% tiklash"""
    if not isinstance(data, dict):
        return False
    try:
        # 1. Restore tracked HRs
        tracked_hrs = data.get("tracked_hrs", {})
        for u, info in tracked_hrs.items():
            u_clean = u.strip().lstrip("@").lower()
            if is_forbidden_hr_username(u_clean):
                continue
            existing = get_tracked_hr(u_clean)
            company = info.get("company", "")
            last_seen_id = info.get("last_seen_msg_id", 0)
            interview_time = info.get("interview_time", "")
            reminded = 1 if info.get("reminded_2min") else 0
            status = info.get("status", "waiting")
            if not existing:
                execute_query(
                    "INSERT INTO tracked_hrs (username, company, last_seen_msg_id, interview_time, reminded_2min, status) VALUES (?, ?, ?, ?, ?, ?)",
                    (u_clean, company, last_seen_id, interview_time, reminded, status),
                    commit=True
                )
            else:
                execute_query(
                    "UPDATE tracked_hrs SET company = ?, last_seen_msg_id = MAX(last_seen_msg_id, ?), interview_time = ?, reminded_2min = ?, status = ? WHERE LOWER(username) = ?",
                    (company or existing.get("company", ""), last_seen_id, interview_time or existing.get("interview_time", ""), reminded, status or existing.get("status", "waiting"), u_clean),
                    commit=True
                )

        # 2. Restore seen hashes
        hashes = data.get("seen_job_hashes", [])
        for h in hashes:
            if h:
                exist = execute_query("SELECT id FROM seen_vacancies WHERE content_hash = ?", (h,), fetch_one=True)
                if not exist:
                    fake_id = f"backup_{h[:16]}"
                    execute_query(
                        "INSERT OR IGNORE INTO seen_vacancies (id, title, company, url, source, match_score, ai_summary, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (fake_id, "Restored Job", "Company", "#", "backup", 70, "Restored from backup", h),
                        commit=True
                    )

        # 3. Restore applied jobs
        applied_list = data.get("applied_jobs", [])
        for item in applied_list:
            v_id = item.get("vacancy_id") or ""
            title = item.get("title") or ""
            comp = item.get("company") or ""
            url = item.get("url") or ""
            c_hash = item.get("content_hash") or ""
            if v_id:
                mark_job_applied(v_id, title, comp, url, content_hash=c_hash)

        # 4. Restore custom call timers
        timers = data.get("custom_call_timers", [])
        for t in timers:
            t_time = t.get("target_time")
            t_reason = t.get("reason", "")
            if t_time:
                exist = execute_query("SELECT id FROM custom_call_timers WHERE target_time = ? AND is_done = 0", (t_time,), fetch_one=True)
                if not exist:
                    add_custom_call_timer(t_time, t_reason)

        return True
    except Exception as e:
        import logging
        logging.getLogger("Alfred.Database").error(f"restore_from_backup_data xatosi: {e}")
        return False

def add_custom_call_timer(target_time_iso: str, reason: str = "") -> int:
    """Foydalanuvchi aytgan vaqtda qo'ng'iroq qilish taymerini saqlash"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO custom_call_timers (target_time, reason, is_done) VALUES (?, ?, 0)",
            (target_time_iso, reason)
        )
        conn.commit()
        return cursor.lastrowid or 1
    finally:
        conn.close()

def get_pending_call_timers() -> List[Dict[str, Any]]:
    """Kutilayotgan faol qo'ng'iroq taymerlarini olish"""
    return execute_query(
        "SELECT * FROM custom_call_timers WHERE is_done = 0 ORDER BY target_time ASC",
        fetch_all=True
    ) or []

def mark_call_timer_done(timer_id: int):
    """Qo'ng'iroq bajarildi deb belgilash"""
    execute_query(
        "UPDATE custom_call_timers SET is_done = 1 WHERE id = ?",
        (timer_id,),
        commit=True
    )

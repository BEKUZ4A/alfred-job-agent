import os
import sqlite3
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any
from config import DATABASE_URL

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
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS applied_jobs (
            id SERIAL PRIMARY KEY,
            vacancy_id TEXT UNIQUE,
            title TEXT,
            company TEXT,
            url TEXT,
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
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS managed_keywords (
            keyword TEXT PRIMARY KEY,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        
        # Ustunlar mavjudligini tekshirish
        for col, col_type in [("job_type", "TEXT DEFAULT 'vacancy'"), ("raw_text", "TEXT DEFAULT ''"), 
                              ("salary", "TEXT DEFAULT ''"), ("red_flag", "TEXT DEFAULT ''")]:
            try:
                cursor.execute(f"ALTER TABLE seen_vacancies ADD COLUMN {col} {col_type}")
            except sqlite3.OperationalError:
                pass

    conn.commit()
    conn.close()

# ----------------- VAKANSIYALAR FUNKSIYALARI -----------------

def is_vacancy_seen(vacancy_id: str) -> bool:
    row = execute_query("SELECT 1 FROM seen_vacancies WHERE id = ?", (vacancy_id,), fetch_one=True)
    return row is not None

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
    red_flag: str = ""
):
    if is_postgres():
        execute_query("""
        INSERT INTO seen_vacancies (id, title, company, url, source, match_score, ai_summary, job_type, raw_text, salary, red_flag, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT (id) DO UPDATE SET
            title = EXCLUDED.title,
            company = EXCLUDED.company,
            url = EXCLUDED.url,
            match_score = EXCLUDED.match_score,
            ai_summary = EXCLUDED.ai_summary,
            job_type = EXCLUDED.job_type,
            raw_text = EXCLUDED.raw_text,
            salary = EXCLUDED.salary,
            red_flag = EXCLUDED.red_flag
        """, (vacancy_id, title, company, url, source, match_score, ai_summary, job_type, raw_text, salary, red_flag), commit=True)
    else:
        execute_query("""
        INSERT OR REPLACE INTO seen_vacancies (id, title, company, url, source, match_score, ai_summary, job_type, raw_text, salary, red_flag, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (vacancy_id, title, company, url, source, match_score, ai_summary, job_type, raw_text, salary, red_flag), commit=True)

def get_vacancy_by_id(vacancy_id: str) -> Optional[Dict]:
    return execute_query("SELECT * FROM seen_vacancies WHERE id = ?", (vacancy_id,), fetch_one=True)

def get_recent_vacancies(limit: int = 10) -> List[Dict]:
    return execute_query("SELECT * FROM seen_vacancies ORDER BY created_at DESC LIMIT ?", (limit,), fetch_all=True) or []

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

def add_tracked_hr(username: str, company: str = "", cal_event_3d: str = "", cal_event_7d: str = ""):
    username = username.strip().lstrip("@").lower()
    if is_postgres():
        execute_query("""
        INSERT INTO tracked_hrs (username, company, cal_event_3d, cal_event_7d, status, last_updated)
        VALUES (?, ?, ?, ?, 'waiting', CURRENT_TIMESTAMP)
        ON CONFLICT (username) DO UPDATE SET
            company = EXCLUDED.company,
            cal_event_3d = EXCLUDED.cal_event_3d,
            cal_event_7d = EXCLUDED.cal_event_7d,
            status = 'waiting',
            last_updated = CURRENT_TIMESTAMP
        """, (username, company, cal_event_3d, cal_event_7d), commit=True)
    else:
        execute_query("""
        INSERT OR REPLACE INTO tracked_hrs (username, company, cal_event_3d, cal_event_7d, status, last_updated)
        VALUES (?, ?, ?, ?, 'waiting', CURRENT_TIMESTAMP)
        """, (username, company, cal_event_3d, cal_event_7d), commit=True)

def get_tracked_hr(username: str) -> Optional[Dict]:
    username = username.strip().lstrip("@").lower()
    return execute_query("SELECT * FROM tracked_hrs WHERE LOWER(username) = ?", (username,), fetch_one=True)

def get_all_tracked_hrs() -> List[Dict]:
    return execute_query("SELECT * FROM tracked_hrs ORDER BY added_at DESC", fetch_all=True) or []

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

def delete_tracked_hr(username: str):
    username = username.strip().lstrip("@").lower()
    execute_query("DELETE FROM tracked_hrs WHERE LOWER(username) = ?", (username,), commit=True)

# ----------------- TOPSHIRILGAN ISHLAR (APPLIED JOBS) -----------------

def mark_job_applied(vacancy_id: str, title: str, company: str, url: str) -> bool:
    if is_postgres():
        execute_query("""
        INSERT INTO applied_jobs (vacancy_id, title, company, url, status, applied_at)
        VALUES (?, ?, ?, ?, 'applied', CURRENT_TIMESTAMP)
        ON CONFLICT (vacancy_id) DO NOTHING
        """, (vacancy_id, title, company, url), commit=True)
    else:
        execute_query("""
        INSERT OR IGNORE INTO applied_jobs (vacancy_id, title, company, url, status, applied_at)
        VALUES (?, ?, ?, ?, 'applied', CURRENT_TIMESTAMP)
        """, (vacancy_id, title, company, url), commit=True)
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

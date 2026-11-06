import sqlite3
import os
from datetime import datetime
from typing import List, Dict, Optional

DB_FILE = os.path.join(os.path.dirname(__file__), "seen_vacancies.db")

def get_connection():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Ma'lumotlar bazasini yaratish yoki tayyorlash"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS seen_vacancies (
        id TEXT PRIMARY KEY,
        title TEXT,
        company TEXT,
        url TEXT,
        source TEXT,
        match_score INTEGER,
        ai_summary TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    conn.commit()
    conn.close()

def is_vacancy_seen(vacancy_id: str) -> bool:
    """Vakansiya ilgari ko'rilganmi yoki yo'qligini tekshirish"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM seen_vacancies WHERE id = ?", (vacancy_id,))
    row = cursor.fetchone()
    conn.close()
    return row is not None

def mark_vacancy_seen(vacancy_id: str, title: str, company: str, url: str, source: str, match_score: int, ai_summary: str):
    """Vakansiyani ko'rilgan deb belgilash"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT OR REPLACE INTO seen_vacancies (id, title, company, url, source, match_score, ai_summary, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (vacancy_id, title, company, url, source, match_score, ai_summary, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()

def get_recent_vacancies(limit: int = 10) -> List[Dict]:
    """Oxirgi topilgan vakansiyalar ro'yxatini olish"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM seen_vacancies ORDER BY created_at DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

# Dastur ishga tushganda bazani ishga tushirib qo'yamiz
init_db()

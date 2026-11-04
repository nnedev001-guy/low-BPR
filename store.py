"""SQLite 저장소와 회사 명단 로딩."""
import csv
import os
import sqlite3
from contextlib import contextmanager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "data", "prices.db"))
COMPANIES_CSV = os.environ.get("COMPANIES_CSV", os.path.join(BASE_DIR, "data", "companies.csv"))
INDEXES = {"KOSPI": "코스피", "KOSDAQ": "코스닥"}


_schema_ready = False


@contextmanager
def connect():
    """SQLite 연결. 스키마는 프로세스당 한 번만 만든다.
    잠금 대기는 10초로 제한해, 문제가 있으면 요청이 무한정 멈추지 않고 오류로 끝나게 한다."""
    global _schema_ready
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        if not _schema_ready:
            conn.execute("CREATE TABLE IF NOT EXISTS prices (code TEXT NOT NULL, date TEXT NOT NULL, "
                         "open REAL, high REAL, low REAL, close REAL, volume REAL, PRIMARY KEY (code, date))")
            conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
            conn.commit()
            _schema_ready = True
        yield conn
        conn.commit()
    finally:
        conn.close()


def load_companies():
    """data/companies.csv (code,name,market) 를 읽는다. 없으면 빈 목록."""
    if not os.path.exists(COMPANIES_CSV):
        return []
    rows = []
    with open(COMPANIES_CSV, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            code = (r.get("code") or "").strip()
            if not code or code.startswith("#"):
                continue
            rows.append({
                "code": code.zfill(6),
                "name": (r.get("name") or "").strip(),
                "market": (r.get("market") or "").strip().upper(),
            })
    return rows


def get_meta(key, default=None):
    with connect() as c:
        row = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_meta(key, value):
    with connect() as c:
        c.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", (key, str(value)))

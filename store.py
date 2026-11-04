"""시세 저장소와 회사 명단 로딩.

Render 등 배포 환경에서 SQLite 잠금("database is locked") 문제가 생기지 않도록
- 웹 요청은 DB를 읽기 전용으로만 연다.
- 갱신(refresh)은 새 DB 파일을 따로 만든 뒤 os.replace 로 한 번에 바꿔 끼운다.
- 갱신 상태(마지막 갱신 시각·오류)는 DB가 아닌 status.json 에 기록한다.
그래서 읽기와 쓰기가 같은 파일을 동시에 잠그는 일이 없다.
"""
import csv
import json
import os
import sqlite3
import threading
from contextlib import contextmanager

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE_DIR, "data", "prices.db"))
STATUS_PATH = os.path.join(os.path.dirname(DB_PATH), "status.json")
# 명단 이력: data/lists/<공표일 YYYY-MM-DD>.csv — 공표일부터 다음 명단 공표 전날까지 적용
LISTS_DIR = os.environ.get("LISTS_DIR", os.path.join(BASE_DIR, "data", "lists"))
INDEXES = {"KOSPI": "코스피", "KOSDAQ": "코스닥"}

SCHEMA = ("CREATE TABLE IF NOT EXISTS prices (code TEXT NOT NULL, date TEXT NOT NULL, "
          "open REAL, high REAL, low REAL, close REAL, volume REAL, PRIMARY KEY (code, date))")
SCHEMA_INFO = ("CREATE TABLE IF NOT EXISTS info (code TEXT PRIMARY KEY, market_cap REAL, "
               "shares REAL, as_of TEXT)")   # 시가총액(억 원), 주식수(시총/당시 종가로 역산)
_status_lock = threading.Lock()


def _ensure_db():
    """DB 파일이 없으면 빈 DB를 만들어 원자적으로 둔다."""
    if os.path.exists(DB_PATH):
        return
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    tmp = f"{DB_PATH}.init.{os.getpid()}.{threading.get_ident()}"
    conn = sqlite3.connect(tmp)
    conn.execute(SCHEMA)
    conn.execute(SCHEMA_INFO)
    conn.commit()
    conn.close()
    if not os.path.exists(DB_PATH):
        os.replace(tmp, DB_PATH)
    else:
        os.remove(tmp)


@contextmanager
def connect():
    """읽기 전용 연결 (웹 요청·보고서용)."""
    _ensure_db()
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def replace_prices(rows_by_code, market_caps=None):
    """새 DB 파일에 시세를 모두 쓴 뒤 기존 DB와 한 번에 교체.
    rows_by_code: {code: [(date, open, high, low, close, volume), ...]}
    market_caps: {code: 시가총액(억 원)}
    받지 못한 종목(None)·시가총액은 기존 DB에 있던 값을 그대로 옮긴다."""
    _ensure_db()
    market_caps = market_caps or {}
    tmp = f"{DB_PATH}.new.{os.getpid()}"
    if os.path.exists(tmp):
        os.remove(tmp)
    new = sqlite3.connect(tmp)
    new.execute(SCHEMA)
    new.execute(SCHEMA_INFO)
    keep = [code for code, rows in rows_by_code.items() if not rows]
    with connect() as old:
        for code in keep:
            new.executemany(
                "INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?)",
                [tuple(r) for r in old.execute("SELECT * FROM prices WHERE code=?", (code,))])
        try:   # 이전 버전 DB에는 info 테이블이 없을 수 있음
            old_info = {r["code"]: tuple(r) for r in old.execute("SELECT * FROM info")}
        except sqlite3.OperationalError:
            old_info = {}
    for code, rows in rows_by_code.items():
        if rows:
            new.executemany("INSERT OR REPLACE INTO prices VALUES (?,?,?,?,?,?,?)",
                            [(code, *r) for r in rows])
    today = max((r[0] for rows in rows_by_code.values() if rows for r in rows), default="")
    for code, cap in market_caps.items():
        rows = rows_by_code.get(code) or []
        last_close = next((r[4] for r in reversed(rows) if r[4]), None)
        shares = cap * 1e8 / last_close if last_close else None
        old_info.pop(code, None)
        new.execute("INSERT OR REPLACE INTO info VALUES (?,?,?,?)", (code, cap, shares, today))
    for row in old_info.values():
        new.execute("INSERT OR REPLACE INTO info VALUES (?,?,?,?)", row)
    new.commit()
    new.close()
    os.replace(tmp, DB_PATH)


def market_caps():
    """{code: {"market_cap": 억 원, "as_of": 날짜}} — 최신 종가 × 주식수로 다시 계산."""
    out = {}
    with connect() as c:
        try:
            rows = c.execute(
                "SELECT i.code, i.market_cap, i.shares, i.as_of, "
                "(SELECT close FROM prices p WHERE p.code=i.code AND p.close>0 ORDER BY date DESC LIMIT 1) AS last_close, "
                "(SELECT date FROM prices p WHERE p.code=i.code AND p.close>0 ORDER BY date DESC LIMIT 1) AS last_date "
                "FROM info i").fetchall()
        except sqlite3.OperationalError:
            return out
    for r in rows:
        cap = r["shares"] * r["last_close"] / 1e8 if r["shares"] and r["last_close"] else r["market_cap"]
        out[r["code"]] = {"market_cap": cap, "as_of": r["last_date"] or r["as_of"]}
    return out


def list_versions():
    """보관된 명단의 공표일 목록 (오름차순)."""
    if not os.path.isdir(LISTS_DIR):
        return []
    out = []
    for fn in os.listdir(LISTS_DIR):
        name, ext = os.path.splitext(fn)
        if ext == ".csv" and len(name) == 10 and name[4] == "-" and name[7] == "-":
            out.append(name)
    return sorted(out)


def list_version_for(as_of=None):
    """as_of 날짜에 적용되는 명단의 공표일. as_of 가 없으면 최신 명단,
    첫 명단 공표 전 날짜면 첫 명단."""
    vers = list_versions()
    if not vers:
        return None
    if not as_of:
        return vers[-1]
    ok = [v for v in vers if v <= as_of]
    return ok[-1] if ok else vers[0]


def _read_list(version):
    rows = []
    with open(os.path.join(LISTS_DIR, version + ".csv"), encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            code = (r.get("code") or "").strip()
            if not code or code.startswith("#"):
                continue
            rows.append({
                "code": code.zfill(6),
                "name": (r.get("name") or "").strip(),
                "market": (r.get("market") or "").strip().upper(),
                "pbr": (r.get("pbr") or "").strip(),
                "gics": (r.get("gics") or "").strip(),
            })
    return rows


def load_companies(as_of=None):
    """as_of 날짜에 적용되는 명단 (기본: 최신 명단). 없으면 빈 목록."""
    ver = list_version_for(as_of)
    return _read_list(ver) if ver else []


def all_companies():
    """보관된 모든 명단의 합집합 (같은 종목은 최신 명단 정보 우선). 시세 수집용."""
    out = {}
    for ver in list_versions():
        for c in _read_list(ver):
            out[c["code"]] = c
    return list(out.values())


def list_history():
    """[{date, count, kospi, kosdaq, added, removed}] — 명단별 규모와 직전 명단 대비 편입·제외 수."""
    out, prev = [], None
    for ver in list_versions():
        rows = _read_list(ver)
        codes = {c["code"] for c in rows}
        out.append({
            "date": ver, "count": len(rows),
            "kospi": sum(1 for c in rows if c["market"] == "KOSPI"),
            "kosdaq": sum(1 for c in rows if c["market"] == "KOSDAQ"),
            "added": len(codes - prev) if prev is not None else None,
            "removed": len(prev - codes) if prev is not None else None,
        })
        prev = codes
    return out


def _read_status():
    try:
        with open(STATUS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def get_meta(key, default=None):
    v = _read_status().get(key)
    return default if v is None else str(v)


def set_meta(key, value):
    with _status_lock:
        os.makedirs(os.path.dirname(STATUS_PATH), exist_ok=True)
        st = _read_status()
        st[key] = str(value)
        tmp = f"{STATUS_PATH}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(st, f, ensure_ascii=False)
        os.replace(tmp, STATUS_PATH)

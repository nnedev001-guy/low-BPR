"""네이버 차트(fchart)에서 151개사 + 코스피/코스닥 일봉을 받아 SQLite에 저장.

사용: python refresh.py            (평일 장 마감 후 cron 으로 실행)
"""
import datetime as dt
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

import store

KST = ZoneInfo("Asia/Seoul")
START_DATE = "20261026"   # 11월 1주 직전 주부터 보관
FCHART = "https://fchart.stock.naver.com/sise.nhn?symbol={sym}&timeframe=day&count={n}&requestType=0"
ITEM_RE = re.compile(r'<item data="([^"]+)"')
NAME_RE = re.compile(r'<chartdata[^>]*name="([^"]*)"')


def fetch(symbol, count):
    url = FCHART.format(sym=symbol, n=count)
    req = urllib.request.Request(url, headers={"User-Agent": "lowpbr-dashboard/1.0"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                text = r.read().decode("euc-kr", errors="replace")
            break
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
    name = NAME_RE.search(text)  # 이름은 참고용
    rows = []
    for raw in ITEM_RE.findall(text):
        d, o, h, l, c, v = raw.split("|")
        if d < START_DATE:
            continue
        rows.append((f"{d[:4]}-{d[4:6]}-{d[6:]}", float(o), float(h), float(l), float(c), float(v)))
    return (name.group(1) if name else ""), rows


def days_needed():
    start = dt.datetime.strptime(START_DATE, "%Y%m%d").date()
    return max(10, (dt.date.today() - start).days + 10)


def run(verbose=True):
    companies = store.load_companies()
    n = days_needed()
    symbols = list(store.INDEXES) + [c["code"] for c in companies]
    errors, ok = [], 0

    def one(sym):
        try:
            return sym, fetch(sym, n)[1], None
        except Exception as e:  # 네트워크 오류 등
            return sym, None, str(e)

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(one, symbols))
    with store.connect() as c:
        for sym, rows, err in results:
            if err or not rows:
                errors.append(f"{sym}: {err or '데이터 없음'}")
                continue
            c.executemany(
                "INSERT OR REPLACE INTO prices(code,date,open,high,low,close,volume) VALUES (?,?,?,?,?,?,?)",
                [(sym, *r) for r in rows],
            )
            ok += 1
    now = dt.datetime.now(KST).isoformat(timespec="seconds")
    store.set_meta("last_refresh", now)
    store.set_meta("last_refresh_errors", "\n".join(errors))
    if verbose:
        print(f"[{now}] 갱신 완료: {ok}/{len(symbols)} 종목, 오류 {len(errors)}건")
        for e in errors:
            print("  -", e)
    return ok, errors


if __name__ == "__main__":
    ok, errors = run()
    sys.exit(0 if ok else 1)

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
PROGRESS = {"done": 0, "total": 0, "errors": 0}   # 웹 화면 진행률 표시용
START_DATE = "20261026"   # 11월 1주 직전 주부터 보관
FCHART = "https://fchart.stock.naver.com/sise.nhn?symbol={sym}&timeframe=day&count={n}&requestType=0"
ITEM_RE = re.compile(r'<item data="([^"]+)"')
NAME_RE = re.compile(r'<chartdata[^>]*name="([^"]*)"')


def fetch(symbol, count):
    url = FCHART.format(sym=symbol, n=count)
    req = urllib.request.Request(url, headers={"User-Agent": "lowpbr-dashboard/1.0"})
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                text = r.read().decode("euc-kr", errors="replace")
            break
        except Exception:
            if attempt == 1:
                raise
            time.sleep(2)
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

    PROGRESS.update(done=0, total=len(symbols), errors=0)
    print(f"[refresh] 시작: {len(symbols)}개 종목", flush=True)
    # 시세 서버에 먼저 한 번 접속해 보고, 안 되면 151개를 다 기다리지 않고 바로 중단
    try:
        fetch("KOSPI", 5)
    except Exception as e:
        msg = f"시세 서버(fchart.stock.naver.com) 접속 실패: {type(e).__name__}: {e}"
        print("[refresh]", msg, flush=True)
        store.set_meta("last_refresh", dt.datetime.now(KST).isoformat(timespec="seconds"))
        store.set_meta("last_refresh_ok", 0)
        store.set_meta("last_refresh_errors", msg)
        return 0, [msg]

    def one(sym):
        try:
            res = sym, fetch(sym, n)[1], None
        except Exception as e:  # 네트워크 오류 등
            res = sym, None, f"{type(e).__name__}: {e}"
            PROGRESS["errors"] += 1
        PROGRESS["done"] += 1
        return res

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(one, symbols))
    rows_by_code = {}
    for sym, rows, err in results:
        if err or not rows:
            errors.append(f"{sym}: {err or '데이터 없음'}")
            rows_by_code[sym] = None          # 기존 값 유지
            continue
        rows_by_code[sym] = rows
        ok += 1
    if ok:
        store.replace_prices(rows_by_code)    # 새 파일로 만든 뒤 한 번에 교체 (잠금 없음)
    now = dt.datetime.now(KST).isoformat(timespec="seconds")
    store.set_meta("last_refresh", now)
    store.set_meta("last_refresh_errors", "\n".join(errors))
    store.set_meta("last_refresh_ok", ok)
    if verbose:
        print(f"[{now}] 갱신 완료: {ok}/{len(symbols)} 종목, 오류 {len(errors)}건", flush=True)
        for e in errors[:10]:
            print("  -", e, flush=True)
    return ok, errors


if __name__ == "__main__":
    ok, errors = run()
    sys.exit(0 if ok else 1)

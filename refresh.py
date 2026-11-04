"""네이버 차트(fchart)에서 151개사 + 코스피/코스닥 일봉을, 네이버 증권에서 시가총액을 받아 저장.

사용: python refresh.py            (평일 장 마감 후 cron 으로 실행)
"""
import datetime as dt
import json
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
MCAP_URL = "https://m.stock.naver.com/api/stock/{code}/integration"


def parse_krw_eok(text):
    """'34조 5,678억' / '5,678억' / '1조' / '123456' 같은 문자열을 억 원 단위 숫자로."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    t = str(text).replace(",", "").replace(" ", "").replace("원", "")
    m = re.fullmatch(r"(?:(\d+(?:\.\d+)?)조)?(?:(\d+(?:\.\d+)?)억)?", t)
    if m and (m.group(1) or m.group(2)):
        return float(m.group(1) or 0) * 10000 + float(m.group(2) or 0)
    try:
        return float(t)
    except ValueError:
        return None


def _find_market_value(obj):
    """네이버 응답 JSON에서 시가총액 항목을 찾는다 (code/key 가 marketValue 인 것)."""
    if isinstance(obj, dict):
        if obj.get("code") == "marketValue" and "value" in obj:
            return obj["value"]
        for k, v in obj.items():
            if k in ("marketValue", "marketValueFull", "marketCap") and not isinstance(v, (dict, list)):
                return v
        for v in obj.values():
            r = _find_market_value(v)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_market_value(v)
            if r is not None:
                return r
    return None


def fetch_market_cap(code):
    """시가총액(억 원). 받지 못하면 예외."""
    req = urllib.request.Request(MCAP_URL.format(code=code),
                                 headers={"User-Agent": "Mozilla/5.0 (lowpbr-dashboard)", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.loads(r.read().decode("utf-8"))
    val = parse_krw_eok(_find_market_value(data))
    if not val:
        raise ValueError("시가총액 항목 없음")
    return val


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

    def cap(code):
        try:
            return code, fetch_market_cap(code), None
        except Exception as e:
            return code, None, f"{type(e).__name__}: {e}"

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(one, symbols))
        caps = list(pool.map(cap, [c["code"] for c in companies]))
    cap_ok = {code: v for code, v, _ in caps if v}
    cap_err = [f"{code}: {e}" for code, v, e in caps if not v]
    print(f"[refresh] 시가총액 {len(cap_ok)}/{len(caps)}개사" + (f", 예: {cap_err[0]}" if cap_err else ""), flush=True)
    store.set_meta("market_cap_ok", len(cap_ok))
    store.set_meta("market_cap_error", cap_err[0] if cap_err else "")
    rows_by_code = {}
    for sym, rows, err in results:
        if err or not rows:
            errors.append(f"{sym}: {err or '데이터 없음'}")
            rows_by_code[sym] = None          # 기존 값 유지
            continue
        rows_by_code[sym] = rows
        ok += 1
    if ok:
        store.replace_prices(rows_by_code, cap_ok)    # 새 파일로 만든 뒤 한 번에 교체 (잠금 없음)
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

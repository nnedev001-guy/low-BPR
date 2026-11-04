"""저PBR 기업 주간 주가 추이 대시보드 (Flask)."""
import datetime as dt
import os
import threading
import time
import traceback
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, request, send_from_directory

import build_info
import refresh
import report
import store

KST = ZoneInfo("Asia/Seoul")
app = Flask(__name__)
_lock = threading.Lock()
BUILD = build_info.load()
print('[app] build', BUILD, flush=True)


def last_expected_close(now=None):
    """가장 최근에 장이 마감된 평일 16:30 KST (데이터 반영 여유 포함)."""
    now = now or dt.datetime.now(KST)
    t = now.replace(hour=16, minute=30, second=0, microsecond=0)
    if now < t:
        t -= dt.timedelta(days=1)
    while t.weekday() >= 5:
        t -= dt.timedelta(days=1)
    return t


def is_stale():
    last = store.get_meta("last_refresh")
    if not last:
        return True
    if store.get_meta("last_refresh_ok", "1") == "0":   # 지난번에 하나도 못 받았으면 10분마다 재시도
        return True
    return dt.datetime.fromisoformat(last) < last_expected_close()


def refresh_async():
    if not _lock.acquire(blocking=False):
        return False

    def job():
        try:
            refresh.run()
        except Exception as e:
            print("[refresh] 실패:", repr(e), flush=True)
            store.set_meta("last_refresh_errors", f"전체 실패: {e!r}")
        finally:
            _lock.release()
    threading.Thread(target=job, daemon=True).start()
    return True


def scheduler():
    """웹 서비스 안에서 평일 16:30 KST 이후 하루 한 번 자동 갱신."""
    while True:
        try:
            if is_stale():
                refresh_async()
        except Exception as e:
            print("scheduler error:", e)
        time.sleep(600)


if os.environ.get("DISABLE_SCHEDULER") != "1":
    threading.Thread(target=scheduler, daemon=True).start()


@app.route("/")
def index():
    return send_from_directory(os.path.join(app.root_path, "templates"), "index.html")


@app.before_request
def _t0():
    request._t0 = time.time()


@app.after_request
def _log(resp):
    if request.path.startswith("/api/"):
        print(f"[req] {request.method} {request.full_path} -> {resp.status_code} "
              f"{(time.time() - getattr(request, '_t0', time.time())) * 1000:.0f}ms", flush=True)
    return resp


@app.route("/api/weeks")
def weeks():
    companies = store.load_companies()
    out = {
        "companies": companies,
        "universe": len(companies),
        "refreshing": _lock.locked(),
        "progress": dict(refresh.PROGRESS),
        "build": BUILD,
        "weeks": [], "months": [], "range": {"min": None, "max": None},
        "last_refresh": None, "last_refresh_ok": 0, "last_refresh_errors": [],
    }
    try:
        caps = store.market_caps()
        for c in companies:
            c["market_cap"] = (caps.get(c["code"]) or {}).get("market_cap")
        companies.sort(key=lambda c: (c["market_cap"] is None, -(c["market_cap"] or 0), c["name"]))
        out["market_cap_as_of"] = max((v["as_of"] or "" for v in caps.values()), default=None)
        out.update({
            "weeks": report.available_weeks(),
            "months": report.available_months(),
            "range": report.data_range(),
            "last_refresh": store.get_meta("last_refresh"),
            "last_refresh_ok": int(store.get_meta("last_refresh_ok", "0") or 0),
            "last_refresh_errors": (store.get_meta("last_refresh_errors") or "").splitlines()[:5],
        })
    except Exception as e:  # DB 문제여도 화면에는 원인을 보여 준다
        print("[weeks] DB 오류:", repr(e), flush=True)
        traceback.print_exc()
        out["last_refresh_errors"] = [f"데이터 파일 오류: {e!r}"]
    return jsonify(out)


@app.route("/api/diag")
def diag():
    """배포 환경 점검용: 단계별 소요 시간."""
    steps, t = [], time.time()

    def mark(name, fn):
        nonlocal t
        try:
            val = fn()
            steps.append({"step": name, "ok": True, "ms": round((time.time() - t) * 1000), "value": val})
        except Exception as e:
            steps.append({"step": name, "ok": False, "ms": round((time.time() - t) * 1000), "error": repr(e)})
        t = time.time()

    mark("companies", lambda: len(store.load_companies()))
    mark("db_path", lambda: store.DB_PATH)
    mark("db_meta", lambda: store.get_meta("last_refresh"))
    mark("db_rows", lambda: _count_rows())
    mark("naver", lambda: len(refresh.fetch("KOSPI", 3)[1]))
    mark("market_cap_sample", lambda: refresh.fetch_market_cap("004020"))
    mark("market_cap_stored", lambda: len(store.market_caps()))
    mark("market_cap_last_error", lambda: store.get_meta("market_cap_error"))
    return jsonify({"build": BUILD, "refreshing": _lock.locked(), "progress": dict(refresh.PROGRESS),
                    "threads": [th.name for th in threading.enumerate()], "steps": steps})


def _count_rows():
    with store.connect() as c:
        return c.execute("SELECT COUNT(*) FROM prices").fetchone()[0]


@app.route("/api/report")
def api_report():
    kind = request.args.get("kind", "week")
    a, b = request.args.get("a") or request.args.get("week"), request.args.get("b")
    band = min(max(float(request.args.get("band", 0.5)), 0.0), 5.0)
    if not a:
        ws = report.available_weeks()
        if not ws:
            return jsonify({"error": "데이터가 아직 없습니다. 잠시 후 다시 시도하세요."}), 404
        kind, a = "week", ws[0]["monday"]
    try:
        start, end, label = report.resolve_period(kind, a, b)
    except ValueError:
        return jsonify({"error": "기간 형식이 올바르지 않습니다."}), 400
    return jsonify(report.build(start, end, label, band))


@app.route("/api/compare")
def api_compare():
    codes = [c.strip().zfill(6) for c in request.args.get("codes", "").split(",") if c.strip()][:10]
    start, end = request.args.get("start", ""), request.args.get("end", "")
    if not codes or not start or not end:
        return jsonify({"error": "기업과 기간을 선택하세요."}), 400
    if end < start:
        start, end = end, start
    return jsonify(report.compare(codes, start, end))


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    started = refresh_async()
    return jsonify({"started": started, "refreshing": True})


@app.route("/healthz")
def healthz():
    return "ok"


print(f"[app] 시작 · DB {store.DB_PATH} · 명단 {len(store.load_companies())}개사", flush=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), debug=False)

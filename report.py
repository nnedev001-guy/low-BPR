"""기간별 보고서 / 기업 비교 계산."""
import datetime as dt
import statistics

import store

WEEKDAYS = "월화수목금"


def week_label(monday):
    """월요일이 속한 달 기준 n주차. 예: 2026-11-02 -> '11월 1주'."""
    return f"{monday.month}월 {(monday.day - 1) // 7 + 1}주"


REPORT_START = "2026-11-02"   # 저PBR 명단 공표일


def _trading_dates(start="0000-00-00", end="9999-99-99"):
    with store.connect() as c:
        return [r["date"] for r in c.execute(
            "SELECT DISTINCT date FROM prices WHERE code='KOSPI' AND date BETWEEN ? AND ? ORDER BY date",
            (start, end))]


def available_weeks():
    """DB에 거래일이 있는 주(월요일 기준) 목록, 최신 주가 먼저."""
    weeks = {}
    for d in _trading_dates(REPORT_START):
        day = dt.date.fromisoformat(d)
        monday = day - dt.timedelta(days=day.weekday())
        weeks.setdefault(monday, []).append(d)
    out = []
    for monday in sorted(weeks, reverse=True):
        friday = monday + dt.timedelta(days=4)
        out.append({
            "monday": monday.isoformat(),
            "friday": friday.isoformat(),
            "label": week_label(monday),
            "range": f"{monday.month}/{monday.day}~{friday.month}/{friday.day}",
            "trading_days": len(weeks[monday]),
        })
    return out


def available_months():
    months = sorted({d[:7] for d in _trading_dates(REPORT_START)}, reverse=True)
    return [{"month": m, "label": f"{int(m[:4])}년 {int(m[5:])}월"} for m in months]


def data_range():
    ds = _trading_dates()
    return {"min": ds[0] if ds else None, "max": ds[-1] if ds else None}


def resolve_period(kind, a, b=None):
    """kind: week(a=월요일) | weeks(a=시작 월요일, b=끝 월요일) | month(a=YYYY-MM).
    반환: (start, end, label)"""
    if kind == "month":
        y, m = int(a[:4]), int(a[5:7])
        start = dt.date(y, m, 1)
        end = (dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1))
        return max(start.isoformat(), REPORT_START), end.isoformat(), f"{y}년 {m}월"
    m1 = dt.date.fromisoformat(a)
    m1 -= dt.timedelta(days=m1.weekday())
    m2 = m1
    if kind == "weeks" and b:
        m2 = dt.date.fromisoformat(b)
        m2 -= dt.timedelta(days=m2.weekday())
        if m2 < m1:
            m1, m2 = m2, m1
    label = week_label(m1) if m1 == m2 else f"{week_label(m1)}~{week_label(m2)}"
    return m1.isoformat(), (m2 + dt.timedelta(days=4)).isoformat(), label


def _series(c, code, start, end):
    return [dict(r) for r in c.execute(
        "SELECT date, open, high, low, close, volume FROM prices "
        "WHERE code=? AND date BETWEEN ? AND ? ORDER BY date", (code, start, end))]


def _change(rows):
    """첫 거래일 시가 -> 마지막 거래일 종가 (시가 0 = 거래정지일은 건너뜀)."""
    traded = [r for r in rows if r["open"] and r["volume"]]
    if not traded:
        return None
    o, cl = traded[0]["open"], traded[-1]["close"]
    return {"open": o, "close": cl, "pct": (cl / o - 1) * 100,
            "start": traded[0]["date"], "end": traded[-1]["date"]}


def build(start, end, label, band=0.5):
    companies = store.load_companies()

    with store.connect() as c:
        idx_dates = [r["date"] for r in c.execute(
            "SELECT date FROM prices WHERE code='KOSPI' AND date BETWEEN ? AND ? ORDER BY date", (start, end))]
        indexes = {}
        for code, name in store.INDEXES.items():
            rows = _series(c, code, start, end)
            indexes[code] = {"name": name, "change": _change(rows), "daily": rows}

        items, missing = [], []
        for co in companies:
            rows = _series(c, co["code"], start, end)
            ch = _change(rows)
            if ch is None:
                missing.append(co)
                continue
            mkt = co["market"] if co["market"] in store.INDEXES else None
            bench = indexes[mkt]["change"]["pct"] if mkt and indexes[mkt]["change"] else None
            pct = ch["pct"]
            status = "up" if pct > band else ("down" if pct < -band else "flat")
            items.append({
                **co, **ch, "status": status,
                "bench": bench,
                "excess": (pct - bench) if bench is not None else None,
                "daily": rows,
            })

    groups = {k: sorted([i for i in items if i["status"] == k], key=lambda x: -x["pct"])
              for k in ("up", "flat", "down")}
    total = len(items)

    def summary(lst):
        pcts = [i["pct"] for i in lst]
        return {
            "count": len(lst),
            "share": (len(lst) / total * 100) if total else 0,
            "avg": statistics.mean(pcts) if pcts else None,
            "median": statistics.median(pcts) if pcts else None,
            "max": max(pcts) if pcts else None,
            "min": min(pcts) if pcts else None,
        }

    all_pcts = [i["pct"] for i in items]
    beat = [i for i in items if i["excess"] is not None and i["excess"] > 0]
    by_market = {}
    for mkt in store.INDEXES:
        lst = [i for i in items if i["market"] == mkt]
        p = [i["pct"] for i in lst]
        by_market[mkt] = {
            "count": len(lst),
            "avg": statistics.mean(p) if p else None,
            "median": statistics.median(p) if p else None,
            "up": sum(1 for i in lst if i["status"] == "up"),
            "flat": sum(1 for i in lst if i["status"] == "flat"),
            "down": sum(1 for i in lst if i["status"] == "down"),
            "index_pct": indexes[mkt]["change"]["pct"] if indexes[mkt]["change"] else None,
        }

    # 일별 누적 수익률(주 첫 시가 대비 각 날 종가): 저PBR 동일가중 평균 vs 지수
    trend = []
    for d in idx_dates:
        vals = []
        for i in items:
            row = next((r for r in i["daily"] if r["date"] == d and r["close"]), None)
            if row:
                vals.append((row["close"] / i["open"] - 1) * 100)
        point = {"date": d, "lowpbr": statistics.mean(vals) if vals else None}
        for code, ix in indexes.items():
            row = next((r for r in ix["daily"] if r["date"] == d), None)
            point[code] = ((row["close"] / ix["change"]["open"] - 1) * 100) if row and ix["change"] else None
        trend.append(point)

    from zoneinfo import ZoneInfo
    now = dt.datetime.now(ZoneInfo("Asia/Seoul"))
    last = dt.date.fromisoformat(end)
    complete = now.date() > last or (now.date() == last and (now.hour, now.minute) >= (15, 40))
    intraday = bool(idx_dates) and idx_dates[-1] == now.date().isoformat() and (now.hour, now.minute) < (15, 40)

    return {
        "week": {"monday": start, "friday": end, "label": label,
                 "trading_days": idx_dates, "complete": complete, "intraday": intraday,
                 "weekday_names": [WEEKDAYS[dt.date.fromisoformat(d).weekday()] for d in idx_dates]},
        "band": band,
        "universe": len(companies),
        "total": total,
        "summary": {k: summary(v) for k, v in groups.items()},
        "overall": {
            "avg": statistics.mean(all_pcts) if all_pcts else None,
            "median": statistics.median(all_pcts) if all_pcts else None,
            "beat_count": len(beat),
            "beat_share": (len(beat) / total * 100) if total else 0,
        },
        "by_market": by_market,
        "indexes": {k: {"name": v["name"], "change": v["change"]} for k, v in indexes.items()},
        "trend": trend,
        "groups": groups,
        "missing": missing,
        "last_refresh": store.get_meta("last_refresh"),
    }


def compare(codes, start, end):
    """선택 기업들의 기간 내 일별 시가·종가와 누적 등락률(첫 시가 기준)."""
    names = {c["code"]: c for c in store.load_companies()}
    out = []
    with store.connect() as c:
        dates = [r["date"] for r in c.execute(
            "SELECT date FROM prices WHERE code='KOSPI' AND date BETWEEN ? AND ? ORDER BY date", (start, end))]
        for code in list(codes) + list(store.INDEXES):
            rows = _series(c, code, start, end)
            ch = _change(rows)
            meta = names.get(code) or {"code": code, "name": store.INDEXES.get(code, code),
                                       "market": "INDEX" if code in store.INDEXES else ""}
            traded = [r for r in rows if r["open"] and r["volume"]]
            base = ch["open"] if ch else None
            out.append({
                **meta, "is_index": code in store.INDEXES, "change": ch, "daily": rows,
                "high": max((r["high"] for r in traded), default=None),
                "low": min((r["low"] for r in traded), default=None),
                "cum": {r["date"]: ((r["close"] / base - 1) * 100 if base and r["close"] and (r["volume"] or code in store.INDEXES) else None)
                        for r in rows},
            })
    return {"start": start, "end": end, "dates": dates, "series": out,
            "last_refresh": store.get_meta("last_refresh")}

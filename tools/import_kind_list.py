"""KIND에서 내려받은 저PBR 기업 엑셀을 명단 이력 파일로 변환.

사용법:
    python tools/import_kind_list.py <내려받은 파일.xls> [--date YYYY-MM-DD] [--dry-run]

- KIND 엑셀은 실제로 cp949 로 된 HTML 표다(시장구분 "유가"/"코스닥").
- 공표일(--date 생략 시 파일의 "공표일자" 중 가장 늦은 날)로 data/lists/<공표일>.csv 를 만든다.
- 직전 명단과 비교해 편입·제외 기업을 출력한다. 기존 명단 파일은 지우지 않는다(이력 보관).
"""
import argparse
import csv
import os
import re
import sys
from html.parser import HTMLParser

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
import store  # noqa: E402

MARKET = {"유가": "KOSPI", "유가증권": "KOSPI", "코스피": "KOSPI", "KOSPI": "KOSPI",
          "코스닥": "KOSDAQ", "KOSDAQ": "KOSDAQ"}
FIELDS = ["code", "name", "market", "pbr", "gics", "announced"]


class _Table(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self._row, self._cell = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def read_kind(path):
    raw = open(path, "rb").read()
    for enc in ("cp949", "utf-8-sig", "utf-8"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise SystemExit("파일 인코딩을 알 수 없습니다 (KIND 엑셀 파일인지 확인하세요).")
    t = _Table()
    t.feed(text)
    if not t.rows:
        raise SystemExit("표를 찾지 못했습니다 (KIND 저PBR 기업 엑셀 파일인지 확인하세요).")
    head = t.rows[0]
    col = {h: i for i, h in enumerate(head)}
    need = ["시장구분", "종목코드", "회사명"]
    if any(h not in col for h in need):
        raise SystemExit(f"필요한 열({', '.join(need)})이 없습니다. 열 이름: {head}")

    def get(r, h):
        i = col.get(h)
        return r[i] if i is not None and i < len(r) else ""

    out = []
    for r in t.rows[1:]:
        code = get(r, "종목코드").strip()
        if not code:
            continue
        mk = get(r, "시장구분").strip()
        out.append({
            "code": code.zfill(6), "name": get(r, "회사명"),
            "market": MARKET.get(mk, mk.upper()),
            "pbr": get(r, "PBR"), "gics": get(r, "GICS업종"), "announced": get(r, "공표일자"),
        })
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("--date", help="공표일 YYYY-MM-DD (생략 시 파일의 공표일자)")
    ap.add_argument("--dry-run", action="store_true", help="파일을 쓰지 않고 비교 결과만 출력")
    a = ap.parse_args()

    rows = read_kind(a.file)
    date = a.date or max((r["announced"] for r in rows if re.fullmatch(r"\d{4}-\d{2}-\d{2}", r["announced"])),
                         default=None)
    if not date:
        raise SystemExit("공표일을 알 수 없습니다. --date YYYY-MM-DD 로 지정하세요.")
    bad = [r for r in rows if r["market"] not in ("KOSPI", "KOSDAQ")]
    kospi = sum(r["market"] == "KOSPI" for r in rows)
    print(f"명단 {date}: {len(rows)}개사 (코스피 {kospi} · 코스닥 {len(rows) - kospi - len(bad)})")
    if bad:
        print(f"  주의: 시장구분을 알 수 없는 행 {len(bad)}개 — {[r['name'] for r in bad]}")
    if kospi == len(rows) or kospi == 0:
        print("  주의: 한 시장만 들어 있습니다. KIND에서 시장구분 '전체'로 내려받았는지 확인하세요.")

    prev_ver = max((v for v in store.list_versions() if v < date), default=None)
    if prev_ver:
        prev = {c["code"]: c for c in store.load_companies(prev_ver)}
        cur = {r["code"]: r for r in rows}
        added = [cur[c] for c in cur if c not in prev]
        removed = [prev[c] for c in prev if c not in cur]
        print(f"직전 명단 {prev_ver}: {len(prev)}개사 → 편입 {len(added)} · 제외 {len(removed)} · 유지 {len(cur) - len(added)}")
        for r in added:
            print(f"  + {r['name']}({r['code']}, {r['market']})")
        for r in removed:
            print(f"  - {r['name']}({r['code']}, {r['market']})")

    if a.dry_run:
        return
    os.makedirs(store.LISTS_DIR, exist_ok=True)
    out = os.path.join(store.LISTS_DIR, f"{date}.csv")
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print(f"저장: {os.path.relpath(out, BASE)}")


if __name__ == "__main__":
    main()

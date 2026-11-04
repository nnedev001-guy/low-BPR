# 저PBR 기업 주간 주가 동향 대시보드

한국거래소 KIND 기업밸류업 통합페이지에 공표된 **저PBR 기업**(2026-11 공표 151개사)의
주간 주가 등락을 코스피·코스닥 지수와 비교해 보여 주는 사내 임원 보고용 웹 대시보드입니다.

## 기준
- 주차: 월요일이 포함된 주(월~금). 예) 11월 1주 = 11/2~11/6
- 주간 등락률: 그 주 첫 거래일 **시가** → 마지막 거래일 **종가**
- 보합: ±0.5% / ±0.75% / ±1.0% 중 선택 (기본 ±0.5%)
- 지수 대비: 기업 등락률 − 소속 시장(코스피/코스닥) 지수 등락률 (%p)
- 거래정지 등으로 해당 주 거래가 없는 기업은 "집계 제외"로 따로 표시
- 시세 출처: 네이버 금융 일봉(fchart.stock.naver.com)

## 기능
- **기간 보고서:** 주간 / 여러 주 합산(예: 11월 2주~11월 4주) / 월간 선택, 보합 범위 선택, 상승·보합·하락 통계, 코스피·코스닥 대비, 일별 누적 추이, 상위·하위 10, 전체 기업 목록(시장: 전체·코스피·코스닥, 등락 필터)
- **기업 비교:** 기업 최대 8개사 검색·선택 + 시작/종료일 지정 → 누적 등락률 추이, 기간 요약(시가·종가·최고·최저·지수 대비), 일별 시가·종가 표
- **PDF 다운로드:** 각 화면 결과를 A4 PDF 파일로 바로 저장 (인쇄 버튼으로 브라우저 PDF 저장도 가능)

## 구성
| 파일 | 역할 |
|---|---|
| `data/companies.csv` | 대상 기업 명단 (`code,name,market`, market = KOSPI/KOSDAQ). 매년 5월·11월 명단 변경 시 교체 |
| `refresh.py` | 명단 + 코스피/코스닥 일봉을 받아 `data/prices.db`(SQLite)에 저장 |
| `report.py` | 주차별 통계 계산 |
| `app.py` | Flask 웹 서버. 평일 16:30(KST) 이후 하루 한 번 자동 갱신(내장 스케줄러) |
| `templates/index.html` | 대시보드 화면 (PDF 인쇄 시 1쪽 요약 + 전체 목록) |

## 로컬 실행
```bash
pip install -r requirements.txt
python refresh.py          # 시세 수집 (1~2분)
python app.py              # http://localhost:8000
```

## Render 배포
- 저장소 루트의 `render.yaml`(Blueprint) 사용, 또는 Web Service 수동 생성:
  - Build: `pip install -r requirements.txt`
  - Start: `gunicorn app:app --workers 1 --threads 4 --timeout 120 --bind 0.0.0.0:$PORT`
- 무료 플랜은 디스크가 재시작 때 초기화되지만, 서버가 켜질 때 데이터가 오래됐으면 자동으로 다시 수집하므로 별도 DB가 필요 없습니다.
- 스케줄러는 서버 안에서 돕니다. 무료 플랜은 15분간 접속이 없으면 잠들고, 다음 접속 시 깨어나면서 최신 데이터를 받습니다(첫 화면에 1~2분 걸릴 수 있음).
- `--workers 1` 을 유지하세요(스케줄러 중복 실행 방지).

## 환경 변수 (선택)
- `DB_PATH`: SQLite 경로 (기본 `data/prices.db`)
- `COMPANIES_CSV`: 명단 경로 (기본 `data/companies.csv`)
- `DISABLE_SCHEDULER=1`: 내장 자동 갱신 끄기 (외부 cron 으로 `python refresh.py` 실행 시)

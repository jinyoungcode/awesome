# 3일 모의투자용 데이터 준비 방법 (윈도우 기준)

> 작성: ② 자료 수집팀 / 2026-11-25 — 총괄 지시(3일 모의 일정안) 회신, D-042(v1.1), **D-043·D-044 반영(v1.2, 2026-11-26)**, 대표 PC 실패 대응(v1.3, 2026-11-27: 재시도·구간 분할·이어받기·설치본 stockking 사용)
> 실행 장소: **대표 윈도우 PC** (클라우드 세션은 KIS 서버에 접속 불가)
> 데이터는 시세 재배포 금지 약관 때문에 **저장소에 커밋 금지** → 모두 `%USERPROFILE%\stockking-data` 아래에 저장

## ★ 오늘 밤 1회 (11/26 → 2일차 11/27 첫 S02 주문용) — PowerShell 그대로

> 대표 PC 기준: 저장소 `C:\stockking\awesome`(브랜치 `claude/stock-king-03-dev` 그대로, **전환하지 않음**), `.venv`, 모의 키 `%APPDATA%\stockking\secrets.env`
> 방식: 자료팀 코드를 **옆 폴더(`C:\stockking\awesome-data`)에 git worktree로** 받아 같은 `.venv` 파이썬으로 실행. 현재 작업 폴더·브랜치는 건드리지 않음

**0) 구성종목 파일 2개 (없으면, 약 3분)** — 브라우저에서 `data.krx.co.kr` → 기본 통계 → 지수 → 주가지수 → 지수구성종목 → `코스피 200` 조회 → 다운로드(CSV) / `코스닥 150` 같은 방법. 아래 경로로 저장:
`%USERPROFILE%\stockking-data\constituents\kospi200.csv`, `...\kosdaq150.csv`

**1) 코드 받기 + 환경 (1분)**
```powershell
cd C:\stockking\awesome
git fetch origin claude/stock-king-02-data
git worktree add ..\awesome-data origin/claude/stock-king-02-data
#  (이미 있으면) git -C ..\awesome-data checkout --detach origin/claude/stock-king-02-data
$env:STOCKKING_SECRETS_FILE = "$env:APPDATA\stockking\secrets.env"
$env:STOCKKING_DATA_DIR     = "$env:USERPROFILE\stockking-data"
$env:PYTHONUTF8 = "1"
$PY  = ".\.venv\Scripts\python.exe"
$COL = "..\awesome-data\teams\02-data\collectors\collect.py"
$C   = "$env:USERPROFILE\stockking-data\constituents"
$OUT = "$env:USERPROFILE\stockking-data\s02_inputs\2026-11-26"
New-Item -ItemType Directory -Force $C, $OUT | Out-Null
```

**2) 연결 확인 (10초)**
```powershell
& $PY $COL check
```
삼성전자 현재가와 `"KOSPI200": {"name": ..., "rows": N}`이 나오면 정상.

**3) S02 입력 파일 4종 생성**
```powershell
& $PY $COL s02-inputs --kospi200 "$C\kospi200.csv" --kosdaq150 "$C\kosdaq150.csv" --out $OUT --date 2026-11-26 --status-now --interval 0.5
```
| 구성 | 호출 수 | 예상 시간 (`--interval 0.5`) | 기본 1.0초 |
|---|---|---|---|
| **전체** KOSPI200+KOSDAQ150 (약 350종목) | 약 1,750회 | **약 15~20분** | 약 30~35분 |
| **대안: KOSPI200만** (`--kosdaq150 ...` 부분을 지움) | 약 1,000회 | **약 9~12분** | 약 17~20분 |

- `--date 2026-11-26 --status-now`: 자정을 넘겨도 그대로 쓰면 됩니다(11/26 다음 거래일 전이라 지금 상태 = 11/26 상태). **단, 11/27 장 시작(09:00) 전까지만** — 장중에 돌리면 상태가 11/27 기준이 됩니다.
- **중간에 끊겨도 같은 명령을 다시 실행하면 이어서 합니다** (`$OUT\_cache\` 에 받은 종목 저장, 다시 받지 않음). 종목마다 `[12/350] 005930 조회 일봉 350행` 형태로 진행이 표시됩니다.
- 모의 서버 간헐 오류(HTTP 500·EGW00201)는 1·2·4·8초 간격으로 최대 5회 자동 재시도합니다(v1.3, 11/27 새벽 수정).
- 실행 첫 줄에 `stockking 패키지: C:\stockking\awesome\src\stockking` 처럼 **현재 저장소(.venv 설치본)** 경로가 나와야 합니다(토큰 파일 캐시가 있는 최신 kis.py 사용 → 재실행 시 토큰 403 방지). `..\awesome-data\src`가 나오면 `.venv`에 stockking이 설치되지 않은 것이므로 `& $PY -m pip install -e .` 후 다시 실행.
- `EGW00201`(호출 한도)이 자주 보이면 `--interval 1.0`으로 다시 실행. 처음부터 다시 만듭니다.
- **KOSPI200만 대안의 영향:** 유니버스가 KOSPI 종목으로만 줄어듭니다(KOSDAQ 신호 없음). `index.csv`에는 KOSDAQ150 지수도 그대로 들어갑니다. 2일차 주문 경로 점검에는 충분합니다.
- KOSDAQ150 지수 코드 오류가 나면: 화면 메시지를 그대로 자료팀에 전달(임시로 `--kosdaq150-code <코드>` 지정).

**4) 확인 (1분)**
```powershell
Get-Content "$OUT\manifest.json" -Encoding UTF8 | Select-String "universe_passed|bad_ohlc_count|status_checked|KOSPI200|KOSDAQ150|excluded_by_rule"
```
`universe_passed` > 0, `bad_ohlc_count` 0, `status_checked` true, 지수 각 200행 이상이면 통과.

**5) 프로그램팀 plan 실행 (현재 폴더 그대로)**
```powershell
& $PY -m stockking.ops.s02_runner plan --bars "$OUT\bars.csv" --universe "$OUT\universe.txt" --markets "$OUT\markets.csv" --index "$OUT\index.csv" --signal-date 2026-11-26 --exec-date 2026-11-27
```

**6) (선택) 오늘 분봉도 적재 — plan 이후, 약 12~25분**
```powershell
& $PY $COL minute --kospi200 "$C\kospi200.csv" --kosdaq150 "$C\kosdaq150.csv" --date 2026-11-26 --interval 0.5
```
늦으면 생략해도 됩니다(과거일자 분봉으로 나중에 백필 가능).

---

## ★ 점검 주문 후보만 다시 만들기 (KIS 호출 없음, 주말 실행 가능)

T1 판정은 **정수 수량 q로 q × 가격 ∈ 목표 90~110%** (D-005, 11/28 총괄 요청). T5는 1주 가격 ∈ 목표 110% 초과~125%. `tech_candidates.csv`에 `q`, `order_amount` 열이 있습니다.
```powershell
& $PY $COL tech-candidates --inputs "$env:USERPROFILE\stockking-data\s02_inputs\2026-11-27"
```
- 입력 폴더의 `_cache`(11/27 일봉·상태)·`markets.csv`·`index.csv`만 읽습니다. 캐시를 지우거나 다시 받지 않으므로 주말에 돌려도 11/27 기준 그대로입니다(`--status-now` 불필요).
- 운용 금액은 `STOCKKING_OPERATING_CAPITAL`(숫자/auto → `var\operating_capital.json`)을 따르며, `--capital 10000000`처럼 직접 지정 가능
- 기존 파일은 `tech_candidates_prev.csv`로 보관, `manifest.json`의 `tech_pick`·`tech_ranges` 갱신

---

## ★ D-048 국면 복귀 예상·전환 알림

**복귀 예상 (10초, 매일 s02-inputs 뒤 아무 때나)**
```powershell
& $PY $COL regime-outlook --index-csv "$env:USERPROFILE\stockking-data\s02_inputs\2026-11-27\index.csv"
```
- 시나리오: 횡보(지금 수준 유지) / 월 +3% / 월 +5% / 월 +10% 반등 → 각각 '지수 > 200일 평균'이 처음 성립하는 거래일 수와 날짜(주말만 제외한 근사)
- 횡보는 평균이 지수와 같아지기만 하고 넘지 못할 수 있어 "평균과 1% 이내 접근일"도 함께 표시
- 결과 `regime_outlook.json`(같은 폴더)

**전환 알림 (자동):** `s02-inputs`가 끝날 때 로그에 `[REGIME_ON]`/`[REGIME_OFF]` 줄과 종가·200일 평균·격차를 찍고, 전날과 바뀌면 `★전환`을 붙입니다. `manifest.json`에 `regime`(지수별 close·sma200·on·prev_on·changed·gap_pct)과 `warnings`(`REGIME_ON KOSPI200 (전환)` 등)가 들어갑니다.

---

## ★ D-047 지수값 점검 (11/27 18:35 실행 직후, 약 2분)

S02 0건 원인이 국면 필터(지수 > 200일 평균) OFF이고, KIS 모의 지수 흐름(KOSPI200 2025-10 약 493 → 2026-06 약 1,477 → 11/26 727, 하루 8% 넘는 급변 12회)이 비정상으로 보여 대조합니다.

**1) KIS 재조회 대조 (KRX 파일 없이 바로 가능)**
```powershell
& $PY $COL verify-index --index-csv "$env:USERPROFILE\stockking-data\s02_inputs\2026-11-27\index.csv" --interval 0.5
```
- 8% 초과 급변일과 표본 5일을 **하루짜리 구간으로 다시 조회**해 index.csv 값과 비교합니다.
  - 불일치가 있으면 → 자료팀 구간 합치기(페이지 넘김) 문제입니다.
  - 모두 일치하는데 급변이 있으면 → KIS 모의 원천값 자체 문제입니다.
- 결과 `index_check.json`(같은 폴더). 화면 요약 두 줄을 그대로 전달해 주세요.

**2) KRX 공표값 대조 (대표가 CSV 2개 받기, 약 3분)**
- `data.krx.co.kr` → 기본 통계 → 지수 → 주가지수 → **개별지수 시세 추이** (화면번호 [11003])
- 지수: `코스피 200` / 조회기간: `20251001` ~ `20261127` → 조회 → 다운로드 **CSV** → `%USERPROFILE%\stockking-data\krx_kospi200.csv`
- 같은 방법으로 `코스닥 150` → `%USERPROFILE%\stockking-data\krx_kosdaq150.csv`
```powershell
& $PY $COL verify-index --index-csv "$env:USERPROFILE\stockking-data\s02_inputs\2026-11-27\index.csv" --krx-kospi200 "$env:USERPROFILE\stockking-data\krx_kospi200.csv" --krx-kosdaq150 "$env:USERPROFILE\stockking-data\krx_kosdaq150.csv" --no-refetch
```
- `KRX 최대 오차`가 0.1% 이내면 KIS 값이 공표값과 같은 것 → 국면 OFF는 실제 시장 상황입니다. 크게 다르면 KIS 모의 지수를 쓰지 않고 **KRX 지수로 index.csv를 대체**하는 방안을 총괄에 올립니다.

**1차 결과 (11/27, PC 세션):** KOSPI200·KOSDAQ150 각 279행, KIS 하루 재조회 **불일치 0건** → 자료팀 구간 합치기는 정상, 값은 KIS 모의 원천 그대로. 급변(8% 초과) KOSPI200 12일·KOSDAQ150 10일이 실제 시장인지는 KRX 대조(2)로 판정.

**2차 결과 (11/27, PC 세션): KRX 공표값과 최대 오차 0.0%** (KOSPI200·KOSDAQ150, 2025-10-01~2026-11-27 각 281일). → **KIS 모의 지수는 실제 시장값.** 급변일도 실제 시장 움직임이며, S02 0건(국면 OFF)은 데이터 오류가 아니라 실제 시장 국면 때문. `index-from-krx`는 사용하지 않음. (자료팀이 앞서 "실제 시장으로 보기 어렵다"고 한 의견은 틀렸음 — 정정)

**2-1) KRX 값이 다르면 — 대체 index.csv 만들기 (10초)**
```powershell
& $PY $COL index-from-krx --krx-kospi200 "$env:USERPROFILE\stockking-data\krx_kospi200.csv" --krx-kosdaq150 "$env:USERPROFILE\stockking-data\krx_kosdaq150.csv" --out "$env:USERPROFILE\stockking-data\s02_inputs\2026-11-27\index.csv"
```
기존 KIS 지수 파일은 같은 폴더 `index_kis.csv`로 보관됩니다. KRX 조회 기간은 200거래일 이상(예: 20251001~오늘)이어야 합니다. **이 대체는 총괄 결정 후에만 사용**합니다.

**3) D-027 점검 주문 후보:** 18:35 `s02-inputs` 실행 결과 `tech_candidates.csv`에 자동으로 생성됩니다(국면 필터와 무관). 운용 금액이 커서(예: 1,000만 원 → T1 목표 1주 180만~220만 원) 가격대 안 종목이 없으면, 상한 이하에서 가장 가까운 가격의 종목을 `in_band=False`로 대체 후보로 냅니다. 이 경우 매매팀·총괄이 대체 후보를 쓸지, 운용 금액을 낮출지 정해야 합니다.

---

## 1. 일정과 마감 (D-042·D-043 반영: 모의 5거래일 11/26·11/27·11/30·12/1·12/2)

| 날짜 | 할 일 | 마감 | 비고 |
|---|---|---|---|
| **11/26(목) 오전~장중** | 최초 준비(§2) + `collect.py check` 로 연결 확인 | 15:00 | **check가 실패하면 즉시 자료팀에 화면 전달** |
| **11/26(목)·11/27(금)·11/30(월)** 16:00 | `run_daily.bat` 실행 (30~60분) | **18:00** (11/26 첫날은 17:00 권장) | 다음 거래일 S02 주문계획(18:30)용 |
| **12/1(월)** 16:00 | `run_daily.bat` 실행 → **D-027 후보 확인**(§5) | 후보 **17:30** / 입력 18:00 | 4일차 마감 D-027 판정, 5일차(12/2) 실행용 |
| 12/2(화) 16:00 | `run_daily.bat` (분봉 적재 계속) | — | 모의 종료일 |

> 예행연습 시간이 없습니다. 11/26 16:00 실행이 **첫 실데이터 실행**이므로, 오전에 `check`만이라도 먼저 돌려 키·접속·지수코드 문제를 미리 걸러 주세요. 16:00 실행이 실패해도 17:00 전 재실행할 여유(약 30분)는 있습니다.

- 장 마감(15:30) 뒤 **15:45 이후**에 돌려야 당일 일봉·지수가 확정되고, 관리·경고·정지 상태(U6/U7)가 당일 기준으로 반영됩니다.
- 프로그램팀 15:40~15:50 마감 스크립트(`daily_reconcile`, `ledger_close`, `paper_report`)가 끝난 뒤 돌리세요. 동시에 돌리면 KIS 토큰 발급 제한(1분 1회)·호출 한도에 걸릴 수 있습니다.

## 2. 최초 1회 준비

1. **저장소와 Python:** 프로그램팀 `PAPER_RUNBOOK.md` §0의 1~2번을 마치면 됩니다(같은 Python 사용).
2. **KIS 모의 앱키:** 프로그램팀 `teams/03-dev/SECRETS.md` §3 윈도우 방법대로 `%USERPROFILE%\.config\stockking\secrets.env`에 넣으면 자료팀 도구도 그 키를 그대로 씁니다. 따로 설정할 것은 없습니다.
3. **구성종목 파일 2개** (월 1회 갱신, 12월 정기변경 직후 반드시 갱신):
   - KRX 정보데이터시스템 `data.krx.co.kr` → 기본 통계 → 지수 → 주가지수 → **지수구성종목**
   - `코스피 200` 조회 → 다운로드(CSV) → `%USERPROFILE%\stockking-data\constituents\kospi200.csv` 로 저장
   - `코스닥 150` 조회 → 다운로드(CSV) → `%USERPROFILE%\stockking-data\constituents\kosdaq150.csv` 로 저장
   - 파일명만 맞으면 됩니다. 인코딩(EUC-KR)은 도구가 알아서 읽습니다.
4. **연결 확인** (명령 프롬프트, 저장소 폴더에서):
   ```
   python teams\02-data\collectors\collect.py check
   ```
   삼성전자(005930) 현재가와 `KOSPI200` 지수 행 수가 나오면 정상입니다.

## 3. 매일 실행 (한 줄)

명령 프롬프트에서 저장소 폴더로 이동한 뒤:
```
teams\02-data\collectors\run_daily.bat
```
이 파일이 순서대로 실행합니다.
1. S02 입력 파일 생성 → `%USERPROFILE%\stockking-data\s02_inputs\YYYY-MM-DD\` (약 25분)
2. 국내 1분봉 당일 적재 → `%USERPROFILE%\stockking-data\clean\minute_kr\` (약 25분, 대표 결정 2026-11-25)

**자동 실행(선택):** 작업 스케줄러 → 기본 작업 만들기 → 매주 월~금 16:00 → 프로그램 시작: `cmd.exe`, 인수: `/c teams\02-data\collectors\run_daily.bat`, 시작 위치: 저장소 폴더.

## 4. 결과 확인 (2분)

`%USERPROFILE%\stockking-data\s02_inputs\YYYY-MM-DD\` 폴더:

| 파일 | 프로그램팀 인자 | 확인할 것 |
|---|---|---|
| `bars.csv` | `--bars` | 존재, 수 MB 이상 |
| `universe.txt` | `--universe` | 종목코드 수십~수백 줄 |
| `markets.csv` | `--markets` | `symbol,market` |
| `index.csv` | `--index` | KOSPI200·KOSDAQ150 각 200행 이상 |
| `manifest.json` | — | `universe_passed` > 0, `bad_ohlc_count` = 0, `index_rows` 각 200 이상, `status_checked` = true, `excluded_by_rule`(제외 사유별 건수) |
| `universe_report.csv` | — | 종목별 탈락 사유 (엑셀로 열기) |
| `tech_candidates.csv` | — | D-027 T1/T5 후보 (§5) |

프로그램팀 실행기에는 이렇게 넘깁니다 (프로그램팀 `PAPER_RUNBOOK.md` 기준):
```
set D=%USERPROFILE%\stockking-data\s02_inputs\YYYY-MM-DD
python -m stockking.ops.s02_runner plan --bars %D%\bars.csv --universe %D%\universe.txt --markets %D%\markets.csv --index %D%\index.csv
```

### `universe.txt`에 적용되는 필터 (COMMON_SPEC §3 U1~U8, 매매팀 S02_IMPL_CHECK I2 회신)

| 규칙 | 적용 방법 |
|---|---|
| U1 KOSPI200·KOSDAQ150 | 구성종목 파일 2개 |
| U2 보통주(우선주·SPAC·리츠 제외) | 종목코드 끝자리 0 + 종목명에 `스팩`·`리츠`·`우`(끝) 등 포함 시 제외 |
| U3 상장 250거래일 이상 | 최근 일봉 수 (약 350거래일 조회) |
| U4 주가 ≥ 2,000원 | T일 현재가(=종가, 원주가) |
| U5 ADV20 ≥ 30억 | **실제 거래대금**(KIS `acml_tr_pbmn`) 20일 평균 — 종가×거래량 근사 아님 |
| U6 관리·투자주의환기·경고·위험·단기과열·정리매매 아님 | KIS 현재가 조회의 종목상태·시장경고·정리매매·단기과열 표시 (T일 장 마감 후 실행 기준) |
| U7 거래정지 아님 | KIS 임시정지 표시 / 종목상태 58. *T+1 정지 예정 공시는 미반영* |
| U8 최근 250일 결측 ≤ 5일 | KOSPI200 지수 거래일을 기준 달력으로 대조 |

제외 사유별 건수는 `manifest.json`의 `excluded_by_rule`, 종목별 사유는 `universe_report.csv`.

## 5. D-027 점검용 주문 T1 대상 종목 지정

- **지정 규칙 (자료팀):** 12/1(월, 4일차) 실행 결과의 `tech_candidates.csv`에서 **role=T1, rank=1** 종목을 T1 대상으로 지정합니다. `manifest.json`의 `tech_pick.T1`에도 같은 값이 나옵니다.
  - 조건: KOSPI200·KOSDAQ150 구성종목 중 12/1 종가가 **목표의 90~110%** (목표 = 모의 운용 금액 ÷ 5, D-044. 15만 원이면 27,000~33,000원), 관리·경고·위험·정지·단기과열·정리매매 아님, **20일 평균 거래대금 1위**
  - rank 1이 3일차 아침에 거래정지 등으로 불가하면 rank 2를 씁니다.
- **지금 특정 종목을 미리 못 박지 않는 이유:** 가격대 조건(정수 3만 원 범위)이라 주가가 며칠 사이 벗어날 수 있습니다. 전날 저녁 종가로 고르는 것이 매매팀 명세("자료팀이 전날 저녁 지정")와도 맞습니다.
- **T2**는 T1 보유분 매도이므로 같은 종목입니다(`role=T1` 목록 = T1·T2 후보).
- **T5**(1주 예외 '목표 초과', 목표의 110% 초과~125%; 15만 원이면 33,000~37,500원) 후보는 같은 파일에 `role=T5`로 각 5종목까지 나옵니다.
- **T4(미국)**는 D-033(T0 국내만)에 따라 이번 범위 밖입니다.
- **운용 금액 지정:** 프로그램팀 설정과 같은 환경변수 `STOCKKING_OPERATING_CAPITAL`(예: `set STOCKKING_OPERATING_CAPITAL=1000000`)을 읽습니다. 없으면 15만 원. 실제 적용 가격대는 `manifest.json`의 `tech_ranges`에서 확인
- 매매팀 요청(4일차 12/1 17:30까지 후보 목록): 12/1 16:00 실행 결과의 `tech_candidates.csv`가 그 목록입니다. 실행 후 대표가 이 파일 내용(종목코드·가격)을 매매팀 창에 전달해 주시면 됩니다(시세 값이라 저장소 커밋 대신 전달).
- ETF는 후보에 넣지 않았습니다(종목 조건으로 충분히 나옴). 후보가 0개면 자료팀에 알려 주세요. KODEX 200(069500) 등 ETF로 대체 지정하겠습니다.

## 6. 불가능하거나 위험한 항목 (총괄 보고)

| 항목 | 상태 | 영향·대응 |
|---|---|---|
| 실데이터 사전 검증 | **불가(현재)** — 클라우드에서는 KIS 접속 불가 | 코드는 가짜 응답 테스트 12건 통과. **11/26 오전 `check`가 첫 실검증** |
| KOSDAQ150 지수 KIS 코드 | 미확인 (도구가 후보 2개를 지수명으로 검증) | 예행연습에서 실패하면 `--kosdaq150-code`로 지정. 프로그램팀에 확인 요청함 |
| 구성종목 시점별 이력 | 실행 시점 목록 사용 | 3일 모의에는 영향 없음 |
| 수정주가 | KIS 제공 수정주가 사용 (자체 수정계수 아님) | 3일 모의에는 충분. 백테스트용은 별도 |
| T+1 거래정지 예정 공시 (U7 후단) | 미반영 | 3일 동안 해당 종목이 계획에 오르면 대표가 KIS 앱에서 확인 |
| 일정 여유 | D-042로 예행연습 없이 11/26 당일 첫 실행 | 실패 시 2일차 S02 주문이 빠질 수 있음 → D-027 점검용 주문으로 보완(총괄 판단) |

## 7. 문제 발생 시
- `EGW00201`(호출 한도): 1분 뒤 다시 실행 — 완료된 부분은 건너뛰고 이어서 합니다(분봉). S02 입력은 처음부터 다시 만듭니다(약 25분).
- `KOSPI200 지수 확인 실패` / `KOSDAQ150 지수 코드를 찾지 못했습니다`: 화면 메시지를 자료팀에 전달
- `거래일이 아니거나 지수 데이터가 아직 없습니다`: 15:45 이전 실행 또는 휴장일 → 시간 확인
- 로그: `%USERPROFILE%\stockking-data\logs\`

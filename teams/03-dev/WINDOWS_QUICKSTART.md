# 윈도우 최소 설치 + 1일차(11/26) 기술 점검 — 대표님용 한 장 안내

> 작성: 프로그램 생성팀 / 2026-11-25 (11/26 갱신) · 근거: D-042(모의 11/26 시작, 1일차는 기술 점검만), **D-043(모의 5거래일: 11/26·11/27·11/30·12/1·12/2)**, **D-044(모의 운용 금액 자유 설정)**, D-002·D-023(모의 전용)
> 아래 명령은 **PowerShell** 에 한 줄씩 붙여 넣습니다. `▶ 정상이면` 줄과 비슷한 화면이 나오면 다음으로 넘어갑니다.
> 키(앱키·시크릿)는 **채팅·메신저·저장소에 절대 붙여 넣지 마세요.** 5단계의 메모장 파일에만 넣습니다.
> 실전 앱키는 1일차에 필요 없습니다(D-041: 실전 키는 나중에 시세 조회 전용, 모의 키와 별도 파일).

---

## A. 오늘 밤 설치 (약 20~30분)

### 1. Python 설치
1. https://www.python.org/downloads/windows/ 에서 **Python 3.12** "Windows installer (64-bit)" 내려받기
2. 설치 첫 화면 아래의 **"Add python.exe to PATH" 체크** → Install Now
3. 시작 메뉴에서 **Windows PowerShell** 을 새로 열고 확인:
```powershell
py --version
```
▶ 정상이면: `Python 3.12.x`
✖ `'py'은(는) ... 인식되지 않습니다` → PowerShell 을 닫았다 다시 열기. 그래도 안 되면 Python 설치를 다시 하며 PATH 체크.

### 2. Git 설치
https://git-scm.com/download/win → "64-bit Git for Windows Setup" → 기본값으로 계속 Next → Install. **PowerShell 을 새로 열고**:
```powershell
git --version
```
▶ 정상이면: `git version 2.xx.x.windows.x`

### 3. 코드 내려받기 (OneDrive 밖, `C:\stockking`)
```powershell
git clone https://github.com/jinyoungcode/awesome.git C:\stockking
```
▶ 처음이면 GitHub 로그인 창(브라우저)이 뜹니다 → 로그인 → 승인.
▶ 정상이면: `Cloning into 'C:\stockking'...` 뒤에 `done.` 으로 끝남
```powershell
cd C:\stockking
git checkout claude/stock-king-03-dev
```
▶ 정상이면: `branch 'claude/stock-king-03-dev' set up to track 'origin/claude/stock-king-03-dev'.` / `Switched to a new branch ...`

### 4. 가상환경 + 설치 + 테스트
```powershell
py -3 -m venv .venv
Set-ExecutionPolicy -Scope Process Bypass -Force
.\.venv\Scripts\Activate.ps1
```
▶ 정상이면: 줄 맨 앞에 `(.venv)` 가 붙음 (이후 모든 명령은 이 창에서)
```powershell
python -m pip install -e ".[dev]"
```
▶ 정상이면: 마지막 줄 `Successfully installed ... stockking-0.1.0 ...`
```powershell
python -m pytest -q
```
▶ 정상이면: 마지막 줄 `187 passed` (숫자는 더 클 수 있음, **failed 가 없어야** 함)

### 5. 모의투자 키 넣기 (저장소 밖, 본인만 읽기)
```powershell
New-Item -ItemType Directory -Force "$env:APPDATA\stockking" | Out-Null
notepad "$env:APPDATA\stockking\secrets.env"
```
메모장이 "새 파일을 만드시겠습니까?" → **예**. 아래 4줄을 쓰고 `=` 뒤에 **모의투자용** 값을 넣은 뒤 저장(Ctrl+S)·닫기:
```
KIS_PAPER_APP_KEY=모의투자_앱키
KIS_PAPER_APP_SECRET=모의투자_앱시크릿
KIS_PAPER_ACCOUNT_NO=모의계좌번호_앞8자리
KIS_PAPER_ACCOUNT_PRODUCT=01
```
(따옴표·공백 없이. 계좌번호가 `12345678-01` 이면 앞 8자리 `12345678`, 뒤 2자리는 `01`)
권한을 본인만으로 제한:
```powershell
icacls "$env:APPDATA\stockking\secrets.env" /inheritance:r /grant:r "$($env:USERNAME):(R,W)"
```
▶ 정상이면: `1개 파일을 처리했습니다. 0개 파일을 처리하지 못했습니다.`

### 6. 첫 연결 점검 (주문 없음 — 밤에 해도 됨)
```powershell
python -m stockking.ops.connect_check
```
▶ 정상이면:
```
  [PASS] 1. 토큰 발급
  [PASS] 2. 현재가 조회 — 005930 현재가 …원 / 기준가 …원
  [PASS] 3. 잔고 조회 — 예수금 …원, 보유 0종목
  [PASS] 3-1. 운용 금액 ≤ 모의 예수금 (D-044) — 운용 금액 …원
(주문 단계 생략 — 장중에 --order 로 다시 실행하세요)
== 모의 계좌 연결 점검: 통과 (4/4) ==
```

### 7. 모의 운용 금액 — D-044·D-046
**대표님이 따로 정하지 않으면 1일차 시작 시점의 모의 주문 가능 금액 전액**이 운용 금액이자 장부 원금입니다(D-046). 매일 여는 PowerShell 창에서 첫 명령 전에 아래 **둘 중 하나**를 입력합니다(아래 B-1 '매일 시작할 때'에 포함):
```powershell
$env:STOCKKING_OPERATING_CAPITAL = "auto"        # 기본(D-046): 1일차 첫 점검 때 주문 가능 금액 전액으로 고정
$env:STOCKKING_OPERATING_CAPITAL = "1000000"     # 또는 직접 지정: 예 100만 원 (쉼표 가능 "1,000,000")
```
- `auto` 는 1일차 첫 `ledger_close --open`(또는 `connect_check`) 때 읽은 **D+2 예수금(주문 가능 금액 근사)** 을 `var\operating_capital.json` 에 저장하고, 이후 5거래일 동안 **같은 값**을 씁니다. 다시 정하려면 그 파일을 지우세요(보유 종목이 없을 때만).
- 주문 목표(÷5)·비중 22%·일 손실 −5%·1주 예외(가격 ≤ 30%, 손실 ≤ 2%)가 **모두 이 금액 기준**입니다.
- 직접 지정한 금액이 모의 예수금보다 크면 `3-1` 이 FAIL 이 되고 S02 주문 제출이 멈춥니다.
- **S02 첫 주문계획(1일차 18:30 plan) 전에 정해져 있어야** 합니다. 가상 장부(`var\s02\state.json`)가 처음 만들어질 때 금액이 고정됩니다.
- 둘 다 입력하지 않으면 15만 원으로 계산됩니다. 실전(T0)은 15만 원 고정이며 `auto`·다른 금액을 거부합니다(D-031·D-032).

### 자주 나는 오류
| 화면 | 조치 |
|---|---|
| `비밀값 KIS_PAPER_APP_KEY 이(가) 없습니다` | 5단계 파일 이름·위치 확인: `notepad "$env:APPDATA\stockking\secrets.env"` 로 다시 열어 4줄 확인 |
| `비밀값 파일이 OneDrive 동기화 폴더 안에 있습니다` | 5단계 경로(`%APPDATA%`)를 그대로 쓰세요 |
| `KIS 토큰 발급 제한 … 1분 후 다시 실행하세요` (HTTP 403) | 토큰은 1분에 1번만 발급됩니다. 1분 기다렸다 다시 실행. 한 번 발급되면 `%APPDATA%\stockking\kis_paper_token.json` 에 약 24시간 저장돼 이후 명령은 다시 발급하지 않습니다(저장소 밖, 본인만 읽기, 앱키는 저장 안 함) |
| `KIS 토큰 발급 실패` | 실전 키가 아니라 **모의투자** 키인지, 앱키·시크릿이 바뀌지 않았는지 확인 |
| `EGW00201` (호출 한도) | 1분 기다렸다 다시 실행 |
| `.\.venv\Scripts\Activate.ps1 ... 스크립트를 실행할 수 없으므로` | 바로 앞의 `Set-ExecutionPolicy -Scope Process Bypass -Force` 를 같은 창에서 먼저 실행 |
| 그 외 | 화면을 그대로 캡처해 알려 주세요(키 값은 화면에 나오지 않습니다) |

---

## B. 모의 일정 (D-043: 5거래일)

| 일차 | 날짜 | 내용 |
|---|---|---|
| 1 | 11/26(목) | 기술 점검만 (아래 표) + 18:30 S02 첫 주문계획 |
| 2 | 11/27(금) | S02 주문 시작: 08:25 open → 08:40 `s02_runner submit` → 15:15 `cancel` → 15:45 `sync`·대사 → 18:30 plan |
| 3 | 11/30(월) | 2일차와 같음 |
| 4 | 12/1(화) | 같음 + **15:50 `python -m stockking.ops.tech_orders --check`** (D-027 점검용 주문 필요 여부 판정) |
| 5 | 12/2(수) | 같음 + 판정이 '필요'면 점검용 주문 T1~T3 실행 (`PAPER_RUNBOOK.md` §2-1) → 최종 `paper_report` |

## B-1. 1일차 11/26(목) 기술 점검 — 시간순 한 장 (S02 주문 없음)

**매일 시작할 때 (PowerShell 새 창):**
```powershell
cd C:\stockking
Set-ExecutionPolicy -Scope Process Bypass -Force
.\.venv\Scripts\Activate.ps1
git pull
$env:STOCKKING_OPERATING_CAPITAL = "auto"      # D-046 기본. 금액을 직접 정하셨으면 그 숫자로 (§A-7)
```

| 시각 | 명령 | ▶ 정상이면 | 점검표 |
|---|---|---|---|
| 08:25 | `python -m stockking.ops.ledger_close --open` | (1일차·auto) `운용 금액 auto: …원` 다음 `시작점 저장: var\ledger\paper\2026\open_state_20261126.json` | C1 준비 |
| 08:30 | `python -m stockking.ops.startup_check` | 마지막 줄 `신규 주문 시작 가능` | C5 준비 |
| 08:35 | `python -m stockking.ops.connect_check` | `통과 (4/4)` | — |
| 09:30 이후 | `python -m stockking.ops.connect_check --order` | `통과 (7/7)` — 1주 매수(현재가 −10%) 접수 → 취소 → 주문내역 확인 | C6(1건) |
| 10:00 | `python -m stockking.ops.killswitch_drill --operator 대표 --kis` | `킬스위치 훈련: 통과 (10/10)` — 매수 차단·청산 매도 허용·주문 0건 | **C4** |
| 10:30 | **재시작 실습** ① `python -m stockking.ops.connect_check --order` 실행 후 `4. 1주 지정가 매수 접수 … PASS` 가 보이면 즉시 **Ctrl+C** ② `python -m stockking.ops.startup_check` → `시작 가능` 또는 `미확인 주문 …` ③ 미확인이 나오면 KIS 모의(앱/HTS)에서 그 주문 상태 확인 후 `python -m stockking.ops.startup_check --resolve <주문번호> --outcome unfilled_canceled --operator 대표` ④ KIS 모의에서 남은 1주 매수 주문이 있으면 **취소** ⑤ `python -m stockking.ops.restart_drill --operator 대표` | ⑤ `재시작 복구 훈련: 통과 (9/9)` | **C5** |
| 15:40 | `python -m stockking.ops.daily_reconcile` | `일일 대사 20261126: 통과` | **C3** |
| 15:45 | `python -m stockking.ops.ledger_close --close` | `현금(D+2 예수금) 1원 단위 일치` PASS, `보유 수량 일치` PASS | **C1** |
| 15:50 | `python -m stockking.ops.paper_report` | C1·C3·C4·C5 `PASS`, C2·C6 `PENDING`(1일차는 S02 없음 — 정상) | 요약 |
| 18:30 | 2일차용 S02 주문계획 — 자료팀 파일 `C:\stockking\data\20261126\` 에 4개(`bars.csv`, `universe.txt`, `markets.csv`, `index.csv`)가 오면: `python -m stockking.ops.s02_runner plan --bars data\20261126\bars.csv --universe data\20261126\universe.txt --markets data\20261126\markets.csv --index data\20261126\index.csv --signal-date 2026-11-26 --exec-date 2026-11-27` | `주문계획 N건 → var\order_plan\order_plan_20261126.csv` | 2일차 C2·C6 |

**비상 중단** (언제든): `New-Item var\KILL_SWITCH -ItemType File` → 신규 매수 즉시 차단(청산 매도는 허용, D-026). 원인 확인 후 해제: `Remove-Item var\KILL_SWITCH`

**결과 보고:** 각 단계의 `== … 통과/미통과 ==` 줄을 총괄 창에 알려 주세요. 실패(FAIL)가 나와도 **실계좌와 무관**하며(모의 전용 차단), 화면만 주시면 프로그램팀이 바로 고칩니다.

### 참고
- `scripts\paper_day.ps1 -Step open|drill|close|plan` 은 위 명령들을 묶은 것입니다. 개발 환경에 PowerShell 이 없어 아직 실행 검증 전이므로, **1일차는 이 문서의 명령을 한 줄씩** 쓰시고 묶음 스크립트는 2일차 이후 쓰겠습니다.
- 국내 시장가 매도(S02 청산)의 KIS 모의 지원 여부는 첫 S02 청산 또는 점검 주문 T2에서 확인합니다. 미지원이면 `$env:STOCKKING_KR_SELL_MODE = "marketable_limit"` 한 줄로 시장성 지정가로 바뀝니다.

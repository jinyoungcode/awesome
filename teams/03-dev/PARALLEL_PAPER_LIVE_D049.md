# D-049 실전·모의 병행 운용 — 설계와 구현 계획

> 작성: 프로그램 생성팀 / 2026-11-27 · 근거: D-049(실전 중 모의 병행), D-027(점검 주문은 모의만), D-046(auto 운용 금액), D-028(단계별 한도)
> **실계좌 코드는 계속 잠금입니다.** `config.LIVE_TRADING_APPROVED = False` 는 이 작업에서 바꾸지 않습니다. 1단계는 모의 쪽 분리만 구현하고, 실계좌 클라이언트는 총괄 승인과 코드 리뷰 뒤 2단계에서 만듭니다.

## 1. 요구 사항 → 설계 대응

| D-049 요구 | 설계 | 강제 위치 |
|---|---|---|
| 계좌 분리 | **계좌 프로필** `paper` / `live` 를 프로세스 시작 시 1회 정함 (`STOCKKING_ACCOUNT`, 기본 `paper`) | `config.account_profile()` |
| 키 파일 분리 | `%APPDATA%\stockking\paper\secrets.env` / `...\live\secrets.env`. paper 파일은 `KIS_PAPER_*`, live 파일은 `KIS_LIVE_*` 키만 허용 — 다른 쪽 키가 들어 있으면 실행 거부 | `config.load_secret_file` |
| 토큰 캐시 분리 | 같은 프로필 폴더의 `kis_token.json` | `kis.default_token_cache` |
| 상태 파일·var 분리 | `var\paper\…`, `var\live\…` (감사 로그, 킬스위치, 운용 금액, S02 장부, 주문계획, 대사, 장부, 진단 로그 전부) | `paths.var_root()` 한 곳에서만 경로 생성 |
| 장부 분리 | `var\<프로필>\ledger\YYYY\` — 회계팀이 계좌별로 따로 받음 | `ledger_close` |
| 주문 ID 접두어 분리 | 모의 `PP-`, 실전 `LV-` (현재 `SK-` → `PP-`) | `domain.new_client_order_id`, `OrderRouter` 가 접두어·프로필 불일치 주문 거부 |
| 한 프로세스가 두 계좌에 주문 금지 | ① 프로필은 프로세스당 1회 확정(바꾸면 예외) ② `OrderRouter` 는 프로필과 같은 계좌 종류의 브로커만 받음 ③ `var\<프로필>\.lock` 으로 같은 프로필 프로세스 동시 실행도 막음 | `config`, `OrderRouter.__init__`, `ops.common` |
| 손실 한도·킬스위치 계좌별 | 킬스위치 파일 `var\<프로필>\KILL_SWITCH`. 손실 한도는 그 계좌 장부의 원금·고점·일/주 손익으로 계산. **실전 킬스위치는 모의를 멈추지 않고, 모의 킬스위치도 실전을 멈추지 않음** | `KillSwitch`, `RiskGate` |
| 병행 모의 운용 금액 auto | paper 프로필은 `STOCKKING_OPERATING_CAPITAL=auto` 가 기본. `var\paper\operating_capital.json` 에 고정. live 는 auto 금지(이미 PAPER 전용), 단계 금액(T0 15만 원) 고정 | `risk.stages` |

## 2. 구조

```
대표 PC
├─ 창 1 (모의)  STOCKKING_ACCOUNT=paper → KisPaperClient(모의 도메인) → var\paper\
└─ 창 2 (실전)  STOCKKING_ACCOUNT=live  → KisLiveClient(실전 도메인, 2단계) → var\live\
       └ 두 창은 파일·키·토큰·로그를 하나도 공유하지 않음

비교 도구 compare_paper_live (주문 기능 없음, 브로커 클라이언트를 만들지 않음)
  var\paper\ 와 var\live\ 를 읽기만 해서 신호 일치·체결 여부·슬리피지 비율 보고 (D-049: 수량 1:1 비교 안 함)
```

- 주문계획(`s02_runner plan`)은 같은 입력 파일로 각 프로필에서 따로 만듭니다. 운용 금액이 달라 수량은 다르고, 신호 종목·방향은 같아야 합니다. 비교 도구가 이를 검사합니다.
- 점검 주문(`tech_orders`)은 지금처럼 paper 프로필에서만 실행됩니다(D-027). live 프로필에서는 명령 자체가 거부됩니다.
- S03·D 시리즈 등 실전에 투입하지 않은 전략은 paper 프로필에서만 켤 수 있게 전략별 허용 프로필을 둡니다(`strategy_profiles = {"S02": {"paper", "live"}, "S03": {"paper"}}`).

## 3. 안전 규칙 (테스트로 고정)
1. 프로필 확정 뒤 다른 프로필의 브로커·경로·키를 요청하면 `AccountProfileMismatch`.
2. 주문 ID 접두어가 프로필과 다르면 `OrderRouter` 가 증권사 전송 전에 거부하고 감사 로그에 남김.
3. `startup_check`·`daily_reconcile`·`recovery` 는 자기 프로필 감사 로그만 읽고, 다른 접두어 기록이 섞여 있으면 시작을 막음.
4. 같은 프로필 프로세스가 이미 실행 중이면(`.lock` 의 PID 가 살아 있으면) 시작 거부. 비정상 종료로 남은 잠금은 PID 확인 후 자동 해제.
5. live 프로필은 `LIVE_TRADING_APPROVED=False` 인 동안 어떤 명령도 브로커를 만들지 못함(현재와 같음).
6. 비교 도구는 브로커·라우터를 import 하지 않음(정적 검사 테스트).

## 4. 구현 계획

### 1단계 — 지금 진행 (모의만, 실계좌 잠금 유지)
| # | 작업 | 파일 | 테스트 |
|---|---|---|---|
| 1 | `paths.py` 신설: `var_root()`, `secrets_dir()`. 모든 `Path("var/…")` 하드코딩(현재 약 12곳)을 이 함수로 교체 | `src/stockking/paths.py`, ops 전반 | 하드코딩 경로 금지 AST 검사(인코딩 검사와 같은 방식) |
| 2 | `account_profile()` 1회 확정 + `AccountProfileMismatch` | `config.py` | 프로필 변경 시 예외 |
| 3 | 키 파일 프로필 폴더화 + 키 접두어 검사. **기존 경로 호환**: paper 프로필은 `paper\secrets.env` 가 없으면 기존 `secrets.env` 를 읽고 이동 안내 | `config.py` | live 키가 든 paper 파일 거부, 기존 경로 호환 |
| 4 | 주문 ID 접두어 `PP-`/`LV-` + 라우터 검사. 기존 `SK-` 기록은 paper 로 간주(과거 로그 호환) | `domain.py`, `broker/router.py` | 접두어 불일치 전송 0건 |
| 5 | 프로필별 `.lock` | `ops/common.py` | 이중 실행 거부, 남은 잠금 해제 |
| 6 | `migrate_var` 도구: 기존 `var\*` → `var\paper\*` (dry-run → 실행, 원본은 `var\_pre_d049_백업` 으로) | `ops/migrate_var.py` | 이동 후 paper_report 결과 동일 |
| 7 | 전략별 허용 프로필, `tech_orders` 의 live 거부 재확인 | `strategy/`, `ops/tech_orders.py` | S03 live 실행 거부 |
| 8 | `paper_day.ps1 -Account paper` 인자, 문서 갱신 | `scripts/`, `WINDOWS_QUICKSTART.md` | — |

예상: 1~8 합쳐 1.5일. 모의 기간(~12/2)에 경로가 바뀌면 혼란이 생기므로 **코드는 미리 머지하되 `migrate_var` 실행은 12/2 마감 뒤**로 둡니다(아래 결정 1).

### 2단계 — 총괄·대표 승인 후 (실계좌 코드)
- `KisLiveClient`(실전 도메인 고정, `KIS_LIVE_*` 키만), live 프로필 `OrderRouter` 경로, `LIVE_TRADING_APPROVED` 해제 절차(DECISIONS 기록 + 코드 리뷰 + 승인 커밋)
- live 손실 한도는 `limits_for(T0)`(일 −5%, 주 −8%, 최종 −20%), 운용 금액은 15만 원 고정
- `compare_paper_live` 보고서(신호 일치율, 체결 여부, 슬리피지 비율) → 매매팀·회계팀 전달

## 5. 총괄 결정 (2026-11-27) 과 1단계 구현 현황

| 결정 | 반영 |
|---|---|
| migrate_var·키 파일 이동은 12/2 모의 마감 뒤 | `ops/migrate_var.py` (미리보기 기본, `--apply --operator`) — 아래 §6 절차 |
| KILL_ALL 추가: 두 계좌 신규 매수 차단, 청산 매도 허용(D-026), 계좌별 한도·킬스위치는 그대로 | `ops/kill_all.py` `--status/--trip/--clear`, `KillSwitch` 가 계좌별 파일 + `var\KILL_ALL` 둘 다 확인 |
| 주문 ID 접두어 PP-/LV- 확정 | `config.ORDER_ID_PREFIX`, 라우터 전송 전 검사, 시작 점검이 다른 계좌 접두어 기록 차단 |
| 1단계는 `claude/stock-king-03-dev-d049` 에서, 12/2 뒤 총괄 확인 후 dev 병합 | 이 브랜치 |
| STOCKKING_ACCOUNT 가 없으면 지금 경로로 동작(호환 모드) | `account_profile() is None` → `var\`, `%APPDATA%\stockking\secrets.env`, `kis_paper_token.json`, 주문 ID `SK-`, 잠금 없음, 운용 금액 기본 15만 원(기존과 같음) — `test_compat_mode_keeps_legacy_paths` 와 기존 테스트 210개 그대로 통과 |
| 2단계(실계좌 코드) 별도 승인 전 착수 안 함 | `KisLiveClient` 없음, `LIVE_TRADING_APPROVED=False` 그대로 |

1단계 구현 목록 (§4 의 1~8): `paths.py`(var 경로 한 곳, 하드코딩 금지 AST 검사), `config.account_profile()`(프로세스당 1회 고정), 계좌별 키 파일·토큰(paper 는 이동 전 기존 파일 사용, live 는 기존 파일 절대 사용 안 함, 다른 계좌 키 섞이면 거부), 접두어, `.lock`(윈도우는 프로세스를 건드리지 않는 방식으로 PID 확인), `migrate_var`, S03·D 시리즈 실계좌 거부(2단계 대비), `paper_day.ps1 -Account paper`. 테스트: `tests/test_d049_accounts.py` 17개.

## 6. 12/2 마감 뒤 전환 절차 (대표 PC, 총괄 확인 후)
```powershell
cd C:\stockking\awesome
git pull                                   # d049 가 dev 에 병합된 뒤
Remove-Item Env:STOCKKING_ACCOUNT -ErrorAction SilentlyContinue
$PY = ".\.venv\Scripts\python.exe"
& $PY -m stockking.ops.migrate_var                         # 미리보기: 옮길 항목 확인
& $PY -m stockking.ops.migrate_var --apply --operator 대표  # var\ → var\paper\ (백업 var\_pre_d049_backup\), 키 파일 → paper\
$env:STOCKKING_ACCOUNT = "paper"
& $PY -m stockking.ops.startup_check                       # 이동된 감사 로그로 정상 시작되는지
```
- 이후 모의는 항상 `$env:STOCKKING_ACCOUNT = "paper"`(또는 `paper_day.ps1 -Account paper`). 이 값 없이 실행하면 명령이 거부되어, 빈 `var\` 를 보고 기록을 놓치는 일이 없습니다.
- 운용 금액(총괄 결정 11/27): 병행 모의는 **T0 시작일(paper 첫 실행일)의 주문 가능 금액 전액으로 다시 고정**합니다. 5일 점검 주문 손익이 병행 모의 기준 금액에 섞이지 않게 하기 위해서입니다.
  - `migrate_var --apply` 는 기존 `operating_capital.json` 을 지우지 않고 `var\paper\archive\operating_capital_<고정일>.json` 으로 보관합니다.
  - paper 첫 `connect_check`(또는 `ledger_close --open`)가 새로 고정하고, 감사 로그에 `capital_fixed`(새 값·이전 값·사유 `D-049 restart`)를 남깁니다.
  - 그 뒤로는 파일 값을 이어 씁니다. 다시 고정하려면 대표 승인 뒤 `connect_check --refix-capital --operator 대표` 를 실행합니다. 이전 파일은 archive 로 보관되고 사유 `refix (승인: 대표)` 가 기록됩니다.
- 비상 정지: `& $PY -m stockking.ops.kill_all --trip --reason "…" --operator 대표` / 해제 `--clear --operator 대표`.

## 7. 2단계 설계 범위 추가 — D-051·D-052 실계좌 점검 주문 L_TECH (설계만, 착수 승인 전)

> 근거: D-052(D-027의 유일한 예외), D-051(T0→T1 조건 중 '실계좌 점검 주문 4건 정상 체결·정산'), 리스크관리실 조건 ①~⑥. **2단계 착수 승인과 대표 운용 ON 승인 전에는 구현·실행하지 않습니다.**

### 7.1 식별자
| 항목 | 값 |
|---|---|
| strategy_id · reason_code | `L_TECH` |
| 청산 사유 exit_reason | `X_LTECH` (15:15 재시도는 `X_ADMIN`) |
| 장부 book | `LTECH` — 전략 성과·C2 에서 제외, 장부 대사·회계·손실 한도에는 포함 |
| 주문 ID | `LV-` (live 프로필) |
| 명령 | `python -m stockking.ops.live_tech_orders --run L1-buy / L1-sell / L2-buy / L2-sell / sweep / --status --operator 대표` |

### 7.2 실행 조건 (하나라도 어기면 증권사 접속 전에 거부)
1. live 프로필(`STOCKKING_ACCOUNT=live`) + `TradingMode.LIVE` + 실계좌 브로커 + `LIVE_TRADING_APPROVED` + 운용 ON 승인 기록. paper 프로필이나 호환 모드에서는 명령 자체가 거부됩니다(`T_TECH` 가 모의 전용인 것과 정반대).
2. `--operator` 필수. 실행자·시각·명령을 `var\live\journal` 에 기록합니다(조건 ⑥).
3. **영구 카운터**(조건 ①): `var\live\ltech_counter.json` 에 주문 1건마다 `{leg, side, symbol, client_order_id, ts, operator}` 를 덧붙이기만 합니다.
   - 증권사로 보내기 **전에** 먼저 기록합니다. 그래서 전송 중 다운돼도 1건으로 셉니다.
   - 한도는 매수 2·매도 2, 합계 4. 다 쓰면 모든 `--run` 이 영구 거부됩니다.
   - **해제·초기화 명령은 만들지 않습니다.** 파일을 지우거나 고쳐도 소용없도록, 매번 감사 로그의 `L_TECH` 주문 요청 수와 비교해 **큰 쪽**을 씁니다. 둘이 다르면 시작 점검이 막고 경보를 냅니다.
4. 순서 규칙(D-052):
   - L1-buy → 같은 날 L1-sell → 다른 날 L2-buy → 같은 날 L2-sell 순서만 허용합니다.
   - L2 는 L1 과 다른 종목이어야 합니다.
   - 열린 L_TECH 보유분이 있으면 매수는 거부합니다.
5. 종목 조건(D-052): KOSPI200 구성, 거래대금 상위, 상태 이상 없음(자료팀 후보 파일 role `L_TECH`). 실행 시점 현재가로 **1주 가격 ≤ 주문 목표의 110%**(15만 원이면 33,000원)를 다시 확인합니다. 수량은 1주 고정입니다.
6. **실행 금지일**(조건 ⑤): 그날 T0 일중 손실 한도에 도달했거나, 계좌 킬스위치 또는 KILL_ALL 이 발동 중이면 **매수 회차(L1-buy·L2-buy)를 시작하지 않습니다.**
   - 이미 산 보유분의 청산 매도는 예외로 허용합니다(D-026, 오버나이트 금지 ④). 아래 '결정 필요 1'.

### 7.3 리스크 관문 (우회 없음, 조건 ②)
- `RiskGate(limits_for(T0))` 를 그대로 거칩니다: 1건 손실 2%(15만 원 기준 3,000원, 손절가 필수), T0 일중 −5%·주간 −8%·최종 −20%, 킬스위치 중 매수 금지(D-026), 비중 22.1%, 공매도 금지.
- **시장가 X_LTECH**(조건 ③): `market_sell_exit_reasons` 에 `X_LTECH` 를 넣습니다. 다만 다음을 **모두** 만족할 때만 통과합니다. 다른 live 시장가 규칙은 바꾸지 않습니다.
  - 관문 조건: `strategy_id == "L_TECH"` · 매도 · 국내 · 보유 수량 이내
  - 라우터 조건: live 프로필 · LIVE 모드 · 실계좌 브로커
  - 모의에서 `X_LTECH` 가 오면 거부합니다(`X_TECH` 처리의 거울상).
- 매도 방식: 1회차는 국내 시장가(`X_LTECH`), 2회차는 시장성 지정가(기준가 ×0.98, 호가 단위 내림, `X_LTECH`).

### 7.4 오버나이트 금지 (조건 ④)
- `--run sweep` 을 15:15 에 실행합니다(paper_day 처럼 live 일정 스크립트에 고정).
  1. 체결 조회로 L_TECH 보유 잔량과 미체결 매도를 확인합니다.
  2. 미체결 매도는 취소하고, 잔량을 `X_ADMIN` 시장성 지정가(×0.98)로 1회 다시 냅니다.
  3. 15:20 에 다시 조회해 잔량이 남아 있으면 **경보**를 냅니다:
     - 콘솔에 빨간 줄로 표시
     - 감사 로그에 `ltech_overnight_alert` 기록
     - `var\live\ALERT_LTECH` 파일 생성 → 다음 날 `startup_check` 가 대표 확인(`resolve`) 전까지 모든 live 신규 매수를 막음
- 재시도 주문은 같은 매도 회차의 잔량을 대신하는 것이라 카운터 4건에 더하지 않고, 감사 로그에만 연결해 남깁니다(`orgn` 회차 표시). → '결정 필요 2'

### 7.5 정산·수수료 확인 (D-052)
- `ledger_close` 가 book `LTECH` 의 실제 수수료·세금 차감액을 따로 집계합니다.
- `CostModel.kr_v024`(D-045) 예상값과 차이를 보고합니다. 회계팀 대사 입력으로 씁니다.

### 7.6 테스트 (2단계 구현 시 필수)
| 테스트 | 확인 내용 |
|---|---|
| `test_ltech_fifth_order_refused` | 4건 뒤 5번째는 어느 회차든 거부. 카운터 파일을 지워도 감사 로그 기준으로 거부 |
| `test_ltech_refused_on_paper_and_compat` | paper 프로필·호환 모드·모의 브로커에서 거부 |
| `test_ltech_refused_while_kill_switch_or_daily_limit` | 계좌 킬스위치·KILL_ALL·일중 한도 도달일에는 매수 회차 거부, 보유분 청산 매도는 허용 |
| `test_x_ltech_rejected_for_other_strategies` | S02·T_TECH 등 다른 전략이 `X_LTECH` 시장가를 쓰면 관문 거부, 모의에서 `X_LTECH` 거부 |
| `test_ltech_counter_written_before_send` | 증권사 전송 중 예외가 나도 1건으로 셈 |
| `test_ltech_sequence_and_symbol_rules` | 순서 위반, 같은 종목 2회차, 보유 중 매수, 가격 33,000원 초과는 거부 |
| `test_ltech_sweep_retries_then_alerts` | 15:15 미체결 → X_ADMIN ×0.98 재시도 → 잔량 남으면 경보 파일 생성, 시작 점검 차단 |
| `test_ltech_book_excluded_from_strategy_and_c2` | 성과·C2 제외, 대사·손실 한도 포함 |
| `test_ltech_sweep_retry_not_counted` | X_ADMIN 재시도 뒤에도 카운터 그대로, 감사 로그에 원 매도 회차와 연결 |

### 7.7 총괄 결정 (2026-11-27, main 8d7042b D-052 보완)
1. **조건 ⑤는 매수 회차에만 적용합니다.** 한도 도달일·킬스위치(계좌별·KILL_ALL) 발동 중에도 보유 1주의 청산 매도(L1-sell·L2-sell·sweep)는 허용합니다. → §7.2-6 그대로, 테스트 `test_ltech_refused_while_kill_switch_or_daily_limit`
2. **X_ADMIN 재시도는 4건 카운터에 넣지 않습니다.** 같은 매도 회차의 잔량 처리로 보고, 감사 로그에 원 매도 회차와 연결해 남깁니다. → 테스트 `test_ltech_sweep_retry_not_counted` 추가
3. **휴대폰 알림을 보냅니다(대표 결정 2026-11-27).** 콘솔·감사 로그 `ltech_overnight_alert`·`var\live\ALERT_LTECH`·다음 날 live 신규 매수 차단에 더해 휴대폰 알림을 추가합니다. **채널: 텔레그램 봇(대표 선택 2026-11-27)** — 준비 절차 `TELEGRAM_ALERT_SETUP.md`, 키는 `%APPDATA%\stockking\live\secrets.env` 의 `TELEGRAM_BOT_TOKEN`·`TELEGRAM_CHAT_ID`.
   - 알림 전송이 실패해도 주문·차단 동작은 그대로입니다(알림은 보조 수단). 실패는 감사 로그와 콘솔에 남깁니다.

2단계 착수는 여전히 별도 승인 전까지 하지 않습니다.

### 7.8 휴대폰 알림 채널 후보 → 텔레그램 봇 선택(2026-11-27)
프로그램은 대표 PC의 Python 표준 라이브러리만으로 HTTPS 요청 1번을 보내는 방식으로 만듭니다. 토큰·주소는 `%APPDATA%\stockking\live\secrets.env` 에 넣고 저장소에는 절대 커밋하지 않습니다. 알림 내용에는 계좌번호·금액·키를 넣지 않습니다(예: `[주식왕] 15:20 실계좌 점검 주문 매도 미체결 1주 — 확인 필요`).

| 채널 | 비용 | 준비 | 장점 | 단점 |
|---|---|---|---|---|
| **텔레그램 봇** (권장) | 무료 | 앱 설치 → BotFather 로 봇 만들기 → 토큰·내 chat_id 를 키 파일에 저장(10분) | 즉시 푸시, 설정 간단, 토큰 만료 없음, 이후 다른 경보(킬스위치·손실 한도)에도 재사용 | 텔레그램 앱이 필요 |
| 카카오톡 '나에게 보내기' | 무료 | 카카오 개발자 앱 등록 + 로그인 동의. 액세스 토큰 6시간, 갱신 토큰 약 2개월마다 재로그인 | 이미 쓰는 앱 | 토큰 갱신 관리가 번거롭고, 갱신이 끊기면 알림이 조용히 멈출 위험 |
| ntfy (푸시 앱) | 무료 | ntfy 앱 설치 + 추측 어려운 토픽 이름 1개 | 가입 없이 주소 하나로 끝 | 공용 서버는 토픽 이름을 아는 사람이 볼 수 있음(내용 최소화 필요) |
| 이메일(Gmail 앱 비밀번호) | 무료 | Gmail 2단계 인증 + 앱 비밀번호 | 기록이 메일함에 남음 | 푸시가 늦거나 스팸함으로 갈 수 있음 |
| 디스코드·슬랙 웹훅 | 무료 | 서버/워크스페이스 + 웹훅 주소 | 설정 간단 | 해당 앱 필요, 웹훅 주소 유출 시 누구나 글 작성 가능 |
| 문자(SMS) 발송 서비스 | 건당 유료 | 업체 가입·발신번호 등록(사업자·본인 인증) | 앱 없이 문자로 받음 | 비용, 가입 절차 가장 복잡 |

### 7.9 텔레그램 알림 범위 (총괄 결정 2026-11-27, 구현은 2단계 승인 뒤)
| 계좌 | 보내는 사건 | 표시 |
|---|---|---|
| live | 킬스위치·KILL_ALL 발동/해제, 일·주·최종 손실 한도 도달, `startup_check` 시작 차단, L_TECH 오버나이트 경보 | `[실전]` |
| paper | 킬스위치·KILL_ALL 발동/해제만 | `[모의]` |

**설계**
- 모듈 `stockking.notify`(표준 라이브러리 HTTPS 1회, 제한 시간 5초). 전송 실패는 감사 로그 `notify_failed`·콘솔에만 남기고, 주문·차단 동작은 바꾸지 않습니다. 예외를 밖으로 던지지 않습니다.
- 내용 원칙: 사건 종류·계좌 구분·시각·조치 안내만 넣습니다. 계좌번호·금액·키는 넣지 않습니다. 예: `[실전] 09:41 일중 손실 한도 도달 — 신규 매수 정지. startup_check 로 확인`
- **중복 방지**: 사건마다 키 `(계좌, 종류, 날짜, 대상)`를 만들고, 보낸 키는 `var\<프로필>\notify_sent.jsonl` 에 남겨 같은 키는 다시 보내지 않습니다. KILL_ALL 은 두 계좌 공용이라 `var\notify_sent_shared.jsonl` 하나만 씁니다. 그래서 두 프로세스가 동시에 봐도 1번만 갑니다(파일 잠금 후 기록, 기록에 성공한 쪽만 전송). 해제 알림은 발동 알림과 다른 키입니다.
- **키 위치(수정)**: 모의 프로세스는 실전 키 파일을 읽을 수 없게 분리돼 있습니다(D-049). 그래서 텔레그램 키는 두 계좌 공용 파일 `%APPDATA%\stockking\notify.env` 에 둡니다. 이 파일에는 `TELEGRAM_BOT_TOKEN`·`TELEGRAM_CHAT_ID` 만 허용하고, `KIS_*` 가 들어 있으면 실행을 거부합니다. 권한·OneDrive·저장소 밖 검사는 다른 키 파일과 같습니다. → `TELEGRAM_ALERT_SETUP.md` 5단계를 이 경로로 바꿨습니다.
- **notify.env 안전 조건(총괄 승인 2026-11-27)** — `stockking.notify.load_notify_file()` 이 읽을 때마다 검사합니다.
  1. 허용 키는 `TELEGRAM_BOT_TOKEN`·`TELEGRAM_CHAT_ID` 두 개뿐입니다. 그 외 키가 하나라도 있으면 거부하고 알림을 끕니다. 오류 메시지에는 키 이름만 넣고 값은 넣지 않습니다.
  2. 권한 검사: 윈도우는 `icacls` 결과를 읽어, 현재 사용자 외의 계정(Everyone·Users·Authenticated Users 등)이 있으면 **경고**합니다. 경고는 콘솔과 감사 로그 `notify_file_insecure` 에 남기고, icacls 잠금 명령을 안내합니다. POSIX 는 그룹·기타 권한이 있으면 경고합니다. 경고만 하고 알림은 계속 보냅니다.
  3. 파일이 저장소 폴더 안에 있거나 OneDrive 동기화 폴더 안에 있으면 거부합니다(기존 키 파일 규칙과 같음). 저장소 `.gitignore` 에도 `notify.env` 를 추가합니다.
  4. 토큰 비노출: 토큰은 `Secret` 으로 감싸 `repr`·`str` 이 `****` 입니다. 전송 주소(`/bot<토큰>/sendMessage`)는 예외·로그에 남기기 전에 토큰 부분을 `****` 로 바꿉니다. `urllib` 오류 메시지에 들어간 주소도 같은 방식으로 지웁니다. 응답 원문 진단 로그(STOCKKING_KIS_DEBUG)와 같은 마스킹 함수를 씁니다.
- 테스트(2단계): 전송 실패해도 킬스위치·관문 결과 동일, 같은 사건 2번 → 전송 1번, KILL_ALL 두 프로필 동시 → 1번, paper 는 손실 한도 사건을 보내지 않음, 메시지에 숫자 금액·계좌번호 패턴이 없음. notify.env 추가: 허용 외 키(예: `KIS_PAPER_APP_KEY`, `FOO`) 거부, 저장소 안·OneDrive 안 파일 거부, 넓은 권한 경고(윈도우는 icacls 출력 예시로, POSIX 는 chmod 644 로), **토큰 마스킹**(전송 실패·HTTPError·URLError·예외 traceback·감사 로그·콘솔 출력 어디에도 토큰 문자열이 없음을 가짜 토큰으로 검사).

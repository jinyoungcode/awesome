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

## 5. 결정 필요 (총괄)
1. **var 이동 시점**: 12/2 모의 마감 뒤 `migrate_var` 실행(권장), 아니면 지금 즉시.
2. **전체 정지 스위치**: 계좌별 킬스위치 외에 두 계좌를 한 번에 멈추는 `KILL_ALL` 파일을 둘지(권장: 둔다. 계좌별 한도는 그대로 따로 적용하고, 대표 수동 비상정지용으로만 사용).
3. **주문 ID 접두어**: `PP-`/`LV-` 로 확정할지(회계팀 장부 대사에 쓰임).

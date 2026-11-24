# 3거래일 모의투자 점검 절차서 (D-022, D-023)

> 작성: 프로그램 생성팀 / 2026-11-24 · 실행 장소: **대표 PC** (클라우드 세션은 KIS 서버 접속 불가, `SECRETS.md` §2)
> 모든 스크립트는 모의투자 전용입니다. 실계좌 코드는 차단되어 있습니다(`config.LIVE_TRADING_APPROVED=False`).
> **운용 기준 자금 15만 원(D-031·D-032, 예비금 없음)**: 주문 목표 3만 원(= 15만 ÷ 5), 1주 예외 가격 상한 4.5만 원, 최종 손절 손실 3만 원(−20%). 설정값 `STOCKKING_OPERATING_CAPITAL=150000`(기본).
> 손실 한도는 자금 단계 설정을 따릅니다: `STOCKKING_CAPITAL_STAGE=paper`(기본, T0 값 적용: 일 −5%, 주 −8%, 원금 −20% 최종 손절 — D-028).

## 0. 준비 (D-1, 한 번만)

1. Python 3.11 이상 설치 → 저장소 내려받기 → `claude/stock-king-03-dev` 브랜치
2. `pip install -e ".[dev]"` 후 `python -m pytest` → 전부 통과 확인
3. KIS 가입 → KIS Developers에서 **모의투자 계좌 신청 + 모의 앱키 발급**. 모의투자 신청 때 투자금을 고를 수 있으면 **15만 원에 가장 가까운 금액**으로 신청하세요. 모의 계좌 금액이 15만 원과 크게 다르면 스크립트가 경고합니다(비중·손실 한도가 실전과 달라짐)
4. 키 설정: `SECRETS.md` §3 (비밀값 파일, 권한 600)
5. 오프라인 훈련 2종(증권사 접속 없음, 지금 바로 가능):
   ```
   python -m stockking.ops.restart_drill
   python -m stockking.ops.killswitch_drill --operator 대표
   ```

## 1. 매일 일정 (KST)

| 시각 | 명령 | 목적 |
|---|---|---|
| 08:25 | `python -m stockking.ops.ledger_close --open` | 장부 시작점 저장 (증권사 D+2 예수금·보유수량) |
| 08:30 | `python -m stockking.ops.startup_check` | 킬스위치·미확인 주문·로그 손상 확인 → "시작 가능"이어야 진행 |
| 08:35 | `python -m stockking.ops.connect_check` | 토큰·현재가·잔고 (주문 없음) |
| 장중 1회 (1일차) | `python -m stockking.ops.connect_check --order` | 1주 지정가 주문 → 즉시 취소 → 내역 확인 |
| 장중 (1일차) | `python -m stockking.ops.killswitch_drill --operator 대표 --kis` | 실제 모의 계좌로 킬스위치 훈련 (주문 0건 전송) |
| 장중 (1일차) | 아래 §2 재시작 실습 | 실제 재시작 복구 |
| 15:40 | `python -m stockking.ops.daily_reconcile` | 주문 내역 대사·중복·수동/미기록 주문 확인(D-029) → `var/reconcile/날짜.json` |
| 15:45 | `python -m stockking.ops.ledger_close --close` | 회계팀 양식 장부(trades·positions·daily_pnl) 작성 + 현금 1원·수량 1주 단위 대사 (C1) |
| 15:50 | `python -m stockking.ops.paper_report` | D-024 점검표 C1~C6 현황 |
| 2일차 15:50 | `python -m stockking.ops.tech_orders --check` | D-027 발동 조건 판정 (S02 주문 3건 미만·매수 체결 0·청산 미실행) |

키를 파일에 두지 않으려면 모든 명령 뒤에 `--prompt-keys`를 붙여 그때그때 입력하면 됩니다.

## 2. 재시작 복구 실습 (1일차)
1. `connect_check --order` 실행 중 주문 접수 직후 **Ctrl+C**로 강제 종료하거나 터미널을 닫습니다.
2. `startup_check`를 실행합니다. 미확인 주문이 있으면 "시작 불가"가 나와야 정상입니다.
3. KIS 앱에서 해당 주문 상태를 확인한 뒤 기록합니다.
   `python -m stockking.ops.startup_check --resolve <주문번호> --outcome unfilled_canceled --operator 대표`
4. 다시 `startup_check`를 실행해 "시작 가능"이 나오면 통과입니다.

## 2-1. 점검용 주문 T1~T3 (D-027, 3일차 — 발동 조건 충족 시만)

모의투자 전용입니다. LIVE 모드·실전 도메인·실계좌 브로커에서는 실행 자체가 거부되고, 모든 주문은 리스크 관문을 거칩니다. 감사 로그에 `reason_code=T_TECH`로 남아 전략 성과에서 제외됩니다.

| 시각 | 명령 |
|---|---|
| 08:40~08:59 | `python -m stockking.ops.tech_orders --run T1 --symbol <자료팀 지정 종목> --operator 대표` |
| T1 체결 30분 후 | `python -m stockking.ops.tech_orders --run T2 --symbol <같은 종목> --operator 대표` |
| 장중 | `python -m stockking.ops.tech_orders --run T3 --symbol 005930 --operator 대표` |
| 15:15 이후 | `python -m stockking.ops.tech_orders --run T3-cancel --operator 대표` |

T4(미국)·T5(1주 예외)는 PR #2 병합과 D-021/D-025 구현 후에 추가됩니다.

## 3. 통과 점검표 (D-024 확정) 와 준비 상태

| 항목 | 기준 | 확인 수단 | 준비 상태 |
|---|---|---|---|
| 킬스위치 1회 훈련 | 매수 차단·**청산 매도 허용(D-026)**·0건 전송·자동발동·수동해제 | `killswitch_drill --kis` | ✅ 준비됨 |
| 재시작 복구 확인 | 미확인 주문 식별, 중복 차단, 킬스위치 유지 | `restart_drill` + §2 실습 | ✅ 준비됨 |
| 오주문·중복 0건 | 증권사 내역 ↔ 감사 로그 | `daily_reconcile` | ✅ 준비됨 |
| 장부·잔고 대사 100% | 시작점 + 당일 체결 = 증권사 마감 잔고 (1원·1주) | `ledger_close --open/--close` | ✅ 준비됨 (국내. 수수료는 체결내역 값 확인 전 추정치 → C-2 실측으로 확정) |
| 모의 주문 5건 이상 | S02 주문 + 부족분 T_TECH(D-027) | 전략 자동 실행기 + `tech_orders` | ⚠ T1~T3 준비됨, S02 자동 실행기 **미구현**(S02 명세서 + PR #2 병합 필요) |
| 신호 일치 100% | 같은 데이터로 다시 만든 주문계획 = 실제 주문 | S02 주문계획(`strategy/s02.py`) + 비교 리포트 | ⚠ S02 신호·주문계획 골격 완료, 수량(D-005 사이징)·주문 제출·비교 리포트는 PR #2 병합 후 |

**3일 일정의 위험:** 전략 자동 실행기(S02 신호 → 주문)는 매매팀 S02 명세서와 PR #2 병합이 있어야 만들 수 있습니다. 둘이 늦어지면 1일차는 연결·훈련만 하고, 2~3일차에 주문 5건과 신호 일치를 확인하는 일정이 빠듯해집니다. 미통과 시 연장(총괄 권고)을 전제로 합니다.

## 4. 문제 발생 시
- 아무 이상이나 감지되면 먼저 `touch var/KILL_SWITCH`(윈도우: `type nul > var\KILL_SWITCH`)로 중단 → 원인 확인 → `killswitch` 해제는 사람이 수동으로
- 오류 코드 EGW00201(호출 한도 초과): 1분 대기 후 재실행 / EGW00121(토큰 만료): 재실행하면 새 토큰 발급

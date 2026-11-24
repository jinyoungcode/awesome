# 3거래일 모의투자 점검 절차서 (D-022, D-023)

> 작성: 프로그램 생성팀 / 2026-11-24 · 실행 장소: **대표 PC** (클라우드 세션은 KIS 서버 접속 불가, `SECRETS.md` §2)
> 모든 스크립트는 모의투자 전용입니다. 실계좌 코드는 차단되어 있습니다(`config.LIVE_TRADING_APPROVED=False`).

## 0. 준비 (D-1, 한 번만)

1. Python 3.11 이상 설치 → 저장소 내려받기 → `claude/stock-king-03-dev` 브랜치
2. `pip install -e ".[dev]"` 후 `python -m pytest` → 전부 통과 확인
3. KIS 가입 → KIS Developers에서 **모의투자 계좌 신청 + 모의 앱키 발급**
4. 키 설정: `SECRETS.md` §3 (비밀값 파일, 권한 600)
5. 오프라인 훈련 2종(증권사 접속 없음, 지금 바로 가능):
   ```
   python -m stockking.ops.restart_drill
   python -m stockking.ops.killswitch_drill --operator 대표
   ```

## 1. 매일 일정 (KST)

| 시각 | 명령 | 목적 |
|---|---|---|
| 08:30 | `python -m stockking.ops.startup_check` | 킬스위치·미확인 주문·로그 손상 확인 → "시작 가능"이어야 진행 |
| 08:35 | `python -m stockking.ops.connect_check` | 토큰·현재가·잔고 (주문 없음) |
| 장중 1회 (1일차) | `python -m stockking.ops.connect_check --order` | 1주 지정가 주문 → 즉시 취소 → 내역 확인 |
| 장중 (1일차) | `python -m stockking.ops.killswitch_drill --operator 대표 --kis` | 실제 모의 계좌로 킬스위치 훈련 (주문 0건 전송) |
| 장중 (1일차) | 아래 §2 재시작 실습 | 실제 재시작 복구 |
| 15:40 | `python -m stockking.ops.daily_reconcile` | 주문 내역 대사·중복·미기록 주문 확인 → `var/reconcile/날짜.json` |

키를 파일에 두지 않으려면 모든 명령 뒤에 `--prompt-keys`를 붙여 그때그때 입력하면 됩니다.

## 2. 재시작 복구 실습 (1일차)
1. `connect_check --order` 실행 중 주문 접수 직후 **Ctrl+C**로 강제 종료하거나 터미널을 닫습니다.
2. `startup_check`를 실행합니다. 미확인 주문이 있으면 "시작 불가"가 나와야 정상입니다.
3. KIS 앱에서 해당 주문 상태를 확인한 뒤 기록합니다.
   `python -m stockking.ops.startup_check --resolve <주문번호> --outcome unfilled_canceled --operator 대표`
4. 다시 `startup_check`를 실행해 "시작 가능"이 나오면 통과입니다.

## 3. 통과 점검표 (총괄 권고안 — 대표 승인 대기) 와 준비 상태

| 항목 | 기준 | 확인 수단 | 준비 상태 |
|---|---|---|---|
| 킬스위치 1회 훈련 | 차단·0건 전송·자동발동·수동해제 | `killswitch_drill --kis` | ✅ 준비됨 |
| 재시작 복구 확인 | 미확인 주문 식별, 중복 차단, 킬스위치 유지 | `restart_drill` + §2 실습 | ✅ 준비됨 |
| 오주문·중복 0건 | 증권사 내역 ↔ 감사 로그 | `daily_reconcile` | ✅ 준비됨 |
| 장부·잔고 대사 100% | 증권사 잔고 ↔ 시스템 장부 | `daily_reconcile`이 잔고 저장 | ⚠ 부분 — **회계팀 장부 양식** 수령 후 자동 비교 추가 |
| 모의 주문 5건 이상 | 전략 신호로 낸 주문 | 전략 자동 실행기 | ❌ **미구현** — S02 명세서 + PR #2 병합 필요 |
| 신호 일치 100% | 같은 날 백테스트 신호 = 모의 주문 | 신호 비교 리포트 | ❌ **미구현** — 위와 같음 |

**3일 일정의 위험:** 전략 자동 실행기(S02 신호 → 주문)는 매매팀 S02 명세서와 PR #2 병합이 있어야 만들 수 있습니다. 둘이 늦어지면 1일차는 연결·훈련만 하고, 2~3일차에 주문 5건과 신호 일치를 확인하는 일정이 빠듯해집니다. 미통과 시 연장(총괄 권고)을 전제로 합니다.

## 4. 문제 발생 시
- 아무 이상이나 감지되면 먼저 `touch var/KILL_SWITCH`(윈도우: `type nul > var\KILL_SWITCH`)로 중단 → 원인 확인 → `killswitch` 해제는 사람이 수동으로
- 오류 코드 EGW00201(호출 한도 초과): 1분 대기 후 재실행 / EGW00121(토큰 만료): 재실행하면 새 토큰 발급

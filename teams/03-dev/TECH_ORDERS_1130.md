# 11/30(월) D-027 점검용 주문 T1~T5 실행 절차 (D-047) — 대표 PC PowerShell

> 작성: 프로그램 생성팀 / 2026-11-27 · 근거: D-047(C6을 점검용 주문으로 11/30에 채움), D-027(모의 전용·관문 필수·T_TECH), D-033(T4 미국 제외), D-044·D-046(운용 금액 기준)
> 모든 주문은 **리스크 관문을 통과**해야 나가고, **PAPER 모드·모의 도메인·모의 브로커가 아니면 실행 자체가 거부**됩니다(실계좌 승인 상태여도 거부, 테스트 포함). 감사 로그에 `reason_code=T_TECH`로 남아 전략 성과에서 빠집니다.

## 0. 준비 (11/30 08:20, PowerShell 새 창)
```powershell
cd C:\stockking
Set-ExecutionPolicy -Scope Process Bypass -Force
.\.venv\Scripts\Activate.ps1
git pull
$env:STOCKKING_OPERATING_CAPITAL = "auto"
$C = "$env:USERPROFILE\stockking-data\s02_inputs\2026-11-27\tech_candidates.csv"   # 자료팀 11/27 실행 결과
Test-Path $C        # True 여야 함
python -m pytest -q # 맨 끝 줄에 failed 가 없어야 함
```
평소 아침 순서(`ledger_close --open` → `startup_check` → `connect_check`)를 먼저 마칩니다(WINDOWS_QUICKSTART B-1).

## 1. 08:35 dry-run — 주문 없이 관문 판정만
```powershell
python -m stockking.ops.tech_orders --run T1 --candidates $C --operator 대표 --dry-run
python -m stockking.ops.tech_orders --run T3 --candidates $C --operator 대표 --dry-run
python -m stockking.ops.tech_orders --run T5 --candidates $C --operator 대표 --dry-run
```
▶ 정상: `[dry-run] T1 buy 000000 N주 …원 → 관문 통과 (전송 안 함)`
- T1 이 `목표의 90~110%를 정수 수량으로 못 맞춤` → `--rank 2` 로 다시 시도
- T5 는 '목표 초과' 1주 예외입니다. 운용 금액이 1천만 원이면 목표가 200만 원이라 **1주 220만~250만 원 종목**이 필요하고, 후보가 없을 수 있습니다. 후보가 없거나 관문이 거부하면 T5 는 **'해당 없음'으로 기록**하고 건너뜁니다(아래 §5).

## 2. 08:45 T1 매수 (장전 동시호가 지정가)
```powershell
python -m stockking.ops.tech_orders --run T1 --candidates $C --operator 대표
```
▶ `T1 submitted: 000000 N주 주문번호 …`

## 3. 09:05 이후 체결 확인
```powershell
python -m stockking.ops.tech_orders --status
```
▶ `T1 000000 N주 주문번호 … → 체결 N/N주`
- 10:00까지 `체결 0/N주`면: KIS 모의 앱에서 T1 주문을 취소 → `--rank 1` 그대로 다시 §2 실행(현재가로 새 지정가)

## 4. T1 체결 30분 뒤 T2 매도 (T1 보유분 전량, 시장가)
```powershell
python -m stockking.ops.tech_orders --run T2 --candidates $C --operator 대표 --dry-run
python -m stockking.ops.tech_orders --run T2 --candidates $C --operator 대표
python -m stockking.ops.tech_orders --status
```
- KIS 가 시장가 매도를 거부하면(주문 유형 오류): `$env:STOCKKING_KR_SELL_MODE = "marketable_limit"` 입력 후 T2 를 다시 실행 → 기준가 × 0.98 지정가로 나갑니다. **이 결과(시장가 지원 여부)를 꼭 기록**(T0 청산 방식 결정, PAPER_3DAY_CHECK §6.1-1)

## 5. 10:00 T3(−10% 지정가 1주) · T5(1주 예외)
```powershell
python -m stockking.ops.tech_orders --run T3 --candidates $C --operator 대표
python -m stockking.ops.tech_orders --run T5 --candidates $C --operator 대표     # §1 dry-run 이 통과했을 때만
```
- **C6(5건) 채우기:** T4(미국)가 빠져 점검 주문은 T1·T2·T3·T5의 **최대 4건**이고, S02 주문은 0건입니다. 5건을 채우려면 다른 종목으로 T3 를 한 번 더 냅니다(총괄 승인 필요):
  `python -m stockking.ops.tech_orders --run T3 --candidates $C --rank 2 --operator 대표`
- T5 가 '해당 없음'이면 T3 두 번을 포함해 T1·T2·T3·T3' = 4건이므로, 총괄·매매팀이 5번째 주문을 정해야 합니다(아래 '결정 필요').
- T5 로 산 1주는 모의 종료(12/2)까지 보유해도 됩니다(청산은 S02·대표 판단).

## 6. 15:15 이후 T3 전부 취소
```powershell
python -m stockking.ops.tech_orders --run T3-cancel --operator 대표 --dry-run
python -m stockking.ops.tech_orders --run T3-cancel --operator 대표
python -m stockking.ops.tech_orders --status
```
▶ T3 마다 `… · 취소`

## 7. 15:45 마감 (평소와 같음)
```powershell
python -m stockking.ops.s02_runner sync
python -m stockking.ops.ledger_close --close
python -m stockking.ops.daily_reconcile
python -m stockking.ops.paper_report
```
▶ `paper_report`: C6 `접수 S02 0건 + T_TECH N건`, C2 에 날짜별 `계획 0·제출 0 [KOSPI200 종가/200일 평균(OFF)] 일치`(D-047) 근거가 나오고, `var\reconcile\paper_report_20261130.json` 에 저장됩니다.

## 결정 필요 (총괄·매매팀)
1. **C6 5번째 주문**: T4 제외로 최대 4건(T1·T2·T3·T5). T5 후보가 없으면 3건+T3' = 4건. 5번째를 (a) T3 를 다른 종목으로 1건 더, (b) 1일차 기술 점검 주문(connect_check 1주 매수 2건)을 C6 에 포함, (c) T1 을 다른 종목(rank 2)으로 1회 더 중 무엇으로 할지
2. **T5 가격대**: 운용 금액 auto(1천만 원)면 T5 는 1주 220만~250만 원 종목이 필요해 사실상 불가 → T5 를 이번에 생략할지

## 안전 장치 재확인 (②)
| 검사 | 위치 | 테스트 |
|---|---|---|
| PAPER 모드·모의 브로커·모의 도메인 아니면 실행 거부 (LIVE 승인 상태여도) | `tech_orders.assert_paper_only` + `OrderRouter`(T_TECH·X_TECH 는 모의만) | `test_tech_orders_refuse_*`, `test_x_tech_rejected_outside_paper_even_if_live_approved`, `test_dry_run_paper_only` |
| 관문 필수(우회 없음): D-005 90~110%, 1건 손실 2%, 비중 22.1%(예외 30.1%), 시장가는 국내 청산 매도+사유 코드만, 킬스위치 중 매수 차단 | `RiskGate` | `test_t1_price_limit_and_kill_switch_blocks`, `test_t4_pending_t5_through_gate`, `test_market_sell_rules.py` |
| dry-run 은 증권사 전송 0건 | `run_scenario(dry_run=True)` | `test_t1_qty_from_target_and_dry_run_sends_nothing` |

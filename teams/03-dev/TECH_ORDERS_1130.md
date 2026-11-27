# 11/30(월) D-027 점검용 주문 실행 절차 (D-047) — 대표 PC PowerShell

> 작성: 프로그램 생성팀 / 2026-11-27 (총괄 승인 반영판) · 근거: D-047(C6을 점검용 주문으로 11/30에 채움), D-027(모의 전용·관문 필수·T_TECH), D-033(T4 미국 제외), D-044·D-046(운용 금액 기준)
> 모든 주문은 **리스크 관문을 통과**해야 나가고, **PAPER 모드·모의 도메인·모의 브로커가 아니면 실행 자체가 거부**됩니다(실계좌 승인 상태여도 거부, 테스트 포함). 감사 로그에 `reason_code=T_TECH`로 남아 전략 성과에서 빠집니다.

## 총괄 결정 (11/27)
- **C6 5건** = T1 · T2 · T3 · T3′(`--rank 2`) · **T5**. T5 가 '해당 없음'이면 T5 대신 **T3″(`--rank 3`)**.
- `connect_check` 주문은 C6 에 넣지 않습니다(보고서도 '기타'로 따로 셉니다).
- T5 는 후보가 없으면 '해당 없음'으로 건너뜁니다. `in_band=False` 후보는 프로그램이 T5 에서 자동으로 뺍니다.
- T3 의 `--rank N` 은 **T1 후보의 N+1 순위 종목**을 씁니다(T3=T1 2순위, T3′=3순위, T3″=4순위). T1 을 `--rank 2` 로 냈다면 T3 와 종목이 겹치므로 T3 들을 `--rank 2`·`3`·`4` 로 한 칸씩 미룹니다.

## 0. 준비 (11/30 08:20, PowerShell 새 창)
```powershell
cd C:\stockking\awesome
git pull
$env:STOCKKING_OPERATING_CAPITAL = "auto"
$env:PYTHONUTF8 = "1"        # 예비(인코딩 수정은 이번 커밋에 포함)
$PY = ".\.venv\Scripts\python.exe"
$C  = "$env:USERPROFILE\stockking-data\s02_inputs\2026-11-27\tech_candidates.csv"   # 자료팀 11/27 실행 결과
Test-Path $C                 # True 여야 함
& $PY -m pytest -q           # 맨 끝 줄에 failed 가 없어야 함
```
`Activate.ps1` 은 쓰지 않습니다. 이후 모든 명령은 `& $PY -m …` 형태입니다.
평소 아침 순서(`ledger_close --open` → `startup_check` → `connect_check`)를 먼저 마칩니다(WINDOWS_QUICKSTART B-1의 `python` 을 `& $PY` 로 바꿔 실행).

## 1. 08:35 dry-run — 주문 없이 관문 판정만
```powershell
& $PY -m stockking.ops.tech_orders --run T1 --candidates $C --operator 대표 --dry-run
& $PY -m stockking.ops.tech_orders --run T3 --candidates $C --operator 대표 --dry-run
& $PY -m stockking.ops.tech_orders --run T3 --candidates $C --rank 2 --operator 대표 --dry-run
& $PY -m stockking.ops.tech_orders --run T5 --candidates $C --operator 대표 --dry-run
```
▶ 정상: `[dry-run] T1 buy 000000 N주 …원 → 관문 통과 (전송 안 함)`
- T1 이 `목표의 90~110%를 정수 수량으로 못 맞춤` → `--rank 2` 로 다시 시도(위 '총괄 결정'대로 T3 순위도 한 칸씩 미룸)
- T5 가 `쓸 종목이 없습니다` 또는 `관문 거부` → **T5 '해당 없음'**. 이때는 T3″ 를 dry-run 합니다:
  `& $PY -m stockking.ops.tech_orders --run T3 --candidates $C --rank 3 --operator 대표 --dry-run`
  (운용 금액 1천만 원이면 T5 는 1주 220만~250만 원 종목이 필요해 해당 없음일 가능성이 큽니다)

## 2. 08:45 T1 매수 (장전 동시호가 지정가)
```powershell
& $PY -m stockking.ops.tech_orders --run T1 --candidates $C --operator 대표
```
▶ `T1 submitted: 000000 N주 주문번호 …`

## 3. 09:05 이후 체결 확인
```powershell
& $PY -m stockking.ops.tech_orders --status
```
▶ `T1 000000 N주 주문번호 … → 체결 N/N주`
- **10:00까지 `체결 0/N주`면 프로그램으로 취소** 후 다시 냅니다(KIS 앱 조작 안 함):
  ```powershell
  & $PY -m stockking.ops.tech_orders --run T1-cancel --operator 대표 --dry-run
  & $PY -m stockking.ops.tech_orders --run T1-cancel --operator 대표
  & $PY -m stockking.ops.tech_orders --status        # T1 … · 취소 확인
  & $PY -m stockking.ops.tech_orders --run T1 --candidates $C --operator 대표   # 현재가로 새 지정가
  ```
  ▶ `T1 취소 000000 성공: …`. 일부만 체결됐다면 남은 수량만 취소되고, 체결분은 T2 로 청산합니다(재주문 안 함).

## 4. T1 체결 30분 뒤 T2 매도 (T1 보유분 전량, 시장가)
```powershell
& $PY -m stockking.ops.tech_orders --run T2 --candidates $C --operator 대표 --dry-run
& $PY -m stockking.ops.tech_orders --run T2 --candidates $C --operator 대표
& $PY -m stockking.ops.tech_orders --status
```
- KIS 가 시장가 매도를 거부하면(주문 유형 오류): `$env:STOCKKING_KR_SELL_MODE = "marketable_limit"` 입력 후 T2 를 다시 실행 → 기준가 × 0.98 지정가로 나갑니다. **이 결과(시장가 지원 여부)를 꼭 기록**(T0 청산 방식 결정, PAPER_3DAY_CHECK §6.1-1)

## 5. 10:00 T3 · T3′ · T5(또는 T3″)
```powershell
& $PY -m stockking.ops.tech_orders --run T3 --candidates $C --operator 대표
& $PY -m stockking.ops.tech_orders --run T3 --candidates $C --rank 2 --operator 대표
& $PY -m stockking.ops.tech_orders --run T5 --candidates $C --operator 대표            # §1 에서 T5 가 통과했을 때
& $PY -m stockking.ops.tech_orders --run T3 --candidates $C --rank 3 --operator 대표   # T5 '해당 없음'일 때만
& $PY -m stockking.ops.tech_orders --status
```
- T5 로 산 1주는 모의 종료(12/2)까지 보유해도 됩니다(청산은 S02·대표 판단).

## 6. 15:15 이후 T3 전부 취소
```powershell
& $PY -m stockking.ops.tech_orders --run T3-cancel --operator 대표 --dry-run
& $PY -m stockking.ops.tech_orders --run T3-cancel --operator 대표
& $PY -m stockking.ops.tech_orders --status
```
▶ T3 마다 `… · 취소` (T3′·T3″ 포함 한 번에)

## 7. 15:45 마감 (평소와 같음)
```powershell
& $PY -m stockking.ops.s02_runner sync
& $PY -m stockking.ops.ledger_close --close
& $PY -m stockking.ops.daily_reconcile
& $PY -m stockking.ops.paper_report
```
▶ `paper_report`:
- C6 `접수 S02 0건 + T_TECH 5건 (connect_check 등 기타 N건 제외)` → PASS. T1 을 취소 후 다시 냈다면 그 주문도 접수 1건으로 셉니다. 취소 명령은 세지 않습니다.
- C2 는 날짜별 `계획 0·제출 0 [KOSPI200 종가/200일 평균(OFF)] 일치` 형태입니다. 근거는 `s02_runner plan` 이 감사 로그에 남긴 `plan_count`·`regime`(지수 종가·200일 평균·ON/OFF)이고, 실행·재현 신호가 모두 0건이면 D-047 에 따라 '일치'입니다.
- 결과는 `var\reconcile\paper_report_20261130.json` 에 저장됩니다.

## 안전 장치 재확인
| 검사 | 위치 | 테스트 |
|---|---|---|
| PAPER 모드·모의 브로커·모의 도메인 아니면 실행 거부 (LIVE 승인 상태여도, 취소 명령 포함) | `tech_orders.assert_paper_only` + `OrderRouter`(T_TECH·X_TECH 는 모의만) | `test_tech_orders_refuse_*`, `test_x_tech_rejected_outside_paper_even_if_live_approved`, `test_dry_run_paper_only`, `test_t1_cancel_by_program_no_time_rule` |
| 관문 필수(우회 없음): D-005 90~110%, 1건 손실 2%, 비중 22.1%(예외 30.1%), 시장가는 국내 청산 매도+사유 코드만, 킬스위치 중 매수 차단 | `RiskGate` | `test_t1_price_limit_and_kill_switch_blocks`, `test_t4_pending_t5_through_gate`, `test_market_sell_rules.py` |
| dry-run 은 증권사 전송 0건 | `run_scenario(dry_run=True)` | `test_t1_qty_from_target_and_dry_run_sends_nothing`, `test_t1_cancel_by_program_no_time_rule` |
| T5 는 in_band=False 후보 제외, T3 순위 규칙 | `pick_symbol` | `test_t5_skips_out_of_band_candidates` |
| Windows(cp949)에서 한글 파일 읽기·쓰기 오류 방지: src·tests 의 텍스트 입출력 모두 `encoding="utf-8"` | 전체 | `test_encoding_lint.py`(AST 검사) |

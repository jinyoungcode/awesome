# 장부(Ledger) 양식 v0.2

> 작성: ⑤ 총무 회계팀 / v0.1 2026-11-23 → **v0.2 2026-11-24**
> v0.2 반영: D-006(한·미 주식, 내부 계산은 원화 환산), D-022(한투), D-027(점검용 주문 `reason_code=T_TECH`), D-029(수동 매매 별도 집계), D-030(전용 계좌)
> 원칙: **1원 단위 기록, 당일 마감, 증권사 잔고와 매일 대사.** 원화 금액은 정수(원)로, 달러 금액은 소수 2자리(센트)로, 비율은 소수로 쓴다(0.0123 = 1.23%).
> 모의(`mode=paper`)·실전(`mode=live`)·백테스트(`mode=backtest`)는 같은 양식을 쓰되 **절대 합산하지 않는다.**

## 파일 구성

| 파일 | 내용 | 작성 주체 | 주기 |
|---|---|---|---|
| `trades.csv` | 체결 1건당 1행 | 시스템 자동 → 회계팀 검증 | 체결 시 |
| `positions.csv` | 일말 보유 종목 | 시스템 자동 | 매 거래일 마감 후(국내 15:30 이후, 미국은 다음 날 아침) |
| `daily_pnl.csv` | 일별 손익·누적 수익률·대사 결과. **`book`별로 1행씩** | 시스템 자동 + 회계팀 대사 | 매 거래일 |
| `cashflows.csv` | 입금·출금·배당·이자 | 회계팀 | 발생 당일 |
| `fx_conversions.csv` | 원↔달러 환전 | 회계팀(시스템 자동 기록 가능) | 발생 당일 |
| `tax_reserve.csv` | 해외주식 양도세 충당금 | 회계팀 | 월말 |
| `opex.csv` | 운영비 | 회계팀 | 발생 시 |

실데이터는 `ledger/<mode>/<YYYY>/` 아래에 연도별로 쌓는다(예: `ledger/live/2026/trades.csv`). 이 폴더의 CSV는 **헤더만 있는 양식**이다.

> 🔒 보안: `account`에는 별칭만 쓴다(예: `KIS-AUTO-01`, `KIS-PAPER-01`). 계좌번호 전체, API 키, 비밀번호는 절대 기록하지 않는다.

---

## 공통 키 필드

| 필드 | 값 | 설명 |
|---|---|---|
| `mode` | `paper` / `live` / `backtest` | |
| `book` | `AUTO` / `MANUAL` | **D-029.** 시스템 주문번호와 매칭되는 체결은 `AUTO`. 매칭되지 않는 체결(대표 수동 매매)은 `MANUAL`. 리스크 한도와 전략 성과는 `AUTO`만 본다 |
| `market` | `KOSPI` / `KOSDAQ` / `KONEX` / `KR_ETF` / `NYSE` / `NASDAQ` / `AMEX` | 거래세율 결정에 쓴다 |
| `currency` | `KRW` / `USD` | 체결 통화 |
| `reason_code` | 전략 신호면 빈칸, 기술 점검 주문은 `T_TECH`(D-027) | `T_TECH`는 전략 성과에서 빼고 비용·대사에는 포함한다 |

---

## 1. trades.csv — 체결 내역

| 필드 | 설명 | 국내 예 | 미국 예 |
|---|---|---|---|
| trade_date / settle_date | 체결일 / 결제일. 국내 거래세율과 SEC fee 요율은 **결제일** 기준 | 2026-12-01 / 2026-12-03 | 2026-12-01 / 2026-12-02 |
| side, qty, price | `BUY`/`SELL`, 수량(소수점 거래 시 소수), 체결가(현지 통화) | SELL, 3, 20000 | SELL, 2, 25.10 |
| gross_amount | qty × price (현지 통화) | 60000 | 50.20 |
| commission | 위탁수수료(체결내역 그대로) | 10 | 0.13 |
| sell_tax | 국내 거래세+농특세(매도만) | 120 | 0 |
| sec_fee / finra_taf | 미국 매도 규제 수수료(USD) | 0 | 0.01 / 0.01 |
| net_amount | 매수: −(gross+비용) / 매도: gross−비용 (현지 통화) | 59870 | 50.05 |
| fx_rate | 원화 환산 환율. 국내는 1. 미국은 **체결일 기준환율**(서울외국환중개 매매기준율) | 1 | 1,4xx.xx |
| net_amount_krw | net_amount × fx_rate (원 미만 절사) | 59870 | |

> 숫자 예시는 형식 설명용이다. 실제 수수료 처리 방식은 COST_MODEL C-2에서 실측으로 확정한다.

## 2. positions.csv — 일말 보유
- `avg_cost`: **이동평균법**(현지 통화). 매수 수수료는 avg_cost에 넣지 않고 비용으로 따로 집계한다. 그래야 한투 HTS 평균단가와 직접 대사할 수 있다.
- 미국 종목은 `market_value`(USD) × `fx_rate`(당일 기준환율) = `market_value_krw`.
- `weight = market_value_krw / end_equity_krw` (리스크 한도 1종목 22%(D-005) 점검용).

## 3. daily_pnl.csv — 일별 손익 (`book`별 1행)

| 필드 | 계산 |
|---|---|
| end_equity_krw | 원화 예수금 + 달러 예수금×환율 + 보유 평가액(원화 환산) |
| cash_flow_krw | 당일 외부 입금(+)·출금(−). **환전은 계좌 안 이동이라 cash_flow가 아니다** |
| realized_pnl_krw | 매도분 (매도가 − 평균단가) × 수량, 원화 환산 (비용 차감 전) |
| fx_pnl_krw | 달러 자산의 환율 변동 손익(평가) |
| us_reg_fee_krw | SEC fee + FINRA TAF 원화 환산 |
| daily_return_twr | (end − cash_flow) / start − 1 |
| us_tax_reserve_krw | 그 날짜까지 계상된 해외주식 양도세 충당금(tax_reserve.csv 월말 값) |
| broker_equity_krw / recon_diff_krw / recon_status | 한투 조회 평가총액 / 차이 / `OK`·`DIFF`·`RESOLVED` |

**손익 항등식(매일 검증, `book` 합계 기준):**
`end − start − cash_flow = Δ미실현 + 실현 + 환율손익 − 수수료 − 거래세 − 미국규제수수료 + 배당(세후) − 환전비용`

- 1원이라도 안 맞으면 `recon_status=DIFF`로 두고 다음 거래일 장 시작 전까지 원인을 밝힌다.
- 3거래일이 넘으면 리스크관리실에 보고한다.
- D-024(3일 모의 통과 조건: 대사 100%)와 T0 통과 조건은 이 컬럼으로 판정한다.

**리스크 한도 산정(D-028·D-029):** 일중·주간 손실, 고점 대비 낙폭, 원금 대비 −20%는 **`book=AUTO` 행만으로 계산**한다. `MANUAL` 손익은 월간 결산에 따로 보고한다.

## 4. cashflows.csv
- `type`: `DEPOSIT` / `WITHDRAW` / `DIVIDEND` / `INTEREST` / `TAX_PAYMENT` / `FEE_ADJ`
- 외화 배당은 `currency=USD`, 원천징수(15%) 후 금액을 기록한다.
- `decision_ref`: 입금·증액·출금의 근거 결정번호(D-0xx). 근거 없는 입금은 받지 않는다.

## 5. fx_conversions.csv — 환전
- `direction`: `KRW_TO_USD` / `USD_TO_KRW`
- `applied_rate`: 실제 적용 환율. `reference_rate`: 같은 시각의 매매기준율
- `fx_cost_krw = |applied_rate − reference_rate| × usd_amount` → COST_MODEL C-4 실측 근거로 쓴다.

## 6. tax_reserve.csv — 해외주식 양도세 충당금
- `us_realized_net_pnl_ytd_krw`: 1월 1일부터의 미국 주식 실현손익 합계. 세법상 원화 환산은 **취득일·양도일 기준환율** 기준이며, 수수료 등 필요경비를 차감한다.
- `taxable_ytd_krw = max(0, 순익 − 2,500,000)`, `reserve_required_krw = taxable × 0.22`
- 충당금은 운용 가능 자금에서 뺀다(FUNDING_PLAN Q-7, 대표 결정 대기). 다음 해 5월 신고·납부 후 `cashflows.csv`에 `TAX_PAYMENT`로 기록하고 충당금을 해제한다.

## 7. opex.csv
- `category`: `SERVER` / `DATA` / `API` / `TOOL` / `DOMAIN` / `ETC`
- 외화 결제는 `currency`, `fx_rate`(카드 청구 환율)를 적고, `amount_krw`는 실제 청구 원화로 쓴다.

---

## 프로그램팀 감사 로그(JSONL)와의 연결
- 프로그램팀 감사 로그에 이미 있는 필드(종목·수량·체결가·수수료·세금·주문번호)는 그대로 `trades.csv`로 옮긴다.
- **추가로 필요한 필드:** `settle_date`, `book`, `market`, `currency`, `sec_fee`, `finra_taf`, `fx_rate`, `strategy_id`, `reason_code`
- 장 마감 후 한투 잔고 조회 결과(`broker_equity_krw`, 원화·달러 예수금)를 함께 남겨 달라.

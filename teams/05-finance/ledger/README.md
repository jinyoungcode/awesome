# 장부(Ledger) 양식 v0.1

> 작성: ⑤ 총무 회계팀 / 2026-11-23
> 원칙: **1원 단위 기록, 당일 마감, 증권사 잔고와 매일 대사.** 금액은 모두 원(KRW) 정수로 쓰고, 비율은 소수로 쓴다(0.0123 = 1.23%).
> 모의투자(`mode=paper`)와 실전(`mode=live`)은 같은 양식에 기록하되 **절대 합산하지 않는다.**

## 파일 구성

| 파일 | 내용 | 작성 주체 | 주기 |
|---|---|---|---|
| `trades.csv` | 체결 1건당 1행 | 프로그램팀 시스템이 자동 생성 → 회계팀 검증 | 체결 시 |
| `positions.csv` | 일말 보유 종목 | 시스템 자동 | 매 거래일 장 마감 후 |
| `daily_pnl.csv` | 일별 손익·누적 수익률·대사 결과 | 시스템 자동 + 회계팀 대사 | 매 거래일 |
| `cashflows.csv` | 입금·출금·증액 | 회계팀 수기 | 발생 당일 |
| `opex.csv` | 운영비(서버·데이터·API 등) | 회계팀 수기 | 발생 시 |

실데이터 파일은 `ledger/<mode>/<YYYY>/` 아래에 연도별로 쌓는다(예: `ledger/live/2027/trades.csv`). 이 폴더의 CSV는 **헤더만 있는 양식**이다.

> 🔒 보안: 장부에는 계좌번호 전체를 쓰지 않는다. `account`에는 별칭(예: `KIS-LIVE-01`)만 쓴다. API 키·비밀번호는 절대 기록하지 않는다.

---

## 1. trades.csv — 체결 내역

| 필드 | 설명 | 예 |
|---|---|---|
| trade_id | 고유 ID | `20270104-0001` |
| trade_date | 체결일 | `2027-01-04` |
| settle_date | 결제일(T+2). **증권거래세율은 이 날짜 기준** | `2027-01-06` |
| account | 계좌 별칭 | `KIS-LIVE-01` |
| mode | `paper` / `live` / `backtest` | `live` |
| market | `KOSPI` / `KOSDAQ` / `KONEX` | `KOSPI` |
| ticker / name | 종목코드 / 종목명 | `005930` / 삼성전자 |
| side | `BUY` / `SELL` | `SELL` |
| qty / price | 체결수량 / 체결단가 | `10` / `70000` |
| gross_amount | qty x price | `700000` |
| commission | 위탁수수료(증권사 체결내역 그대로) | `105` |
| sell_tax | 증권거래세+농특세(매도만, 매수는 0) | `1400` |
| other_fee | 기타(있으면) | `0` |
| net_amount | 매수: -(gross+commission+fee) / 매도: gross-commission-tax-fee | `698495` |
| order_id | 증권사 주문번호 | |
| strategy_id | 전략 ID(매매팀 명세서 기준) | `S01-momentum` |

## 2. positions.csv — 일말 보유

- `avg_cost`: **이동평균법**으로 계산한다(매수 시 평균단가 갱신, 매도 시 평균단가 유지). 국내 증권사 잔고 표기 방식과 맞추기 위함이다.
- 매수 수수료는 avg_cost에 **포함하지 않고** 비용으로 따로 집계한다. 그래야 증권사 HTS 평균단가와 직접 대사할 수 있다. 실현손익에서는 비용으로 차감한다.
- `unrealized_pnl = (close_price - avg_cost) x qty`
- `weight = market_value / end_equity`

## 3. daily_pnl.csv — 일별 손익

| 필드 | 계산식 |
|---|---|
| start_equity | 전일 end_equity |
| cash_flow | 당일 입금(+)·출금(-) 합계 (cashflows.csv) |
| end_equity | 예수금(D+2 기준) + 보유 평가금액 |
| realized_pnl | 당일 매도분 (매도가 - 평균단가) x 수량 (비용 차감 전) |
| unrealized_pnl | 보유 평가손익 합계 |
| commission / sell_tax | 당일 합계 |
| dividend_net | 원천징수 후 배당 입금액 |
| daily_return_twr | (end_equity - cash_flow) / start_equity - 1 ※ 입출금은 장 시작 전에 들어온 것으로 간주 |
| cum_return_twr | 전일 (1+cum) x (1+daily) - 1 |
| peak_equity / drawdown | 최고 평가액 / (end_equity / peak_equity - 1) ※ 입출금 보정 지수 기준으로 산출 |
| broker_equity | 증권사 조회 평가 총액 |
| recon_diff | end_equity - broker_equity |
| recon_status | `OK`(차이 0) / `DIFF`(원인 조사) / `RESOLVED` |

**손익 항등식(매일 검증):**
`end_equity - start_equity - cash_flow = Δ미실현손익 + realized_pnl - commission - sell_tax + dividend_net`

이 식이 1원이라도 안 맞으면 `recon_status=DIFF`로 두고, 다음 거래일 장 시작 전까지 원인을 밝힌다. 차이가 사흘 넘게 이어지면 리스크관리실에 보고한다.

## 4. cashflows.csv — 자금 이동

- `type`: `DEPOSIT` / `WITHDRAW` / `DIVIDEND` / `INTEREST` / `FEE_ADJ`
- `decision_ref`: 증액·출금 근거 결정번호(예: `D-00X`). **근거 없는 입금은 기록하지 않고, 먼저 승인을 받는다.**

## 5. opex.csv — 운영비

- `category`: `SERVER` / `DATA` / `API` / `TOOL` / `DOMAIN` / `ETC`
- 외화 결제는 `currency`, `fx_rate`(카드 청구 환율)를 같이 적고, `amount_krw`는 실제 청구 원화를 적는다.

# 데이터 사전 (v0.1 초안)

> 작성: ② 자료 수집팀 / 2026-11-23
> 원칙: 국내 금액은 **원(KRW) 정수**, 미국 금액은 **USD 소수(원값)** — 통화는 `currency` 열로 구분(D-006), 수량은 **주(株) 정수**, 날짜는 `YYYY-MM-DD`(KST 거래일), 시각은 `Asia/Seoul` ISO-8601. 비율은 소수(0.05 = 5%).
> 모든 테이블은 공통 메타 필드(`source`, `collected_at`, `batch_id`)를 가진다. 원천 원본(raw)은 덮어쓰지 않고 append-only로 보관한다.

## 0. 공통 메타 필드

| 필드 | 타입 | 설명 |
|---|---|---|
| `source` | str | 원천 코드: `KRX_OPENAPI`, `KRX_DATA`, `DART`, `KIS`, `KIWOOM`, `DATAGOKR`, `MANUAL` |
| `collected_at` | datetime | 수집 시각 (KST) |
| `batch_id` | str | 수집 배치 ID (재현·재처리 추적용) |

## 1. `instrument_master` — 종목 마스터 (갱신: 매일 장 시작 전 08:00)

| 필드 | 타입 | 단위 | 설명 | 출처 |
|---|---|---|---|---|
| `isin` | str(12) | — | 표준코드 (**영구 식별자, PK**) | KRX |
| `ticker` | str(6) | — | 단축코드. 재사용될 수 있으므로 PK로 쓰지 않음 | KRX |
| `name_kr` | str | — | 종목명 (변경 이력은 `instrument_history`) | KRX |
| `market` | enum | — | `KOSPI` / `KOSDAQ` / `KONEX` / `NYSE` / `NASDAQ` / `AMEX` | KRX / KIS |
| `currency` | enum | — | `KRW` / `USD` | 산출 |
| `security_type` | enum | — | `COMMON` / `PREFERRED` / `ETF` / `ETN` / `REIT` / `SPAC` / `OTHER` | KRX |
| `corp_code` | str(8) | — | DART 고유번호 (재무·공시 조인 키) | DART |
| `sector_krx` | str | — | KRX 업종 분류 | KRX |
| `listed_date` | date | — | 상장일 | KRX |
| `delisted_date` | date/null | — | 상장폐지일 (상장 중이면 null) | KRX |
| `delisting_reason` | str/null | — | 상폐 사유 | KRX/KIND |
| `face_value` | int | 원 | 액면가 | KRX |

## 2. `daily_price_raw` — 일봉 원주가 (갱신: 매일 18:00, 정정 반영 위해 T+1 08:00 재수집)

PK: (`isin`, `date`)

| 필드 | 타입 | 단위 | 설명 | 출처 |
|---|---|---|---|---|
| `date` | date | — | 거래일 | KRX |
| `open` / `high` / `low` / `close` | int | 원 | 시가/고가/저가/종가 (**비수정**) | KRX |
| `volume` | int | 주 | 거래량 | KRX |
| `value` | int | 원 | 거래대금 | KRX |
| `market_cap` | int | 원 | 시가총액 | KRX |
| `shares_outstanding` | int | 주 | 상장주식수 | KRX |
| `price_limit_up` / `price_limit_down` | int | 원 | 상·하한가 (가능 시) | KRX/KIS |
| `is_halted` | bool | — | 거래정지 여부 (정지일은 OHLC null, volume 0) | KRX |

## 3. `corporate_action` — 기업 이벤트 (갱신: 매일 20:00, DART 공시 기반)

PK: (`isin`, `ex_date`, `action_type`)

| 필드 | 타입 | 단위 | 설명 | 출처 |
|---|---|---|---|---|
| `action_type` | enum | — | `SPLIT`(액면분할) / `REVERSE_SPLIT`(병합) / `RIGHTS`(유상증자) / `BONUS`(무상증자) / `CASH_DIV` / `STOCK_DIV` / `MERGER` / `SPINOFF` / `CAPITAL_REDUCTION`(감자) | DART |
| `announce_date` | date | — | 공시 접수일 | DART |
| `ex_date` | date | — | 권리락/배당락/기준가 조정일 | DART/KRX |
| `ratio` | decimal | — | 조정 비율 (예: 1주→5주 분할 = 5.0) | 산출 |
| `cash_amount` | int | 원/주 | 주당 현금배당 | DART |
| `issue_price` | int | 원 | 유상증자 발행가 | DART |
| `rcept_no` | str(14) | — | DART 접수번호 (근거 추적) | DART |

## 4. `adj_factor` — 수정계수 (갱신: `corporate_action` 변경 시 재계산, 버전 관리)

PK: (`isin`, `date`, `version`)

| 필드 | 타입 | 설명 |
|---|---|---|
| `price_factor` | decimal(18,10) | 수정주가 = 원주가 × `price_factor` (최신일 = 1.0, 과거로 누적) |
| `volume_factor` | decimal(18,10) | 수정거래량 = 원거래량 ÷ `price_factor` 와 동일 원리 |
| `include_dividend` | bool | 배당 재투자 반영(총수익) 여부. 기본 계수는 `false`, 총수익 계수는 별도 행 |
| `version` | str | 산출 로직 버전 (예: `af-v1`) |

> 수정주가는 **저장하지 않고 조회 시 계산**한다 (원주가 × 계수). 계수만 버전 관리하면 재현성이 보장된다.

## 5. `index_membership` — 지수 구성종목 이력 (갱신: 정기변경 + 수시, 주 1회 점검)

PK: (`index_code`, `isin`, `start_date`)

| 필드 | 타입 | 설명 |
|---|---|---|
| `index_code` | enum | `KOSPI200` / `KOSDAQ150` |
| `start_date` | date | 편입일 |
| `end_date` | date/null | 편출일 (편입 중이면 null) |

## 6. `trading_status` — 시장조치 이력 (갱신: 매일 18:00)

| 필드 | 타입 | 설명 |
|---|---|---|
| `status_type` | enum | `ADMIN`(관리종목) / `WARNING` / `RISK` / `HALT` / `LIQUIDATION`(정리매매) / `UNFAITHFUL_DISCLOSURE` |
| `start_date` / `end_date` | date | 적용 기간 |

## 7. `minute_price` — 분봉 (갱신: 장중 실시간 + 15:40 당일분 확정 적재) — *매매팀 요청 시 활성화*

PK: (`isin`, `ts`)

| 필드 | 타입 | 단위 | 설명 |
|---|---|---|---|
| `ts` | datetime | — | 봉 **시작** 시각 (1분봉) |
| `open`/`high`/`low`/`close` | int | 원 | 비수정 |
| `volume` | int | 주 | 해당 1분 거래량 |

## 8. `financial_statement` — 재무제표 (갱신: 매일 20:00, 정기보고서 시즌 집중)

PK: (`corp_code`, `fiscal_year`, `fiscal_quarter`, `fs_div`, `account_id`, `rcept_no`)

| 필드 | 타입 | 단위 | 설명 | 출처 |
|---|---|---|---|---|
| `fiscal_year` / `fiscal_quarter` | int | — | 회계연도 / 분기(1~4, 4=연간) | DART |
| `fs_div` | enum | — | `CFS`(연결) / `OFS`(별도) | DART |
| `account_id` | str | — | XBRL 계정 ID (예: `ifrs-full_Revenue`) | DART |
| `account_name` | str | — | 계정명 | DART |
| `amount` | int | 원 | 금액 (DART 원 단위 그대로) | DART |
| `rcept_no` | str(14) | — | 접수번호 | DART |
| `available_date` | date | — | **사용 가능일 = 공시 접수일 다음 거래일** (look-ahead 방지 핵심 필드) | 산출 |
| `is_restated` | bool | — | 정정공시에 의한 수정값 여부 | 산출 |

## 9. `investor_flow` — 투자자별 순매수 (갱신: 매일 18:30) — *P1 선택*

| 필드 | 타입 | 단위 | 설명 |
|---|---|---|---|
| `investor_type` | enum | — | `FOREIGN` / `INSTITUTION` / `INDIVIDUAL` / `OTHER_CORP` |
| `net_buy_volume` | int | 주 | 순매수량 |
| `net_buy_value` | int | 원 | 순매수대금 |

## 10. `trading_calendar` — 거래일 캘린더 (갱신: 연 1회 + 임시휴장 공지 시)

| 필드 | 타입 | 설명 |
|---|---|---|
| `date` | date | 일자 |
| `is_open` | bool | 개장 여부 |
| `open_time` / `close_time` | time | 개장·폐장 시각 (수능일 등 지연개장 반영) |
| `note` | str | 휴장 사유 |

## 11. `fx_rate` — 환율 (갱신: 매 영업일 09:30, v0.2 추가)

PK: (`date`, `pair`)

| 필드 | 타입 | 단위 | 설명 | 출처 |
|---|---|---|---|---|
| `date` | date | — | 한국 고시일 | ECOS |
| `pair` | enum | — | `USDKRW` | — |
| `rate` | decimal(10,2) | 원/달러 | 매매기준율 | ECOS |
| `is_filled` | bool | — | 휴일 forward fill 여부 | 산출 |

> 일봉 내보내기 형식은 `DAILY_INTERFACE.md`, 분봉 적재안은 `MINUTE_SOURCES.md` §3.

## 변경 이력
| 버전 | 날짜 | 내용 |
|---|---|---|
| v0.1 | 2026-11-23 | 최초 작성 |
| v0.2 | 2026-11-24 | 미국 주식(통화·시장), `fx_rate` 추가 |

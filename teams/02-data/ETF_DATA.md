# S03 ETF 듀얼모멘텀 백테스트 데이터 준비 (v0.1)

> 작성: ② 자료 수집팀 / 2026-11-27 — D-048 ③ (하락 국면 대응으로 S03 백테스트·모의 준비 병행)
> 기준 명세: `teams/04-trading/strategies/S03_ETF_DUAL_MOMENTUM.md` v0.1. 총괄 요청의 "§11"은 명세에 없어서, 데이터가 걸린 §2(투자 대상)·§4(신호, TR 가격)·§7(V3 금)·§8(벤치마크)에 맞췄습니다.

## 1. 데이터 요건 ↔ 명세 대조

| 명세 | 필요 데이터 | 자료팀 준비 | 원천 | 상태 |
|---|---|---|---|---|
| §2 위험자산 A | 069500 KODEX 200 일봉 (2002-10 상장) | `etf-bars` | KIS 기간별 시세(원가격) | 도구 준비 |
| §2 위험자산 B | 229200 KODEX 코스닥150 일봉 (2015-10 상장) | `etf-bars` | KIS | 도구 준비 |
| §2 B 상장 전 | KOSDAQ150 지수 − 연 0.25% proxy | `etf-bars --kosdaq150-index` (상장 첫날 종가에 맞춰 거꾸로 이어 붙임, `proxy=True`) | KRX 개별지수 시세 추이(코스닥 150) CSV | 도구 준비 |
| §2 안전자산 | 114260 KODEX 국고채3년 일봉 (2009-07 상장) | `etf-bars` | KIS | 도구 준비 |
| §4 TR 가격 | **분배금 이력**(지급기준일·주당 분배금) → 분배락일(기준일 직전 거래일)에 재투자 | `etf-bars --dist` → `tr_close` | KRX 정보데이터시스템 ETF 분배금 현황 또는 SEIBro(한국예탁결제원) ETF 분배금 내역 CSV ⚠ 메뉴 확인 필요 | **대표 다운로드 필요** |
| §4 RET(252)·SMA(250) | 신호 시작 전 최소 약 500거래일 | 기본 시작일 2009-01-01 | — | 충족 |
| §7 V3 | 132030 KODEX 골드선물(H) 일봉 (2010-10 상장) | `etf-bars` 기본 포함 | KIS | 도구 준비 |
| §8 벤치마크 ① | KOSPI200 TR 지수 | KRX CSV 그대로 사용 | KRX 개별지수 시세 추이(코스피 200 TR) | 대표 다운로드 |
| §8 벤치마크 ② | 069500 + 114260 | 위와 같음 | — | — |
| 백테스트 기간 | ROADMAP P2: 최소 5년 | 2009~ (229200은 proxy 포함 시 2010-01~) | — | 충족 |

## 2. 실행 (대표 PC, PowerShell)
```powershell
$E = "$env:USERPROFILE\stockking-data\s03"
& $PY $COL etf-bars --out $E --start 2009-01-01 --interval 0.5 `
  --dist "$env:USERPROFILE\stockking-data\krx_etf_dist.csv" `
  --kosdaq150-index "$env:USERPROFILE\stockking-data\krx_kosdaq150_long.csv"
```
- 4종목 × 약 18년 ÷ 140일 구간 ≈ 190회 호출 → `--interval 0.5`로 약 2~3분
- 출력: `etf_bars.csv` (`date,symbol,open,high,low,close,volume,value,tr_factor,tr_close,proxy`), `etf_manifest.json`
- `--dist` 없이 돌리면 TR = 원가격으로 만들고 경고합니다(신호 왜곡 → 백테스트 전 반드시 보완)
- proxy용 KOSDAQ150 지수 CSV는 **2010-01-04 ~ 2015-10-31** 이상을 포함해야 합니다(KRX 개별지수 시세 추이, 코스닥 150)

## 3. 점검 기준 (QUALITY.md 준용)
| 점검 | 기준 |
|---|---|
| 상장일 이후 결측 | 0일 (KOSPI200 지수 거래일 기준) |
| 분배락일 수익률 | 원가격 하락 ≈ 분배금/전일 종가, TR 수익률은 정상 범위 |
| 분할·병합 | KIS 수정주가(FID_ORG_ADJ_PRC=0)와 원가격 비율이 1이 아닌 날 → 수정 이벤트로 기록 (2차) |
| proxy 연결부 | 상장 첫날 전후 TR 수익률이 지수 수익률과 ±0.5%p 이내 |
| 벤치마크 | KOSPI200 TR 연수익률 − KODEX 200 TR 연수익률 ≈ 보수(연 0.15%) 수준 |

## 4. 한계
- KIS 일봉 과거 깊이(2009년까지)는 ⚠ 실측 필요. 부족하면 KRX ETF 개별종목 시세 추이 CSV로 대체
- 분배금은 수동 CSV 의존(자동 API 미확인). 연 1~4회라 갱신 부담은 작음
- 현금 이자 0 가정(명세 §2)

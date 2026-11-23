# ④ 매도매수팀 작업 현황

> 최종 갱신: 2026-11-23 (2회차 — 대표 결정 Q-T01~Q-T03 반영)

## 1차 과업 진행 상태

| # | 과업 | 상태 | 산출물 |
|---|---|---|---|
| 1 | 투자 스타일 정의 | 초안 완료, **대표 결정 대기** | [INVESTMENT_STYLE.md](INVESTMENT_STYLE.md), [DECISIONS_REQUESTED.md](DECISIONS_REQUESTED.md) |
| 2 | 첫 전략 3개 명세서 | 명세 완료 (`SPEC`) → 프로그램팀 구현 대기 | [COMMON_SPEC](strategies/COMMON_SPEC.md), [S01 추세 돌파](strategies/S01_TF_BREAKOUT.md), [S02 눌림목 반등](strategies/S02_MR_PULLBACK.md), [S03 ETF 듀얼모멘텀](strategies/S03_ETF_DUAL_MOMENTUM.md) |
| 3 | 전략 평가 기준 | 완료 (합격선은 Q-T02 결정에 연동) | [EVALUATION.md](EVALUATION.md) §1·§3 |
| 4 | 과최적화 방지 규칙 | 완료 | [EVALUATION.md](EVALUATION.md) §2·§4 |
| 5 | 리스크관리실 한도 협의 | 협의안 제출, **회신 대기** | [RISK_ALIGNMENT.md](RISK_ALIGNMENT.md) |

## 대표 결정 반영 (2026-11-23)
- **Q-T01 데이트레이딩 / Q-T02 공격형·최종 손절 −20% / Q-T03 국내+해외** 결정 반영
- 검토 결과: 초단타는 불가, 분봉 데이트레이딩은 조건부 가능 → [DAYTRADING_REVIEW.md](DAYTRADING_REVIEW.md)
- 스타일 v0.2([INVESTMENT_STYLE.md](INVESTMENT_STYLE.md)), 리스크 수정안([RISK_ALIGNMENT.md](RISK_ALIGNMENT.md) §4), 데이트레이딩 평가 기준([EVALUATION.md](EVALUATION.md) §3.5)
- 데이트레이딩 전략 후보 D01(국내 ORB), D02(국내 VWAP 눌림), D03(미국 ORB) — 상세 명세는 Q-T04·Q-T10~Q-T12 결정 후 작성
- 새 결정 요청: Q-T10(20% 기준), Q-T11(분봉 데이터 예산), Q-T12(미국 시작 시점), Q-T04·Q-T06 재질의

## 추가 반영 (2026-11-23)
- **D-002 승인**(모의투자 통과 전 실계좌 투입 금지)에 따라 모의투자 통과 기준 추천안을 넣었습니다: 기간 8주(최대 12주), 신호 일치 100%, 실행 괴리 ±2%p(8주), 슬리피지는 가정값의 2배 이내, 운영 무사고. 상세는 `EVALUATION.md` §3.3과 각 전략 명세서 마지막 절에 있습니다.

## 전략 레지스트리

| ID | 이름 | 유형 | 슬리브(제안) | 튜닝 파라미터 | 상태 |
|---|---|---|---|---|---|
| S01 | 추세 돌파 스윙 (TF-BRK) | 추세추종, 손익비형 | 30% | 3개 / 36조합 | SPEC |
| S02 | 상승추세 눌림목 반등 (MR-PB) | 단기 평균회귀, 승률형 | 30% | 3개 / 27조합 | SPEC |
| S03 | 국내 ETF 듀얼모멘텀 (ETF-DM) | 자산배분, Core | 40% | 1개 / 3조합 | SPEC |

## 다른 팀에 보낸 요청 (2026-11-23)

| 받는 팀 | 요청 | 막혀 있는 작업 |
|---|---|---|
| ① 총괄/리스크실 | 한도·킬스위치 확정, 대표 결정사항 D-번호 기록, 3자 검토 절차 | 사이징 확정, P2 Gate |
| ② 자료팀 | 일봉(원·수정), 시점별 구성종목, 상태 플래그, ETF·지수, 호가단위 검증 | P2 백테스트 전체 |
| ③ 프로그램팀 | 백테스트 엔진 요건(COMMON_SPEC 구현), 리포트 자동 생성, 리스크 관문 | P2 백테스트 전체 |
| ⑤ 회계팀 | 수수료·거래세·ETF 과세·배당세 확정값 | 비용 모델 확정 |

## 폴더 구조
```
teams/04-trading/
├── README.md                 ← 이 파일
├── INVESTMENT_STYLE.md       투자 스타일 정의안
├── DECISIONS_REQUESTED.md    대표 결정 요청 (Q-T01~Q-T09)
├── EVALUATION.md             평가 지표·합격 기준·과최적화 방지·생애주기
├── RISK_ALIGNMENT.md         리스크 한도 협의안
├── strategies/
│   ├── COMMON_SPEC.md        공통 규격 (데이터·유니버스·지표·주문·비용·사이징·출력)
│   ├── S01_TF_BREAKOUT.md
│   ├── S02_MR_PULLBACK.md
│   ├── S03_ETF_DUAL_MOMENTUM.md
│   └── _TEMPLATE.md
├── research/                 (P2부터) 백테스트 리포트, trial_log.csv
└── journal/                  (P3부터) 운용 일지
```

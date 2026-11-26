# 프로그램 생성팀 진행 현황

## 2026-11-23 — 1차 과업 1회차 결과

| # | 과업 | 결과물 | 상태 |
|---|---|---|---|
| 1 | 증권사 API 기술 비교 | `BROKER_API.md` — **KIS 주력 / 키움 REST 대체 권고** | 완료 (총괄 승인 대기) |
| 2 | 시스템 아키텍처 초안 | `ARCHITECTURE.md` | 완료 v0.1 |
| 3 | 리스크 관문 설계 | `ARCHITECTURE.md` §3 + `src/stockking/risk/` 구현·테스트 | 완료 (한도 수치는 임시값) |
| 4 | 저장소 구조·코딩 규칙·테스트 정책 | `CONVENTIONS.md`, `.gitignore`, `.env.example`, `pyproject.toml` | 완료 |
| 5 | 백테스트 엔진 MVP 범위 | `BACKTEST_MVP.md` + `src/stockking/backtest/` 구현 | 완료 (MVP) |

**테스트:** `python -m pytest` → 38개 통과 (리스크 관문 16, 백테스트 9, 라우터·감사로그 4, KIS 모의 어댑터 3, 모드·비밀값 6)

### 안전 장치 현황
- LIVE 모드: `config.LIVE_TRADING_APPROVED = False` 상수 차단, `resolve_mode("live")` 예외
- 라우터: `is_paper=True` 아닌 브로커 연결 거부
- KIS 어댑터: 모의 도메인 하드코딩, 실전 도메인 거부
- 비밀값: `.env` gitignore, `Secret` 래퍼, 감사 로그 마스킹, 커밋 검사 테스트

### 다른 팀에 보낸 요청
- 총괄/리스크실: 증권사 선정 승인, 한도 확정값, 킬스위치 중 청산 허용 여부, 약관 점검
- ~~D-002 승인~~ → 2026-11-23 승인 확인(총괄 공지). 요구사항 "실계좌 경로 기본 비활성 + 별도 설정과 대표 승인 기록 있어야 전환"은 현 설계로 충족: `LIVE_TRADING_APPROVED=False` 상수(환경변수로 못 켬), 해제는 승인 결정번호 기록 + 2인 리뷰 후 코드 변경으로만
- 자료수집팀: 일봉 규격 합의, 샘플 데이터(상폐 포함), 파이프라인 요구사항
- 매도매수팀: 전략 명세서 필수 항목, 평가 지표 정의 확인
- 총무회계팀: 수수료·세율 확정값(법령 근거), 장부 연동 필드

### 다음 할 일 (P1)
1. (증권사 승인 후) KIS 모의투자 앱키 발급 → 잔고·시세·체결 조회 구현, 모의 서버 통합 테스트
2. 자료수집팀 규격 확정 → 수집기·저장소 구현
3. 백테스트 MVP 제외 항목 중 '높음' 4건(상폐, 가격제한폭/호가단위, 참여율 상한, 워크포워드)
4. 운영: 스케줄러, 텔레그램 알림, 잔고 대사

## 2026-11-24 — D-019 소수점 거래 조사 (총괄 요청, 기한 12/4)

- 결과물: `FRACTIONAL_SHARES.md` — KIS Open API 소수점 주문 API는 공개 자료에서 확인 안 됨, 국내 소수점은 가격 지정 불가·회차 일괄 체결이라 데이트레이딩에 부적합
- 권고: P3 모의투자는 정수 주식으로 시작(10만 원 단계는 가격대 유니버스 + 1주 예외안), 수량 `Decimal` 전환 설계안은 PR #2 머지 후 별도 PR
- 대표 확인 필요: KIS 포털(로그인 필요)에서 소수점 주문 API 유무 확인

## 2026-11-24 — D-020·D-021 반영 계획

- D-021(대표 승인): 10만 원 단계는 정수 주문 + 목표 90~110%를 정수로 못 맞추는 종목은 1주 예외 매수 허용, 소수점은 API 확인 시 적용 → `FRACTIONAL_SHARES.md` §4 A+C 권고안 채택
- 구현 대기 조건: ① PR #2 병합 ② 리스크관리실의 1주 예외 안전 한도(최대 금액, 22% 비중 초과 허용 범위, 1건 최대 손실) 수령
- 구현 예정 위치: `sizing.py`(1주 예외 판정, 사유 코드 반환), `risk/gate.py`(예외 주문 전용 한도 검사, 기본 거부), 테스트 동반
- D-020: 모의 검증은 스윙(S02) 먼저 → 일봉 엔진(현행)으로 착수 가능, 분봉·실시간 엔진은 후순위
- 증권사 최종 선택(한투 vs 토스) 미결. 참고: 토스 Open API는 모의투자 환경이 없다고 알려져 있어, 선택 시 D-002(모의 통과 전 실계좌 금지)와의 충돌 여부를 먼저 검토해야 함

## 2026-11-24 — D-022(한투 통일)·D-023(모의 3거래일) 준비

- 결과물: `PAPER_RUNBOOK.md`(3거래일 절차·점검표), `SECRETS.md`(키 주입 방안), `src/stockking/ops/`(점검 스크립트 6종), `src/stockking/krx.py`(호가단위)
- 스크립트: `connect_check`(토큰→시세→잔고→1주 주문→취소→내역), `killswitch_drill`, `restart_drill`, `startup_check`, `daily_reconcile`
- KIS 어댑터 확장: 현재가·잔고·취소·당일 주문체결 조회 (TR ID 공식 문서 재확인 필요)
- 키 주입: 저장소 밖 비밀값 파일(위치·권한 강제 검사) 또는 실행 시 직접 입력. 클라우드 세션 보안 입력은 KIS 구조상·네트워크상 불가
- 테스트 61개 통과 (신규 23)
- 미구현: 전략 자동 실행기·신호 일치 리포트(S02 명세서 + PR #2 병합 필요), 장부 자동 대사(회계팀 양식 필요)

## 2026-11-24 — D-024~D-030 반영

| 결정 | 반영 | 상태 |
|---|---|---|
| D-024 점검표 확정 | `ops/paper_report.py` C1~C6 집계 (C1·C2는 대기 표시) | 완료 |
| D-025 1주 예외 한도 | 값은 `risk/stages.py` 상수로 고정, 관문 검사는 D-021과 함께 | PR #2 병합 후 |
| D-026 킬스위치 중 청산 매도 허용 | `risk/gate.py`, 킬스위치 훈련 1b·1c 항목 추가 | 완료 |
| D-027 T_TECH 점검용 주문 | `ops/tech_orders.py` T1~T3 + 발동 판정, 모의 전용 차단·테스트 | T1~T3 완료, T4·T5는 PR #2 병합 후 |
| D-028 실전 손실 한도 | `risk/stages.py` 단계별 한도(`STOCKKING_CAPITAL_STAGE`), 원금 −20% 최종 손절 관문, 최고점 −15% 사이징 배수 | 완료. 1건 손실 2%는 손절가 필드가 필요해 PR #2 병합 후. `RiskLimits` 기본값 교체도 PR #2 충돌 회피 위해 병합 후 |
| D-029 수동 매매 분리 | 일일 대사에서 시스템이 모르는 주문을 수동 매매로 분리 기록 | 감지·분리 완료, 한도 계산 제외는 실행기 구현 시 |
| D-030 전용 위탁계좌 | 코드 변경 없음(계좌번호는 비밀값 파일로 주입) | 해당 없음 |

테스트 79개 통과 (신규 18).

## 2026-11-24 — D-031·D-032 시작 자금 15만 원

- `risk/stages.py`: `STARTING_CAPITAL=150_000`, `operating_capital()`(환경변수로 변경 가능), 모의 계좌 평가금과 운용 기준 자금이 ±20% 넘게 다르면 경고
- 운영 스크립트(`connect_check`, `tech_orders`)가 경고 출력, `PAPER_RUNBOOK.md` 시작금 15만 원으로 갱신
- 미결 위험: KIS 모의 계좌 기본 예수금이 15만 원보다 훨씬 크면 한도가 계좌 전체 기준으로 계산됨 → S02 실행기에서 운용 기준 자금(15만 원) 가상 장부로 계산하도록 설계 예정

## 2026-11-24 — S02 실행기 골격 + C1 장부 자동 대사

- `indicators.py`: SMA, Wilder RSI(워밍업 50봉), RET — COMMON_SPEC §5 정의
- `strategy/s02.py`: E1~E7 진입(순위: RSI↑·ADV20↓·코드), X_STOP→X_TP→X_TIME 청산(X_ADMIN/X_KILL 최우선), 데이터 QC 실패 시 청산만, 슬롯 = min(6, 5 − 보유), 주문계획 CSV(§12.1). 수량은 sizer 주입식 — PR #2 `sizing.py` + D-021/D-025 병합 후 연결
- `ledger.py` + `ops/ledger_close.py`: 회계팀 장부 v0.2 필드 그대로 trades/positions/daily_pnl 작성(국내), book=AUTO/MANUAL(감사 로그 주문번호 매칭, D-029), T_TECH reason_code(D-027), 결제일 T+2, 장 시작 스냅샷 + 당일 체결 = 마감 잔고 1원·1주 대사 → C1 자동 판정
- 남은 일(PR #2 병합 후): S02 수량·주문 제출·15:15 LDAY 취소 실행기, 신호 일치(C2) 비교, 미국 장부 필드(환율·SEC fee·FINRA TAF), CostModel 미국 수수료 필드
- 테스트 91개 통과

## 2026-11-24 — S02 v1.0 대조, D-033~D-035

- S02 v1.0(bcad9b6) 대조: 신호 규칙(§1~§8)은 v0.1과 같아 `s02.py` 로직 변경 없음(버전 표기만 갱신). §10 15만 원 사이징은 `strategy/d005_sizer.py` + v1.0 예제 3건 테스트(PR #2 `sizing.py` 필요 → 병합 전 자동 건너뜀, PR #2와 합친 상태에서 통과 확인)
- D-035: `US_DAYTRADING_COST_VALIDATION.md` 초안 — 비용 실측 항목·절차, 별도 검증 구간, 비용 시나리오 S0~S4·손익분기, 판정 제안, 재현 절차
- `.gitattributes`(INBOX.md merge=union) — 총괄 d8dee39와 동일 내용

## 2026-11-24 — PR #2 병합(4d7c493) 후 미뤄 둔 작업 + D-036

| 항목 | 반영 |
|---|---|
| RiskLimits 기본값 T0 교체 | 일 −5%, 주 −8%, 월 없음, 1건 손실 2%, 최종 손절 −20% (D-028) |
| 1건 손실 2% (D-028) | `OrderRequest.stop_price` 추가, 관문 TRADE_LOSS·BAD_STOP·NO_STOP 검사 (`require_stop_for_buy` 로 손절가 필수화 가능) |
| D-021/D-025/D-036 관문 검사 | `order_target`·`sizing_exception` 필드, 관문 `_check_d005`: 90~110% 밴드, 1주 예외 종류 재계산(UNDER/OVER), 1주 가격 ≤ 30%, '목표 초과' 동시 1개('목표 미달' 제외), 예외는 손절가 필수 |
| D-025 vs 비중 22% 충돌 | 22% 한도가 '목표 초과' 예외(가격 22~30%)를 전부 막던 문제 발견 → 1주 예외 신규 진입은 비중 한도 30% 적용, 추가 매수는 22% 유지 |
| D-036 사이저 | `d005_sizer`: ONE_SHARE_UNDER / ONE_SHARE_OVER, 동시 1개 한도는 OVER 만 |
| 미국 비용 필드 | `CostModel.sec_fee_rate`, `finra_taf_per_share`, `finra_taf_max`, `us_from_usd()`, 백테스트·로컬 모의 매도에 `sell_charges()` 적용 |
| 테스트 | 147개 통과 |

다음: S02 주문 실행기(주문계획 → 관문 → KIS 제출, 15:15 LDAY 잔량 취소), C2 신호 일치 비교, 미국 장부 필드(환율·SEC/TAF), 결제일 기준 연도별 거래세표(회계팀 요청).

## 2026-11-24 — S02 주문 실행기 (C2·C6)

- `ops/s02_runner.py`: plan(18:30, D-005 사이저·국내만 D-033) → submit(08:40, 청산 먼저·관문·손절가 필수·청산 MOO 허용) → cancel(15:15 LDAY 잔량) → sync(체결 → 15만 원 가상 장부, 중복 반영 방지) → verify(C2: 같은 입력 재계산 = 제출 기록)
- 가상 장부(var/s02/state.json, 원자적 저장): 모의 계좌 금액과 무관하게 15만 원 기준으로 사이징·한도 계산 (D-032 위험 해소)
- 모의 전용: PAPER 모드·모의 브로커가 아니면 submit/cancel 거부
- paper_report: C2 자동 판정 연결 → C1~C6 모두 자동 판정 가능
- D-025 해석(리스크실 확정) docs/DECISIONS.md 기록, main(4f10468) 병합 반영
- 테스트 156개 통과
- 외부 의존: 자료팀 입력 파일 4종(PAPER_RUNBOOK §3-1)

## 2026-11-25 — 시장가 규칙 (리스크관리실 결정)

- 관문: 시장가는 국내 청산 매도 + 사유 코드(X_STOP/X_TP/X_TIME/X_ADMIN/X_KILL, D-027 T2용 X_TECH)만, 수량 ≤ 보유. 국내 매수·미국은 지정가만
- `OrderRequest.exit_reason` 추가, S02 실행기·T2가 사유 코드 전달
- KIS 모의 시장가 미지원 대비: `STOCKKING_KR_SELL_MODE=marketable_limit` → 기준가×0.98 호가 올림 지정가 (`krx.ceil_to_tick`, `marketable_sell_limit`)
- 테스트 169개 통과

## 2026-11-25 — Codex 검토 버그 수정 + 윈도우 실행 환경

- Codex #1·#2 수정(1e98c90): 제출 시 미체결 매수 현금·슬롯 예약, 재시작 후 재제출 방지, 부분체결 누적 반영. 경계 사례(110% 주문 비중 22% 초과) 리스크실 판단 요청
- 윈도우: `scripts/setup_windows.ps1`(Python·git 확인, .venv, 설치, 테스트, `%APPDATA%\stockking\secrets.env` 자리·본인만 읽기 권한, 오프라인 훈련, 한글 요약), `scripts/paper_day.ps1 -Step check|open|drill|cancel|close|plan`
- 비밀값: 윈도우 기본 경로 `%APPDATA%\stockking\secrets.env`, OneDrive 폴더 거부, 메모장 BOM 허용
- `data/` gitignore (유료·약관 제한 데이터 커밋 방지)
- ⚠ .ps1 은 개발 환경에 PowerShell 이 없어 미실행 — 대표 PC 첫 실행으로 검증 필요

## 2026-11-25 — D-042 대응 (모의 11/26 시작)

- 리스크실 비중 22.1% 허용오차, 매매팀 I1(실현 순손익에 매수 수수료) 반영 — 335a7d4, 177 passed
- `WINDOWS_QUICKSTART.md`: 대표 PC 최소 설치(Python·Git·clone·브랜치·venv·pip·pytest·비밀값 파일·첫 연결 점검, 단계별 '정상 화면')와 1일차 기술 점검 시간표 한 장

## 2026-11-26 — D-043·D-044, 리스크실 허용오차 확정

- 비중 허용오차 0.1%p: 일반 22.1%, 1주 예외 신규 진입 30.1% (수수료는 계산에 그대로) — 테스트: 22.1% 통과·22.2% 거부, 33,000원 4건 수수료 반영 후 통과
- D-044: `STOCKKING_OPERATING_CAPITAL` 로 모의 운용 금액 자유 설정(쉼표 허용), 미설정 15만 원, 실전 T0는 15만 원 외 값 거부. 운용 금액 > 모의 예수금이면 connect_check 3-1 FAIL·tech_orders/s02 submit 중단(이하이면 경고 없음)
- D-043: 5거래일 일정, D-027 판정 4일차(12/1)·실행 5일차(12/2) 문서 반영. `paper_day.ps1 -OperatingCapital`
- 179 passed

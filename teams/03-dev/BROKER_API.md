# 증권사 API 기술 비교 (1차 과업 #1)

> 작성: 프로그램 생성팀 / 2026-11-23 · 상태: **초안 — 총괄 승인(A) 대기**
> RACI상 증권사/API 선정은 프로그램팀 R, 총괄 A, 리스크·자료·매매·회계 C 입니다.
> ⚠ 아래 유량·도메인·TR 정보는 공개 블로그/서드파티 자료 기준이며 **공식 포털 문서로 재확인 전 확정값이 아닙니다.** (§5 근거)

## 1. 결론 (권고)

| 순위 | 선택 | 이유 |
|---|---|---|
| **1순위(주력)** | **한국투자증권 KIS Open API** | REST + WebSocket → **Linux 서버/클라우드 운영 가능**, 모의투자 전용 도메인 제공, 공식 GitHub 예제·커뮤니티 자료 최다, 해외주식 확장 용이 |
| 2순위(대체) | 키움 REST API (2025.3 출시) | REST 기반으로 OS 제약 해소, 모의 도메인 별도 제공. 다만 출시 기간이 짧아 운영 사례·안정성 데이터가 KIS보다 적음 |
| 비권고 | 키움 OpenAPI+ / LS xingAPI (COM/OCX) | **Windows 전용 + 32bit Python + 로그인 창(GUI)** 필요 → 무인 서버 운영·자동 재기동·컨테이너화가 어려움 |

**설계상 보험:** 코드는 `Broker` 인터페이스(`src/stockking/broker/base.py`) 뒤에 증권사 차이를 가둬 두었기 때문에, 1순위가 문제 되면 어댑터만 교체하면 됩니다.

## 2. 비교표

| 항목 | KIS Open API | 키움 REST API | 키움 OpenAPI+ | LS증권 OPEN API (REST) / xingAPI |
|---|---|---|---|---|
| 방식 | REST + WebSocket | REST + WebSocket | Windows COM(OCX) | REST(신규) / COM(xingAPI) |
| 운영 OS | 무관 (Linux OK) | 무관 | **Windows 전용**, 32bit | REST 무관 / xing은 Windows |
| 무인 운영 | 가능 (토큰 발급 자동화) | 가능 | 어려움 (로그인 GUI, 버전처리 팝업) | REST 가능 |
| 모의투자 | 지원 — 별도 도메인 `openapivts.koreainvestment.com:29443`, 모의 전용 앱키 | 지원 — `mockapi.kiwoom.com`, 모의용 토큰 별도 | 지원(모의 서버 접속) | 지원 — 모의 별도 신청·별도 앱키 |
| 모의투자 제약 | 유량 낮음(초당 ~1건 수준 보고), 일부 API/실시간 미지원 보고 | 실전 토큰 재사용 불가 | — | 일부 상품 시장가 불가 보고 |
| 인증 | appkey/appsecret → access token(약 24h), **토큰 발급 빈도 제한(분당 1회 수준)** | appkey/secret → token | 공동인증서 로그인 | appkey/secret → token |
| 대표 오류 | EGW00201(유량 초과), EGW00121(토큰 만료) | — | — | — |
| 실전/모의 구분 | 도메인 + **TR ID 접두어(실전 T / 모의 V)** | 도메인 | 서버 선택 | 도메인/포트 |
| 생태계 | 공식 GitHub 샘플, 커뮤니티 큼 | 공식 CLI·샘플(2026.7 공개 보도) | 자료 가장 많으나 레거시 | 중간 |

## 3. 우리 시스템에 미치는 설계 영향 (KIS 기준)

1. **토큰 캐시 필수** — 재발급 빈도 제한 때문에 프로세스 재시작마다 새로 받으면 막힘 → 토큰은 메모리 캐시, 디스크 캐시 시 `var/`(gitignore) 아래 권한 600으로만.
2. **유량 제어기(throttle)** — 모의투자는 특히 낮음. 어댑터 내부에서 최소 호출 간격 강제(`KisPaperClient.min_interval_sec`, 현재 보수적으로 1초). 유량 초과 응답 시 지수 백오프.
3. **주문은 재전송하지 않는다** — 네트워크 타임아웃 시 주문 성공 여부가 불명확. 자동 재전송은 **중복주문 사고**의 1순위 원인이므로, 타임아웃 → 미체결/체결 조회로 상태 확인 후 판단(P1 후반 구현).
4. **실전/모의 혼용 차단** — 모의 도메인 하드코딩 + 실전 도메인 거부 + TR ID `V` 접두어 테스트(`tests/test_kis_paper.py`).
5. **실시간 시세(WebSocket)** — 모의투자 환경에서 제약 보고가 있어, P1에서는 **일봉/분봉 REST 폴링 + 장 마감 후 배치** 위주로 설계하고 실시간은 P3 이후 검토.

## 4. 공식 문서로 반드시 재확인할 항목 (착수 전 체크리스트)

- [ ] KIS 모의투자 REST 유량(초당/분당) 공식값
- [ ] 국내주식 현금주문 TR ID: 모의 매수 `VTTC0012U` / 매도 `VTTC0011U` (구 `VTTC0802U/0801U`에서 변경 이력 있음)
- [ ] 토큰 유효기간·재발급 제한 공식값
- [ ] 모의투자에서 미지원 API 목록 (정정/취소, 예약주문, 실시간 체결통보 등)
- [ ] 이용약관: 개인 본인 계좌 자동매매 허용 범위, 재배포/데이터 저장 제한 (→ 총괄 컴플라이언스 점검과 공동 확인)

## 5. 근거 (2026-11-23 검색, 대부분 2차 자료)

- 키움 REST API 모의 도메인(`mockapi.kiwoom.com`)·토큰 분리: [AlgoLab 키움 REST API 가이드](https://algolab.co.kr/blog/kiwoom-rest-api-algotrading-guide-2026), [kiwoom-rest-api (GitHub)](https://github.com/younghwan91/kiwoom-rest-api)
- 키움 REST API 출시(2025.3): [아시아경제](https://www.asiae.co.kr/en/article/2025032415230534675), CLI·샘플 공개: [한국경제 2026.7](https://www.hankyung.com/article/202607215879P)
- KIS 모의투자 유량·토큰 발급 제한(실측 주장): [WikiDocs KIS 실측](https://wikidocs.net/453543), 오류코드: [AlgoLab KIS 오류코드](https://algolab.co.kr/blog/kis-api-error-codes), 모의 제약: [AlgoLab KIS 모의투자 한계](https://algolab.co.kr/blog/kis-paper-trading-limits-2026)
- LS증권 OPEN API 모의투자·별도 앱키: [AlgoLab LS OPEN API](https://algolab.co.kr/blog/ls-securities-openapi-rest-order-2026), [krsec Go 라이브러리](https://pkg.go.dev/github.com/smallfish06/krsec@v0.18.0)
- 증권사 API 종합 비교: [AlgoLab 비교](https://algolab.co.kr/blog/kr-broker-api-comparison)

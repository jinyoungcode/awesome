# 자료팀 일일 수집 운영 안내 (v1.0)

> 작성: ② 자료 수집팀 / 2026-11-25
> 근거: 대표 결정(2026-11-25) — 국내 분봉은 "약 1년 백필 + **오늘부터 매일 적재**", 데이터 보관은 `STORAGE.md` 안대로(운영 머신 로컬 + 암호화 무료 클라우드 백업)
> 도구: `teams/02-data/collectors/` (KIS **시세 조회 전용**, 주문 기능 없음, 모의투자 도메인만 사용)

## 0. 지금 상태와 시작 조건
- 수집 코드와 테스트(가짜 응답 9건)는 준비됐다. **실제 수집은 대표의 한투 가입 → 모의투자 앱키 발급 후 시작**한다.
- 빠진 날은 손실이 아니다. KIS 과거일자 분봉 조회(보관 약 1년 ⚠)로 최근 1년은 언제든 다시 받을 수 있다. "매일 적재"의 목적은 **1년이 지나 사라지기 전에 우리 DB에 남기는 것**이다. 키 발급이 며칠 늦어져도 백필로 메운다.

## 1. 최초 1회 준비 (대표 PC 또는 운영 머신)
1. Python 3.11+ 설치, 이 저장소 내려받기
2. 환경변수 설정 (프로그램팀과 같은 이름, **값은 저장소·채팅에 올리지 말 것**)
   - `KIS_PAPER_APP_KEY`, `KIS_PAPER_APP_SECRET`, `KIS_PAPER_ACCOUNT_NO`
   - 선택: `STOCKKING_DATA_DIR` (기본 `~/stockking-data`, Git 저장소 안이면 도구가 거부)
3. 구성종목 파일 내려받기: KRX 정보데이터시스템(data.krx.co.kr) → 지수 → 주가지수 → 지수구성종목 → `코스피 200`, `코스닥 150` 각각 CSV 다운로드 (월 1회·정기변경일(6월·12월 선물옵션 만기 다음 거래일) 직후 갱신)
4. 연결 확인: `python teams/02-data/collectors/collect.py check`

## 2. 매일 할 일 (거래일 16:00 이후 자동 실행)

| 순서 | 명령 | 소요(모의 도메인, 1초 1건 기준) |
|---|---|---|
| ① 당일 분봉 적재 | `python teams/02-data/collectors/collect.py minute` | 약 350종목 × 4회 ≈ 25분 |
| ② 과거 분봉 백필 (밤) | `python teams/02-data/collectors/collect.py minute-backfill --days 260 --max-days 15` | 하루치 ≈ 25분 → 15일치 ≈ 6시간. 약 18일 밤이면 1년치 완료 |
| ③ 백업 | `rclone sync ~/stockking-data crypt-remote:stockking-data` (STORAGE.md) | 수 분 |

- 처음 한 번은 ①에 `--kospi200 <파일> --kosdaq150 <파일>`을 붙여 종목 목록을 등록한다. 이후엔 등록 목록(`registry/kr_symbols.txt`)을 쓰고, **편출·상폐 종목도 지우지 않고 계속 적재**한다.
- 백필은 **오래된 날짜부터** 채운다(보관 기간 밖으로 먼저 사라지는 날을 우선 확보).
- 중간에 끊겨도 다시 실행하면 이어서 한다(완료 종목 건너뜀). 휴장일은 자동 판별해 표식을 남긴다.

### 자동 실행 등록 예
- **Windows 작업 스케줄러:** 트리거 "매주 월~금 16:05", 동작 `python <저장소>\teams\02-data\collectors\collect.py minute`; 두 번째 작업 "월~금 20:00" `... minute-backfill --days 260 --max-days 15`
- **Linux/Mac cron:** 
  ```
  5 16 * * 1-5  cd ~/awesome && python3 teams/02-data/collectors/collect.py minute >> ~/stockking-data/logs/minute.log 2>&1
  0 20 * * 1-5  cd ~/awesome && python3 teams/02-data/collectors/collect.py minute-backfill --days 260 --max-days 15 >> ~/stockking-data/logs/backfill.log 2>&1
  ```
- PC가 꺼져 있던 날은 다음 실행 때 `minute --date YYYY-MM-DD`로 보충하거나 백필이 자동으로 채운다.

## 3. S02 모의투자 입력 파일 (모의 첫날 **전날 18:30까지**)
```
python teams/02-data/collectors/collect.py s02-inputs --kospi200 k200.csv --kosdaq150 kq150.csv --out ~/stockking-data/s02_inputs/YYYY-MM-DD
```
- 출력: `bars.csv`, `universe.txt`, `markets.csv`, `index.csv` (+ 검토용 `universe_report.csv`, `manifest.json`)
- 장 마감 후 **당일(T) 저녁에** 실행해야 관리·경고·정지 상태(U6/U7)가 반영된다. 실행 시간 약 25분(약 1,400회 호출)
- 실행 후 `manifest.json`에서 `universe_passed`(통과 종목 수), `bad_ohlc_count`(0이어야 함), `limitations`를 확인

## 4. 알려진 한계 (품질 리포트에 명시)
| 항목 | 한계 | 해소 계획 |
|---|---|---|
| 국내 분봉 기간 | 약 1년 + 매일 누적 | 시간이 해결. 3~5년 필요 시점에 유료 재상정(DATA-2) |
| 국내 분봉 생존편향 | 과거 1년 중 이미 상폐된 종목은 조회 불가 | 오늘 이후 상폐분은 우리 DB에 남음 |
| 구성종목 | 실행 시점 목록(시점별 이력 아님) | 백테스트용 PIT 이력은 KRX 정기변경 자료로 별도 구축 |
| 수정주가 | S02 모의 입력은 KIS 수정주가 사용 | 자체 수정계수(af-v1) 구축 후 교체 |
| 호출 속도 | 모의 도메인 1초 1건(보수적) → 백필 느림 | 실전 앱키로 **시세만** 조회하면 약 10~20배 빨라짐 — 대표 결정 필요 |
| NXT(넥스트레이드) | KRX 시세만 적재 | 데이트레이딩 명세 확정 시 추가 |
| 분봉 `cum_value` | KIS 분봉의 거래대금은 **누적값** | 분당 거래대금은 차분으로 계산 |

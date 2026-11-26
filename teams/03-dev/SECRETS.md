# KIS 앱키 안전 주입 방안 (D-022 요청 #2)

> 작성: 프로그램 생성팀 / 2026-11-24 · 상태: 검토 완료, 권고안 구현됨

## 1. KIS 인증 구조 (왜 까다로운가)

| 단계 | 키가 들어가는 곳 |
|---|---|
| 토큰 발급 `POST /oauth2/tokenP` | **JSON 본문**에 `appkey`, `appsecret` |
| 이후 모든 호출 | **헤더** `appkey`, `appsecret` + `authorization: Bearer <토큰>` |
| 토큰 | 약 24시간 유효, 재발급 빈도 제한(1분 1회) → 메모리 + **저장소 밖 캐시 파일**(`%APPDATA%\stockking\kis_paper_token.json`, 본인만 읽기, 앱키 지문만 저장·키 자체는 미저장) |

키가 본문과 비표준 헤더 두 곳에 모두 들어갑니다. 그래서 "프록시가 헤더 하나에 키를 넣어 주는" 방식으로는 처리할 수 없습니다.

## 2. 선택지 비교

| 방식 | 키 위치 | 장점 | 단점 | 판정 |
|---|---|---|---|---|
| A. Claude 클라우드 세션의 보안 입력(네트워크 시크릿) | 세션 프록시가 요청 헤더에 주입 | 키가 컨테이너·채팅에 안 보임 | ① 토큰 발급은 **본문**에 키가 필요해 주입 불가 ② 이 컨테이너에서 KIS 모의 서버(`openapivts.koreainvestment.com:29443`)로 연결 시 **프록시가 연결을 끊음**(2026-11-23 실측) | **사용 불가** |
| B. 환경변수 | 셸 세션 메모리 | 단순, 코드 지원 완료 | 셸 기록·프로세스 목록 등으로 새기 쉬움 | 보조 |
| C. **저장소 밖 비밀값 파일 (권고)** | `~/.config/stockking/secrets.env` | 재부팅 후에도 유지, 코드가 위치·권한을 강제 검사 | PC 계정이 털리면 노출 | **권고** |
| D. 실행 시 직접 입력 (`--prompt-keys`) | 메모리에만 | 디스크에 아무것도 안 남음 | 무인 자동 운용 불가 | 첫 연결 점검용 |
| E. OS 키체인 / 클라우드 시크릿 매니저 | 암호화 저장소 | 가장 안전 | 추가 의존성·서버 필요 | 무인 서버 운용 단계(P4~)에서 도입 |

## 3. 권고: 대표 PC에서 C(평상시) + D(첫 점검)

**결론: KIS 연결 스크립트는 Claude 클라우드 세션이 아니라 대표님 PC(또는 이후 전용 서버)에서 실행해야 합니다.** 위 A의 ②처럼 클라우드 컨테이너에서는 KIS 서버에 닿지 않습니다.

코드가 강제하는 안전장치 (`src/stockking/config.py`):
- 비밀값 파일이 **저장소 폴더 안에 있으면 거부**
- 리눅스·맥에서 파일 권한이 600보다 넓으면 **거부**
- 키는 `Secret`으로 감싸 로그·오류 메시지에 출력되지 않음, 감사 로그는 `appkey/appsecret/token`을 자동으로 `****` 처리
- 찾는 순서: 환경변수 → 비밀값 파일(`STOCKKING_SECRETS_FILE` 또는 기본 경로)

### 설정 방법 (맥·리눅스)
```bash
mkdir -p ~/.config/stockking
cat > ~/.config/stockking/secrets.env   # 아래 4줄을 붙여넣고 Ctrl+D
chmod 600 ~/.config/stockking/secrets.env
```
```
KIS_PAPER_APP_KEY=발급받은앱키
KIS_PAPER_APP_SECRET=발급받은시크릿
KIS_PAPER_ACCOUNT_NO=모의계좌앞8자리
KIS_PAPER_ACCOUNT_PRODUCT=01
```

### 설정 방법 (윈도우 — 대표 PC)
- 위치: **`%APPDATA%\stockking\secrets.env`** (예: `C:\Users\<사용자>\AppData\Roaming\stockking\secrets.env`). 코드의 윈도우 기본 경로입니다.
- `scripts\setup_windows.ps1` 이 이 파일을 **값 없이** 만들고, 권한을 **본인 계정만 읽기·쓰기**(icacls, 상속 제거)로 설정합니다. 기존 파일은 덮어쓰지 않습니다.
- `notepad "$env:APPDATA\stockking\secrets.env"` 로 열어 `=` 뒤에 값을 넣고 저장합니다(메모장 BOM 저장도 읽힘).
- 코드가 거부하는 위치: 저장소 폴더 안, **OneDrive 동기화 폴더 안**(키가 클라우드로 복제되는 것 방지).
- 다른 경로를 쓰려면 환경변수 `STOCKKING_SECRETS_FILE` 로 지정합니다.

## 4. 절대 하지 말 것
- 키를 채팅, 저장소, 이슈/PR, 스크린샷, 메신저에 올리기
- 저장소 안의 `.env`에 실전 키 넣기(`.env`는 커밋 차단되지만 백업·동기화 폴더로 새어 나갈 수 있음)
- 실전 키 발급 — D-002/D-023에 따라 3거래일 모의 점검 통과와 대표 승인 전까지 실전 키는 만들지도 않습니다

## 5. 키가 유출됐다고 의심되면
1. KIS Developers에서 해당 앱키 **즉시 폐기·재발급**
2. `touch var/KILL_SWITCH`(킬스위치)로 자동 주문 중단
3. 총괄·리스크관리실에 보고 → `docs/DECISIONS.md`에 사고 기록

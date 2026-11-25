"""실전 앱키 **시세 조회 전용** 클라이언트 (D-041, 2026-11-25 대표 승인).

D-041 조건과 이 모듈의 대응:
1. 주문 기능 코드 없음 → 주문 관련 메서드·TR·경로가 없다. 호출 가능한 것은
   토큰 발급(POST /oauth2/tokenP)과 아래 허용 목록의 시세 조회(GET)뿐이며, 그 밖의 호출은 예외로 거부한다.
2. 실전 키는 모의 키와 별도 파일 → 환경변수를 읽지 않고 `secrets_live_quotes.env` 파일에서만 읽는다.
   모의 비밀값 파일과 같은 파일이면 거부한다.
3. 실전 주문 차단 유지 → 프로그램팀 `config.LIVE_TRADING_APPROVED`·`KisPaperClient`는 건드리지 않는다.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

KIS_LIVE_BASE_URL = "https://openapi.koreainvestment.com:9443"
LIVE_QUOTES_SECRETS_FILE = Path.home() / ".config" / "stockking" / "secrets_live_quotes.env"
KEY_NAME, SECRET_NAME = "KIS_LIVE_QUOTE_APP_KEY", "KIS_LIVE_QUOTE_APP_SECRET"

TOKEN_PATH = "/oauth2/tokenP"
# 허용 목록: (경로, TR ID) — 국내주식 시세 조회만
ALLOWED_QUOTES = {
    ("/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice", "FHKST03010100"),
    ("/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice", "FHKST03010200"),
    ("/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice", "FHKST03010230"),
    ("/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100"),
    ("/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice", "FHKUP03500100"),
}


class QuoteOnlyViolation(PermissionError):
    """시세 조회 외 호출 시도."""


def _urllib_transport(method: str, url: str, headers: dict, body: dict) -> dict:
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - 고정 도메인
        return json.loads(resp.read().decode())


def load_live_quote_keys(path: str | Path | None = None) -> tuple[str, str]:
    from stockking.config import DEFAULT_SECRETS_FILE, load_secret_file

    p = Path(path or LIVE_QUOTES_SECRETS_FILE).expanduser().resolve()
    if p == Path(DEFAULT_SECRETS_FILE).expanduser().resolve():
        raise PermissionError("실전 시세 키는 모의 키(secrets.env)와 다른 파일에 두어야 합니다 (D-041)")
    vals = load_secret_file(p)  # 저장소 안 위치·느슨한 권한 거부는 프로그램팀 규칙 그대로
    key, sec = vals.get(KEY_NAME, ""), vals.get(SECRET_NAME, "")
    if not (key and sec):
        raise RuntimeError(f"{p} 에 {KEY_NAME}, {SECRET_NAME} 가 필요합니다 (teams/02-data/DAILY_JOBS.md §5)")
    return key, sec


class KisLiveQuoteClient:
    """KisQuotes가 쓰는 `_call`/`_headers`만 제공한다. 주문 관련 기능 없음."""

    is_paper = False
    min_interval_sec = 0.07  # 실전 시세 초당 약 20건 ⚠ — 여유를 두고 약 14건/초

    def __init__(self, app_key: str, app_secret: str, transport=None) -> None:
        self._key, self._secret = app_key, app_secret
        self._transport = transport or _urllib_transport
        self._token = ""
        self._token_expiry = 0.0
        self._last = 0.0

    def __repr__(self) -> str:
        return "KisLiveQuoteClient(****)"

    def _token_value(self) -> str:
        if self._token and time.time() < self._token_expiry - 60:
            return self._token
        res = self._send("POST", TOKEN_PATH, {"content-type": "application/json"},
                         {"grant_type": "client_credentials", "appkey": self._key, "appsecret": self._secret})
        tok = res.get("access_token")
        if not tok:
            raise RuntimeError(f"KIS 실전 시세 토큰 발급 실패: {res.get('error_code') or res.get('msg1') or 'unknown'}")
        self._token, self._token_expiry = tok, time.time() + int(res.get("expires_in", 3600))
        return tok

    def _headers(self, tr_id: str) -> dict:
        if tr_id not in {t for _, t in ALLOWED_QUOTES}:
            raise QuoteOnlyViolation(f"허용되지 않은 TR: {tr_id}")
        return {"content-type": "application/json; charset=utf-8", "authorization": f"Bearer {self._token_value()}",
                "appkey": self._key, "appsecret": self._secret, "tr_id": tr_id, "custtype": "P"}

    def _call(self, method: str, path: str, headers: dict, params: dict) -> dict:
        if method != "GET" or (path, headers.get("tr_id")) not in ALLOWED_QUOTES:
            raise QuoteOnlyViolation(f"시세 조회 전용 클라이언트: {method} {path} {headers.get('tr_id')} 거부")
        return self._send("GET", path, headers, params)

    def _send(self, method: str, path: str, headers: dict, body: dict) -> dict:
        if not (path == TOKEN_PATH and method == "POST") and not (method == "GET" and path in {p for p, _ in ALLOWED_QUOTES}):
            raise QuoteOnlyViolation(f"{method} {path} 거부")
        wait = self.min_interval_sec - (time.monotonic() - self._last)
        if wait > 0 and self._last:
            time.sleep(wait)
        self._last = time.monotonic()
        url = KIS_LIVE_BASE_URL + path
        if method == "GET" and body:
            url, body = f"{url}?{urllib.parse.urlencode(body)}", {}
        return self._transport(method, url, headers, body)

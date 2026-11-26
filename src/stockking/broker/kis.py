"""한국투자증권(KIS) Open API 어댑터 — **모의투자 전용**.

- 실전 도메인으로는 생성 자체가 불가능하다(KisPaperClient 는 모의 도메인 하드코딩).
- 비밀값(appkey/appsecret/토큰)은 환경변수에서만 읽고, 로그·예외 메시지에 노출하지 않는다.
- 네트워크 계층(transport)은 주입식이라 단위 테스트에서 가짜로 대체한다.

⚠ TR ID·필드명·유량 제한은 KIS Developers 공식 문서로 재확인해야 한다(BROKER_API.md §4).
"""

from __future__ import annotations

import json
import time
import hashlib
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from dataclasses import dataclass
from typing import Callable

from ..config import Secret, require_secret
from decimal import Decimal

from ..domain import OrderRequest, OrderType, Side
from ..market import Market, market_of
from .base import BrokerOrderResult

KIS_PAPER_BASE_URL = "https://openapivts.koreainvestment.com:29443"
_KIS_LIVE_HOST_MARKERS = ("openapi.koreainvestment.com:9443",)

# 모의투자 국내주식 현금주문 TR ID (공식 문서 재확인 필요)
TR_PAPER_BUY = "VTTC0012U"
TR_PAPER_SELL = "VTTC0011U"
TR_PAPER_CANCEL = "VTTC0013U"         # 정정취소 (구 VTTC0803U)
TR_PAPER_BALANCE = "VTTC8434R"        # 잔고 조회
TR_PAPER_DAILY_ORDERS = "VTTC8001R"   # 일별 주문체결 조회(3개월 이내)
TR_PRICE = "FHKST01010100"            # 현재가 (실전·모의 공통)

# 모의투자 해외주식(미국) 주문 TR ID (D-006, 공식 문서 재확인 필요)
TR_PAPER_US_BUY = "VTTT1002U"
TR_PAPER_US_SELL = "VTTT1001U"
US_EXCHANGES = ("NASD", "NYSE", "AMEX")  # KIS 해외거래소코드(OVRS_EXCG_CD)

# KIS 주문구분(ORD_DVSN): 00 지정가, 01 시장가
_ORD_DVSN = {OrderType.LIMIT: "00", OrderType.MARKET: "01"}

Transport = Callable[[str, str, dict, dict], dict]  # (method, url, headers, body) -> json


def _urllib_transport(method: str, url: str, headers: dict, body: dict) -> dict:
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - 고정 도메인
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:   # 403 등도 KIS 오류 본문(error_code 등)을 살려서 돌려준다
        try:
            payload = json.loads(e.read().decode() or "{}")
        except (ValueError, UnicodeDecodeError):
            payload = {}
        return {"_http_status": e.code, **payload}


# 토큰 재발급 제한(분당 1회 수준) 관련 오류 — 1일차 실측: 1분 안 재실행 시 HTTP 403
_TOKEN_RATE_LIMIT_CODES = {"EGW00133", "EGW00201"}


def default_token_cache() -> Path:
    """토큰 캐시: 저장소 밖, 비밀값 파일과 같은 폴더 (윈도우 %APPDATA%\\stockking, 그 외 ~/.config/stockking)."""
    env = os.environ.get("STOCKKING_TOKEN_CACHE")
    if env:
        return Path(env)
    from ..config import default_secrets_file
    return default_secrets_file().parent / "kis_paper_token.json"


def _restrict_to_owner(path: Path) -> None:
    """본인만 읽기·쓰기 (POSIX chmod 600, 윈도우 icacls). 실패해도 동작은 계속(최선 노력)."""
    try:
        if os.name == "posix":
            path.chmod(0o600)
        else:
            import subprocess
            user = os.environ.get("USERNAME", "")
            if user:
                subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:(R,W)"],
                               capture_output=True, check=False)
    except OSError:
        pass


@dataclass
class KisPaperCredentials:
    app_key: Secret
    app_secret: Secret
    account_no: str        # 계좌 앞 8자리
    account_product: str   # 상품코드 2자리

    @classmethod
    def from_env(cls) -> "KisPaperCredentials":
        import os

        return cls(
            app_key=require_secret("KIS_PAPER_APP_KEY"),
            app_secret=require_secret("KIS_PAPER_APP_SECRET"),
            account_no=require_secret("KIS_PAPER_ACCOUNT_NO").reveal(),
            account_product=os.environ.get("KIS_PAPER_ACCOUNT_PRODUCT", "01"),
        )


class KisPaperClient:
    """KIS 모의투자 REST 클라이언트 (인증 + 현금주문). 시세/잔고는 P1 후반에 확장."""

    is_paper = True
    min_interval_sec = 1.0  # 모의투자 유량이 낮다(초당 1~2건 수준 보고). 공식값 확인 전 보수적으로.

    def __init__(self, creds: KisPaperCredentials, transport: Transport | None = None,
                 base_url: str = KIS_PAPER_BASE_URL, token_cache: Path | None | bool = None) -> None:
        if base_url != KIS_PAPER_BASE_URL or any(m in base_url for m in _KIS_LIVE_HOST_MARKERS):
            raise PermissionError("KisPaperClient 는 모의투자 도메인만 허용합니다 (실계좌 차단)")
        self._creds = creds
        self._base = base_url
        self._transport = transport or _urllib_transport
        self._token: Secret | None = None
        self._token_expiry: float = 0.0
        self._last_call: float = 0.0
        self.last_response: dict = {}
        # token_cache: None=기본 경로, False=파일 캐시 안 함(테스트용 가짜 transport 기본), Path=지정 경로
        if token_cache is None:
            token_cache = default_token_cache() if transport is None else False
        self._token_cache: Path | None = token_cache or None

    def _key_id(self) -> str:
        # 캐시가 다른 앱키의 토큰을 쓰지 않도록 앱키 지문(해시 앞 12자리)만 저장 — 키 자체는 저장 안 함
        return hashlib.sha256(self._creds.app_key.reveal().encode()).hexdigest()[:12]

    def _load_cached_token(self) -> bool:
        if not self._token_cache or not self._token_cache.exists():
            return False
        try:
            c = json.loads(self._token_cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if c.get("key_id") != self._key_id() or c.get("base_url") != self._base or time.time() >= c.get("expires_at", 0) - 60:
            return False
        self._token, self._token_expiry = Secret(c["access_token"]), float(c["expires_at"])
        return True

    def _save_cached_token(self) -> None:
        if not self._token_cache:
            return
        try:
            self._token_cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._token_cache.with_suffix(".tmp")
            tmp.write_text(json.dumps({"access_token": self._token.reveal(), "expires_at": self._token_expiry,
                                       "key_id": self._key_id(), "base_url": self._base}), encoding="utf-8")
            _restrict_to_owner(tmp)
            tmp.replace(self._token_cache)
        except OSError:
            pass   # 캐시 실패는 치명적이지 않음(다음 실행에서 재발급)

    # --- 인증 ---
    def _access_token(self) -> str:
        # 토큰은 약 24시간 유효, 재발급 빈도 제한(분당 1회 수준) → 메모리 + 파일(저장소 밖) 캐시
        if self._token and time.time() < self._token_expiry - 60:
            return self._token.reveal()
        if self._load_cached_token():
            return self._token.reveal()
        res = self._call("POST", "/oauth2/tokenP", {"content-type": "application/json"}, {
            "grant_type": "client_credentials",
            "appkey": self._creds.app_key.reveal(),
            "appsecret": self._creds.app_secret.reveal(),
        })
        token = res.get("access_token")
        if not token:
            code = res.get("error_code") or res.get("msg_cd") or ""
            status = res.get("_http_status")
            detail = res.get("error_description") or res.get("msg1") or ""
            if status == 403 or code in _TOKEN_RATE_LIMIT_CODES:
                raise RuntimeError(f"KIS 토큰 발급 제한(HTTP {status or '-'} {code}) — 토큰은 1분에 1번만 발급됩니다. "
                                   "1분 후 다시 실행하세요. (계속되면 모의투자 앱키·시크릿 확인)")
            raise RuntimeError(f"KIS 토큰 발급 실패: HTTP {status or '-'} {code} {detail}".strip())
        self._token = Secret(token)
        self._token_expiry = time.time() + int(res.get("expires_in", 3600))
        self._save_cached_token()
        return token

    def _headers(self, tr_id: str) -> dict:
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self._access_token()}",
            "appkey": self._creds.app_key.reveal(),
            "appsecret": self._creds.app_secret.reveal(),
            "tr_id": tr_id,
            "custtype": "P",
        }

    def _call(self, method: str, path: str, headers: dict, body: dict) -> dict:
        wait = self.min_interval_sec - (time.monotonic() - self._last_call)
        if wait > 0 and self._last_call:
            time.sleep(wait)
        self._last_call = time.monotonic()
        url = self._base + path
        if method == "GET" and body:
            url, body = f"{url}?{urllib.parse.urlencode(body)}", {}
        res = self._transport(method, url, headers, body)
        self.last_response = res  # 주문 직후 KRX_FWDG_ORD_ORGNO(취소에 필요) 확인용
        return res

    # --- 주문 ---
    def build_order_body(self, order: OrderRequest) -> dict:
        return {
            "CANO": self._creds.account_no,
            "ACNT_PRDT_CD": self._creds.account_product,
            "PDNO": order.symbol,
            "ORD_DVSN": _ORD_DVSN[order.order_type],
            "ORD_QTY": str(order.quantity),
            "ORD_UNPR": str(order.limit_price if order.order_type is OrderType.LIMIT else 0),
        }

    def build_us_order_body(self, order: OrderRequest) -> dict:
        """미국 주식 모의 주문 본문. 모의투자 해외주문은 지정가만 지원하는 것으로 알려져 있어 지정가만 허용한다."""
        if order.order_type is not OrderType.LIMIT:
            raise ValueError("미국 주식 모의 주문은 지정가(LIMIT)만 허용합니다")
        if order.native_limit_price is None or order.native_limit_price <= 0:
            raise ValueError("미국 주식 주문에는 USD 지정가(native_limit_price)가 필요합니다")
        exchange = (order.exchange or "").upper()
        if exchange not in US_EXCHANGES:
            raise ValueError(f"미국 거래소 코드가 필요합니다: {US_EXCHANGES} 중 하나 (받은 값: {order.exchange!r})")
        price = Decimal(str(order.native_limit_price)).quantize(Decimal("0.01"))
        return {
            "CANO": self._creds.account_no,
            "ACNT_PRDT_CD": self._creds.account_product,
            "OVRS_EXCG_CD": exchange,
            "PDNO": order.symbol,
            "ORD_QTY": str(order.quantity),
            "OVRS_ORD_UNPR": str(price),
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": "00",
        }

    def place_order(self, order: OrderRequest) -> BrokerOrderResult:
        if market_of(order.symbol) is Market.US:
            tr_id = TR_PAPER_US_BUY if order.side is Side.BUY else TR_PAPER_US_SELL
            path, body = "/uapi/overseas-stock/v1/trading/order", self.build_us_order_body(order)
        else:
            tr_id = TR_PAPER_BUY if order.side is Side.BUY else TR_PAPER_SELL
            path, body = "/uapi/domestic-stock/v1/trading/order-cash", self.build_order_body(order)
        res = self._call("POST", path, self._headers(tr_id), body)
        ok = res.get("rt_cd") == "0"
        output = res.get("output") or {}
        return BrokerOrderResult(ok, order.client_order_id, output.get("ODNO"), res.get("msg1", ""))

    # ------------------------------------------------------------------
    # 조회·취소 (D-022 모의 계좌 연결 점검용). ⚠ TR ID·필드명은 공식 문서 재확인 필요
    # ------------------------------------------------------------------
    @staticmethod
    def _ok(res: dict, what: str) -> dict:
        if res.get("rt_cd") != "0":
            raise RuntimeError(f"KIS {what} 실패: [{res.get('msg_cd', '')}] {res.get('msg1', '')}")
        return res

    def inquire_price(self, symbol: str) -> dict:
        """국내주식 현재가. 반환: {'price': 현재가, 'base_price': 기준가(전일종가)}"""
        res = self._ok(self._call("GET", "/uapi/domestic-stock/v1/quotations/inquire-price",
                                  self._headers(TR_PRICE), {"FID_COND_MRKT_DIV_CODE": "J",
                                                            "FID_INPUT_ISCD": symbol}), "현재가 조회")
        out = res.get("output") or {}
        return {"price": int(out.get("stck_prpr", 0)), "base_price": int(out.get("stck_sdpr", 0))}

    def inquire_balance(self) -> dict:
        """잔고. 반환: {'cash': 예수금, 'total_equity': 총평가, 'positions': {종목: (수량, 평균가)}}"""
        res = self._ok(self._call("GET", "/uapi/domestic-stock/v1/trading/inquire-balance",
                                  self._headers(TR_PAPER_BALANCE), {
                                      "CANO": self._creds.account_no, "ACNT_PRDT_CD": self._creds.account_product,
                                      "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02", "UNPR_DVSN": "01",
                                      "FUND_STTL_ICLD_YN": "N", "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00",
                                      "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""}), "잔고 조회")
        positions = {r["pdno"]: (int(r.get("hldg_qty", 0)), float(r.get("pchs_avg_pric", 0)))
                     for r in res.get("output1") or [] if int(r.get("hldg_qty", 0)) > 0}
        summary = (res.get("output2") or [{}])[0]
        details = {r["pdno"]: {"name": r.get("prdt_name", ""), "qty": int(r.get("hldg_qty", 0)),
                               "avg_cost": float(r.get("pchs_avg_pric", 0) or 0),
                               "close_price": int(float(r.get("prpr", 0) or 0)),
                               "market_value": int(float(r.get("evlu_amt", 0) or 0))}
                   for r in res.get("output1") or [] if int(r.get("hldg_qty", 0)) > 0}
        return {"cash": int(summary.get("dnca_tot_amt", 0)), "total_equity": int(summary.get("tot_evlu_amt", 0)),
                # D+2 예수금(가수도정산) — 당일 체결이 반영된 현금. 장부 현금 대사 기준 (필드명 재확인 필요)
                "cash_d2": int(summary.get("prvs_rcdl_excc_amt", summary.get("dnca_tot_amt", 0)) or 0),
                "positions": positions, "details": details}

    def cancel_order(self, broker_order_id: str, symbol: str, quantity: int,
                     krx_org_no: str | None = None) -> BrokerOrderResult:
        """미체결 주문 전량 취소. krx_org_no 는 주문 응답 output.KRX_FWDG_ORD_ORGNO."""
        org = krx_org_no or ((self.last_response.get("output") or {}).get("KRX_FWDG_ORD_ORGNO", ""))
        res = self._call("POST", "/uapi/domestic-stock/v1/trading/order-rvsecncl", self._headers(TR_PAPER_CANCEL), {
            "CANO": self._creds.account_no, "ACNT_PRDT_CD": self._creds.account_product,
            "KRX_FWDG_ORD_ORGNO": org, "ORGN_ODNO": broker_order_id, "ORD_DVSN": "00",
            "RVSE_CNCL_DVSN_CD": "02", "ORD_QTY": "0", "ORD_UNPR": "0", "QTY_ALL_ORD_YN": "Y"})
        output = res.get("output") or {}
        return BrokerOrderResult(res.get("rt_cd") == "0", "", output.get("ODNO") or broker_order_id, res.get("msg1", ""))

    def inquire_daily_orders(self, yyyymmdd: str) -> list[dict]:
        """당일 주문·체결 내역(첫 페이지). 각 행: odno, symbol, side, ord_qty, filled_qty, avg_price, canceled"""
        res = self._ok(self._call("GET", "/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
                                  self._headers(TR_PAPER_DAILY_ORDERS), {
                                      "CANO": self._creds.account_no, "ACNT_PRDT_CD": self._creds.account_product,
                                      "INQR_STRT_DT": yyyymmdd, "INQR_END_DT": yyyymmdd, "SLL_BUY_DVSN_CD": "00",
                                      "INQR_DVSN": "00", "PDNO": "", "CCLD_DVSN": "00", "ORD_GNO_BRNO": "",
                                      "ODNO": "", "INQR_DVSN_3": "00", "INQR_DVSN_1": "",
                                      "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""}), "주문체결 조회")
        rows = []
        for r in res.get("output1") or []:
            rows.append({"odno": r.get("odno", ""), "symbol": r.get("pdno", ""), "name": r.get("prdt_name", ""),
                         "ord_date": r.get("ord_dt", yyyymmdd),
                         "side": "sell" if r.get("sll_buy_dvsn_cd") == "01" else "buy",
                         "ord_qty": int(r.get("ord_qty", 0)), "filled_qty": int(r.get("tot_ccld_qty", 0)),
                         "avg_price": float(r.get("avg_prvs", 0) or 0), "canceled": r.get("cncl_yn") == "Y"})
        return rows

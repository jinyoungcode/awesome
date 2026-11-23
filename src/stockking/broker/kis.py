"""한국투자증권(KIS) Open API 어댑터 — **모의투자 전용**.

- 실전 도메인으로는 생성 자체가 불가능하다(KisPaperClient 는 모의 도메인 하드코딩).
- 비밀값(appkey/appsecret/토큰)은 환경변수에서만 읽고, 로그·예외 메시지에 노출하지 않는다.
- 네트워크 계층(transport)은 주입식이라 단위 테스트에서 가짜로 대체한다.

⚠ TR ID·필드명·유량 제한은 KIS Developers 공식 문서로 재확인해야 한다(BROKER_API.md §4).
"""

from __future__ import annotations

import json
import time
import urllib.request
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
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - 고정 도메인
        return json.loads(resp.read().decode())


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
                 base_url: str = KIS_PAPER_BASE_URL) -> None:
        if base_url != KIS_PAPER_BASE_URL or any(m in base_url for m in _KIS_LIVE_HOST_MARKERS):
            raise PermissionError("KisPaperClient 는 모의투자 도메인만 허용합니다 (실계좌 차단)")
        self._creds = creds
        self._base = base_url
        self._transport = transport or _urllib_transport
        self._token: Secret | None = None
        self._token_expiry: float = 0.0
        self._last_call: float = 0.0

    # --- 인증 ---
    def _access_token(self) -> str:
        # 토큰은 약 24시간 유효, 재발급 빈도 제한(분당 1회 수준) → 메모리 캐시 필수
        if self._token and time.time() < self._token_expiry - 60:
            return self._token.reveal()
        res = self._call("POST", "/oauth2/tokenP", {"content-type": "application/json"}, {
            "grant_type": "client_credentials",
            "appkey": self._creds.app_key.reveal(),
            "appsecret": self._creds.app_secret.reveal(),
        })
        token = res.get("access_token")
        if not token:
            raise RuntimeError(f"KIS 토큰 발급 실패: {res.get('error_code') or res.get('msg1') or 'unknown'}")
        self._token = Secret(token)
        self._token_expiry = time.time() + int(res.get("expires_in", 3600))
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
        return self._transport(method, self._base + path, headers, body)

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

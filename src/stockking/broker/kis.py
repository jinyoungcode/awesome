"""한국투자증권(KIS) Open API 어댑터 — **모의투자 전용**.

- 실전 도메인으로는 생성 자체가 불가능하다(KisPaperClient 는 모의 도메인 하드코딩).
- 비밀값(appkey/appsecret/토큰)은 환경변수에서만 읽고, 로그·예외 메시지에 노출하지 않는다.
- 네트워크 계층(transport)은 주입식이라 단위 테스트에서 가짜로 대체한다.

⚠ TR ID·필드명·유량 제한은 KIS Developers 공식 문서로 재확인해야 한다(BROKER_API.md §4).
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable

from ..config import Secret, require_secret
from ..domain import OrderRequest, OrderType, Side
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
        self.last_response: dict = {}

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

    def place_order(self, order: OrderRequest) -> BrokerOrderResult:
        tr_id = TR_PAPER_BUY if order.side is Side.BUY else TR_PAPER_SELL
        res = self._call("POST", "/uapi/domestic-stock/v1/trading/order-cash",
                         self._headers(tr_id), self.build_order_body(order))
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

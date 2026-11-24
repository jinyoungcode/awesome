"""핵심 도메인 타입. 금액·가격은 원(KRW) 정수, 수량은 주(株) 정수.

미국 주식(D-006)도 내부 금액은 원화 환산 정수로 다루고, 증권사 전송용 현지 통화 가격만
OrderRequest.native_limit_price 에 따로 담는다.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    LIMIT = "limit"    # 지정가
    MARKET = "market"  # 시장가


class OrderStatus(str, Enum):
    NEW = "new"
    REJECTED_BY_RISK = "rejected_by_risk"
    SUBMITTED = "submitted"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELED = "canceled"
    REJECTED_BY_BROKER = "rejected_by_broker"


_order_seq = itertools.count(1)


def new_client_order_id(prefix: str = "SK") -> str:
    """중복 주문 방지용 내부 주문번호(멱등 키)."""
    return f"{prefix}-{datetime.now():%Y%m%d%H%M%S}-{next(_order_seq):06d}"


@dataclass(frozen=True)
class OrderRequest:
    symbol: str          # 국내 6자리 코드(예: "005930") 또는 미국 티커(예: "AAPL")
    side: Side
    quantity: int
    order_type: OrderType = OrderType.LIMIT
    limit_price: int | None = None          # 원(KRW) 환산 가격 — 리스크 관문·장부 기준
    strategy_id: str = "unknown"
    client_order_id: str = field(default_factory=new_client_order_id)
    native_limit_price: Decimal | None = None  # 현지 통화 가격(미국: USD) — 증권사 전송용
    exchange: str | None = None                # 미국 거래소 코드(KIS: NASD/NYSE/AMEX)
    stop_price: float | None = None            # 매수 시 초기 손절가(원 환산) — 1건 손실 2% 검사(D-028)
    order_target: int | None = None            # D-005 주문 목표(원). 주면 관문이 90~110%·1주 예외를 검사
    sizing_exception: str = ""                 # "" | ONE_SHARE_UNDER | ONE_SHARE_OVER (D-021/D-036)

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("quantity 는 1 이상이어야 합니다")
        if self.order_type is OrderType.LIMIT and (self.limit_price is None or self.limit_price <= 0):
            raise ValueError("지정가 주문에는 양수 limit_price 가 필요합니다")


@dataclass(frozen=True)
class Fill:
    client_order_id: str
    symbol: str
    side: Side
    quantity: int
    price: int
    commission: int
    tax: int
    filled_at: datetime | date


@dataclass
class Position:
    symbol: str
    quantity: int = 0
    avg_price: float = 0.0

    def market_value(self, price: int) -> int:
        return self.quantity * price


@dataclass(frozen=True)
class AccountSnapshot:
    """리스크 체크에 필요한 계좌 상태."""

    cash: int
    positions: dict[str, Position]
    prices: dict[str, int]                # 평가에 쓸 현재가
    start_of_day_equity: int              # 당일 시작 평가금액 (일 손실 한도 계산 기준)
    start_of_week_equity: int | None = None
    start_of_month_equity: int | None = None
    principal: int | None = None          # 투입 원금 (최종 손절 기준, D-016)
    over_exception_symbols: frozenset = frozenset()  # '목표 초과' 1주 예외로 보유 중인 종목 (D-025/D-036)
    peak_equity: int | None = None        # 계좌 최고 평가금 (사이징 축소 기준, D-016)

    @property
    def equity(self) -> int:
        return self.cash + sum(
            p.quantity * self.prices.get(s, int(p.avg_price)) for s, p in self.positions.items()
        )

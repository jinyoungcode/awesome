"""브로커 어댑터 공통 인터페이스.

전략·리스크 코드는 이 인터페이스만 알고, 증권사별 차이는 어댑터 안에 가둔다.
(증권사 교체 시 어댑터만 바꾸면 되도록)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..domain import Fill, OrderRequest, Position


@dataclass(frozen=True)
class BrokerOrderResult:
    accepted: bool
    client_order_id: str
    broker_order_id: str | None = None
    message: str = ""


class Broker(Protocol):
    """모든 어댑터가 구현해야 하는 최소 기능."""

    is_paper: bool  # True 가 아니면 OrderRouter 가 주문을 거부한다(현 단계)

    def get_price(self, symbol: str) -> int: ...
    def get_cash(self) -> int: ...
    def get_positions(self) -> dict[str, Position]: ...
    def place_order(self, order: OrderRequest) -> BrokerOrderResult: ...
    def cancel_order(self, broker_order_id: str, symbol: str, quantity: int) -> BrokerOrderResult: ...
    def get_fills(self) -> list[Fill]: ...

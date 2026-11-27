"""로컬 시뮬레이션 브로커 — 단위 테스트·드라이런용.

증권사 모의투자 서버와는 별개다. 지정가는 현재가에 닿으면 전량 즉시 체결로 단순화한다.
"""

from __future__ import annotations

from datetime import datetime

from ..backtest.costs import CostModel
from ..domain import Fill, OrderRequest, OrderType, Position, Side
from .base import BrokerOrderResult


class PaperBroker:
    is_paper = True

    def __init__(self, cash: int, prices: dict[str, int], cost_model: CostModel) -> None:
        self._cash = cash
        self._prices = dict(prices)
        self._positions: dict[str, Position] = {}
        self._fills: list[Fill] = []
        self._costs = cost_model
        self._seq = 0

    # --- 시세 ---
    def set_price(self, symbol: str, price: int) -> None:
        self._prices[symbol] = price

    def get_price(self, symbol: str) -> int:
        return self._prices[symbol]

    def get_cash(self) -> int:
        return self._cash

    def get_positions(self) -> dict[str, Position]:
        return {s: Position(p.symbol, p.quantity, p.avg_price) for s, p in self._positions.items() if p.quantity}

    def get_fills(self) -> list[Fill]:
        return list(self._fills)

    # --- 주문 ---
    def place_order(self, order: OrderRequest) -> BrokerOrderResult:
        market = self._prices.get(order.symbol)
        if market is None:
            return BrokerOrderResult(False, order.client_order_id, message="unknown symbol")
        if order.order_type is OrderType.LIMIT:
            crosses = market <= order.limit_price if order.side is Side.BUY else market >= order.limit_price
            if not crosses:
                return BrokerOrderResult(False, order.client_order_id, message="limit not marketable (MVP: no resting orders)")
            price = market
        else:
            price = market

        gross = price * order.quantity
        commission = self._costs.commission(gross)
        tax = self._costs.sell_tax(gross) if order.side is Side.SELL else 0
        pos = self._positions.setdefault(order.symbol, Position(order.symbol))

        if order.side is Side.BUY:
            if gross + commission > self._cash:
                return BrokerOrderResult(False, order.client_order_id, message="insufficient cash")
            new_qty = pos.quantity + order.quantity
            pos.avg_price = (pos.avg_price * pos.quantity + gross) / new_qty
            pos.quantity = new_qty
            self._cash -= gross + commission
        else:
            if order.quantity > pos.quantity:
                return BrokerOrderResult(False, order.client_order_id, message="insufficient position")
            pos.quantity -= order.quantity
            self._cash += gross - commission - tax

        self._seq += 1
        self._fills.append(Fill(order.client_order_id, order.symbol, order.side, order.quantity,
                                price, commission, tax, datetime.now()))
        return BrokerOrderResult(True, order.client_order_id, broker_order_id=f"PAPER-{self._seq}")

    def cancel_order(self, broker_order_id: str, symbol: str, quantity: int) -> BrokerOrderResult:
        return BrokerOrderResult(False, "", broker_order_id, "MVP: 즉시체결 모델이라 취소할 미체결 없음")

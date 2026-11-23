"""주문 라우터 — 전략이 주문을 낼 수 있는 유일한 통로.

전략 → OrderRouter.submit() → [모드 확인] → [RiskGate] → Broker → Journal
브로커 객체를 전략에 직접 넘기지 않는다(관문 우회 원천 차단).
"""

from __future__ import annotations

from dataclasses import dataclass

from ..audit import Journal
from ..config import LIVE_TRADING_APPROVED, TradingMode
from ..domain import AccountSnapshot, OrderRequest, OrderStatus
from ..risk import RiskDecision, RiskGate


@dataclass(frozen=True)
class RoutedOrder:
    order: OrderRequest
    status: OrderStatus
    decision: RiskDecision
    broker_order_id: str | None = None
    message: str = ""


class OrderRouter:
    def __init__(self, broker, risk_gate: RiskGate, journal: Journal, mode: TradingMode) -> None:
        if mode is TradingMode.LIVE and not LIVE_TRADING_APPROVED:
            raise PermissionError("LIVE 모드 라우터 생성 금지 (대표 승인 전)")
        if not getattr(broker, "is_paper", False) and not LIVE_TRADING_APPROVED:
            raise PermissionError("모의투자(is_paper=True) 브로커만 연결할 수 있습니다")
        self._broker = broker
        self._gate = risk_gate
        self._journal = journal
        self.mode = mode

    def submit(self, order: OrderRequest, account: AccountSnapshot,
               reference_price: int | None = None) -> RoutedOrder:
        self._journal.write("order_request", mode=self.mode, order=order)
        decision = self._gate.check(order, account, reference_price)
        self._journal.write("risk_decision", client_order_id=order.client_order_id,
                            approved=decision.approved, reasons=list(decision.reasons))
        if not decision:
            return RoutedOrder(order, OrderStatus.REJECTED_BY_RISK, decision, message="; ".join(decision.reasons))
        try:
            result = self._broker.place_order(order)
        except Exception as exc:  # 네트워크 오류 등 — 상태 불명확, 재전송 금지(중복주문 위험)
            self._journal.write("broker_error", client_order_id=order.client_order_id, error=repr(exc))
            return RoutedOrder(order, OrderStatus.REJECTED_BY_BROKER, decision, message=f"broker error: {exc!r}")
        self._journal.write("broker_response", client_order_id=order.client_order_id,
                            accepted=result.accepted, broker_order_id=result.broker_order_id, message=result.message)
        status = OrderStatus.SUBMITTED if result.accepted else OrderStatus.REJECTED_BY_BROKER
        return RoutedOrder(order, status, decision, result.broker_order_id, result.message)

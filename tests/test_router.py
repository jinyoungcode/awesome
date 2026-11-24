import pytest

from stockking.audit import Journal
from stockking.broker import PaperBroker
from stockking.config import TradingMode
from stockking.domain import AccountSnapshot, OrderRequest, OrderStatus, OrderType, Side
from stockking.execution import OrderRouter
from stockking.risk import RiskGate


class FakeLiveBroker:
    is_paper = False


def test_router_rejects_non_paper_broker():
    with pytest.raises(PermissionError):
        OrderRouter(FakeLiveBroker(), RiskGate(), Journal(), TradingMode.PAPER)


def test_router_rejects_live_mode(costs):
    with pytest.raises(PermissionError):
        OrderRouter(PaperBroker(1, {}, costs), RiskGate(), Journal(), TradingMode.LIVE)


def test_router_flow_and_journal(costs):
    broker = PaperBroker(10_000_000, {"005930": 70_000}, costs)
    journal = Journal()
    router = OrderRouter(broker, RiskGate(), journal, TradingMode.PAPER)
    acct = AccountSnapshot(broker.get_cash(), broker.get_positions(), {"005930": 70_000}, 10_000_000)

    ok = router.submit(OrderRequest("005930", Side.BUY, 10, OrderType.LIMIT, 70_000), acct)
    assert ok.status is OrderStatus.SUBMITTED
    assert broker.get_positions()["005930"].quantity == 10

    bad = router.submit(OrderRequest("005930", Side.BUY, 1000, OrderType.LIMIT, 70_000), acct)
    assert bad.status is OrderStatus.REJECTED_BY_RISK
    assert len(broker.get_fills()) == 1  # 거부된 주문은 브로커에 도달하지 않음
    events = [r["event"] for r in journal.records]
    assert events.count("order_request") == 2 and events.count("broker_response") == 1


def test_journal_scrubs_secrets(tmp_path):
    j = Journal(tmp_path / "audit.jsonl")
    rec = j.write("x", headers={"appkey": "AAA", "authorization": "Bearer T", "tr_id": "V1"})
    assert rec["headers"]["appkey"] == "****" and rec["headers"]["tr_id"] == "V1"
    assert "AAA" not in (tmp_path / "audit.jsonl").read_text()

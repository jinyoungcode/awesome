"""리스크관리실 결정(2026-11-24): 시장가는 국내 청산 매도만, 청산 사유 코드 필수, 수량 ≤ 보유."""

import pytest

from stockking.domain import AccountSnapshot, OrderRequest, OrderType, Position, Side
from stockking.krx import ceil_to_tick, marketable_sell_limit
from stockking.risk import RiskGate, RiskLimits

L = RiskLimits(allow_market_orders=True)


def snap(sym="005930", qty=2, price=70_000):
    return AccountSnapshot(1_000_000, {sym: Position(sym, qty, price)} if qty else {}, {sym: price}, 1_000_000)


def check(order, s=None):
    return RiskGate(L).check(order, s or snap(order.symbol))


def test_kr_market_exit_sell_ok():
    assert check(OrderRequest("005930", Side.SELL, 2, OrderType.MARKET, exit_reason="X_STOP"))


def test_market_buy_rejected():
    d = check(OrderRequest("005930", Side.BUY, 1, OrderType.MARKET))
    assert any("MARKET_BUY_NOT_ALLOWED" in r for r in d.reasons)


def test_market_sell_without_reason_rejected():
    d = check(OrderRequest("005930", Side.SELL, 1, OrderType.MARKET))
    assert any("MARKET_SELL_NO_EXIT_REASON" in r for r in d.reasons)
    d2 = check(OrderRequest("005930", Side.SELL, 1, OrderType.MARKET, exit_reason="WHATEVER"))
    assert not d2


def test_us_market_rejected_both_sides():
    s = snap("AAPL", qty=1, price=300_000)
    for side in (Side.SELL, Side.BUY):
        d = RiskGate(L).check(OrderRequest("AAPL", side, 1, OrderType.MARKET, exit_reason="X_TP"), s)
        assert any("MARKET_US_NOT_ALLOWED" in r or "MARKET_BUY" in r for r in d.reasons)


def test_market_sell_over_holdings_rejected():
    d = check(OrderRequest("005930", Side.SELL, 3, OrderType.MARKET, exit_reason="X_TIME"))
    assert any("NO_SHORT" in r for r in d.reasons)


def test_master_switch_off_blocks_all_market():
    d = RiskGate(RiskLimits()).check(OrderRequest("005930", Side.SELL, 1, OrderType.MARKET, exit_reason="X_STOP"),
                                     snap())
    assert any("MARKET_ORDER_NOT_ALLOWED" in r for r in d.reasons)


@pytest.mark.parametrize("p,exp", [(19_990, 19_990), (19_995, 20_000), (4_996, 5_000), (68_601, 68_700), (49_990, 50_000)])
def test_ceil_to_tick(p, exp):
    assert ceil_to_tick(p) == exp


def test_marketable_sell_limit():
    assert marketable_sell_limit(70_000) == 68_600   # 70,000 × 0.98, 100원 단위


def test_x_tech_rejected_outside_paper_even_if_live_approved(monkeypatch):
    from stockking import config
    from stockking.audit import Journal
    from stockking.config import TradingMode
    from stockking.domain import OrderStatus
    from stockking.execution import router as router_mod

    class LiveBroker:
        is_paper = False

        def place_order(self, o):
            raise AssertionError("실계좌로 전송되면 안 됨")

    monkeypatch.setattr(config, "LIVE_TRADING_APPROVED", True)
    monkeypatch.setattr(router_mod, "LIVE_TRADING_APPROVED", True)
    r = router_mod.OrderRouter(LiveBroker(), RiskGate(L), Journal(), TradingMode.LIVE)
    for o in (OrderRequest("005930", Side.SELL, 1, OrderType.MARKET, exit_reason="X_TECH"),
              OrderRequest("005930", Side.BUY, 1, OrderType.LIMIT, 70_000, "T_TECH")):
        res = r.submit(o, snap())
        assert res.status is OrderStatus.REJECTED_BY_RISK and "TECH_ORDER_PAPER_ONLY" in res.message

from datetime import datetime, timedelta

import pytest

from stockking.domain import AccountSnapshot, OrderRequest, OrderType, Position, Side
from stockking.risk import KillSwitch, RiskGate, RiskLimits


def snap(cash=10_000_000, positions=None, prices=None, sod=None):
    prices = prices or {"005930": 70_000}
    positions = positions or {}
    s = AccountSnapshot(cash, positions, prices, 0)
    return AccountSnapshot(cash, positions, prices, sod if sod is not None else s.equity)


def buy(qty=10, px=70_000, sym="005930"):
    return OrderRequest(sym, Side.BUY, qty, OrderType.LIMIT, px, "t")


def test_normal_order_approved():
    assert RiskGate().check(buy(), snap())


def test_kill_switch_blocks_everything(tmp_path):
    ks = KillSwitch(tmp_path / "KS")
    gate = RiskGate(kill_switch=ks)
    ks.trip("manual")
    d = gate.check(buy(), snap())
    assert not d and "KILL_SWITCH" in d.reasons[0]


def test_kill_switch_file_from_outside(tmp_path):
    f = tmp_path / "KS"
    gate = RiskGate(kill_switch=KillSwitch(f))
    f.write_text("ops touched it")
    assert not gate.check(buy(), snap())


def test_kill_switch_reset_requires_operator(tmp_path):
    ks = KillSwitch(tmp_path / "KS")
    ks.trip("x")
    with pytest.raises(ValueError):
        ks.reset("")
    ks.reset("risk-officer")
    assert not ks.is_tripped()


def test_daily_loss_trips_kill_switch():
    gate = RiskGate()
    d = gate.check(buy(), snap(cash=9_400_000, sod=10_000_000))  # -6% (기본 T0 한도 -5%, D-028)
    assert not d and "DAILY_LOSS_LIMIT" in d.reasons[0]
    assert gate.kill_switch.is_tripped()


def test_position_weight_limit():
    d = RiskGate(RiskLimits(max_order_value=10**9)).check(buy(qty=40), snap())  # 2.8M / 10M = 28%
    assert not d and any("POSITION_WEIGHT" in r for r in d.reasons)


def test_order_value_limit():
    d = RiskGate().check(buy(qty=30), snap(cash=100_000_000))  # 2.1M > 2M
    assert any("ORDER_VALUE" in r for r in d.reasons)


def test_max_positions():
    pos = {f"00000{i}": Position(f"00000{i}", 1, 1000) for i in range(5)}
    prices = {**{k: 1000 for k in pos}, "005930": 70_000}
    d = RiskGate().check(buy(qty=1), snap(positions=pos, prices=prices))
    assert any("MAX_POSITIONS" in r for r in d.reasons)


def test_no_short_selling():
    d = RiskGate().check(OrderRequest("005930", Side.SELL, 5, OrderType.LIMIT, 70_000), snap())
    assert any("NO_SHORT_SELLING" in r for r in d.reasons)


def test_price_band():
    d = RiskGate().check(buy(px=100_000), snap())
    assert any("PRICE_BAND" in r for r in d.reasons)


def test_market_order_blocked_by_default():
    o = OrderRequest("005930", Side.BUY, 1, OrderType.MARKET)
    assert any("MARKET_ORDER" in r for r in RiskGate().check(o, snap()).reasons)


def test_missing_price_fails_closed():
    d = RiskGate().check(buy(sym="999999"), snap())
    assert not d and any("NO_PRICE" in r for r in d.reasons)


def test_duplicate_order_id_blocked():
    gate, o = RiskGate(), buy()
    assert gate.check(o, snap())
    assert any("DUPLICATE" in r for r in gate.check(o, snap()).reasons)


def test_order_rate_limit():
    gate = RiskGate(RiskLimits(max_orders_per_minute=3))
    now = datetime(2026, 11, 23, 10, 0)
    for i in range(3):
        assert gate.check(buy(qty=1), snap(), now=now + timedelta(seconds=i))
    d = gate.check(buy(qty=1), snap(), now=now + timedelta(seconds=10))
    assert any("ORDER_RATE" in r for r in d.reasons)
    assert gate.check(buy(qty=1), snap(), now=now + timedelta(minutes=2))


def test_exception_fails_closed():
    class Broken(AccountSnapshot):
        @property
        def equity(self):
            raise RuntimeError("boom")

    d = RiskGate().check(buy(), Broken(1, {}, {"005930": 70_000}, 1))
    assert not d and "fail-closed" in d.reasons[0]


def test_memory_only_kill_switch_ignores_file(tmp_path):
    f = tmp_path / "KS"
    f.write_text("live ops halt")
    ks = KillSwitch(f, memory_only=True)
    assert not ks.is_tripped()
    ks.trip("bt")
    assert f.read_text() == "live ops halt"

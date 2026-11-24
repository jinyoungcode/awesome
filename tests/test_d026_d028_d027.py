from datetime import datetime, timedelta

import pytest

from stockking import config
from stockking.audit import Journal
from stockking.config import TradingMode
from stockking.domain import AccountSnapshot, OrderRequest, OrderType, Position, Side
from stockking.ops import tech_orders
from stockking.ops.paper_report import build_report
from stockking.ops.recovery import RecoveryReport
from stockking.risk import KillSwitch, RiskGate
from stockking.risk.stages import CapitalStage, limits_for, resolve_stage, size_multiplier


def snap(pos_qty=0, cash=1_000_000, sod=None, principal=None):
    pos = {"005930": Position("005930", pos_qty, 70_000)} if pos_qty else {}
    s = AccountSnapshot(cash, pos, {"005930": 70_000}, 0)
    return AccountSnapshot(cash, pos, {"005930": 70_000}, sod or s.equity, principal=principal)


def order(side, qty=1):
    return OrderRequest("005930", side, qty, OrderType.LIMIT, 70_000, "t")


# ---------- D-026 ----------
def test_kill_switch_blocks_buy_allows_liquidating_sell(tmp_path):
    ks = KillSwitch(tmp_path / "KS")
    ks.trip("incident")
    gate = RiskGate(kill_switch=ks)
    assert not gate.check(order(Side.BUY), snap(pos_qty=2))
    assert gate.check(order(Side.SELL, 2), snap(pos_qty=2))


def test_kill_switch_still_blocks_short_sell(tmp_path):
    ks = KillSwitch(tmp_path / "KS")
    ks.trip("incident")
    d = RiskGate(kill_switch=ks).check(order(Side.SELL, 3), snap(pos_qty=2))
    assert not d and any("NO_SHORT" in r for r in d.reasons)


def test_daily_loss_trips_but_sell_allowed():
    gate = RiskGate(limits_for(CapitalStage.T0))
    s = snap(pos_qty=1, cash=860_000, sod=1_000_000)  # 평가 930,000 = -7%
    assert gate.check(order(Side.SELL), s)
    assert gate.kill_switch.is_tripped()
    assert not gate.check(order(Side.BUY), s)


# ---------- D-028 ----------
def test_stage_limits():
    t0, t1 = limits_for(CapitalStage.T0), limits_for(CapitalStage.T1)
    assert (t0.max_daily_loss_pct, t1.max_daily_loss_pct) == (0.05, 0.03)
    assert t0.max_weekly_loss_pct == 0.08 and t0.max_monthly_loss_pct is None and t0.final_stop_pct == 0.20
    assert limits_for(CapitalStage.PAPER).max_daily_loss_pct == 0.05
    assert resolve_stage("T1") is CapitalStage.T1
    with pytest.raises(ValueError):
        resolve_stage("t9")


def test_t0_allows_4pct_daily_loss_t1_blocks():
    s = snap(cash=960_000, sod=1_000_000)  # -4%
    assert RiskGate(limits_for(CapitalStage.T0)).check(order(Side.BUY), s)
    assert not RiskGate(limits_for(CapitalStage.T1)).check(order(Side.BUY), s)


def test_final_stop_from_principal():
    gate = RiskGate(limits_for(CapitalStage.T0))
    s = snap(pos_qty=1, cash=9_000, sod=79_000, principal=100_000)  # 평가 79,000 = 원금 -21%
    d = gate.check(order(Side.BUY), s)
    assert not d and "FINAL_STOP" in d.reasons[0] and gate.kill_switch.is_tripped()
    assert gate.check(order(Side.SELL), s)  # 전량 청산 매도는 허용


def test_size_multiplier():
    assert size_multiplier(85_000, 100_000) == 0.5
    assert size_multiplier(86_000, 100_000) == 1.0
    assert size_multiplier(50_000, None) == 1.0


# ---------- D-027 ----------
class FakeKis:
    is_paper = True
    _base = "https://openapivts.koreainvestment.com:29443"

    def __init__(self, price=15_000, positions=None):
        self.price, self.positions, self.sent, self.canceled = price, positions or {}, [], []
        self.last_response = {}

    def inquire_price(self, s):
        return {"price": self.price, "base_price": self.price}

    def inquire_balance(self):
        return {"cash": 1_000_000, "total_equity": 1_000_000, "positions": self.positions}

    def place_order(self, o):
        from stockking.broker import BrokerOrderResult
        self.sent.append(o)
        self.last_response = {"output": {"KRX_FWDG_ORD_ORGNO": "06010", "ODNO": f"{len(self.sent):07d}"}}
        return BrokerOrderResult(True, o.client_order_id, f"{len(self.sent):07d}", "ok")

    def cancel_order(self, odno, symbol, qty, krx_org_no=None):
        from stockking.broker import BrokerOrderResult
        self.canceled.append((odno, krx_org_no))
        return BrokerOrderResult(True, "", odno, "취소")


def test_tech_orders_refuse_non_paper_mode():
    for mode in (TradingMode.LIVE, TradingMode.BACKTEST):
        with pytest.raises(tech_orders.TechOrdersForbidden):
            tech_orders.run_scenario("T1", FakeKis(), Journal(), "000000", "op", [], mode=mode)


def test_tech_orders_refuse_even_if_live_approved(monkeypatch):
    monkeypatch.setattr(config, "LIVE_TRADING_APPROVED", True)
    with pytest.raises(tech_orders.TechOrdersForbidden):
        tech_orders.run_scenario("T1", FakeKis(), Journal(), "000000", "op", [], mode=TradingMode.LIVE)


def test_tech_orders_refuse_live_broker():
    k = FakeKis()
    k.is_paper = False
    with pytest.raises(tech_orders.TechOrdersForbidden):
        tech_orders.assert_paper_only(TradingMode.PAPER, k)
    k2 = FakeKis()
    k2._base = "https://openapi.koreainvestment.com:9443"
    with pytest.raises(tech_orders.TechOrdersForbidden):
        tech_orders.assert_paper_only(TradingMode.PAPER, k2)


def test_tech_orders_refuse_live_env(monkeypatch):
    monkeypatch.setenv("STOCKKING_MODE", "live")
    with pytest.raises(config.LiveTradingNotApproved):
        tech_orders.run_scenario("T1", FakeKis(), Journal(), "000000", "op", [])


def test_t1_routes_through_gate_and_tags_t_tech():
    k, j = FakeKis(), Journal()
    msg = tech_orders.run_scenario("T1", k, j, "000000", "대표", [], mode=TradingMode.PAPER)
    assert "submitted" in msg and k.sent[0].strategy_id == "T_TECH"
    ev = [r["event"] for r in j.records]
    assert ev[:3] == ["order_request", "risk_decision", "broker_response"]
    assert j.records[-1]["reason_code"] == "T_TECH" and j.records[-1]["krx_org_no"] == "06010"


def test_t1_price_limit_and_kill_switch_blocks(tmp_path, monkeypatch):
    with pytest.raises(ValueError):
        tech_orders.run_scenario("T1", FakeKis(price=25_000), Journal(), "000000", "op", [], mode=TradingMode.PAPER)
    (tmp_path / "KILL_SWITCH").write_text("x")  # conftest 가 킬스위치 경로를 tmp 로 돌림
    k = FakeKis()
    msg = tech_orders.run_scenario("T1", k, Journal(), "000000", "op", [], mode=TradingMode.PAPER)
    assert "rejected_by_risk" in msg and not k.sent  # 관문 우회 없음


def test_t2_requires_t1_wait_and_position():
    now = datetime(2026, 11, 27, 10, 0)
    rec = [{"event": "tech_order", "scenario": "T1", "symbol": "000000", "accepted": True,
            "ts": (now - timedelta(minutes=10)).isoformat()}]
    with pytest.raises(RuntimeError, match="30분"):
        tech_orders.run_scenario("T2", FakeKis(positions={"000000": (1, 15000.0)}), Journal(), "000000", "op",
                                 rec, now=now, mode=TradingMode.PAPER)
    rec[0]["ts"] = (now - timedelta(minutes=31)).isoformat()
    with pytest.raises(RuntimeError, match="보유분"):
        tech_orders.run_scenario("T2", FakeKis(), Journal(), "000000", "op", rec, now=now, mode=TradingMode.PAPER)
    k = FakeKis(positions={"000000": (1, 15000.0)})
    msg = tech_orders.run_scenario("T2", k, Journal(), "000000", "op", rec, now=now, mode=TradingMode.PAPER)
    assert "submitted" in msg and k.sent[0].order_type is OrderType.MARKET and k.sent[0].side is Side.SELL


def test_t3_and_cancel_time_rule():
    k, j = FakeKis(price=70_000), Journal()
    tech_orders.run_scenario("T3", k, j, "005930", "op", [], mode=TradingMode.PAPER)
    assert k.sent[0].limit_price == 63_000
    with pytest.raises(RuntimeError, match="15:15"):
        tech_orders.run_scenario("T3-cancel", k, j, None, "op", j.records,
                                 now=datetime(2026, 11, 27, 15, 0), mode=TradingMode.PAPER)
    tech_orders.run_scenario("T3-cancel", k, j, None, "op", j.records,
                             now=datetime(2026, 11, 27, 15, 16), mode=TradingMode.PAPER)
    assert k.canceled == [("0000001", "06010")]


def test_t4_t5_pending():
    for s in ("T4", "T5"):
        with pytest.raises(NotImplementedError):
            tech_orders.run_scenario(s, FakeKis(), Journal(), "AAPL", "op", [], mode=TradingMode.PAPER)


def test_trigger_status():
    def req(cid, sid, side):
        return [{"event": "order_request", "order": {"client_order_id": cid, "strategy_id": sid, "side": side}},
                {"event": "broker_response", "client_order_id": cid, "accepted": True}]
    st = tech_orders.trigger_status(req("a", "S02", "buy") + req("b", "S02", "buy"))
    assert st["run_tech_orders"] and "S02 주문 2건 < 3" in st["reasons"] and "청산(매도) 경로 미실행" in st["reasons"]
    st2 = tech_orders.trigger_status(req("a", "S02", "buy") + req("b", "S02", "buy") + req("c", "S02_v1", "sell"))
    assert not st2["run_tech_orders"]


def test_paper_report_counts():
    recs = [{"event": "order_request", "order": {"client_order_id": c, "strategy_id": s}}
            for c, s in [("a", "S02"), ("b", "T_TECH"), ("c", "connect_check")]]
    recs.append({"event": "drill", "kind": "kill_switch", "passed": True})
    rep = RecoveryReport(accepted={"a": "1", "b": "2", "c": "3"})
    rows = dict((n, (st, why)) for n, st, why in build_report(recs, [], rep))
    assert rows["C4 킬스위치 1회 훈련"][0] == "PASS"
    assert rows["C6 모의 주문 5건 이상"][0] == "PENDING" and "S02 1건 + T_TECH 1건" in rows["C6 모의 주문 5건 이상"][1]


# ---------- D-031/D-032 ----------
def test_operating_capital_and_warning(monkeypatch):
    from stockking.risk.stages import STARTING_CAPITAL, capital_warning, operating_capital
    monkeypatch.delenv("STOCKKING_OPERATING_CAPITAL", raising=False)
    assert STARTING_CAPITAL == operating_capital() == 150_000
    assert operating_capital() // 5 == 30_000
    assert capital_warning(150_000) is None and capital_warning(170_000) is None
    assert "운용 기준" in capital_warning(100_000_000)
    monkeypatch.setenv("STOCKKING_OPERATING_CAPITAL", "0")
    with pytest.raises(ValueError):
        operating_capital()

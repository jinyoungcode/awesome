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
    (tmp_path / "KILL_SWITCH").write_text("x", encoding="utf-8")  # conftest 가 킬스위치 경로를 tmp 로 돌림
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
    k, j = FakeKis(price=30_000), Journal()
    tech_orders.run_scenario("T3", k, j, "005930", "op", [], mode=TradingMode.PAPER)
    assert k.sent[0].limit_price == 27_000
    with pytest.raises(RuntimeError, match="15:15"):
        tech_orders.run_scenario("T3-cancel", k, j, None, "op", j.records,
                                 now=datetime(2026, 11, 27, 15, 0), mode=TradingMode.PAPER)
    tech_orders.run_scenario("T3-cancel", k, j, None, "op", j.records,
                             now=datetime(2026, 11, 27, 15, 16), mode=TradingMode.PAPER)
    assert k.canceled == [("0000001", "06010")]


def test_t4_pending_t5_through_gate():
    with pytest.raises(NotImplementedError):
        tech_orders.run_scenario("T4", FakeKis(), Journal(), "AAPL", "op", [], mode=TradingMode.PAPER)
    # 15만 원: 목표 30,000 → T5 '목표 초과' 1주 35,000원(23.3% ≤ 30.1%, 손실 2,800 ≤ 3,000) 통과
    k = FakeKis(price=35_000)
    msg = tech_orders.run_scenario("T5", k, Journal(), "000500", "op", [], mode=TradingMode.PAPER)
    assert "submitted" in msg and k.sent[0].sizing_exception == "ONE_SHARE_OVER" and k.sent[0].quantity == 1
    # 목표 범위 안 가격은 예외 불필요 → 관문 거부
    k2 = FakeKis(price=30_000)
    assert "rejected_by_risk" in tech_orders.run_scenario("T5", k2, Journal(), "000500", "op", [],
                                                          mode=TradingMode.PAPER) and not k2.sent

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
def test_operating_capital_and_capital_error(monkeypatch):
    from stockking.risk.stages import STARTING_CAPITAL, capital_error, operating_capital
    monkeypatch.delenv("STOCKKING_OPERATING_CAPITAL", raising=False)
    monkeypatch.delenv("STOCKKING_CAPITAL_STAGE", raising=False)
    assert STARTING_CAPITAL == operating_capital() == 150_000            # 미설정 기본 15만 원
    # D-044: 운용 금액 ≤ 예수금이면 정상(경고 없음), 초과면 오류
    assert capital_error(100_000_000) is None and capital_error(150_000) is None
    assert "예수금" in capital_error(149_999)
    monkeypatch.setenv("STOCKKING_OPERATING_CAPITAL", "1,000,000")       # 모의: 임의 금액
    assert operating_capital() == 1_000_000 and capital_error(5_000_000) is None
    assert capital_error(999_999)
    monkeypatch.setenv("STOCKKING_CAPITAL_STAGE", "t0")                   # T0 실전은 15만 원 고정
    with pytest.raises(ValueError, match="15만"):
        operating_capital()
    monkeypatch.setenv("STOCKKING_CAPITAL_STAGE", "paper")
    monkeypatch.setenv("STOCKKING_OPERATING_CAPITAL", "0")
    with pytest.raises(ValueError):
        operating_capital()


def test_auto_capital_fixed_once_and_paper_only(tmp_path, monkeypatch):
    from stockking.risk.stages import fix_auto_capital, operating_capital
    f = tmp_path / "operating_capital.json"
    monkeypatch.setenv("STOCKKING_AUTO_CAPITAL_FILE", str(f))
    monkeypatch.setenv("STOCKKING_OPERATING_CAPITAL", "auto")
    monkeypatch.delenv("STOCKKING_CAPITAL_STAGE", raising=False)
    with pytest.raises(RuntimeError, match="connect_check"):
        operating_capital()                                      # 고정 전
    assert fix_auto_capital(9_876_543, today="2026-11-26") == 9_876_543
    assert fix_auto_capital(1_000_000) == 9_876_543              # 이후 날짜에도 처음 값 유지
    assert operating_capital() == 9_876_543
    monkeypatch.setenv("STOCKKING_CAPITAL_STAGE", "t0")
    with pytest.raises(ValueError, match="모의투자 전용"):
        operating_capital()
    with pytest.raises(ValueError, match="모의투자 전용"):
        fix_auto_capital(1)


def test_kr_v024_cost_model():
    from stockking.backtest.costs import CostModel
    c = CostModel.kr_v024()
    assert c.commission(30_000) == 45 and c.sell_tax(30_000) == 60 and c.source == "COST_MODEL v0.2.4"


def test_ledger_open_fixes_auto_capital(tmp_path, monkeypatch):
    """1일차 장부 시작점(08:25)이 연결 점검(08:35)보다 먼저여도 auto 가 고정되고 원금이 기록된다."""
    import json
    from stockking.ops import ledger_close
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("STOCKKING_OPERATING_CAPITAL", "auto")
    monkeypatch.setenv("STOCKKING_AUTO_CAPITAL_FILE", str(tmp_path / "cap.json"))
    monkeypatch.delenv("STOCKKING_CAPITAL_STAGE", raising=False)

    class K:
        def inquire_balance(self):
            return {"cash": 5_000_000, "cash_d2": 5_000_000, "total_equity": 5_000_000, "positions": {}, "details": {}}
    monkeypatch.setattr(ledger_close, "make_client", lambda a: K())
    assert ledger_close.main(["--open", "--date", "20261126"]) == 0
    st = json.loads((tmp_path / "var/ledger/paper/2026/open_state_20261126.json").read_text(encoding="utf-8"))
    assert st["principal"] == 5_000_000
    assert json.loads((tmp_path / "cap.json").read_text(encoding="utf-8"))["capital"] == 5_000_000


def test_t1_qty_from_target_and_dry_run_sends_nothing(tmp_path):
    cand = tmp_path / "tech_candidates.csv"
    cand.write_text("role,rank,symbol,market,price,adv20\nT1,1,000660,KOSPI,15000,1\nT1,2,035420,KOSPI,9800,1\n"
                    "T5,1,000500,KOSPI,35000,1\n", encoding="utf-8")
    c = tech_orders.load_candidates(cand)
    assert tech_orders.pick_symbol("T1", c) == "000660" and tech_orders.pick_symbol("T3", c) == "035420"
    assert tech_orders.pick_symbol("T5", c) == "000500" and tech_orders.pick_symbol("T1", c, rank=3) is None
    k, j = FakeKis(price=15_000), Journal()
    msg = tech_orders.run_scenario("T1", k, j, "000660", "op", [], mode=TradingMode.PAPER, dry_run=True)
    assert "[dry-run]" in msg and "2주" in msg and "관문 통과" in msg and not k.sent
    msg2 = tech_orders.run_scenario("T1", k, j, "000660", "op", [], mode=TradingMode.PAPER)
    assert "submitted" in msg2 and k.sent[0].quantity == 2 and k.sent[0].order_target == 30_000


def test_status_lines():
    recs = [{"event": "tech_order", "scenario": "T1", "symbol": "000660", "qty": 2, "accepted": True,
             "broker_order_id": "0000000001"}]
    rows = [{"odno": "1", "filled_qty": 2, "ord_qty": 2, "canceled": False}]
    assert "체결 2/2주" in tech_orders.status_lines(recs, rows)[0]


def test_dry_run_paper_only():
    with pytest.raises(tech_orders.TechOrdersForbidden):
        tech_orders.run_scenario("T1", FakeKis(), Journal(), "000660", "op", [], mode=TradingMode.LIVE, dry_run=True)


def test_t3_cancel_cancels_all_open_t3():
    from datetime import datetime as dt
    k, j = FakeKis(price=30_000), Journal()
    tech_orders.run_scenario("T3", k, j, "000660", "op", [], mode=TradingMode.PAPER)
    tech_orders.run_scenario("T3", k, j, "035420", "op", j.records, mode=TradingMode.PAPER)
    msg = tech_orders.run_scenario("T3-cancel", k, j, None, "op", j.records, now=dt(2026, 11, 30, 15, 16),
                                   mode=TradingMode.PAPER)
    assert len(k.canceled) == 2 and "000660" in msg and "035420" in msg
    with pytest.raises(RuntimeError, match="취소할 T3"):
        tech_orders.run_scenario("T3-cancel", k, j, None, "op", j.records, now=dt(2026, 11, 30, 15, 17),
                                 mode=TradingMode.PAPER)


def test_t1_cancel_by_program_no_time_rule():
    """미체결 T1 은 KIS 앱이 아니라 --run T1-cancel 로 취소 (시간 제한 없음, 이미 취소한 건 재취소 안 함)."""
    from datetime import datetime as dt
    k, j = FakeKis(price=30_000), Journal()
    tech_orders.run_scenario("T1", k, j, "000660", "op", [], mode=TradingMode.PAPER)
    assert "[dry-run]" in tech_orders.run_scenario("T1-cancel", k, j, None, "op", j.records, dry_run=True,
                                                   now=dt(2026, 11, 30, 9, 30), mode=TradingMode.PAPER)
    assert not k.canceled
    msg = tech_orders.run_scenario("T1-cancel", k, j, None, "op", j.records, now=dt(2026, 11, 30, 9, 30),
                                   mode=TradingMode.PAPER)
    assert len(k.canceled) == 1 and "000660" in msg
    with pytest.raises(RuntimeError, match="취소할 T1"):
        tech_orders.run_scenario("T1-cancel", k, j, None, "op", j.records, mode=TradingMode.PAPER)
    with pytest.raises(tech_orders.TechOrdersForbidden):
        tech_orders.run_scenario("T1-cancel", k, j, None, "op", j.records, mode=TradingMode.LIVE)
    assert len(tech_orders.status_lines(j.records, [])) == 1   # 취소 기록은 상태 목록에 안 섞임


def test_t5_skips_out_of_band_candidates():
    c = {"T5": [{"symbol": "000001", "rank": "1", "in_band": "False"}, {"symbol": "000002", "rank": "2", "in_band": "True"}],
         "T1": [{"symbol": "000010"}, {"symbol": "000020"}, {"symbol": "000030"}, {"symbol": "000040"}]}
    assert tech_orders.pick_symbol("T5", c) == "000002"
    assert tech_orders.pick_symbol("T5", c, rank=2) is None
    assert tech_orders.pick_symbol("T5", {"T5": [{"symbol": "000001", "in_band": "false"}]}) is None
    # T3 --rank N → T1 후보 N+1 순위 (T3'=--rank 2, T3''=--rank 3)
    assert [tech_orders.pick_symbol("T3", c, rank=n) for n in (1, 2, 3)] == ["000020", "000030", "000040"]

from datetime import date, datetime, timedelta

import pytest

from stockking.audit import Journal
from stockking.backtest.costs import CostModel
from stockking.broker import BrokerOrderResult
from stockking.config import TradingMode
from stockking.data import Bar
from stockking.ops import s02_runner as R
from stockking.ops.paper_report import build_report
from stockking.ops.recovery import RecoveryReport
from stockking.ops.tech_orders import TechOrdersForbidden

SD, ED = date(2025, 9, 1), date(2025, 9, 2)
COSTS = CostModel(0.00018, 0.002, 0, source="TEST")


def series(sym, closes, start=SD - timedelta(days=300)):
    return [Bar(start + timedelta(days=i), sym, c, c, c, c, 100_000) for i, c in enumerate(closes)][-len(closes):]


def pullback(sym, last):
    up = [last - 4_000 + 15 * i for i in range(299)]
    return series(sym, up[:-2] + [up[-3] - 100, last])  # 이틀 하락 → 과매도, 장기 상승추세 유지


def fixture():
    bars = pullback("000100", 18_450) + pullback("000200", 28_500)
    idx = series("KOSPI200", [300 + i for i in range(301)])          # 국면 ON
    markets = {"000100": "KOSPI", "000200": "KOSPI"}
    return bars, {"000100", "000200", "AAPL"}, markets, idx


def state():
    return {"starting_cash": 150_000, "cash": 150_000, "realized_pnl": 0, "positions": {}, "last_exits": {},
            "peak_equity": 150_000}


class FakeKis:
    is_paper = True
    _base = "https://openapivts.koreainvestment.com:29443"

    def __init__(self):
        self.sent, self.canceled, self.last_response = [], [], {}

    def place_order(self, o):
        self.sent.append(o)
        n = f"{len(self.sent):07d}"
        self.last_response = {"output": {"KRX_FWDG_ORD_ORGNO": "06010", "ODNO": n}}
        return BrokerOrderResult(True, o.client_order_id, n, "ok")

    def cancel_order(self, odno, symbol, qty, krx_org_no=None):
        self.canceled.append((odno, krx_org_no))
        return BrokerOrderResult(True, "", odno, "취소")

    def inquire_daily_orders(self, day):
        return self.rows


def plan_rows():
    rows = R.make_plan(state(), *fixture(), SD, ED)
    return rows


def test_plan_uses_d005_sizer_and_kr_only():
    rows = plan_rows()
    by = {r.ticker: r for r in rows}
    assert set(by) == {"000100", "000200"}                          # AAPL 제외(D-033)
    assert (by["000200"].qty, by["000200"].exception) == (1, "")     # 28,500원 정수 1주
    assert (by["000100"].qty, by["000100"].exception) == (1, "ONE_SHARE_UNDER")


def test_regime_off_no_entries():
    bars, uni, mk, _ = fixture()
    idx = R.make_plan(state(), bars, uni, mk, series("KOSPI200", [600 - i for i in range(301)]), SD, ED)
    assert idx == []


def _csv_rows(rows):
    from dataclasses import asdict
    return [{k: ("" if v is None else str(v)) for k, v in asdict(r).items()} for r in rows]


def test_submit_cancel_sync_verify_flow():
    rows, st, kis, j = plan_rows(), state(), FakeKis(), Journal()
    prices = {"000100": 18_450, "000200": 28_500}
    res = R.submit_plan(_csv_rows(rows), st, kis, j, prices, mode=TradingMode.PAPER)
    assert [r["status"] for r in res] == ["submitted", "submitted"]
    assert all(o.stop_price and o.order_target == 30_000 for o in kis.sent)
    # C2: 같은 입력으로 다시 만든 계획 = 제출 기록
    rep = R.compare_signals(plan_rows(), R.submitted(j.records, SD.isoformat()))
    assert rep["match"]
    # 15:15 취소: 000100 미체결, 000200 체결
    odno = {r["ticker"]: r["broker_order_id"] for r in res}
    kis.rows = [{"odno": odno["000100"], "filled_qty": 0, "ord_qty": 1, "canceled": False, "avg_price": 0},
                {"odno": odno["000200"], "filled_qty": 1, "ord_qty": 1, "canceled": False, "avg_price": 28_400.0}]
    with pytest.raises(RuntimeError):
        R.cancel_open_entries(j.records, kis, j, "20250902", now=datetime(2025, 9, 2, 15, 0), mode=TradingMode.PAPER)
    assert R.cancel_open_entries(j.records, kis, j, "20250902", now=datetime(2025, 9, 2, 15, 16),
                                 mode=TradingMode.PAPER) == ["000100"]
    assert kis.canceled == [(odno["000100"], "06010")]
    # 장 마감 후 가상 장부 반영 (두 번 실행해도 한 번만)
    for _ in range(2):
        st = R.sync_fills(st, j.records, kis.rows, ED, COSTS)
    assert st["positions"]["000200"]["qty"] == 1 and st["positions"]["000200"]["holding_days"] == 0
    assert st["positions"]["000200"]["init_stop"] == pytest.approx(28_400 * 0.92)
    assert st["cash"] == 150_000 - 28_400 - COSTS.commission(28_400)


def test_signal_mismatch_detected():
    rep = R.compare_signals(plan_rows(), [{"ticker": "000200", "side": "BUY", "qty": 2, "limit_price": "28500"}])
    assert not rep["match"] and rep["extra"] and rep["missing"]


def test_runner_paper_only():
    with pytest.raises(TechOrdersForbidden):
        R.submit_plan([], state(), FakeKis(), Journal(), {}, mode=TradingMode.LIVE)
    k = FakeKis()
    k.is_paper = False
    with pytest.raises(TechOrdersForbidden):
        R.submit_plan([], state(), k, Journal(), {}, mode=TradingMode.PAPER)


def test_sell_sync_realizes_pnl_and_marks_exit():
    st = state()
    st["positions"] = {"000200": {"qty": 1, "entry_price": 28_000, "entry_date": "2025-09-01",
                                  "init_stop": 25_760, "holding_days": 2, "exception": ""}}
    st["cash"] = 122_000
    recs = [{"event": "s02_submit", "client_order_id": "x", "ticker": "000200", "side": "SELL", "order_type": "MOO",
             "qty": 1, "limit_price": None, "broker_order_id": "0000009", "signal_date": "2025-09-01"}]
    st = R.sync_fills(st, recs, [{"odno": "9", "filled_qty": 1, "avg_price": 29_000.0}], ED, COSTS)
    assert "000200" not in st["positions"] and st["last_exits"]["000200"] == ED.isoformat()
    comm, tax = COSTS.commission(29_000), COSTS.sell_charges(29_000, 1)
    assert st["realized_pnl"] == 1_000 - comm - tax and st["cash"] == 122_000 + 29_000 - comm - tax


def test_state_atomic_save(tmp_path):
    R.save_state(state(), tmp_path / "s.json")
    assert R.load_state(tmp_path / "s.json")["cash"] == 150_000


def test_paper_report_c2():
    rep = RecoveryReport()
    rows = dict((n, s) for n, s, _ in build_report([], [], rep, [], [{"date": "d", "match": True}]))
    assert rows["C2 신호 일치 100%"] == "PASS"

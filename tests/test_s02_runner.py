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
                                  "init_stop": 25_760, "holding_days": 2, "exception": "", "buy_comm": 5}}
    st["cash"] = 122_000 - 5
    recs = [{"event": "s02_submit", "client_order_id": "x", "ticker": "000200", "side": "SELL", "order_type": "MOO",
             "qty": 1, "limit_price": None, "broker_order_id": "0000009", "signal_date": "2025-09-01"}]
    st = R.sync_fills(st, recs, [{"odno": "9", "filled_qty": 1, "avg_price": 29_000.0}], ED, COSTS)
    assert "000200" not in st["positions"] and st["last_exits"]["000200"] == ED.isoformat()
    comm, tax = COSTS.commission(29_000), COSTS.sell_charges(29_000, 1)
    # I1: 실현 순손익 = 매도대금 − 매수대금 − 매수·매도 수수료 − 매도세 = 현금 증감과 일치
    assert st["realized_pnl"] == 1_000 - comm - tax - 5 and st["cash"] == 122_000 - 5 + 29_000 - comm - tax
    assert st["cash"] - 150_000 == st["realized_pnl"] - (150_000 - 122_000 - 28_000)


def test_state_atomic_save(tmp_path):
    R.save_state(state(), tmp_path / "s.json")
    assert R.load_state(tmp_path / "s.json")["cash"] == 150_000


def test_paper_report_c2():
    rep = RecoveryReport()
    rows = dict((n, s) for n, s, _ in build_report([], [], rep, [], [{"date": "d", "match": True}]))
    assert rows["C2 신호 일치 100%"] == "PASS"


def test_runner_sell_modes(monkeypatch):
    st = state()
    st["positions"] = {"000200": {"qty": 1, "entry_price": 28_000, "entry_date": "2025-09-01",
                                  "init_stop": 25_760, "holding_days": 5, "exception": ""}}
    row = [{"signal_date": "2025-09-01", "ticker": "000200", "side": "SELL", "order_type": "MOO", "qty": "1",
            "limit_price": "", "init_stop": "", "reason_code": "X_TIME", "exception": ""}]
    k = FakeKis()
    R.submit_plan(row, st, k, Journal(), {"000200": 28_500}, mode=TradingMode.PAPER)
    assert k.sent[0].order_type.value == "market" and k.sent[0].exit_reason == "X_TIME"
    monkeypatch.setenv("STOCKKING_KR_SELL_MODE", "marketable_limit")
    k2 = FakeKis()
    R.submit_plan(row, st, k2, Journal(), {"000200": 28_500}, mode=TradingMode.PAPER)
    assert k2.sent[0].order_type.value == "limit" and k2.sent[0].limit_price == 27_950


# ---------- Codex 검토 #1: 미체결 매수의 현금·슬롯 예약 ----------
def _buy_rows(n, price=32_000, start=0):
    return [{"signal_date": "2025-09-01", "ticker": f"00{start + i + 1:04d}", "side": "BUY", "order_type": "LDAY",
             "qty": "1", "limit_price": str(price), "init_stop": str(price * 0.92), "reason_code": "E_S02_PB",
             "exception": ""} for i in range(n)]


def test_multiple_buys_reserve_cash_in_same_run():
    rows, kis, j = _buy_rows(5), FakeKis(), Journal()
    prices = {r["ticker"]: 32_000 for r in rows}
    res = R.submit_plan(rows, state(), kis, j, prices, mode=TradingMode.PAPER)
    # 32,000원(+수수료 5원) × 4건 = 128,020원 예약 → 5번째는 남은 현금 21,980원으로 부족
    assert [r["status"] for r in res] == ["submitted"] * 4 + ["rejected_by_risk"]
    assert "INSUFFICIENT_CASH" in res[-1]["message"] and len(kis.sent) == 4


def test_restart_reserves_existing_open_orders_and_skips_resubmit():
    rows, j = _buy_rows(4), Journal()
    prices = {f"00{i:04d}": 32_000 for i in range(1, 7)}
    R.submit_plan(rows, state(), FakeKis(), j, prices, mode=TradingMode.PAPER)
    # 재시작: 같은 계획 + 새 행 1건. 기존 4건은 재제출하지 않고, 미체결로 예약된 현금 때문에 새 행은 거부
    kis2 = FakeKis()
    res = R.submit_plan(rows + _buy_rows(1, start=5), state(), kis2, j, prices, mode=TradingMode.PAPER,
                        records=j.records)
    assert [r["ticker"] for r in res] == ["000006"] and res[0]["status"] == "rejected_by_risk"
    assert not kis2.sent
    # 기존 미체결이 취소되면 예약이 풀려 같은 새 행이 통과
    for s in R.submitted(j.records):
        j.write("s02_cancel", client_order_id=s["client_order_id"], accepted=True)
    res2 = R.submit_plan(_buy_rows(1, start=5), state(), FakeKis(), Journal(), prices, mode=TradingMode.PAPER,
                         records=j.records)
    assert res2[0]["status"] == "submitted"


# ---------- Codex 검토 #2: 부분체결 누적 반영 ----------
def test_partial_fill_then_more_then_same_response():
    recs = [{"event": "s02_submit", "client_order_id": "c1", "ticker": "000100", "side": "BUY", "order_type": "LDAY",
             "qty": 2, "limit_price": "15100", "status": "submitted", "broker_order_id": "0000001",
             "signal_date": "2025-09-01", "exception": ""}]
    st = state()
    st = R.sync_fills(st, recs, [{"odno": "1", "filled_qty": 1, "avg_price": 15_000.0}], ED, COSTS)
    assert st["positions"]["000100"]["qty"] == 1 and st["cash"] == 150_000 - 15_000 - COSTS.commission(15_000)
    second = [{"odno": "1", "filled_qty": 2, "avg_price": 15_050.0}]      # 두 번째 1주 15,100원
    st = R.sync_fills(st, recs, second, ED, COSTS)
    st = R.sync_fills(st, recs, second, ED, COSTS)                         # 같은 응답 재조회 → 변화 없음
    pos = st["positions"]["000100"]
    assert pos["qty"] == 2 and pos["entry_price"] == 15_050
    assert st["cash"] == 150_000 - 30_100 - COSTS.commission(30_100)
    assert st["applied"]["c1"]["qty"] == 2
    # 부분 매도 → 추가 매도도 같은 방식
    recs.append({"event": "s02_submit", "client_order_id": "c2", "ticker": "000100", "side": "SELL",
                 "order_type": "MOO", "qty": 2, "limit_price": None, "status": "submitted",
                 "broker_order_id": "0000002", "signal_date": "2025-09-02"})
    cash_before = st["cash"]
    st = R.sync_fills(st, recs, second + [{"odno": "2", "filled_qty": 1, "avg_price": 16_000.0}], ED, COSTS)
    st = R.sync_fills(st, recs, second + [{"odno": "2", "filled_qty": 2, "avg_price": 16_000.0}], ED, COSTS)
    assert "000100" not in st["positions"]
    assert st["cash"] == cash_before + 32_000 - COSTS.commission(32_000) - COSTS.sell_charges(32_000, 2)


def test_boundary_110pct_orders_pass_with_221_tolerance():
    """리스크관리실 결정: 비중 22%에 0.1%p 허용오차. 목표 110%(33,000원) 주문이 앞선 수수료로 22.0x%가 돼도 통과,
    4건 후에는 현금 부족으로 5번째 거부."""
    rows = _buy_rows(5, price=33_000)
    res = R.submit_plan(rows, state(), FakeKis(), Journal(), {r["ticker"]: 33_000 for r in rows}, mode=TradingMode.PAPER)
    assert [r["status"] for r in res] == ["submitted"] * 4 + ["rejected_by_risk"]
    assert "INSUFFICIENT_CASH" in res[-1]["message"]


def test_i1_realized_includes_buy_commission_round_trip():
    recs = [{"event": "s02_submit", "client_order_id": "b", "ticker": "000100", "side": "BUY", "order_type": "LDAY",
             "qty": 1, "limit_price": "30000", "status": "submitted", "broker_order_id": "0000001",
             "signal_date": "2025-09-01", "exception": ""},
            {"event": "s02_submit", "client_order_id": "s", "ticker": "000100", "side": "SELL", "order_type": "MOO",
             "qty": 1, "limit_price": None, "status": "submitted", "broker_order_id": "0000002",
             "signal_date": "2025-09-02"}]
    st = R.sync_fills(state(), recs, [{"odno": "1", "filled_qty": 1, "avg_price": 30_000.0}], SD, COSTS)
    st = R.sync_fills(st, recs, [{"odno": "1", "filled_qty": 1, "avg_price": 30_000.0},
                                 {"odno": "2", "filled_qty": 1, "avg_price": 31_000.0}], ED, COSTS)
    # 왕복 후 현금 변화 = 실현 순손익 (매수·매도 수수료, 매도세 모두 포함)
    assert st["cash"] - 150_000 == st["realized_pnl"]


@pytest.mark.parametrize("contents", [
    None,
    "signal_date,exec_date\n2026-11-26,2026-11-26\n",
    "signal_date,exec_date\n2026-11-25,2026-11-27\n",
    "signal_date,exec_date\n2026-11-26,2026-11-27\n2026-11-26,2026-11-28\n",
])
def test_submit_rejects_missing_or_wrong_plan_before_broker(tmp_path, monkeypatch, contents):
    from stockking.ops import kis_session

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 27, 8, 40, tzinfo=tz)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(R, "PLAN_DIR", tmp_path)
    monkeypatch.setattr(R, "datetime", FrozenDateTime)
    monkeypatch.setattr(kis_session, "make_client", lambda args: pytest.fail("broker accessed"))
    if contents is not None:
        (tmp_path / "order_plan_20261126.csv").write_text(contents, encoding="utf-8")
    assert R.main(["submit", "--signal-date", "2026-11-26", "--operator", "test"]) == 2


def test_zero_signal_plan_replaces_prior_csv_and_does_not_connect(tmp_path, monkeypatch):
    from stockking.ops import kis_session

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(R, "PLAN_DIR", tmp_path)
    monkeypatch.setattr(R, "_inputs", lambda args: ())
    monkeypatch.setattr(R, "make_plan", lambda *args: [])
    monkeypatch.setattr(kis_session, "make_client", lambda args: pytest.fail("broker accessed"))
    path = tmp_path / "order_plan_20261126.csv"
    path.write_text("signal_date,exec_date\n2026-11-26,2026-11-27\n", encoding="utf-8")
    assert R.main(["plan", "--signal-date", "2026-11-26", "--exec-date", "2026-11-27"]) == 0
    assert R.read_plan(path) == []
    assert R.main(["submit", "--signal-date", "2026-11-26", "--operator", "test"]) == 0

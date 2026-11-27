from datetime import date, timedelta

import pytest

from stockking.backtest.costs import CostModel
from stockking.data import Bar
from stockking.indicators import ret, rsi_wilder, sma
from stockking.ledger import daily_rows, journal_index, position_rows, reconcile, settle_date, trade_rows
from stockking.ops.paper_report import build_report
from stockking.ops.recovery import RecoveryReport
from stockking.strategy.s02 import HeldPosition, S02Params, build_order_plan, exit_signals, write_order_plan


# ---------- 지표 ----------
def test_sma_and_ret():
    assert sma([1, 2, 3, 4], 2) == 3.5 and sma([1], 2) is None
    assert ret([100, 110], 1) == pytest.approx(0.10) and ret([100], 1) is None


def test_rsi_wilder_rules():
    assert rsi_wilder([100.0] * 60) == 50.0                       # 변화 없음
    assert rsi_wilder([float(i) for i in range(60)]) == 100.0     # 하락 없음
    assert rsi_wilder([100.0] * 10) is None                      # 워밍업 50봉 미만
    xs = [100.0] * 58 + [101.0, 99.0]                            # 마지막: +1 → −2
    # 수동 계산: n=2, 초기 평균(0,0) → +1: ag=.5, al=0 → −2: ag=.25, al=1 → RSI=100-100/1.25=20
    assert rsi_wilder(xs) == pytest.approx(20.0)


# ---------- S02 ----------
def bars(sym, closes, start=date(2025, 1, 1), vol=100_000):
    out, d = [], start
    for c in closes:
        out.append(Bar(d, sym, c, c, c, c, vol))
        d += timedelta(days=1)
    return out


def uptrend_then_pullback(sym, base=10_000):
    up = [base + 20 * i for i in range(240)]          # 200일선 위 상승추세
    last = up[-1]
    return bars(sym, up + [last - 150, last - 300])     # 이틀 연속 하락 → RSI(2) 매우 낮음, 하루 −10% 이내


def test_entry_signal_and_ranking():
    h = {"000100": uptrend_then_pullback("000100"), "000200": uptrend_then_pullback("000200", 15_000),
         "000300": bars("000300", [10_000 - 10 * i for i in range(242)])}  # 하락추세 → 제외
    plan = build_order_plan(date(2025, 8, 30), date(2025, 9, 1), h, set(h), lambda s: True, [], set())
    buys = [r for r in plan if r.side == "BUY"]
    # RSI 동일 → 거래대금(ADV20) 큰 000200 이 1순위
    assert [(r.ticker, r.rank) for r in buys] == [("000200", 1), ("000100", 2)]
    b = buys[0]
    assert b.order_type == "LDAY" and b.reason_code == "E_S02_PB" and b.qty is None
    assert b.limit_price % 10 == 0 and b.init_stop == pytest.approx(b.limit_price * 0.92)


def test_regime_off_and_held_and_reentry_block():
    h = {"000100": uptrend_then_pullback("000100")}
    p = dict(signal_date=date(2025, 8, 30), exec_date=date(2025, 9, 1), history=h, universe=set(h))
    assert not build_order_plan(**p, regime_on=lambda s: False, held=[], exited_prev_day=set())
    assert not build_order_plan(**p, regime_on=lambda s: True, held=[], exited_prev_day={"000100"})
    other = HeldPosition("000100", 1, 0, 0, strategy_id="S01")
    assert not [r for r in build_order_plan(**p, regime_on=lambda s: True, held=[other], exited_prev_day=set())
                if r.side == "BUY"]


def test_exit_rules_order():
    p = S02Params()
    h = {"A": bars("A", [100] * 10 + [90]), "B": bars("B", [100] * 10 + [120]), "C": bars("C", [100] * 11)}
    held = [HeldPosition("A", 1, 92.0, 1), HeldPosition("B", 1, 50.0, 1), HeldPosition("C", 1, 50.0, 5)]
    assert [(x.symbol, c) for x, c in exit_signals(held, h, p)] == [("A", "X_STOP"), ("B", "X_TP"), ("C", "X_TIME")]
    assert exit_signals(held, h, p, forced={"C": "X_KILL"})[-1][1] == "X_KILL"


def test_sizer_skips_unsizable_and_slots():
    h = {s: uptrend_then_pullback(s) for s in ("000100", "000200", "000300", "000400")}
    sizer = lambda sym, lim, stop: (None, "") if sym == "000100" else (1, "ONE_SHARE")  # noqa: E731
    held = [HeldPosition(f"9{i:05d}", 1, 0, 0) for i in range(3)]   # 이미 3종목 → 남은 슬롯 2
    plan = build_order_plan(date(2025, 8, 30), date(2025, 9, 1), h, set(h), lambda s: True, held, set(),
                            sizer=sizer)
    buys = [r for r in plan if r.side == "BUY"]
    assert len(buys) == 2 and "000100" not in {r.ticker for r in buys} and buys[0].exception == "ONE_SHARE"


def test_data_qc_fail_only_exits(tmp_path):
    h = {"A": bars("A", [100] * 10 + [90]), **{"000100": uptrend_then_pullback("000100")}}
    plan = build_order_plan(date(2025, 8, 30), date(2025, 9, 1), h, {"000100"}, lambda s: True,
                            [HeldPosition("A", 2, 92.0, 1)], set(), data_qc_ok=False)
    assert [(r.side, r.order_type, r.reason_code) for r in plan] == [("SELL", "MOO", "X_STOP")]
    out = write_order_plan(plan, tmp_path)
    assert out.name == "order_plan_20250830.csv" and "X_STOP" in out.read_text(encoding="utf-8")


# ---------- 장부 ----------
COSTS = CostModel(0.00018, 0.002, 0, source="TEST")


def test_settle_date_skips_weekend():
    assert settle_date(date(2026, 11, 26)) == date(2026, 11, 30)  # 목 → 월


def _records():
    return [{"event": "order_request", "order": {"client_order_id": "c1", "strategy_id": "S02"}},
            {"event": "broker_response", "client_order_id": "c1", "accepted": True, "broker_order_id": "0000123"},
            {"event": "order_request", "order": {"client_order_id": "c2", "strategy_id": "T_TECH"}},
            {"event": "broker_response", "client_order_id": "c2", "accepted": True, "broker_order_id": "0000124"}]


def test_trade_rows_book_and_reason():
    orders = [{"odno": "123", "symbol": "000100", "side": "buy", "filled_qty": 2, "avg_price": 14_950.0},
              {"odno": "0000124", "symbol": "000200", "side": "sell", "filled_qty": 1, "avg_price": 20_000.0},
              {"odno": "0000999", "symbol": "000300", "side": "buy", "filled_qty": 1, "avg_price": 5_000.0},
              {"odno": "0000777", "symbol": "000400", "side": "buy", "filled_qty": 0, "avg_price": 0.0}]
    rows = trade_rows(date(2026, 11, 26), orders, journal_index(_records()), COSTS, "paper", "KIS-PAPER-01")
    assert [(r["ticker"], r["book"], r["strategy_id"], r["reason_code"]) for r in rows] == [
        ("000100", "AUTO", "S02", ""), ("000200", "AUTO", "T_TECH", "T_TECH"), ("000300", "MANUAL", "", "")]
    buy, sell = rows[0], rows[1]
    assert buy["gross_amount"] == 29_900 and buy["commission"] == 5 and buy["net_amount_krw"] == -29_905
    assert sell["sell_tax"] == 40 and sell["net_amount_krw"] == 20_000 - 3 - 40
    assert buy["settle_date"] == "2026-11-30" and "추정" in buy["note"]


def test_reconcile_ok_and_diff():
    open_state = {"cash_d2": 150_000, "qty": {"000200": 1}, "equity": {"AUTO": 170_000}}
    trades = [{"ticker": "000100", "side": "BUY", "qty": 2, "net_amount_krw": -29_905},
              {"ticker": "000200", "side": "SELL", "qty": 1, "net_amount_krw": 19_957}]
    close = {"cash_d2": 140_052, "total_equity": 170_000,
             "details": {"000100": {"qty": 2, "close_price": 15_000, "market_value": 30_000, "avg_cost": 14_950}}}
    r = reconcile(open_state, trades, close)
    assert r.status == "OK" and r.diff_krw == 0
    close["cash_d2"] = 140_051
    close["details"]["000100"]["qty"] = 3
    r2 = reconcile(open_state, trades, close)
    assert r2.status == "DIFF" and r2.diff_krw == -1 and r2.qty_diffs == {"000100": (2, 3)}

    pos = position_rows(date(2026, 11, 26), close, "paper", "KIS-PAPER-01", set())
    assert pos[0]["book"] == "AUTO" and pos[0]["weight"] == pytest.approx(30_000 / 170_000, rel=1e-5)
    rows = daily_rows(date(2026, 11, 26), "paper", "KIS-PAPER-01", open_state,
                      [{"book": "AUTO", "commission": 8, "sell_tax": 40}], pos, close, r2, None)
    assert len(rows) == 1 and rows[0]["recon_status"] == "DIFF" and rows[0]["start_equity_krw"] == 170_000


def test_paper_report_c1_from_ledger():
    rep = RecoveryReport()
    ok = dict((n, s) for n, s, _ in build_report([], [], rep, [{"date": "1", "recon_status": "OK"}]))
    bad = dict((n, s) for n, s, _ in build_report([], [], rep, [{"date": "1", "recon_status": "DIFF"}]))
    assert ok["C1 장부·잔고 대사 100%"] == "PASS" and bad["C1 장부·잔고 대사 100%"] == "FAIL"

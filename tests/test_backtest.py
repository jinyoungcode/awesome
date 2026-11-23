from datetime import date, timedelta

import pytest

from stockking.backtest import BacktestConfig, BacktestEngine
from stockking.backtest import metrics
from stockking.backtest.costs import CostModel
from stockking.data import Bar
from stockking.domain import Side
from stockking.risk import RiskLimits
from stockking.strategy import SmaCrossStrategy, TargetOrder


def mk_bars(closes, sym="005930", start=date(2025, 1, 2), opens=None):
    out, d = [], start
    for i, c in enumerate(closes):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        o = opens[i] if opens else c
        out.append(Bar(d, sym, o, max(o, c), min(o, c), c, 1000))
        d += timedelta(days=1)
    return out


class Script:
    """지정한 날짜 인덱스에 지정 주문을 내는 테스트 전략."""
    strategy_id = "script"

    def __init__(self, plan):
        self.plan, self.i = plan, -1

    def on_bar_close(self, ctx):
        self.i += 1
        return self.plan.get(self.i, [])


def engine(costs, **lim):
    return BacktestEngine(BacktestConfig(10_000_000, costs, RiskLimits(**lim)))


def test_next_open_execution_no_lookahead(costs):
    bars = mk_bars([10_000, 10_000, 10_000], opens=[10_000, 12_000, 12_000])
    res = engine(costs).run(Script({0: [TargetOrder("005930", Side.BUY, 10)]}), bars)
    fill = res.fills[0]
    assert fill.filled_at == bars[1].date
    assert fill.price == costs.slipped_price(12_000, True)  # D+1 시가 + 슬리피지


def test_costs_applied_round_trip(costs):
    bars = mk_bars([10_000] * 4)
    res = engine(costs).run(Script({0: [TargetOrder("005930", Side.BUY, 100)],
                                    1: [TargetOrder("005930", Side.SELL, 100)]}), bars)
    buy, sell = res.fills
    assert buy.commission == costs.commission(buy.price * 100)
    assert sell.tax == costs.sell_tax(sell.price * 100) > 0
    final = res.equity_curve[-1][1]
    assert final == 10_000_000 - buy.price * 100 - buy.commission + sell.price * 100 - sell.commission - sell.tax
    assert final < 10_000_000 and res.realized_pnls[0] < 0


def test_zero_cost_flat_market_preserves_equity():
    free = CostModel(0, 0, 0, source="TEST")
    bars = mk_bars([10_000] * 5)
    res = BacktestEngine(BacktestConfig(10_000_000, free)).run(
        Script({0: [TargetOrder("005930", Side.BUY, 100)], 2: [TargetOrder("005930", Side.SELL, 100)]}), bars)
    assert all(v == 10_000_000 for _, v in res.equity_curve)


def test_risk_gate_applies_in_backtest(costs):
    bars = mk_bars([10_000] * 3)
    res = engine(costs).run(Script({0: [TargetOrder("005930", Side.BUY, 1000)]}), bars)  # 10M = 100%
    assert not res.fills and res.rejected


def test_suspended_symbol_not_filled(costs):
    bars = mk_bars([10_000] * 3)
    bars[1] = Bar(bars[1].date, "005930", 10_000, 10_000, 10_000, 10_000, 0)
    res = engine(costs).run(Script({0: [TargetOrder("005930", Side.BUY, 10)]}), bars)
    assert not res.fills and "NO_TRADE" in res.rejected[0][2][0]


def test_unconfirmed_cost_model_warns():
    res = BacktestEngine(BacktestConfig(1_000_000, CostModel(0, 0, 0))).run(Script({}), mk_bars([1000] * 3))
    assert res.summary()["warnings"]


def test_sma_strategy_runs_end_to_end(costs):
    import math
    closes = [int(10_000 + 2_000 * math.sin(i / 8)) for i in range(250)]
    res = engine(costs).run(SmaCrossStrategy(5, 20, budget_per_symbol=1_500_000), mk_bars(closes))
    s = res.summary()
    assert s["trades"] > 0 and 0 <= s["mdd"] < 1 and s["total_commission"] > 0


def test_metrics_basic():
    assert metrics.max_drawdown([100, 120, 90, 130]) == pytest.approx(0.25)
    assert metrics.cagr(100, 121, date(2020, 1, 1), date(2022, 1, 1)) == pytest.approx(0.1, abs=1e-3)
    st = metrics.trade_stats([10, -5, 20, -5])
    assert st["win_rate"] == 0.5 and st["profit_factor"] == 3


def test_bar_validation():
    with pytest.raises(ValueError):
        Bar(date(2025, 1, 2), "005930", 100, 90, 80, 95, 10)  # high < open

"""성과 지표. 매도매수팀 EVALUATION.md 기준이 확정되면 정의를 맞춘다."""

from __future__ import annotations

import math
from datetime import date

TRADING_DAYS = 252


def daily_returns(equity: list[float]) -> list[float]:
    return [equity[i] / equity[i - 1] - 1 for i in range(1, len(equity)) if equity[i - 1] > 0]


def cagr(start: float, end: float, first: date, last: date) -> float:
    years = (last - first).days / 365.25
    if years <= 0 or start <= 0:
        return 0.0
    return (end / start) ** (1 / years) - 1


def max_drawdown(equity: list[float]) -> float:
    peak, mdd = -math.inf, 0.0
    for v in equity:
        peak = max(peak, v)
        if peak > 0:
            mdd = max(mdd, (peak - v) / peak)
    return mdd


def sharpe(returns: list[float], rf_daily: float = 0.0) -> float:
    if len(returns) < 2:
        return 0.0
    ex = [r - rf_daily for r in returns]
    mean = sum(ex) / len(ex)
    var = sum((r - mean) ** 2 for r in ex) / (len(ex) - 1)
    return 0.0 if var == 0 else mean / math.sqrt(var) * math.sqrt(TRADING_DAYS)


def trade_stats(realized_pnls: list[float]) -> dict:
    wins = [p for p in realized_pnls if p > 0]
    losses = [p for p in realized_pnls if p <= 0]
    gross_loss = -sum(losses)
    return {
        "trades": len(realized_pnls),
        "win_rate": len(wins) / len(realized_pnls) if realized_pnls else 0.0,
        "profit_factor": (sum(wins) / gross_loss) if gross_loss > 0 else (math.inf if wins else 0.0),
        "avg_win": sum(wins) / len(wins) if wins else 0.0,
        "avg_loss": sum(losses) / len(losses) if losses else 0.0,
    }

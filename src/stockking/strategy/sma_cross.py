"""엔진 검증용 예제 전략(이동평균 교차). **채택 전략 아님** — 매도매수팀 명세서가 오면 교체."""

from __future__ import annotations

from ..domain import Side
from .base import StrategyContext, TargetOrder


class SmaCrossStrategy:
    def __init__(self, fast: int = 5, slow: int = 20, budget_per_symbol: int = 1_000_000) -> None:
        if fast >= slow:
            raise ValueError("fast < slow 이어야 합니다")
        self.fast, self.slow, self.budget = fast, slow, budget_per_symbol
        self.strategy_id = f"sma_cross_{fast}_{slow}"

    def on_bar_close(self, ctx: StrategyContext) -> list[TargetOrder]:
        orders: list[TargetOrder] = []
        for symbol, bars in ctx.history.items():
            if len(bars) < self.slow + 1:
                continue
            closes = [b.close for b in bars]
            f_now, s_now = sum(closes[-self.fast:]) / self.fast, sum(closes[-self.slow:]) / self.slow
            f_prev = sum(closes[-self.fast - 1:-1]) / self.fast
            s_prev = sum(closes[-self.slow - 1:-1]) / self.slow
            held = ctx.positions.get(symbol, 0)
            if f_prev <= s_prev and f_now > s_now and held == 0:
                qty = self.budget // closes[-1]
                if qty > 0:
                    orders.append(TargetOrder(symbol, Side.BUY, qty, "golden cross"))
            elif f_prev >= s_prev and f_now < s_now and held > 0:
                orders.append(TargetOrder(symbol, Side.SELL, held, "dead cross"))
        return orders

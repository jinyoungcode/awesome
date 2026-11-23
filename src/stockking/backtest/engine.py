"""일봉 이벤트 기반 백테스트 엔진 (MVP).

체결 가정 (BACKTEST_MVP.md §3)
- D일 종가에 신호 생성 → D+1일 시가에 체결 (look-ahead 차단)
- 체결가 = 시가 ± 슬리피지, 전량 체결(MVP). 거래량 0(거래정지) 종목은 체결 불가 → 주문 폐기
- 수수료(매수·매도)·매도세 반영, 원 미만 절사
- 모든 주문은 실전과 동일한 RiskGate 를 통과 (킬스위치는 백테스트 전용 메모리 모드)
- 일 손실 한도로 킬스위치 발동 시 그날은 거래 중단, 다음 거래일에 해제(사람 해제 가정) 및 횟수 기록
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from ..data import Bar
from ..domain import AccountSnapshot, Fill, OrderRequest, OrderType, Position, Side
from ..risk import KillSwitch, RiskGate, RiskLimits
from ..strategy import Strategy, StrategyContext, TargetOrder
from . import metrics
from .costs import CostModel


@dataclass(frozen=True)
class BacktestConfig:
    initial_cash: int
    cost_model: CostModel
    risk_limits: RiskLimits = field(default_factory=RiskLimits)


@dataclass
class BacktestResult:
    equity_curve: list[tuple[date, int]]
    fills: list[Fill]
    rejected: list[tuple[date, TargetOrder, tuple[str, ...]]]
    realized_pnls: list[float]
    kill_switch_events: list[date]
    total_commission: int
    total_tax: int
    traded_value: int
    warnings: list[str]

    def summary(self) -> dict:
        eq = [v for _, v in self.equity_curve]
        first, last = self.equity_curve[0][0], self.equity_curve[-1][0]
        rets = metrics.daily_returns(eq)
        avg_eq = sum(eq) / len(eq)
        years = max((last - first).days / 365.25, 1e-9)
        return {
            "start": first.isoformat(), "end": last.isoformat(),
            "initial_equity": eq[0], "final_equity": eq[-1],
            "total_return": eq[-1] / eq[0] - 1,
            "cagr": metrics.cagr(eq[0], eq[-1], first, last),
            "mdd": metrics.max_drawdown(eq),
            "sharpe": metrics.sharpe(rets),
            **metrics.trade_stats(self.realized_pnls),
            "annual_turnover": self.traded_value / avg_eq / years if avg_eq else 0.0,
            "total_commission": self.total_commission,
            "total_tax": self.total_tax,
            "risk_rejections": len(self.rejected),
            "kill_switch_events": len(self.kill_switch_events),
            "warnings": self.warnings,
        }


class BacktestEngine:
    def __init__(self, config: BacktestConfig) -> None:
        self.cfg = config

    def run(self, strategy: Strategy, bars: list[Bar]) -> BacktestResult:
        cost = self.cfg.cost_model
        gate = RiskGate(self.cfg.risk_limits, KillSwitch(memory_only=True))
        by_date: dict[date, dict[str, Bar]] = defaultdict(dict)
        for b in bars:
            by_date[b.date][b.symbol] = b
        days = sorted(by_date)
        if len(days) < 2:
            raise ValueError("최소 2거래일 이상 데이터가 필요합니다")

        cash = self.cfg.initial_cash
        positions: dict[str, Position] = {}
        history: dict[str, list[Bar]] = defaultdict(list)
        last_close: dict[str, int] = {}
        pending: list[TargetOrder] = []
        fills: list[Fill] = []
        rejected: list = []
        realized: list[float] = []
        kills: list[date] = []
        curve: list[tuple[date, int]] = []
        tot_comm = tot_tax = traded = 0
        warnings = [] if cost.source != "UNCONFIRMED" else ["비용모델 미확정(UNCONFIRMED) — 회계팀 COST_MODEL.md 확정 필요"]
        week_base = month_base = None
        prev_day: date | None = None

        def equity_at(prices: dict[str, int]) -> int:
            return cash + sum(p.quantity * prices.get(s, last_close.get(s, 0)) for s, p in positions.items())

        for day in days:
            today = by_date[day]
            sod_equity = equity_at(last_close) if last_close else cash
            if prev_day is None or day.isocalendar()[:2] != prev_day.isocalendar()[:2]:
                week_base = sod_equity
            if prev_day is None or (day.year, day.month) != (prev_day.year, prev_day.month):
                month_base = sod_equity
            if gate.kill_switch.is_tripped():
                gate.kill_switch.reset(operator="backtest-next-day")

            # 1) 전일 신호를 당일 시가에 체결
            open_prices = {**last_close, **{s: b.open for s, b in today.items()}}
            for k, t in enumerate(sorted(pending, key=lambda x: x.side is Side.BUY)):  # 매도 먼저
                bar = today.get(t.symbol)
                if bar is None or bar.volume == 0:
                    rejected.append((day, t, ("NO_TRADE: 거래정지/데이터 없음",)))
                    continue
                px = cost.slipped_price(bar.open, t.side is Side.BUY)
                order = OrderRequest(t.symbol, t.side, t.quantity, OrderType.LIMIT, px, strategy.strategy_id)
                snap = AccountSnapshot(cash, {s: Position(p.symbol, p.quantity, p.avg_price) for s, p in positions.items()},
                                       open_prices, sod_equity, week_base, month_base)
                ref = last_close.get(t.symbol, bar.open)
                decision = gate.check(order, snap, reference_price=ref,
                                      now=datetime.combine(day, time(9, 0)) + timedelta(minutes=k))
                if not decision:
                    rejected.append((day, t, decision.reasons))
                    if gate.kill_switch.is_tripped() and day not in kills:
                        kills.append(day)
                    continue
                gross = px * t.quantity
                comm = cost.commission(gross)
                tax = cost.sell_tax(gross) if t.side is Side.SELL else 0
                pos = positions.setdefault(t.symbol, Position(t.symbol))
                if t.side is Side.BUY:
                    if gross + comm > cash:
                        rejected.append((day, t, ("INSUFFICIENT_CASH_WITH_FEES",)))
                        continue
                    pos.avg_price = (pos.avg_price * pos.quantity + gross + comm) / (pos.quantity + t.quantity)
                    pos.quantity += t.quantity
                    cash -= gross + comm
                else:
                    realized.append((px - pos.avg_price) * t.quantity - comm - tax)
                    pos.quantity -= t.quantity
                    cash += gross - comm - tax
                    if pos.quantity == 0:
                        del positions[t.symbol]
                tot_comm += comm
                tot_tax += tax
                traded += gross
                fills.append(Fill(order.client_order_id, t.symbol, t.side, t.quantity, px, comm, tax, day))
            pending = []

            # 2) 종가 평가
            for s, b in today.items():
                history[s].append(b)
                last_close[s] = b.close
            curve.append((day, equity_at(last_close)))

            # 3) 종가 기준 신호 생성 (마지막 날 신호는 체결할 다음 날이 없으므로 버림)
            ctx = StrategyContext(day, dict(history),  # 복사 비용 회피: 전략은 history 를 수정하면 안 됨
                                  {s: p.quantity for s, p in positions.items()}, cash, curve[-1][1])
            pending = list(strategy.on_bar_close(ctx))
            prev_day = day

        return BacktestResult(curve, fills, rejected, realized, kills, tot_comm, tot_tax, traded, warnings)

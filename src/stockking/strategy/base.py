"""전략 인터페이스.

전략은 '무엇을 사고팔지(의도)'만 반환한다. 주문 실행·리스크 체크는 엔진/라우터 몫이다.
전략은 오늘 장 마감까지의 데이터(history)만 볼 수 있다 → 미래 데이터 참조(look-ahead) 차단.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from ..data import Bar
from ..domain import Side


@dataclass(frozen=True)
class StrategyContext:
    today: date
    history: dict[str, list[Bar]]   # 종목별, 오늘 포함 과거 봉 (미래 없음)
    positions: dict[str, int]       # 종목별 보유 수량
    cash: int
    equity: int


@dataclass(frozen=True)
class TargetOrder:
    symbol: str
    side: Side
    quantity: int
    reason: str = ""


class Strategy(Protocol):
    strategy_id: str

    def on_bar_close(self, ctx: StrategyContext) -> list[TargetOrder]: ...

"""일봉 데이터 형식(자료수집팀과의 인터페이스 초안).

CSV 컬럼: date,symbol,open,high,low,close,volume   (date=YYYY-MM-DD, 가격=원 정수, 수정주가 기준)
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass(frozen=True)
class Bar:
    date: date
    symbol: str
    open: int
    high: int
    low: int
    close: int
    volume: int

    def __post_init__(self) -> None:
        if not (self.low <= min(self.open, self.close) and max(self.open, self.close) <= self.high):
            raise ValueError(f"OHLC 불일치: {self}")
        if self.volume < 0:
            raise ValueError(f"음수 거래량: {self}")


def load_bars_csv(path: str | Path) -> list[Bar]:
    with Path(path).open(encoding="utf-8") as fh:
        return [
            Bar(date.fromisoformat(r["date"]), r["symbol"].zfill(6), int(r["open"]), int(r["high"]),
                int(r["low"]), int(r["close"]), int(r["volume"]))
            for r in csv.DictReader(fh)
        ]

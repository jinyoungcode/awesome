"""일봉 데이터 형식(자료수집팀과의 인터페이스 초안).

CSV 컬럼: date,symbol,open,high,low,close,volume[,currency]
- date=YYYY-MM-DD, 수정주가 기준
- currency 생략 또는 KRW: 가격은 원 정수
- currency=USD(미국 주식, D-006): 가격은 달러(소수 허용). 로드 시 USD/KRW 환율로 원 정수 환산

환율 시점(look-ahead 방지): 기본값은 **bar 날짜보다 앞선 날짜의 환율**만 쓴다(fx_same_day=False).
같은 날짜 환율이 그날 미국장 시가 전에 확정되는 소스(예: 서울 외환시장 종가·고시환율, KST 날짜 기준)를
쓸 때만 fx_same_day=True 로 둔다. 소스 선정과 날짜 기준은 자료수집팀 확정 사항이다.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from ..market import FxRates, normalize_symbol


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


def load_bars_csv(path: str | Path, fx: FxRates | None = None, fx_same_day: bool = False) -> list[Bar]:
    bars: list[Bar] = []
    with Path(path).open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            day = date.fromisoformat(r["date"])
            currency = (r.get("currency") or "KRW").strip().upper()
            if currency == "KRW":
                conv = int
            elif currency == "USD":
                if fx is None:
                    raise ValueError("USD 가격 데이터에는 환율(FxRates)이 필요합니다")
                conv = lambda v, d=day: fx.to_krw(Decimal(v), d, strictly_before=not fx_same_day)  # noqa: E731
            else:
                raise ValueError(f"지원하지 않는 통화: {currency}")
            bars.append(Bar(day, normalize_symbol(r["symbol"]), conv(r["open"]), conv(r["high"]),
                            conv(r["low"]), conv(r["close"]), int(r["volume"])))
    return bars

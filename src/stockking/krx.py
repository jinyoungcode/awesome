"""KRX 호가단위(틱). 2023-01-25 개편 기준 — 코스피·코스닥 공통 (거래소 공지로 재확인 필요)."""

_TICKS = [(2_000, 1), (5_000, 5), (20_000, 10), (50_000, 50), (200_000, 100), (500_000, 500)]


def tick_size(price: int) -> int:
    for upper, tick in _TICKS:
        if price < upper:
            return tick
    return 1_000


def floor_to_tick(price: int) -> int:
    t = tick_size(price)
    return price - price % t

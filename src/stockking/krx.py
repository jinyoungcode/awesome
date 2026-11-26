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


def ceil_to_tick(price: int) -> int:
    """매도 지정가용 호가 올림 (COMMON_SPEC §7.2)."""
    up = -(-price // tick_size(price)) * tick_size(price)
    return up if up % tick_size(up) == 0 else ceil_to_tick(up)   # 구간 경계를 넘으면 새 단위로 다시 올림


MARKETABLE_SELL_DISCOUNT = 0.98   # 시장가 미지원 시 '바로 팔리는 지정가' = 기준가 × 0.98 (호가 올림)


def marketable_sell_limit(ref_price: int) -> int:
    return ceil_to_tick(int(ref_price * MARKETABLE_SELL_DISCOUNT))

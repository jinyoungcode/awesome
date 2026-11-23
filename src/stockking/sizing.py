"""모의투자 주문 크기 규칙 (D-003 모의투자 기준, D-005).

- 1회 주문 목표 = (시작 자금 + 실현 순손익) ÷ 동시 보유 한도(5)
- 개별 주문 금액은 목표의 90~110% 안이어야 한다(±10%, D-005로 유지 확정).
- 수수료·환전 비용은 주문액과 별도로 계산한다(주문액에 포함하지 않음).
- 금액은 모두 원(KRW) 정수. 미국 주식은 원화 환산 가격을 넣는다.
"""

from __future__ import annotations

import math

ORDER_BAND = (0.90, 1.10)
DEFAULT_SLOTS = 5


def order_target(starting_cash: int, realized_net_pnl: int | float, slots: int = DEFAULT_SLOTS) -> int:
    if slots <= 0:
        raise ValueError("slots 는 1 이상이어야 합니다")
    return max(0, math.floor((starting_cash + realized_net_pnl) / slots))


def within_band(order_value: int, target: int, band: tuple[float, float] = ORDER_BAND) -> bool:
    lo, hi = band
    # 정수 비교로 경계값(정확히 90% / 110%)을 포함한다
    return target > 0 and order_value * 100 >= target * round(lo * 100) and order_value * 100 <= target * round(hi * 100)


def shares_for_target(price: int, target: int, band: tuple[float, float] = ORDER_BAND) -> int | None:
    """목표 금액에 가장 가까우면서 허용 범위 안에 드는 정수 주식 수. 불가능하면 None.

    예) 목표 20,000원: 가격 7,000원 → 3주(21,000원, 105%) / 가격 30,000원 → None(1주가 150%)
    """
    if price <= 0 or target <= 0:
        return None
    candidates = [q for q in (math.floor(target / price), math.ceil(target / price)) if q >= 1]
    valid = [q for q in candidates if within_band(q * price, target, band)]
    if not valid:
        return None
    return min(valid, key=lambda q: (abs(q * price - target), q))

"""D-005 균등 슬롯 사이징 + D-021/D-025 1주 예외 (COMMON_SPEC §15.1, S02 v1.0 §10).

`sizing.shares_for_target`(목표 90~110% 정수 수량)을 쓰고, 못 맞추면 1주 예외(D-021):
- ONE_SHARE_UNDER: 1주 금액 < 목표 90% → '예외 동시 1개' 한도에서 제외 (D-036)
- ONE_SHARE_OVER : 1주 금액 > 목표 110% (≤ 자산 30%) → 동시 1개 한도 유지 (D-025)
두 경우 모두 1건 손실 ≤ 2%, 동시 보유 5종목, 손실 한도는 그대로. 관문(_check_d005)이 같은 규칙을 다시 검사한다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..risk.stages import (ONE_SHARE_EXCEPTION_MAX_CONCURRENT, ONE_SHARE_EXCEPTION_MAX_LOSS_PCT,
                           ONE_SHARE_EXCEPTION_MAX_PRICE_PCT, SLOTS, MAX_TRADE_LOSS_PCT)

ONE_SHARE_UNDER = "ONE_SHARE_UNDER"
ONE_SHARE_OVER = "ONE_SHARE_OVER"


@dataclass(frozen=True)
class SizingDecision:
    qty: int | None
    exception: str = ""
    reason: str = ""


def d005_size(price: int, stop_ref: float, starting_cash: int, realized_net_pnl: int, total_equity: int,
              over_exception_holdings: int) -> SizingDecision:
    from ..sizing import shares_for_target

    target = math.floor((starting_cash + realized_net_pnl) / SLOTS)
    q = shares_for_target(price, target)
    risk_cap = total_equity * MAX_TRADE_LOSS_PCT
    if q is not None:
        if (price - stop_ref) * q > risk_cap:
            return SizingDecision(None, "", f"1건 위험 {(price - stop_ref) * q:,.0f}원 > {risk_cap:,.0f}원")
        return SizingDecision(q)
    # D-021 1주 예외 — D-025 한도 세 가지 모두 통과해야 함
    if price > total_equity * ONE_SHARE_EXCEPTION_MAX_PRICE_PCT:
        return SizingDecision(None, "", f"1주 {price:,}원 > 총자산 30%")
    kind = ONE_SHARE_UNDER if price * 100 < target * 90 else ONE_SHARE_OVER
    if kind == ONE_SHARE_OVER and over_exception_holdings >= ONE_SHARE_EXCEPTION_MAX_CONCURRENT:
        return SizingDecision(None, "", "'목표 초과' 1주 예외 종목 이미 보유(동시 1개)")
    if price - stop_ref > total_equity * ONE_SHARE_EXCEPTION_MAX_LOSS_PCT:
        return SizingDecision(None, "", f"예외 손실 {price - stop_ref:,.0f}원 > 총자산 2%")
    return SizingDecision(1, kind)

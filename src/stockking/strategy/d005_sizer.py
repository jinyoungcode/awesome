"""D-005 균등 슬롯 사이징 + D-021/D-025 1주 예외 (COMMON_SPEC §15.1, S02 v1.0 §10).

PR #2 의 `sizing.shares_for_target`(목표 90~110% 정수 수량)을 그대로 쓴다. PR #2 병합 전에는
import 가 실패하므로 이 모듈은 병합 후에만 쓸 수 있다(테스트는 자동 건너뜀).
목표 미달(50~90%) 주문을 예외 동시 1개 한도에서 뺄지는 대표 결정 대기 → 확정 전까지 D-025 문구대로
'정수로 범위를 못 맞추면 모두 1주 예외'로 처리한다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..risk.stages import (ONE_SHARE_EXCEPTION_MAX_CONCURRENT, ONE_SHARE_EXCEPTION_MAX_LOSS_PCT,
                           ONE_SHARE_EXCEPTION_MAX_PRICE_PCT, SLOTS, MAX_TRADE_LOSS_PCT)

ONE_SHARE = "ONE_SHARE"


@dataclass(frozen=True)
class SizingDecision:
    qty: int | None
    exception: str = ""
    reason: str = ""


def d005_size(price: int, stop_ref: float, starting_cash: int, realized_net_pnl: int, total_equity: int,
              exception_holdings: int) -> SizingDecision:
    from ..sizing import shares_for_target  # PR #2

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
    if exception_holdings >= ONE_SHARE_EXCEPTION_MAX_CONCURRENT:
        return SizingDecision(None, "", "1주 예외 종목 이미 보유(동시 1개)")
    if price - stop_ref > total_equity * ONE_SHARE_EXCEPTION_MAX_LOSS_PCT:
        return SizingDecision(None, "", f"예외 손실 {price - stop_ref:,.0f}원 > 총자산 2%")
    return SizingDecision(1, ONE_SHARE)

"""거래비용 모델.

수치는 총무회계팀 COST_MODEL.md 확정값을 써야 한다. 코드에 기본값을 두지 않아
'모르는 사이 잘못된 세율로 백테스트'하는 사고를 막는다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class CostModel:
    commission_rate: float     # 매수·매도 각각, 체결금액 대비 (예: 0.00015 = 0.015%)
    sell_tax_rate: float       # 매도 시 증권거래세+농특세 합계 (시장별로 다를 수 있음)
    slippage_bps: float        # 체결가 불리 가정, 1bp = 0.01%
    min_commission: int = 0
    source: str = "UNCONFIRMED"  # 근거 문서/버전. 'UNCONFIRMED' 이면 리포트에 경고 표기

    def __post_init__(self) -> None:
        for name in ("commission_rate", "sell_tax_rate", "slippage_bps"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} 는 음수일 수 없습니다")

    def commission(self, gross: int) -> int:
        # 원 미만 절사(증권사별 처리 방식 회계팀 확인 필요)
        return max(self.min_commission, math.floor(gross * self.commission_rate))

    def sell_tax(self, gross: int) -> int:
        return math.floor(gross * self.sell_tax_rate)

    def slipped_price(self, price: int, is_buy: bool) -> int:
        factor = self.slippage_bps / 10_000
        return math.ceil(price * (1 + factor)) if is_buy else math.floor(price * (1 - factor))

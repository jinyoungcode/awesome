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
    # 미국 매도 규제 수수료 (회계팀 COST_MODEL v0.2). 국내는 0.
    sec_fee_rate: float = 0.0            # 매도 체결금액 대비 (2026-04-04 결제분~ 0.0000206)
    finra_taf_per_share: float = 0.0     # 주당, 원 환산값 ($0.000195 × 환율)
    finra_taf_max: float = 0.0           # 1건 상한, 원 환산값 ($9.79 × 환율)

    def __post_init__(self) -> None:
        for name in ("commission_rate", "sell_tax_rate", "slippage_bps", "sec_fee_rate", "finra_taf_per_share",
                     "finra_taf_max"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} 는 음수일 수 없습니다")

    def commission(self, gross: int) -> int:
        # 원 미만 절사(증권사별 처리 방식 회계팀 확인 필요)
        return max(self.min_commission, math.floor(gross * self.commission_rate))

    def sell_tax(self, gross: int) -> int:
        return math.floor(gross * self.sell_tax_rate)

    def us_reg_fees(self, gross: int, qty: int) -> int:
        """미국 매도 SEC fee + FINRA TAF (원 환산). 증권사 부과 단위(센트 올림 등)는 회계팀 C-2 실측으로 확정."""
        sec = math.ceil(gross * self.sec_fee_rate) if self.sec_fee_rate else 0
        taf = math.ceil(min(qty * self.finra_taf_per_share, self.finra_taf_max or math.inf)) if self.finra_taf_per_share else 0
        return sec + taf

    def sell_charges(self, gross: int, qty: int) -> int:
        """매도 시 수수료 외 비용 합계 = 국내 거래세 + 미국 규제 수수료."""
        return self.sell_tax(gross) + self.us_reg_fees(gross, qty)

    @classmethod
    def kr_v024(cls, slippage_bps: float = 5) -> "CostModel":
        """회계팀 COST_MODEL v0.2.4 국내 기본(D-045): 수수료 편도 0.15%(영업점 0.147% + 유관 가능성), 2026~ 매도세 0.20%."""
        return cls(commission_rate=0.0015, sell_tax_rate=0.0020, slippage_bps=slippage_bps, source="COST_MODEL v0.2.4")

    @classmethod
    def us_from_usd(cls, commission_rate: float, usdkrw: float, slippage_bps: float, source: str,
                    sec_fee_rate: float = 0.0000206, taf_usd_per_share: float = 0.000195,
                    taf_usd_max: float = 9.79) -> "CostModel":
        """미국 비용모델: 달러 단위 FINRA TAF 를 기준 환율로 원 환산해 만든다."""
        return cls(commission_rate, 0.0, slippage_bps, source=source, sec_fee_rate=sec_fee_rate,
                   finra_taf_per_share=taf_usd_per_share * usdkrw, finra_taf_max=taf_usd_max * usdkrw)

    def slipped_price(self, price: int, is_buy: bool) -> int:
        factor = self.slippage_bps / 10_000
        return math.ceil(price * (1 + factor)) if is_buy else math.floor(price * (1 - factor))

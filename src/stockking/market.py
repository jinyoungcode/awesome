"""시장 구분과 환율 (D-006: 한·미 주식 지원).

원칙: 시스템 내부의 금액·평가·리스크 계산은 모두 **원(KRW) 정수**로 한다.
- 미국 주식 가격은 해당 일자 환율(USD/KRW)로 원화 환산해 엔진·리스크 관문에 넣는다.
  → 보유 종목 평가에 환율 변동이 자동 반영된다.
- 증권사에 보내는 실제 주문 가격만 현지 통화(USD)를 쓴다 (OrderRequest.native_limit_price).
"""

from __future__ import annotations

import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum


class Market(str, Enum):
    KR = "KR"
    US = "US"


CURRENCY = {Market.KR: "KRW", Market.US: "USD"}

# 국내 6자리 단축코드(숫자 또는 신규 영숫자 코드, 예: 0001A0)
_KR_CODE = re.compile(r"^[0-9][0-9A-Z]{5}$")
# 미국 티커: 영문 1~5자, 클래스 구분 '.' 또는 '-' 허용 (예: AAPL, BRK.B)
_US_TICKER = re.compile(r"^[A-Z]{1,5}([.-][A-Z]{1,2})?$")


def normalize_symbol(symbol: str) -> str:
    s = symbol.strip().upper()
    if s.isdigit() and len(s) < 6:
        s = s.zfill(6)  # 엑셀 등에서 앞자리 0 이 빠진 국내 코드 복원
    return s


def market_of(symbol: str) -> Market:
    s = normalize_symbol(symbol)
    if _KR_CODE.match(s):
        return Market.KR
    if _US_TICKER.match(s):
        return Market.US
    raise ValueError(f"시장을 판별할 수 없는 종목코드: {symbol!r}")


# 기준가 대비 주문가 허용 폭. 국내는 가격제한폭 ±30%, 미국은 가격제한폭이 없다(None = 검사 생략).
PRICE_BAND_PCT: dict[Market, float | None] = {Market.KR: 0.30, Market.US: None}


class FxRates:
    """일자별 USD/KRW 환율. 해당 일자가 없으면 직전 영업일 환율을 쓴다(없으면 오류)."""

    def __init__(self, usdkrw: dict[date, float | Decimal]) -> None:
        if not usdkrw:
            raise ValueError("환율 데이터가 비어 있습니다")
        if any(Decimal(str(v)) <= 0 for v in usdkrw.values()):
            raise ValueError("환율은 양수여야 합니다")
        self._days = sorted(usdkrw)
        self._rates = {d: Decimal(str(v)) for d, v in usdkrw.items()}

    def rate(self, day: date, strictly_before: bool = False) -> Decimal:
        """day 에 쓸 환율.

        strictly_before=True 이면 day 보다 **앞선** 날짜의 환율만 쓴다(look-ahead 방지).
        같은 날짜 환율이 체결 이후에 확정되는 소스(예: 뉴욕 정오 환율)로 미국 주식 체결가를 환산할 때 필요하다.
        """
        prior = [d for d in self._days if (d < day if strictly_before else d <= day)]
        if not prior:
            raise KeyError(f"{day} {'이전' if strictly_before else '이하'} 날짜의 환율이 없습니다")
        return self._rates[prior[-1]]

    def to_krw(self, usd: Decimal | float | str, day: date, strictly_before: bool = False) -> int:
        """USD 금액 → 원 정수(반올림)."""
        rate = self.rate(day, strictly_before)
        return int((Decimal(str(usd)) * rate).quantize(Decimal(1), rounding=ROUND_HALF_UP))

"""운용 모드와 설정.

안전 원칙
- 기본 모드는 PAPER. 알 수 없는 값은 예외.
- LIVE 는 이 버전에서 무조건 거부한다(LiveTradingNotApproved).
  해제는 코드 변경 + 대표 승인 결정번호(DECISIONS.md) + 코드 리뷰를 거쳐야 하며,
  환경변수 하나로 켜지지 않도록 일부러 상수로 막아 둔다.
- 비밀값은 환경변수(.env)에서만 읽는다. 저장소에 기록하지 않는다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum


class TradingMode(str, Enum):
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


# 실계좌 허용 스위치. 대표 승인(DECISIONS.md 에 결정번호 기록) 전까지 False 고정.
LIVE_TRADING_APPROVED: bool = False


class LiveTradingNotApproved(RuntimeError):
    """실계좌 모드 진입 시도 — 승인 전에는 항상 발생."""


def resolve_mode(raw: str | None = None) -> TradingMode:
    value = (raw if raw is not None else os.environ.get("STOCKKING_MODE", "paper")).strip().lower()
    try:
        mode = TradingMode(value)
    except ValueError as exc:
        raise ValueError(f"알 수 없는 STOCKKING_MODE: {value!r} (backtest|paper)") from exc
    if mode is TradingMode.LIVE and not LIVE_TRADING_APPROVED:
        raise LiveTradingNotApproved(
            "실계좌(LIVE) 모드는 대표 승인 전까지 사용할 수 없습니다. "
            "docs/DECISIONS.md 의 승인 기록과 코드 리뷰가 선행되어야 합니다."
        )
    return mode


@dataclass(frozen=True)
class Secret:
    """repr/str 에 값이 찍히지 않는 비밀값 래퍼 (로그 유출 방지)."""

    _value: str

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:  # pragma: no cover - 단순
        return "Secret(****)"

    __str__ = __repr__


def require_secret(name: str) -> Secret:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"환경변수 {name} 가 비어 있습니다. .env 를 확인하세요(.env.example 참고).")
    return Secret(value)

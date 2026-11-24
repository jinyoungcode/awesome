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
from pathlib import Path


class TradingMode(str, Enum):
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


# 실계좌 허용 스위치. 근거: D-002(모의투자 통과 전 실계좌 투입 금지, 2026-11-23 대표 승인).
# P3 Gate 통과 + 실전 전환 대표 승인 결정번호(DECISIONS.md) + 2인 리뷰 전까지 False 고정.
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


DEFAULT_SECRETS_FILE = Path.home() / ".config" / "stockking" / "secrets.env"
_REPO_ROOT = Path(__file__).resolve().parents[2]


class InsecureSecretFile(PermissionError):
    """비밀값 파일 위치·권한이 안전하지 않음."""


def load_secret_file(path: str | Path | None = None) -> dict[str, str]:
    """저장소 밖 KEY=VALUE 파일에서 비밀값을 읽는다 (환경변수 대안, SECRETS.md 참고).

    안전 규칙 (위반 시 InsecureSecretFile):
    - 파일이 저장소 폴더 안에 있으면 거부 (실수로 커밋되는 사고 방지)
    - POSIX 에서 그룹/기타 사용자에게 읽기 권한이 있으면 거부 (chmod 600 필요)
    파일이 없으면 빈 dict.
    """
    p = Path(path or os.environ.get("STOCKKING_SECRETS_FILE") or DEFAULT_SECRETS_FILE).expanduser().resolve()
    if not p.exists():
        return {}
    if p == _REPO_ROOT or _REPO_ROOT in p.parents:
        raise InsecureSecretFile(f"비밀값 파일은 저장소 밖에 두어야 합니다: {p}")
    if os.name == "posix" and p.stat().st_mode & 0o077:
        raise InsecureSecretFile(f"비밀값 파일 권한이 너무 넓습니다. 'chmod 600 {p}' 후 다시 실행하세요")
    values: dict[str, str] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        values[k.strip()] = v.strip().strip('"').strip("'")
    return values


def require_secret(name: str, secret_file: str | Path | None = None) -> Secret:
    """환경변수 → 비밀값 파일 순으로 찾는다. 값은 Secret 으로 감싸 로그 노출을 막는다."""
    value = os.environ.get(name, "") or load_secret_file(secret_file).get(name, "")
    if not value:
        raise RuntimeError(
            f"비밀값 {name} 이(가) 없습니다. 환경변수 또는 {DEFAULT_SECRETS_FILE} (chmod 600) 에 넣으세요. "
            "teams/03-dev/SECRETS.md 참고"
        )
    return Secret(value)

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


# ── D-049 계좌 프로필: 한 프로세스는 한 계좌(paper 또는 live)만 다룬다 ─────────────────────────────
ACCOUNT_PROFILES = ("paper", "live")
# 주문 ID 접두어 (D-049 확정: 모의 PP-, 실전 LV-). None = 호환 모드(STOCKKING_ACCOUNT 미설정, 기존 SK-)
ORDER_ID_PREFIX = {None: "SK", "paper": "PP", "live": "LV"}
_SECRET_KEY_PREFIX = {"paper": "KIS_PAPER_", "live": "KIS_LIVE_"}
_fixed_profile: list[str | None] = []


class AccountProfileMismatch(RuntimeError):
    """프로세스 시작 때 정한 계좌 프로필과 다른 계좌를 쓰려는 시도 (D-049)."""


def account_profile() -> str | None:
    """STOCKKING_ACCOUNT=paper|live. 처음 읽은 값으로 프로세스 끝까지 고정하고, 이후 바뀌면 예외.
    설정이 없으면 None = 호환 모드(D-049 이전 경로 var\\, %APPDATA%\\stockking\\secrets.env 그대로)."""
    raw = os.environ.get("STOCKKING_ACCOUNT", "").strip().lower() or None
    if raw is not None and raw not in ACCOUNT_PROFILES:
        raise ValueError(f"알 수 없는 STOCKKING_ACCOUNT: {raw!r} (paper|live)")
    if not _fixed_profile:
        _fixed_profile.append(raw)
    elif _fixed_profile[0] != raw:
        raise AccountProfileMismatch(
            f"이 프로세스는 계좌 프로필 {_fixed_profile[0] or '호환 모드'} 로 시작했습니다 — "
            f"{raw or '호환 모드'} 로 바꿀 수 없습니다. 창을 따로 열어 실행하세요(D-049)")
    return raw


def _reset_account_profile_for_tests() -> None:
    _fixed_profile.clear()


def order_id_prefix() -> str:
    return ORDER_ID_PREFIX[account_profile()]


def _stockking_dir(is_windows: bool | None = None) -> Path:
    if (os.name == "nt" if is_windows is None else is_windows) and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "stockking"
    return Path.home() / ".config" / "stockking"


def profile_dir(is_windows: bool | None = None) -> Path:
    """키 파일·토큰 캐시 폴더. 호환 모드: %APPDATA%\\stockking / 프로필: %APPDATA%\\stockking\\<paper|live>"""
    prof = account_profile()
    return _stockking_dir(is_windows) / prof if prof else _stockking_dir(is_windows)


def default_secrets_file(is_windows: bool | None = None) -> Path:
    """윈도우: %APPDATA%\\stockking\\secrets.env / 그 외: ~/.config/stockking/secrets.env
    D-049 프로필: ...\\stockking\\<paper|live>\\secrets.env. paper 는 이동 전(migrate_var 전)이면 기존 파일을 쓴다."""
    f = profile_dir(is_windows) / "secrets.env"
    if account_profile() == "paper" and not f.exists():
        legacy = _stockking_dir(is_windows) / "secrets.env"
        if legacy.exists():
            return legacy
    return f
_REPO_ROOT = Path(__file__).resolve().parents[2]


class InsecureSecretFile(PermissionError):
    """비밀값 파일 위치·권한이 안전하지 않음."""


def load_secret_file(path: str | Path | None = None) -> dict[str, str]:
    """저장소 밖 KEY=VALUE 파일에서 비밀값을 읽는다 (환경변수 대안, SECRETS.md 참고).

    안전 규칙 (위반 시 InsecureSecretFile):
    - 파일이 저장소 폴더 안에 있으면 거부 (실수로 커밋되는 사고 방지)
    - POSIX 에서 그룹/기타 사용자에게 읽기 권한이 있으면 거부 (chmod 600 필요)
    - OneDrive 등 클라우드 동기화 폴더 안이면 거부 (윈도우: 키가 클라우드로 복제되는 사고 방지)
    - 윈도우 권한(ACL)은 scripts/setup_windows.ps1 이 본인만 읽기로 설정
    파일이 없으면 빈 dict.
    """
    p = Path(path or os.environ.get("STOCKKING_SECRETS_FILE") or default_secrets_file()).expanduser().resolve()
    if not p.exists():
        return {}
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        od = os.environ.get(var)
        if od and (p == Path(od).resolve() or Path(od).resolve() in p.parents):
            raise InsecureSecretFile(f"비밀값 파일이 OneDrive 동기화 폴더 안에 있습니다: {p} — %APPDATA%\\stockking 로 옮기세요")
    if p == _REPO_ROOT or _REPO_ROOT in p.parents:
        raise InsecureSecretFile(f"비밀값 파일은 저장소 밖에 두어야 합니다: {p}")
    if os.name == "posix" and p.stat().st_mode & 0o077:
        raise InsecureSecretFile(f"비밀값 파일 권한이 너무 넓습니다. 'chmod 600 {p}' 후 다시 실행하세요")
    values: dict[str, str] = {}
    for line in p.read_text(encoding="utf-8-sig").splitlines():  # 메모장 BOM 허용
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        values[k.strip()] = v.strip().strip('"').strip("'")
    prof = account_profile()
    if prof:   # D-049: 모의 키 파일에 실전 키(또는 그 반대)가 섞이면 실행 거부
        other = _SECRET_KEY_PREFIX["live" if prof == "paper" else "paper"]
        wrong = sorted(k for k in values if k.startswith(other))
        if wrong:
            raise InsecureSecretFile(f"{prof} 계좌 키 파일에 다른 계좌 키 {', '.join(wrong)} 가 있습니다: {p} — "
                                     "계좌별 파일로 나누세요(D-049)")
    return values


def require_secret(name: str, secret_file: str | Path | None = None) -> Secret:
    """환경변수 → 비밀값 파일 순으로 찾는다. 값은 Secret 으로 감싸 로그 노출을 막는다."""
    value = os.environ.get(name, "") or load_secret_file(secret_file).get(name, "")
    if not value:
        raise RuntimeError(
            f"비밀값 {name} 이(가) 없습니다. 환경변수 또는 {default_secrets_file()} 에 넣으세요. "
            "teams/03-dev/SECRETS.md 참고"
        )
    return Secret(value)

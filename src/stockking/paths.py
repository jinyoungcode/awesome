"""실행 상태 파일 경로 — D-049: 계좌 프로필별로 var 폴더를 분리한다. src 의 'var/…' 경로는 여기서만 만든다.

- 호환 모드(STOCKKING_ACCOUNT 미설정): var\\            (D-049 이전과 같음, migrate_var 전 명령이 그대로 동작)
- STOCKKING_ACCOUNT=paper            : var\\paper\\
- STOCKKING_ACCOUNT=live             : var\\live\\
- 두 계좌 공용은 KILL_ALL 비상 스위치(var\\KILL_ALL)와 이동 표시(var\\MIGRATED_D049)뿐이다.
"""

from __future__ import annotations

import os
from pathlib import Path

from .config import account_profile

SHARED_VAR = Path("var")
MIGRATED_MARKER = SHARED_VAR / "MIGRATED_D049"


def var_root() -> Path:
    prof = account_profile()
    return SHARED_VAR / prof if prof else SHARED_VAR


def kill_all_file() -> Path:
    """두 계좌 공용 비상 스위치(총괄 결정 2026-11-27): 신규 매수 전부 차단, 청산 매도는 허용(D-026)."""
    return Path(os.environ.get("STOCKKING_KILL_ALL_FILE", str(SHARED_VAR / "KILL_ALL")))


def layout_error() -> str | None:
    """migrate_var 뒤인데 STOCKKING_ACCOUNT 없이 실행하면, 비어 있는 var\\ 를 보고 감사 로그·킬스위치를 놓친다 → 거부."""
    if account_profile() is None and MIGRATED_MARKER.exists():
        return ("var 폴더가 계좌별로 이동됐습니다(migrate_var). 모의는 $env:STOCKKING_ACCOUNT = \"paper\" 를 "
                "먼저 입력하고 다시 실행하세요(D-049)")
    return None

"""모의 운용 금액 해석 (D-044·D-046) — 점검 주문(T1/T5) 후보 가격대 계산용.

STOCKKING_OPERATING_CAPITAL (프로그램팀 `risk/stages.py`와 같은 환경변수):
- 숫자(쉼표·밑줄 허용) → 그 값
- "auto" 또는 비어 있음 → 프로그램팀이 1일차에 고정한 `operating_capital.json`의 `capital` 값을 찾는다
  찾는 순서: STOCKKING_AUTO_CAPITAL_FILE → 현재 폴더 var\\operating_capital.json
           → %STOCKKING_DATA_DIR%\\operating_capital.json → 저장소 var\\operating_capital.json
- 아무 파일도 없으면 기본 15만 원 (경고 출력)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

DEFAULT_CAPITAL = 150_000
AUTO_FILE_NAME = "operating_capital.json"


def _candidates(repo_root: Path | None) -> list[Path]:
    c: list[Path] = []
    if os.environ.get("STOCKKING_AUTO_CAPITAL_FILE"):
        c.append(Path(os.environ["STOCKKING_AUTO_CAPITAL_FILE"]))
    c.append(Path.cwd() / "var" / AUTO_FILE_NAME)
    if os.environ.get("STOCKKING_DATA_DIR"):
        c.append(Path(os.environ["STOCKKING_DATA_DIR"]).expanduser() / AUTO_FILE_NAME)
    if repo_root:
        c.append(repo_root / "var" / AUTO_FILE_NAME)
    return c


def resolve_capital(raw: str | None = None, repo_root: Path | None = None, warn=None) -> tuple[int, str]:
    """(운용 금액, 출처 설명)."""
    warn = warn or (lambda m: print(m, file=sys.stderr))
    raw = (os.environ.get("STOCKKING_OPERATING_CAPITAL", "") if raw is None else raw)
    val = raw.replace(",", "").replace("_", "").strip()
    if val and val.lower() != "auto":
        try:
            n = int(val)
        except ValueError:
            raise ValueError(f"STOCKKING_OPERATING_CAPITAL 값을 읽을 수 없습니다: '{raw}' (숫자 또는 auto)") from None
        if n <= 0:
            raise ValueError("STOCKKING_OPERATING_CAPITAL 은 양수여야 합니다")
        return n, "STOCKKING_OPERATING_CAPITAL"
    for f in _candidates(repo_root):
        if f.exists():
            n = int(json.loads(f.read_text(encoding="utf-8"))["capital"])
            if n <= 0:
                raise ValueError(f"{f} 의 capital 이 양수가 아닙니다")
            return n, str(f)
    warn(f"[경고] 운용 금액이 '{raw or '(없음)'}'인데 고정 파일(operating_capital.json)을 찾지 못해 기본 {DEFAULT_CAPITAL:,}원을 씁니다. "
         "필요하면 --capital 로 지정하세요")
    return DEFAULT_CAPITAL, "default"

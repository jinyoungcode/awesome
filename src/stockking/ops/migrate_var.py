"""D-049 1회성 이동: 기존 var\\ → var\\paper\\, 키 파일·토큰 → %APPDATA%\\stockking\\paper\\ (12/2 모의 마감 뒤 실행, 총괄 결정).

    python -m stockking.ops.migrate_var              # 미리보기(아무것도 안 바꿈)
    python -m stockking.ops.migrate_var --apply --operator 대표

- STOCKKING_ACCOUNT 를 비운 상태(호환 모드)에서 실행합니다.
- 옮기기 전에 var\\ 전체를 var\\_pre_d049_backup\\ 으로 복사합니다(원본 보존).
- 끝나면 var\\MIGRATED_D049 를 남깁니다. 이후 STOCKKING_ACCOUNT 없이 실행하면 명령이 거부됩니다
  (빈 var\\ 를 보고 감사 로그·킬스위치를 놓치는 사고 방지). 모의는 $env:STOCKKING_ACCOUNT = "paper".
- 키 파일은 복사가 아니라 이동합니다(같은 키가 두 곳에 남지 않게). 토큰은 다시 받으면 되므로 지웁니다.
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path

from ..config import _stockking_dir, account_profile
from ..paths import MIGRATED_MARKER, SHARED_VAR

KEEP = {"paper", "live", "KILL_ALL", "_pre_d049_backup", MIGRATED_MARKER.name, "kill_all_log.jsonl"}


def plan(var: Path = SHARED_VAR, keys_dir: Path | None = None) -> dict:
    keys_dir = keys_dir or _stockking_dir()
    items = sorted(p.name for p in var.iterdir() if p.name not in KEEP) if var.exists() else []
    return {"var_items": items,
            "secrets": str(keys_dir / "secrets.env") if (keys_dir / "secrets.env").exists() else None,
            "token": str(keys_dir / "kis_paper_token.json") if (keys_dir / "kis_paper_token.json").exists() else None}


def apply(operator: str, var: Path = SHARED_VAR, keys_dir: Path | None = None) -> dict:
    if not operator:
        raise ValueError("실행자(--operator)를 기록해야 합니다")
    if account_profile() is not None:
        raise RuntimeError("STOCKKING_ACCOUNT 를 비우고(호환 모드) 실행하세요: Remove-Item Env:STOCKKING_ACCOUNT")
    if (var / MIGRATED_MARKER.name).exists():
        raise RuntimeError("이미 이동했습니다(var\\MIGRATED_D049 있음)")
    if (var / "paper").exists() and any((var / "paper").iterdir()):
        raise RuntimeError("var\\paper 에 이미 파일이 있습니다 — 섞이지 않게 중단합니다. 프로그램팀 확인 필요")
    keys_dir = keys_dir or _stockking_dir()
    p = plan(var, keys_dir)
    if (var / ".lock").exists():
        raise RuntimeError("실행 중인 프로그램이 있습니다(var\\.lock) — 모두 끝낸 뒤 실행하세요")
    backup = var / "_pre_d049_backup"
    if p["var_items"]:
        backup.mkdir(parents=True, exist_ok=True)
        for name in p["var_items"]:
            src = var / name
            (shutil.copytree if src.is_dir() else shutil.copy2)(src, backup / name)
    dest = var / "paper"
    dest.mkdir(parents=True, exist_ok=True)
    for name in p["var_items"]:
        shutil.move(str(var / name), str(dest / name))
    if p["secrets"]:
        (keys_dir / "paper").mkdir(parents=True, exist_ok=True)
        shutil.move(p["secrets"], str(keys_dir / "paper" / "secrets.env"))
    if p["token"]:
        Path(p["token"]).unlink()
    (var / MIGRATED_MARKER.name).write_text(json.dumps(
        {"at": datetime.now().isoformat(timespec="seconds"), "operator": operator, **p, "decision": "D-049"},
        ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="실제로 이동 (없으면 미리보기)")
    ap.add_argument("--operator", default="")
    a = ap.parse_args(argv)
    p = apply(a.operator) if a.apply else plan()
    print(("이동 완료" if a.apply else "[미리보기] 이동할 항목") + f": var\\ → var\\paper\\ {p['var_items'] or '없음'}")
    dest_key = "paper\\secrets.env" if p["secrets"] else "-"
    print(f"  키 파일: {p['secrets'] or '없음'} → {dest_key}")
    print(f"  토큰 캐시: {'삭제(다시 발급)' if p['token'] else '없음'}")
    if a.apply:
        print('→ 이제부터 모의는 $env:STOCKKING_ACCOUNT = "paper" 를 먼저 입력하고 실행합니다 (paper_day.ps1 -Account paper)')
    return 0


if __name__ == "__main__":
    from .common import run_cli
    raise SystemExit(run_cli(main))

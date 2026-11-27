"""KILL_ALL 비상 스위치 (총괄 결정 2026-11-27, D-049): 두 계좌(모의·실전)의 신규 매수를 한 번에 막는다.

    python -m stockking.ops.kill_all --status
    python -m stockking.ops.kill_all --trip  --reason "이상 체결" --operator 대표
    python -m stockking.ops.kill_all --clear --operator 대표          # 원인 확인 뒤 사람이 직접

- 청산 매도는 계속 허용(D-026). 계좌별 킬스위치·손실 한도는 그대로 따로 작동합니다.
- 발동·해제는 var\\kill_all_log.jsonl 에 남습니다(두 계좌 공용 기록).
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime

from ..paths import SHARED_VAR, kill_all_file


def _log(action: str, operator: str, reason: str = "") -> None:
    SHARED_VAR.mkdir(parents=True, exist_ok=True)
    with (SHARED_VAR / "kill_all_log.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), "action": action,
                             "operator": operator, "reason": reason}, ensure_ascii=False) + "\n")


def trip(reason: str, operator: str) -> None:
    if not operator or not reason:
        raise ValueError("--reason 과 --operator 를 모두 기록해야 합니다")
    f = kill_all_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(f"{datetime.now().isoformat(timespec='seconds')} {operator}: {reason}\n", encoding="utf-8")
    _log("trip", operator, reason)


def clear(operator: str) -> bool:
    if not operator:
        raise ValueError("해제자(--operator)를 기록해야 합니다")
    f = kill_all_file()
    if not f.exists():
        return False
    f.unlink()
    _log("clear", operator)
    return True


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--status", action="store_true")
    g.add_argument("--trip", action="store_true")
    g.add_argument("--clear", action="store_true")
    ap.add_argument("--reason", default="")
    ap.add_argument("--operator", default="")
    a = ap.parse_args(argv)
    if a.trip:
        trip(a.reason, a.operator)
        print("KILL_ALL 발동: 두 계좌 신규 매수 차단 (청산 매도는 허용)")
    elif a.clear:
        print("KILL_ALL 해제" if clear(a.operator) else "KILL_ALL 이 발동돼 있지 않습니다")
    f = kill_all_file()
    print(f"상태: {'발동 중 — ' + f.read_text(encoding='utf-8').strip() if f.exists() else '정상(꺼짐)'}")
    return 0


if __name__ == "__main__":
    from .common import run_cli
    raise SystemExit(run_cli(main))

"""매일 장 시작 전·재시작 직후 실행: 신규 주문을 시작해도 되는지 판정.

python -m stockking.ops.startup_check                 # 오늘 감사 로그 기준
python -m stockking.ops.startup_check --resolve SK-... --outcome not_sent --operator 대표
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ..audit import Journal
from ..risk import KillSwitch
from .common import journal_dir, journal_path
from .recovery import record_resolution, scan_journal, startup_blockers


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--journal-dir", default=None)
    ap.add_argument("--resolve", help="미확인 주문 client_order_id")
    ap.add_argument("--outcome", help="not_sent | filled | unfilled_canceled | partially_filled")
    ap.add_argument("--operator", default="")
    ap.add_argument("--note", default="")
    a = ap.parse_args(argv)
    paths = sorted(Path(a.journal_dir or journal_dir()).glob("*.jsonl"))
    if a.resolve:
        j = Journal(journal_path())
        record_resolution(j, a.resolve, a.outcome or "", a.operator, a.note)
        j.write("drill", kind="restart", operator=a.operator, passed=True, note=f"장중 재시작 후 {a.resolve} 확인 처리")
        print(f"기록함: {a.resolve} → {a.outcome} (확인자 {a.operator})")
    rep = scan_journal(paths)
    blockers = startup_blockers(rep, KillSwitch())
    print(f"감사 로그 {len(paths)}개, 사용된 주문번호 {len(rep.seen_order_ids)}개, 증권사 접수 {len(rep.accepted)}건")
    if blockers:
        print("신규 주문 시작 불가:")
        for b in blockers:
            print(f"  - {b}")
        return 1
    print("신규 주문 시작 가능")
    return 0


if __name__ == "__main__":
    from .common import run_cli
    raise SystemExit(run_cli(main))

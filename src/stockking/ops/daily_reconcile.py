"""장 마감 후 일일 대사 (D-023 '장부·잔고 대사 100%', '오주문·중복 0건').

python -m stockking.ops.daily_reconcile [--date 20261125]

비교 기준
- 감사 로그에서 증권사가 접수한 주문번호 ↔ KIS 당일 주문내역
- 시스템이 모르는 주문(수동 매매 등, D-013) / 증권사에 없는 주문 / 중복 주문
- 결과는 var/reconcile/YYYYMMDD.json 에 저장 → 회계팀 장부 입력 원천
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from .common import Checklist, journal_path
from .kis_session import add_key_args, make_client
from .recovery import scan_journal


def compare(journal_accepted: dict[str, str], broker_rows: list[dict]) -> dict:
    norm = lambda x: (x or "").lstrip("0")  # noqa: E731
    ours = {norm(odno): cid for cid, odno in journal_accepted.items() if odno}
    theirs = {norm(r["odno"]): r for r in broker_rows}
    dup_odno = [o for o, n in Counter(norm(v) for v in journal_accepted.values() if v).items() if n > 1]
    return {
        "matched": sorted(set(ours) & set(theirs)),
        "missing_at_broker": sorted(set(ours) - set(theirs)),   # 시스템은 접수됐다는데 증권사에 없음
        "unknown_to_system": sorted(set(theirs) - set(ours)),   # 수동 주문 또는 기록 누락
        "duplicate_broker_ids": dup_odno,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    add_key_args(ap)
    a = ap.parse_args(argv)
    day = datetime.strptime(a.date, "%Y%m%d").date()
    kis = make_client(a)
    rows, bal = kis.inquire_daily_orders(a.date), kis.inquire_balance()
    rep = scan_journal(journal_path(day))
    diff = compare(rep.accepted, rows)

    cl = Checklist(f"일일 대사 {a.date}")
    cl.check("시스템 접수 주문이 증권사 내역과 일치", not diff["missing_at_broker"], f"불일치 {diff['missing_at_broker']}")
    # D-029/D-030: 전용 계좌에서 수동 매매는 원칙적으로 없어야 함. 감지되면 별도 집계 대상으로 표시하고 미통과 처리
    cl.check("시스템이 모르는 주문 없음 (있으면 수동 매매로 분리 집계, D-029)", not diff["unknown_to_system"],
             f"수동/미기록 {diff['unknown_to_system']}")
    cl.check("중복 주문 0건", not diff["duplicate_broker_ids"], f"{diff['duplicate_broker_ids']}")
    cl.check("미확인 주문 0건", not rep.unresolved, f"{list(rep.unresolved)}")

    out = Path("var/reconcile") / f"{a.date}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"date": a.date, "balance": bal, "broker_orders": rows, "diff": diff,
                               "manual_orders": [r for r in rows if r["odno"].lstrip("0") in diff["unknown_to_system"]],
                               "passed": cl.passed}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"저장: {out}  (예수금 {bal['cash']:,}원, 총평가 {bal['total_equity']:,}원, 주문 {len(rows)}건)")
    return cl.finish()


if __name__ == "__main__":
    raise SystemExit(main())

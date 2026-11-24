"""재시작 복구 (D-023 '재시작 복구 확인').

원칙: 재시작 후에는 메모리 상태를 믿지 않는다.
1. 킬스위치는 파일로 남아 재시작 후에도 유지된다.
2. 감사 로그에서 이미 쓴 주문번호를 복원해 중복 전송을 막는다.
3. '주문 요청은 기록됐는데 증권사 응답이 없는 주문'(전송 중 다운 또는 통신 오류)은
   증권사 체결조회로 사람이 확인하고 `resolve` 기록을 남기기 전까지 신규 주문을 막는다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from ..audit import Journal
from ..risk import KillSwitch


@dataclass
class RecoveryReport:
    seen_order_ids: set[str] = field(default_factory=set)
    unresolved: dict[str, dict] = field(default_factory=dict)   # client_order_id -> 주문 요청 기록
    accepted: dict[str, str] = field(default_factory=dict)       # client_order_id -> 증권사 주문번호
    corrupt_lines: int = 0


def scan_journal(paths: list[Path] | Path) -> RecoveryReport:
    rep = RecoveryReport()
    pending: dict[str, dict] = {}
    for path in [paths] if isinstance(paths, Path) else paths:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                rep.corrupt_lines += 1  # 기록 중 다운되면 마지막 줄이 잘릴 수 있음
                continue
            ev = rec.get("event")
            if ev == "order_request":
                cid = rec["order"]["client_order_id"]
                rep.seen_order_ids.add(cid)
                pending[cid] = rec
            elif ev == "risk_decision" and not rec.get("approved"):
                pending.pop(rec["client_order_id"], None)  # 관문에서 거부 → 증권사로 안 감
            elif ev == "broker_response":
                cid = rec["client_order_id"]
                pending.pop(cid, None)
                if rec.get("accepted"):
                    rep.accepted[cid] = rec.get("broker_order_id") or ""
            elif ev == "broker_error":
                pass  # 상태 불명 — pending 유지 (사람 확인 필요)
            elif ev == "manual_resolution":
                pending.pop(rec["client_order_id"], None)
    rep.unresolved = pending
    return rep


def startup_blockers(report: RecoveryReport, kill_switch: KillSwitch) -> list[str]:
    """비어 있어야 신규 주문 시작 가능."""
    blockers = []
    if kill_switch.is_tripped():
        blockers.append(f"킬스위치 발동 중: {kill_switch.reason()}")
    for cid, rec in report.unresolved.items():
        o = rec["order"]
        blockers.append(f"미확인 주문 {cid} ({o['symbol']} {o['side']} {o['quantity']}주) — 증권사 체결조회 후 resolve 필요")
    if report.corrupt_lines:
        blockers.append(f"감사 로그 손상 줄 {report.corrupt_lines}개 — 원본 보존 후 확인 필요")
    return blockers


def record_resolution(journal: Journal, client_order_id: str, outcome: str, operator: str, note: str = "") -> None:
    if not operator:
        raise ValueError("확인자(operator)를 기록해야 합니다")
    if outcome not in {"not_sent", "filled", "unfilled_canceled", "partially_filled"}:
        raise ValueError("outcome: not_sent | filled | unfilled_canceled | partially_filled")
    journal.write("manual_resolution", client_order_id=client_order_id, outcome=outcome,
                  operator=operator, note=note)

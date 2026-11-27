"""D-024 3일 모의 점검표 C1~C6 자동 집계 리포트.

python -m stockking.ops.paper_report

근거 자료: var/journal/*.jsonl (주문·훈련 기록), var/reconcile/*.json (일일 대사)
C1 은 ledger_close 대사 결과(var/reconcile/ledger_*.json), C2 는 s02_runner verify 결과(signal_*.json).
"""

from __future__ import annotations

import json
from pathlib import Path

from .common import JOURNAL_DIR
from .recovery import scan_journal
from .tech_orders import load_records

RECONCILE_DIR = Path("var/reconcile")


def c2_evidence(records: list[dict], signals: list[dict]) -> list[dict]:
    """일별 C2 근거: 주문계획 건수, 실제 제출 건수, 국면 진단값(지수 종가·200일 평균), 일치 여부."""
    by: dict[str, dict] = {}
    for r in records:
        if r.get("event") == "s02_plan":
            by.setdefault(r["signal_date"], {}).update(
                {"date": r["signal_date"], "plan_count": r.get("plan_count", len(r.get("rows") or [])),
                 "regime": r.get("regime", {})})
        elif r.get("event") == "s02_submit":
            d = by.setdefault(r["signal_date"], {"date": r["signal_date"]})
            d["submitted"] = d.get("submitted", 0) + 1
    for x in signals:
        d = by.setdefault(x["date"], {"date": x["date"]})
        d["match"] = x.get("match")
        d.setdefault("regime", x.get("regime", {}))
        d.setdefault("plan_count", x.get("plan_count"))
    for d in by.values():
        d.setdefault("submitted", 0)
        if "match" not in d and d.get("plan_count") == 0 and d["submitted"] == 0:
            d["match"] = True          # D-047: 실행 신호·재현 신호 모두 0건이면 일치
            d["match_basis"] = "D-047 둘 다 0건"
    return [by[k] for k in sorted(by)]


def c2_row(records: list[dict], signals: list[dict]) -> tuple[str, str, str]:
    ev = c2_evidence(records, signals)
    if not ev:
        return ("C2 신호 일치 100%", "PENDING", "주문계획·신호 비교 기록 없음 (s02_runner plan/verify)")
    parts, bad, pending = [], [], []
    for d in ev:
        reg = ", ".join(f"{k} {v.get('close')}/{v.get('sma200')}({'ON' if v.get('on') else 'OFF'})"
                        for k, v in (d.get("regime") or {}).items()) or "국면 기록 없음"
        m = d.get("match")
        parts.append(f"{d['date']} 계획 {d.get('plan_count')}·제출 {d['submitted']} [{reg}] "
                     f"{'일치' if m else '불일치' if m is False else '비교 전'}")
        (bad if m is False else pending if m is None else []).append(d["date"])
    status = "FAIL" if bad else "PENDING" if pending else "PASS"
    return ("C2 신호 일치 100%", status, " | ".join(parts))


def build_report(records: list[dict], reconciles: list[dict], recovery,
                 ledgers: list[dict] | None = None, signals: list[dict] | None = None) -> list[tuple[str, str, str]]:
    """(항목, 상태 PASS/FAIL/PENDING, 근거) 목록."""
    rows = []
    ledgers, signals = ledgers or [], signals or []
    if not ledgers:
        rows.append(("C1 장부·잔고 대사 100%", "PENDING", "장부 대사 기록 없음 (ledger_close --open/--close)"))
    else:
        bad_l = [x["date"] for x in ledgers if x.get("recon_status") != "OK"]
        rows.append(("C1 장부·잔고 대사 100%", "PASS" if not bad_l else "FAIL",
                     f"대사 {len(ledgers)}일, 불일치일 {bad_l or '없음'}"))
    rows.append(c2_row(records, signals))
    bad = [r["date"] for r in reconciles if not r.get("passed")]
    dup_or_unknown = sum(len(r["diff"]["duplicate_broker_ids"]) + len(r["diff"]["missing_at_broker"])
                         for r in reconciles)
    if not reconciles:
        rows.append(("C3 오주문·중복 0건", "PENDING", "일일 대사 기록 없음"))
    else:
        rows.append(("C3 오주문·중복 0건", "PASS" if not bad and not dup_or_unknown else "FAIL",
                     f"대사 {len(reconciles)}일, 미통과일 {bad or '없음'}"))
    ks = [r for r in records if r.get("event") == "drill" and r.get("kind") == "kill_switch"]
    rows.append(("C4 킬스위치 1회 훈련", "PASS" if any(r.get("passed") for r in ks) else "PENDING",
                 f"훈련 기록 {len(ks)}회"))
    res = [r for r in records if r.get("event") == "manual_resolution"]
    restart = [r for r in records if r.get("event") == "drill" and r.get("kind") == "restart"]
    c5 = any(r.get("passed") for r in restart) and not recovery.unresolved
    rows.append(("C5 재시작 복구", "PASS" if c5 else "PENDING",
                 f"실습 기록 {len(restart)}회, 확인 처리 {len(res)}건, 현재 미확인 {len(recovery.unresolved)}건"))
    reqs = {r["order"]["client_order_id"]: r["order"] for r in records if r.get("event") == "order_request"}
    acc = [cid for cid in recovery.accepted if cid in reqs]
    by = {"S02": 0, "T_TECH": 0, "기타": 0}
    for cid in acc:
        sid = str(reqs[cid].get("strategy_id", ""))
        by["S02" if sid.startswith("S02") else "T_TECH" if sid == "T_TECH" else "기타"] += 1
    total = by["S02"] + by["T_TECH"]
    rows.append(("C6 모의 주문 5건 이상", "PASS" if total >= 5 else "PENDING",
                 f"접수 S02 {by['S02']}건 + T_TECH {by['T_TECH']}건 (connect_check 등 기타 {by['기타']}건 제외)"))
    return rows


def main() -> int:
    records = load_records()
    reconciles = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(RECONCILE_DIR.glob("[0-9]*.json"))]
    ledgers = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(RECONCILE_DIR.glob("ledger_*.json"))]
    signals = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(RECONCILE_DIR.glob("signal_*.json"))]
    rows = build_report(records, reconciles, scan_journal(sorted(JOURNAL_DIR.glob("*.jsonl"))), ledgers, signals)
    print("D-024 3일 모의 점검표")
    for name, st, why in rows:
        print(f"  [{st:7}] {name} — {why}")
    done = all(st == "PASS" for _, st, _ in rows)
    from datetime import date as _d
    RECONCILE_DIR.mkdir(parents=True, exist_ok=True)
    (RECONCILE_DIR / f"paper_report_{_d.today():%Y%m%d}.json").write_text(json.dumps(
        {"rows": [{"item": n, "status": st, "evidence": why} for n, st, why in rows],
         "c2_evidence": c2_evidence(records, signals)}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("\n종합: " + ("통과" if done else "미완료 — 미통과 시 기간 연장(D-024)"))
    return 0 if done else 1


if __name__ == "__main__":
    from .common import run_cli
    raise SystemExit(run_cli(main))

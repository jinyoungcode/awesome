"""재시작 복구 훈련 (D-023). 증권사에 접속하지 않는 오프라인 훈련 — 첫날 바로 실행 가능.

시나리오: 프로세스 A가 주문 1건 정상 처리 → 2번째 주문을 증권사로 보내는 도중 강제 종료(크래시)
         → 프로세스 B(새 객체)가 감사 로그만으로 상태를 복구하는지 확인.

python -m stockking.ops.restart_drill
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from ..audit import Journal
from ..backtest.costs import CostModel
from ..broker import PaperBroker
from ..config import TradingMode
from ..domain import AccountSnapshot, OrderRequest, OrderStatus, OrderType, Side
from ..execution import OrderRouter
from ..risk import KillSwitch, RiskGate
from .common import Checklist
from .recovery import record_resolution, scan_journal, startup_blockers


class _CrashingBroker(PaperBroker):
    """두 번째 주문 전송 중 프로세스가 죽는 상황 흉내 (Exception 이 아니라 BaseException → 라우터가 못 잡음)."""

    def __init__(self, *a, crash_on: int, **kw):
        super().__init__(*a, **kw)
        self._n, self._crash_on = 0, crash_on

    def place_order(self, order):
        self._n += 1
        if self._n == self._crash_on:
            raise KeyboardInterrupt("simulated crash")
        return super().place_order(order)


def run(workdir: Path) -> Checklist:
    cl = Checklist("재시작 복구 훈련")
    jpath, kfile = workdir / "journal.jsonl", workdir / "KILL_SWITCH"
    prices = {"005930": 70_000}
    costs = CostModel(0.00015, 0.002, 5, source="DRILL")

    def snap(broker):
        return AccountSnapshot(broker.get_cash(), broker.get_positions(), prices, 1_000_000)

    # --- 프로세스 A ---
    broker_a = _CrashingBroker(1_000_000, prices, costs, crash_on=2)
    router_a = OrderRouter(broker_a, RiskGate(kill_switch=KillSwitch(kfile)), Journal(jpath), TradingMode.PAPER)
    o1 = OrderRequest("005930", Side.BUY, 1, OrderType.LIMIT, 70_000, "drill")
    o2 = OrderRequest("005930", Side.BUY, 1, OrderType.LIMIT, 70_000, "drill")
    r1 = router_a.submit(o1, snap(broker_a))
    cl.check("A: 1번 주문 정상 접수", r1.status is OrderStatus.SUBMITTED)
    try:
        router_a.submit(o2, snap(broker_a))
        cl.check("A: 2번 주문 전송 중 크래시 발생", False, "크래시가 일어나지 않음")
    except KeyboardInterrupt:
        cl.check("A: 2번 주문 전송 중 크래시 발생", True)
    KillSwitch(kfile).trip("drill: 크래시 직전 운영자가 킬스위치 발동")
    del broker_a, router_a  # 메모리 상태 폐기 = 재시작

    # --- 프로세스 B (새 객체, 감사 로그·킬스위치 파일만 남음) ---
    rep = scan_journal(jpath)
    ks_b = KillSwitch(kfile)
    cl.check("B: 킬스위치가 재시작 후에도 유지", ks_b.is_tripped())
    cl.check("B: 응답 없는 2번 주문을 '미확인'으로 식별", set(rep.unresolved) == {o2.client_order_id},
             f"미확인 {list(rep.unresolved)}")
    cl.check("B: 1번 주문은 접수 완료로 복원", o1.client_order_id in rep.accepted)
    blockers = startup_blockers(rep, ks_b)
    cl.check("B: 미확인 주문·킬스위치가 있으면 시작 차단", len(blockers) == 2, f"{len(blockers)}건")

    gate_b = RiskGate(kill_switch=ks_b)
    gate_b.preload_order_ids(rep.seen_order_ids)
    ks_b.reset(operator="drill")
    broker_b = PaperBroker(1_000_000, prices, costs)
    dup = gate_b.check(o1, snap(broker_b))
    cl.check("B: 재시작 전 주문번호 재전송 차단(중복 0건)", not dup and any("DUPLICATE" in r for r in dup.reasons))

    jb = Journal(jpath)
    record_resolution(jb, o2.client_order_id, "not_sent", operator="drill", note="체결조회 결과 미접수")
    rep2 = scan_journal(jpath)
    cl.check("B: 확인 기록 후 시작 가능", startup_blockers(rep2, ks_b) == [])
    router_b = OrderRouter(broker_b, gate_b, jb, TradingMode.PAPER)
    r3 = router_b.submit(OrderRequest("005930", Side.BUY, 1, OrderType.LIMIT, 70_000, "drill"), snap(broker_b))
    cl.check("B: 새 주문 정상 처리", r3.status is OrderStatus.SUBMITTED)
    return cl


def main(argv: list[str] | None = None) -> int:
    import argparse

    from .common import journal_path
    ap = argparse.ArgumentParser()
    ap.add_argument("--operator", default="", help="지정하면 결과를 오늘 감사 로그에 훈련 기록으로 남김(C5 근거)")
    a = ap.parse_args(argv)
    print("재시작 복구 훈련 (오프라인, 증권사 접속 없음)")
    with tempfile.TemporaryDirectory() as d:
        cl = run(Path(d))
    if a.operator:
        Journal(journal_path()).write("drill", kind="restart", operator=a.operator, passed=cl.passed,
                                      note="오프라인 훈련. 실제 장중 재시작 실습은 PAPER_RUNBOOK §2")
    return cl.finish()


if __name__ == "__main__":
    from .common import run_cli
    raise SystemExit(run_cli(main))

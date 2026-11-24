"""킬스위치 훈련 (D-023). 기본은 오프라인, --kis 면 실제 모의 계좌 잔고·시세로 훈련(주문은 절대 나가지 않음).

python -m stockking.ops.killswitch_drill --operator 대표
python -m stockking.ops.killswitch_drill --operator 대표 --kis

훈련 내용
 1) 운영자가 파일로 킬스위치 발동(var/KILL_SWITCH) → 주문이 관문에서 차단되고 증권사로 0건 전송
 2) 감사 로그에 차단 사유 기록
 3) 재시작해도 킬스위치 유지
 4) 일 손실 한도 도달 시 자동 발동
 5) 운영자 이름을 남기고 수동 해제 → 주문 경로 복구 (해제 확인은 드라이런, 실제 주문 없음)
"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from ..audit import Journal
from ..config import TradingMode
from ..domain import AccountSnapshot, OrderRequest, OrderStatus, OrderType, Position, Side
from ..execution import OrderRouter
from ..risk import KillSwitch, RiskGate
from .common import Checklist, journal_path


class _CountingBroker:
    """주문이 증권사까지 갔는지 세는 래퍼. 훈련 중에는 실제 전송하지 않는다."""

    is_paper = True

    def __init__(self):
        self.sent = 0

    def place_order(self, order):
        self.sent += 1
        raise RuntimeError("훈련 중 주문 전송 시도 — 있어서는 안 됨")


def run(kill_file: Path, journal: Journal, snap: AccountSnapshot, symbol: str, price: int, operator: str) -> Checklist:
    cl = Checklist("킬스위치 훈련")
    if kill_file.exists():
        cl.check("0. 시작 전 킬스위치 꺼짐", False, f"{kill_file} 이 이미 있음 — 원인 확인 후 해제하고 다시 실행")
        return cl
    broker = _CountingBroker()
    ks = KillSwitch(kill_file)
    router = OrderRouter(broker, RiskGate(kill_switch=ks), journal, TradingMode.PAPER)
    mk = lambda: OrderRequest(symbol, Side.BUY, 1, OrderType.LIMIT, price, "killswitch_drill")  # noqa: E731

    kill_file.parent.mkdir(parents=True, exist_ok=True)
    kill_file.write_text(f"drill by {operator}\n", encoding="utf-8")  # 운영자가 `touch` 한 것과 같음
    r = router.submit(mk(), snap)
    cl.check("1. 파일 발동 후 주문 차단", r.status is OrderStatus.REJECTED_BY_RISK, r.message)
    cl.check("1-1. 증권사 전송 0건", broker.sent == 0)
    cl.check("2. 감사 로그에 차단 사유 기록",
             any(x["event"] == "risk_decision" and any("KILL_SWITCH" in s for s in x["reasons"])
                 for x in journal.records))
    cl.check("3. 재시작(새 객체) 후에도 유지", KillSwitch(kill_file).is_tripped())

    ks.reset(operator=operator)
    cl.check("4-0. 해제 후 파일 삭제됨", not kill_file.exists())
    auto = RiskGate(kill_switch=KillSwitch(kill_file))
    loss_snap = AccountSnapshot(snap.cash, snap.positions, snap.prices, int(snap.equity / 0.97))  # 당일 -3%
    d = auto.check(mk(), loss_snap)
    cl.check("4. 일 손실 한도(-2%) 도달 시 자동 발동", not d and auto.kill_switch.is_tripped(), "; ".join(d.reasons))

    auto.kill_switch.reset(operator=operator)
    gate = RiskGate(kill_switch=KillSwitch(kill_file))
    d2 = gate.check(mk(), snap)
    cl.check("5. 수동 해제 후 주문 경로 복구(드라이런)", bool(d2), "; ".join(d2.reasons))
    cl.check("5-1. 훈련 전체 동안 증권사 전송 0건", broker.sent == 0)
    journal.write("drill", kind="kill_switch", operator=operator, passed=cl.passed)
    return cl


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--operator", required=True, help="훈련 실행자(기록용)")
    ap.add_argument("--kis", action="store_true", help="실제 모의 계좌 잔고·시세 사용 (주문은 보내지 않음)")
    ap.add_argument("--symbol", default="005930")
    from .kis_session import add_key_args, make_client
    add_key_args(ap)
    a = ap.parse_args(argv)

    if a.kis:
        kis = make_client(a)
        q, bal = kis.inquire_price(a.symbol), kis.inquire_balance()
        positions = {s: Position(s, qty, avg) for s, (qty, avg) in bal["positions"].items()}
        equity = bal["total_equity"] or bal["cash"]
        snap = AccountSnapshot(bal["cash"], positions, {a.symbol: q["price"]}, equity)
        cl = run(Path(KillSwitch().flag_file), Journal(journal_path()), snap, a.symbol, q["price"], a.operator)
    else:
        with tempfile.TemporaryDirectory() as d:
            snap = AccountSnapshot(1_000_000, {}, {a.symbol: 70_000}, 1_000_000)
            cl = run(Path(d) / "KILL_SWITCH", Journal(Path(d) / "j.jsonl"), snap, a.symbol, 70_000, a.operator)
    return cl.finish()


if __name__ == "__main__":
    raise SystemExit(main())

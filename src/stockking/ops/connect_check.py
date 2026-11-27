"""모의 계좌 연결 점검 (D-022): 토큰 발급 → 현재가 → 잔고 → 1주 지정가 모의 주문 → 취소 → 주문내역 확인.

python -m stockking.ops.connect_check                         # 1~3단계만 (주문 안 냄)
python -m stockking.ops.connect_check --order                 # 장중에 1주 주문·취소까지
python -m stockking.ops.connect_check --order --prompt-keys   # 키를 파일 대신 직접 입력

주문은 현재가보다 약 10% 낮은 매수 지정가라 체결되지 않을 가능성이 높고, 접수 즉시 취소한다.
주문도 반드시 OrderRouter → RiskGate 를 거친다 (실운영과 같은 경로).
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta, timezone

from ..audit import Journal
from ..config import TradingMode
from ..domain import AccountSnapshot, OrderRequest, OrderStatus, OrderType, Position, Side
from ..execution import OrderRouter
from ..krx import floor_to_tick
from ..risk import KillSwitch, RiskGate
from ..risk.stages import capital_warning
from .common import Checklist, journal_path
from .kis_session import add_key_args, make_client

KST = timezone(timedelta(hours=9))


def krx_open(now: datetime | None = None) -> bool:
    now = now or datetime.now(KST)
    return now.weekday() < 5 and (9, 0) <= (now.hour, now.minute) < (15, 20)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol", default="005930")
    ap.add_argument("--order", action="store_true", help="1주 지정가 모의 주문 후 즉시 취소까지 실행")
    add_key_args(ap)
    a = ap.parse_args(argv)
    cl = Checklist("모의 계좌 연결 점검")
    kis = make_client(a)

    try:
        kis._access_token()
        cl.check("1. 토큰 발급", True)
    except Exception as e:  # noqa: BLE001
        cl.check("1. 토큰 발급", False, str(e))
        return cl.finish()

    try:
        q = kis.inquire_price(a.symbol)
        cl.check("2. 현재가 조회", q["price"] > 0, f"{a.symbol} 현재가 {q['price']:,}원 / 기준가 {q['base_price']:,}원")
    except Exception as e:  # noqa: BLE001
        cl.check("2. 현재가 조회", False, str(e))
        return cl.finish()

    try:
        bal = kis.inquire_balance()
        cl.check("3. 잔고 조회", True, f"예수금 {bal['cash']:,}원, 보유 {len(bal['positions'])}종목")
    except Exception as e:  # noqa: BLE001
        cl.check("3. 잔고 조회", False, str(e))
        return cl.finish()

    if not a.order:
        print("\n(주문 단계 생략 — 장중에 --order 로 다시 실행하세요)")
        return cl.finish()
    if not krx_open():
        cl.check("4. 장 운영시간 확인", False, "평일 09:00~15:20(KST)에만 주문 점검")
        return cl.finish()

    limit = floor_to_tick(int(q["price"] * 0.90))
    positions = {s: Position(s, qty, avg) for s, (qty, avg) in bal["positions"].items()}
    equity = bal["total_equity"] or bal["cash"]
    if (w := capital_warning(equity)):
        print(f"  ※ 경고: {w}")
    snap = AccountSnapshot(bal["cash"], positions, {a.symbol: q["price"]}, equity)
    router = OrderRouter(kis, RiskGate(kill_switch=KillSwitch()), Journal(journal_path()), TradingMode.PAPER)
    order = OrderRequest(a.symbol, Side.BUY, 1, OrderType.LIMIT, limit, "connect_check")
    routed = router.submit(order, snap, reference_price=q["base_price"] or q["price"])
    if not cl.check("4. 1주 지정가 매수 접수 (관문 통과 포함)", routed.status is OrderStatus.SUBMITTED,
                    f"{limit:,}원, 주문번호 {routed.broker_order_id} {routed.message}"):
        return cl.finish()

    org_no = (kis.last_response.get("output") or {}).get("KRX_FWDG_ORD_ORGNO", "")
    time.sleep(1)
    c = kis.cancel_order(routed.broker_order_id, a.symbol, 1, krx_org_no=org_no)
    Journal(journal_path()).write("cancel_response", client_order_id=order.client_order_id,
                                  broker_order_id=routed.broker_order_id, accepted=c.accepted, message=c.message)
    cl.check("5. 주문 취소", c.accepted, c.message)

    time.sleep(1)
    rows = kis.inquire_daily_orders(datetime.now(KST).strftime("%Y%m%d"))
    mine = [r for r in rows if r["odno"].lstrip("0") == (routed.broker_order_id or "").lstrip("0")]
    cl.check("6. 주문내역에서 확인", bool(mine), f"{mine[0] if mine else '내역 없음'}")
    if mine and mine[0]["filled_qty"]:
        print("  ※ 일부/전량 체결됨 — 모의 계좌라 손실 영향 없음, 대사 시 반영")
    return cl.finish()


if __name__ == "__main__":
    raise SystemExit(main())

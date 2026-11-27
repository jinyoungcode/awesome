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
from ..risk.stages import capital_error
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
    ap.add_argument("--refix-capital", action="store_true",
                    help="운용 금액 auto 를 지금 주문 가능 금액으로 다시 고정(대표 승인 필요, 이전 값은 archive 보관, D-049)")
    ap.add_argument("--operator", default="", help="실행·승인자 (--refix-capital 필수)")
    ap.add_argument("--hold-seconds", type=int, default=0,
                    help="재시작 복구 실습용: 주문 접수 후 취소 전 N초 대기(이때 Ctrl+C → 취소 안 된 점검 주문이 남음). 모의 전용")
    add_key_args(ap)
    a = ap.parse_args(argv)
    if a.refix_capital:   # 증권사 접속 전에 확인
        from ..risk.stages import _auto_requested
        if not a.operator:
            raise ValueError("--refix-capital 은 승인자 --operator 가 필요합니다(대표 승인, D-049)")
        if not _auto_requested():
            raise ValueError("--refix-capital 은 운용 금액 auto 에서만 씁니다")
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
        from ..risk.stages import _auto_requested, archive_capital_file, operating_capital
        from .common import fix_capital_logged
        orderable = bal["cash_d2"] or bal["cash"]
        if a.refix_capital:   # D-049: 대표 승인 뒤 명시적 명령으로만 다시 고정 (기존 파일은 archive 로 보관)
            archive_capital_file(f"refix (승인: {a.operator})")
        if _auto_requested():
            cap = fix_capital_logged(orderable, operator=a.operator, command="connect_check")
            print(f"  운용 금액 auto(D-046): {cap:,}원")
        err = capital_error(orderable)
        cl.check("3-1. 운용 금액 ≤ 모의 예수금 (D-044)", err is None, err or f"운용 금액 {operating_capital():,}원")
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
    snap = AccountSnapshot(bal["cash"], positions, {a.symbol: q["price"]}, equity)
    router = OrderRouter(kis, RiskGate(kill_switch=KillSwitch()), Journal(journal_path()), TradingMode.PAPER)
    order = OrderRequest(a.symbol, Side.BUY, 1, OrderType.LIMIT, limit, "connect_check")
    routed = router.submit(order, snap, reference_price=q["base_price"] or q["price"])
    if not cl.check("4. 1주 지정가 매수 접수 (관문 통과 포함)", routed.status is OrderStatus.SUBMITTED,
                    f"{limit:,}원, 주문번호 {routed.broker_order_id} {routed.message}"):
        return cl.finish()

    org_no = (kis.last_response.get("output") or {}).get("KRX_FWDG_ORD_ORGNO", "")
    if a.hold_seconds > 0:
        if not getattr(kis, "is_paper", False):
            raise PermissionError("--hold-seconds 는 모의투자 전용입니다")
        print(f"  [실습] 접수 완료(주문번호 {routed.broker_order_id}). {a.hold_seconds}초 대기 — 지금 Ctrl+C 를 누르면 "
              "취소되지 않은 점검 주문이 남습니다. 이후 startup_check 로 확인하세요.", flush=True)
        time.sleep(a.hold_seconds)
    time.sleep(1)
    c = kis.cancel_order(routed.broker_order_id, a.symbol, 1, krx_org_no=org_no)
    Journal(journal_path()).write("cancel_response", client_order_id=order.client_order_id,
                                  broker_order_id=routed.broker_order_id, cancel_odno=c.broker_order_id,
                                  accepted=c.accepted, message=c.message)
    cl.check("5. 주문 취소", c.accepted, c.message)

    # 모의 서버 반영 지연 대비: 최대 5회, 2초 간격 재조회 (1일차 실측 '내역 없음' 대응)
    target_id = (routed.broker_order_id or "").lstrip("0")
    mine, rows = [], []
    for attempt in range(5):
        time.sleep(2 if attempt else 1)
        rows = kis.inquire_daily_orders(datetime.now(KST).strftime("%Y%m%d"))
        mine = [r for r in rows if r["odno"].lstrip("0") == target_id or r.get("orgn_odno", "").lstrip("0") == target_id]
        if mine:
            break
    if not mine:
        print(f"  ※ 주문내역 {len(rows)}건 중 주문번호 {routed.broker_order_id} 없음. 원문 확인: "
              "$env:STOCKKING_KIS_DEBUG='1' 후 다시 실행 → var\\logs\\kis_raw_날짜.jsonl")
    cl.check("6. 주문내역에서 확인", bool(mine), f"{mine[0] if mine else '내역 없음'}")
    if mine and mine[0]["filled_qty"]:
        print("  ※ 일부/전량 체결됨 — 모의 계좌라 손실 영향 없음, 대사 시 반영")
    return cl.finish()


if __name__ == "__main__":
    from .common import run_cli
    raise SystemExit(run_cli(main))

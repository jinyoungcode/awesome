"""D-027 3일 모의 점검용 주문(T_TECH) — **모의투자 전용**.

발동 조건(2일차 마감 판정): S02 주문 누적 3건 미만 / 체결된 매수 0건 / 청산(매도) 경로 미실행 중 하나라도 해당.
모든 주문은 OrderRouter → RiskGate 를 통과하고 감사 로그에 reason_code=T_TECH 로 남아 전략 성과에서 제외된다.

python -m stockking.ops.tech_orders --check                      # 발동 조건 판정 (오늘까지 감사 로그 기준)
python -m stockking.ops.tech_orders --run T1 --symbol 000000 --operator 대표   # 장전 1주 지정가 매수
python -m stockking.ops.tech_orders --run T2 --symbol 000000 --operator 대표   # T1 보유분 시장가 매도 (체결 30분 후)
python -m stockking.ops.tech_orders --run T3 --symbol 005930 --operator 대표   # -10% 지정가 매수
python -m stockking.ops.tech_orders --run T3-cancel --operator 대표            # 15:15 이후 T3 잔량 취소

T4(미국)·T5(1주 예외)는 PR #2(미국 주문·사이징) 병합 후 추가한다.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

from .. import config
from ..audit import Journal
from ..config import TradingMode
from ..domain import AccountSnapshot, OrderRequest, OrderStatus, OrderType, Position, Side
from ..execution import OrderRouter
from ..krx import floor_to_tick, marketable_sell_limit
from ..risk import KillSwitch, RiskGate
from ..risk.stages import capital_warning
from ..risk.stages import limits_for, resolve_stage
from .common import JOURNAL_DIR, journal_path

REASON_CODE = "T_TECH"
STRATEGY_ID = "T_TECH"
T1_MAX_PRICE = 20_000          # T1: 1주 2만원 이하
T2_MIN_WAIT = timedelta(minutes=30)
T3_DISCOUNT = 0.90
T3_CANCEL_AFTER = (15, 15)
PENDING_AFTER_PR2 = {"T4": "미국 주문(PR #2) 병합 후", "T5": "1주 예외(D-021/D-025) 구현 후"}


class TechOrdersForbidden(PermissionError):
    """점검용 주문은 모의투자에서만 실행할 수 있다 (D-027)."""


def assert_paper_only(mode: TradingMode, broker) -> None:
    """LIVE 승인 여부와 무관하게 모의가 아니면 실행 자체를 거부한다."""
    if mode is not TradingMode.PAPER:
        raise TechOrdersForbidden(f"T_TECH 점검용 주문은 PAPER 모드 전용입니다 (현재 {mode.value})")
    if getattr(broker, "is_paper", False) is not True:
        raise TechOrdersForbidden("T_TECH 점검용 주문은 모의투자 브로커에서만 실행합니다")
    base = getattr(broker, "_base", None)
    if base is not None and "openapivts" not in base:
        raise TechOrdersForbidden(f"모의투자 도메인이 아닙니다: {base}")


# ---------------- 발동 조건 ----------------
def load_records(journal_dir: Path = JOURNAL_DIR) -> list[dict]:
    recs = []
    for p in sorted(Path(journal_dir).glob("*.jsonl")):
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                recs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return recs


def trigger_status(records: list[dict], s02_prefix: str = "S02", buy_fills: int | None = None) -> dict:
    """S02 주문 수·매수 접수·매도 접수를 집계해 3일차 점검용 주문 필요 여부를 판정.

    buy_fills: KIS 체결조회로 확인한 S02 매수 체결 수(없으면 접수 건으로 대신하고 note 에 표시).
    """
    reqs = {r["order"]["client_order_id"]: r["order"] for r in records if r.get("event") == "order_request"}
    accepted = {r["client_order_id"] for r in records if r.get("event") == "broker_response" and r.get("accepted")}
    s02 = {cid: o for cid, o in reqs.items() if str(o.get("strategy_id", "")).startswith(s02_prefix)}
    s02_acc = [o for cid, o in s02.items() if cid in accepted]
    buys = sum(o["side"] == "buy" for o in s02_acc) if buy_fills is None else buy_fills
    sells = sum(o["side"] == "sell" for o in s02_acc)
    reasons = []
    if len(s02) < 3:
        reasons.append(f"S02 주문 {len(s02)}건 < 3")
    if buys == 0:
        reasons.append("체결된 매수 0건")
    if sells == 0:
        reasons.append("청산(매도) 경로 미실행")
    return {"s02_orders": len(s02), "s02_buys": buys, "s02_sells": sells, "run_tech_orders": bool(reasons),
            "reasons": reasons, "note": "" if buy_fills is not None else "매수 체결은 접수 건수로 대신 집계(체결조회 미반영)"}


# ---------------- 실행 ----------------
def _tech_records(records: list[dict], scenario: str) -> list[dict]:
    return [r for r in records if r.get("event") == "tech_order" and r.get("scenario") == scenario]


def run_scenario(scenario: str, kis, journal: Journal, symbol: str | None, operator: str,
                 records: list[dict], now: datetime | None = None, mode: TradingMode | None = None) -> str:
    assert_paper_only(mode or config.resolve_mode(), kis)
    if not operator:
        raise ValueError("실행자(operator)를 기록해야 합니다")
    if scenario in PENDING_AFTER_PR2:
        raise NotImplementedError(f"{scenario}: {PENDING_AFTER_PR2[scenario]}")
    now = now or datetime.now()

    if scenario == "T3-cancel":
        t3 = _tech_records(records, "T3")
        if not t3 or not t3[-1].get("broker_order_id"):
            raise RuntimeError("취소할 T3 주문 기록이 없습니다")
        if (now.hour, now.minute) < T3_CANCEL_AFTER:
            raise RuntimeError("T3 취소는 15:15 이후 실행합니다")
        r = t3[-1]
        c = kis.cancel_order(r["broker_order_id"], r["symbol"], 1, krx_org_no=r.get("krx_org_no"))
        journal.write("tech_order", scenario="T3-cancel", reason_code=REASON_CODE, operator=operator,
                      symbol=r["symbol"], broker_order_id=r["broker_order_id"], accepted=c.accepted, message=c.message)
        return f"T3 취소 {'성공' if c.accepted else '실패'}: {c.message}"

    if not symbol:
        raise ValueError("--symbol 이 필요합니다")
    q, bal = kis.inquire_price(symbol), kis.inquire_balance()
    positions = {s: Position(s, qty, avg) for s, (qty, avg) in bal["positions"].items()}
    equity = bal["total_equity"] or bal["cash"]
    if (w := capital_warning(equity)):
        print(f"  ※ 경고: {w}")
    snap = AccountSnapshot(bal["cash"], positions, {symbol: q["price"]}, equity)
    limits = limits_for(resolve_stage())

    if scenario == "T1":
        if q["price"] > T1_MAX_PRICE:
            raise ValueError(f"T1 대상은 1주 {T1_MAX_PRICE:,}원 이하 (현재 {q['price']:,}원)")
        order = OrderRequest(symbol, Side.BUY, 1, OrderType.LIMIT, q["price"], STRATEGY_ID)
    elif scenario == "T2":
        t1 = [r for r in _tech_records(records, "T1") if r.get("symbol") == symbol and r.get("accepted")]
        if not t1:
            raise RuntimeError("같은 종목의 T1 접수 기록이 없습니다")
        if now - datetime.fromisoformat(t1[-1]["ts"]) < T2_MIN_WAIT:
            raise RuntimeError("T2 는 T1 이후 30분 이상 지나서 실행합니다")
        held = positions.get(symbol)
        if not held or held.quantity < 1:
            raise RuntimeError("T1 보유분이 아직 없습니다(미체결) — 체결 확인 후 실행")
        # T2 목적 = 국내 시장가 매도 경로 점검(KIS 모의 지원 여부 확인 겸). 미지원이면 시장성 지정가 모드로 재실행
        if os.environ.get("STOCKKING_KR_SELL_MODE", "market").lower() == "marketable_limit":
            order = OrderRequest(symbol, Side.SELL, 1, OrderType.LIMIT, marketable_sell_limit(q["base_price"] or q["price"]),
                                 STRATEGY_ID, exit_reason="X_TECH")
        else:
            order = OrderRequest(symbol, Side.SELL, 1, OrderType.MARKET, None, STRATEGY_ID, exit_reason="X_TECH")
        limits = replace(limits, allow_market_orders=True)  # 관문은 그대로 통과해야 함(국내·매도·사유 코드 검사)
    elif scenario == "T3":
        order = OrderRequest(symbol, Side.BUY, 1, OrderType.LIMIT, floor_to_tick(int(q["price"] * T3_DISCOUNT)),
                             STRATEGY_ID)
    else:
        raise ValueError(f"알 수 없는 시나리오: {scenario}")

    router = OrderRouter(kis, RiskGate(limits, kill_switch=KillSwitch()), journal, TradingMode.PAPER)
    routed = router.submit(order, snap, reference_price=q["base_price"] or q["price"])
    org = ((getattr(kis, "last_response", {}) or {}).get("output") or {}).get("KRX_FWDG_ORD_ORGNO", "")
    journal.write("tech_order", scenario=scenario, reason_code=REASON_CODE, operator=operator, symbol=symbol,
                  client_order_id=order.client_order_id, accepted=routed.status is OrderStatus.SUBMITTED,
                  broker_order_id=routed.broker_order_id, krx_org_no=org, message=routed.message)
    return f"{scenario} {routed.status.value}: 주문번호 {routed.broker_order_id} {routed.message}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--run", choices=["T1", "T2", "T3", "T3-cancel", "T4", "T5"])
    ap.add_argument("--symbol")
    ap.add_argument("--operator", default="")
    from .kis_session import add_key_args, make_client
    add_key_args(ap)
    a = ap.parse_args(argv)
    records = load_records()
    if a.check or not a.run:
        st = trigger_status(records)
        print(json.dumps(st, ensure_ascii=False, indent=2))
        print("→ 3일차 점검용 주문 실행 필요" if st["run_tech_orders"] else "→ 점검용 주문 불필요")
        return 0
    print(run_scenario(a.run, make_client(a), Journal(journal_path()), a.symbol, a.operator, records))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

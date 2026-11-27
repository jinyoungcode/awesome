"""D-027 3일 모의 점검용 주문(T_TECH) — **모의투자 전용**.

발동 조건(2일차 마감 판정): S02 주문 누적 3건 미만 / 체결된 매수 0건 / 청산(매도) 경로 미실행 중 하나라도 해당.
모든 주문은 OrderRouter → RiskGate 를 통과하고 감사 로그에 reason_code=T_TECH 로 남아 전략 성과에서 제외된다.

python -m stockking.ops.tech_orders --check                      # 발동 조건 판정 (오늘까지 감사 로그 기준)
python -m stockking.ops.tech_orders --run T1 --symbol 000000 --operator 대표   # 장전 1주 지정가 매수
python -m stockking.ops.tech_orders --run T2 --symbol 000000 --operator 대표   # T1 보유분 시장가 매도 (체결 30분 후)
python -m stockking.ops.tech_orders --run T3 --symbol 005930 --operator 대표   # -10% 지정가 매수
python -m stockking.ops.tech_orders --run T3-cancel --operator 대표            # 15:15 이후 T3 잔량 취소
python -m stockking.ops.tech_orders --run T1-cancel --operator 대표            # 10:00까지 미체결 T1 취소

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
from ..risk.stages import capital_error
from ..risk.stages import limits_for, resolve_stage
from .common import JOURNAL_DIR, journal_path

REASON_CODE = "T_TECH"
STRATEGY_ID = "T_TECH"
T2_MIN_WAIT = timedelta(minutes=30)
T3_DISCOUNT = 0.90
T3_CANCEL_AFTER = (15, 15)
CANCEL_SCENARIOS = {"T3-cancel": "T3", "T1-cancel": "T1"}
PENDING_AFTER_PR2 = {"T4": "T0·모의는 국내만(D-033) — 이번 점검 범위 밖"}


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


def load_candidates(path: str | Path) -> dict[str, list[dict]]:
    """자료팀 tech_candidates.csv (role,rank,symbol,market,price,adv20) → {role: [행, rank 오름차순]}."""
    import csv
    out: dict[str, list[dict]] = {}
    with open(path, encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            r["symbol"] = str(r["symbol"]).strip().zfill(6)
            out.setdefault(r["role"].strip().upper(), []).append(r)
    for rows in out.values():
        rows.sort(key=lambda r: int(r.get("rank") or 99))
    return out


def pick_symbol(scenario: str, candidates: dict[str, list[dict]] | None, rank: int = 1) -> str | None:
    """T1·T2 → role=T1, T5 → role=T5, T3 → T1 후보 중 다른 종목(rank+1) (PAPER_DATA_HOWTO §5)."""
    if not candidates:
        return None
    role, idx = {"T1": ("T1", rank), "T2": ("T1", rank), "T5": ("T5", rank), "T3": ("T1", rank + 1)}.get(scenario, (None, 0))
    rows = candidates.get(role or "", [])
    if scenario == "T5":   # 총괄 결정(11/27): in_band=False 후보는 T5 에 쓰지 않는다
        rows = [r for r in rows if str(r.get("in_band", "")).strip().lower() not in {"false", "0", "n", "no"}]
    return rows[idx - 1]["symbol"] if 0 < idx <= len(rows) else None


def _tech_snapshot(kis, symbol: str):
    """점검 주문은 운용 금액(D-044/D-046) 기준으로 비중·손실·D-005 목표를 계산한다."""
    from ..risk.stages import operating_capital
    from ..sizing import order_target
    q, bal = kis.inquire_price(symbol), kis.inquire_balance()
    broker_cash = bal.get("cash_d2") or bal["cash"]
    if (err := capital_error(broker_cash)):
        raise RuntimeError(err)
    capital = operating_capital()
    positions = {s: Position(s, qty, avg) for s, (qty, avg) in bal["positions"].items()}
    held_value = sum(p.quantity * q["price"] if s == symbol else 0 for s, p in positions.items())
    snap = AccountSnapshot(min(broker_cash, capital), positions, {symbol: q["price"]}, capital + held_value,
                           principal=None)
    return q, positions, snap, order_target(capital, 0)


def run_scenario(scenario: str, kis, journal: Journal, symbol: str | None, operator: str,
                 records: list[dict], now: datetime | None = None, mode: TradingMode | None = None,
                 dry_run: bool = False) -> str:
    """D-027 점검 주문 1건. dry_run=True 면 리스크 관문 판정만 하고 증권사로 보내지 않는다."""
    from ..sizing import shares_for_target
    assert_paper_only(mode or config.resolve_mode(), kis)
    if not operator:
        raise ValueError("실행자(operator)를 기록해야 합니다")
    if scenario in PENDING_AFTER_PR2:
        raise NotImplementedError(f"{scenario}: {PENDING_AFTER_PR2[scenario]}")
    now = now or datetime.now()

    if scenario in CANCEL_SCENARIOS:   # T3-cancel: 15:15 이후 T3 전부 / T1-cancel: 미체결 T1 (시간 제한 없음)
        src = CANCEL_SCENARIOS[scenario]
        done = {r.get("broker_order_id") for r in _tech_records(records, scenario) if r.get("accepted")}
        targets = [r for r in _tech_records(records, src) if r.get("accepted") and r.get("broker_order_id")
                   and r["broker_order_id"] not in done]
        if not targets:
            raise RuntimeError(f"취소할 {src} 주문 기록이 없습니다")
        if scenario == "T3-cancel" and (now.hour, now.minute) < T3_CANCEL_AFTER:
            raise RuntimeError("T3 취소는 15:15 이후 실행합니다")
        if dry_run:
            return f"[dry-run] {scenario} 대상: " + ", ".join(f"{r['symbol']}({r['broker_order_id']})" for r in targets)
        out = []
        for r in targets:   # 미체결 잔량 전량 취소(QTY_ALL_ORD_YN=Y). 이미 체결된 주문은 증권사가 거부 → 메시지 확인
            c = kis.cancel_order(r["broker_order_id"], r["symbol"], int(r.get("qty") or 1), krx_org_no=r.get("krx_org_no"))
            journal.write("tech_order", scenario=scenario, reason_code=REASON_CODE, operator=operator,
                          symbol=r["symbol"], broker_order_id=r["broker_order_id"], cancel_odno=c.broker_order_id,
                          accepted=c.accepted, message=c.message)
            out.append(f"{r['symbol']} {'성공' if c.accepted else '실패'}: {c.message}")
        return f"{src} 취소 " + " / ".join(out)

    if not symbol:
        raise ValueError(f"{scenario}: 쓸 종목이 없습니다 — 후보 파일에 해당 순위 후보가 없거나(T5 는 in_band=False 제외) "
                         "--candidates/--symbol 이 빠졌습니다. T5 면 '해당 없음'으로 기록하고 건너뜁니다")
    q, positions, snap, target = _tech_snapshot(kis, symbol)
    limits = limits_for(resolve_stage())
    price = q["price"]

    if scenario == "T1":   # 국내 매수: D-005 목표의 90~110% 정수 수량 (PAPER_3DAY_CHECK §6.2)
        qty = shares_for_target(price, target)
        if qty is None:
            raise ValueError(f"T1: {symbol} {price:,}원으로는 목표 {target:,}원의 90~110%를 정수 수량으로 못 맞춤 "
                             f"— 1주 가격이 {target // 5:,}원(목표의 20%) 이하인 종목은 항상 맞출 수 있습니다. "
                             "다른 후보(--rank 2) 또는 --symbol 로 지정하세요")
        order = OrderRequest(symbol, Side.BUY, qty, OrderType.LIMIT, price, STRATEGY_ID,
                             stop_price=round(price * 0.92, 2), order_target=target)
    elif scenario == "T2":   # T1 보유분 전량 매도 (청산 경로·매도세)
        t1 = [r for r in _tech_records(records, "T1") if r.get("symbol") == symbol and r.get("accepted")]
        if not t1:
            raise RuntimeError("같은 종목의 T1 접수 기록이 없습니다")
        if now - datetime.fromisoformat(t1[-1]["ts"]) < T2_MIN_WAIT:
            raise RuntimeError("T2 는 T1 이후 30분 이상 지나서 실행합니다")
        held = positions.get(symbol)
        if not held or held.quantity < 1:
            raise RuntimeError("T1 보유분이 아직 없습니다(미체결) — --status 로 체결 확인 후 실행")
        if os.environ.get("STOCKKING_KR_SELL_MODE", "market").lower() == "marketable_limit":
            order = OrderRequest(symbol, Side.SELL, held.quantity, OrderType.LIMIT,
                                 marketable_sell_limit(q["base_price"] or price), STRATEGY_ID, exit_reason="X_TECH")
        else:
            order = OrderRequest(symbol, Side.SELL, held.quantity, OrderType.MARKET, None, STRATEGY_ID,
                                 exit_reason="X_TECH")
        limits = replace(limits, allow_market_orders=True)  # 관문은 그대로(국내·매도·사유 코드·보유 이내)
    elif scenario == "T3":   # −10% 지정가 1주 → 15:15 취소
        order = OrderRequest(symbol, Side.BUY, 1, OrderType.LIMIT, floor_to_tick(int(price * T3_DISCOUNT)), STRATEGY_ID)
    elif scenario == "T5":   # 1주 예외 '목표 초과'(D-021/D-025/D-036) — 관문이 종류·가격 30%·동시 1개·손실 2% 재검사
        order = OrderRequest(symbol, Side.BUY, 1, OrderType.LIMIT, price, STRATEGY_ID,
                             stop_price=round(price * 0.92, 2), order_target=target, sizing_exception="ONE_SHARE_OVER")
    else:
        raise ValueError(f"알 수 없는 시나리오: {scenario}")

    gate = RiskGate(limits, kill_switch=KillSwitch())
    if dry_run:
        d = gate.check(order, snap, reference_price=q["base_price"] or price)
        journal.write("tech_dry_run", scenario=scenario, symbol=symbol, qty=order.quantity, price=order.limit_price,
                      approved=d.approved, reasons=list(d.reasons), operator=operator)
        verdict = "관문 통과" if d else "관문 거부: " + "; ".join(d.reasons)
        return (f"[dry-run] {scenario} {order.side.value} {symbol} {order.quantity}주 "
                f"{'시장가' if order.order_type is OrderType.MARKET else f'{order.limit_price:,}원'} → {verdict} (전송 안 함)")
    router = OrderRouter(kis, gate, journal, TradingMode.PAPER)
    routed = router.submit(order, snap, reference_price=q["base_price"] or price)
    org = ((getattr(kis, "last_response", {}) or {}).get("output") or {}).get("KRX_FWDG_ORD_ORGNO", "")
    journal.write("tech_order", scenario=scenario, reason_code=REASON_CODE, operator=operator, symbol=symbol,
                  qty=order.quantity, client_order_id=order.client_order_id,
                  accepted=routed.status is OrderStatus.SUBMITTED,
                  broker_order_id=routed.broker_order_id, krx_org_no=org, message=routed.message)
    return f"{scenario} {routed.status.value}: {symbol} {order.quantity}주 주문번호 {routed.broker_order_id} {routed.message}"


def status_lines(records: list[dict], broker_rows: list[dict]) -> list[str]:
    """오늘 점검 주문의 접수·체결·취소 상태 (--status)."""
    by = {str(r["odno"]).lstrip("0"): r for r in broker_rows}
    lines = []
    for r in records:
        if r.get("event") != "tech_order" or r.get("scenario") in CANCEL_SCENARIOS:
            continue
        b = by.get(str(r.get("broker_order_id") or "").lstrip("0"))
        state = "미접수" if not r.get("accepted") else ("내역 없음" if not b else
                 f"체결 {b['filled_qty']}/{b['ord_qty']}주" + (" · 취소" if b.get("canceled") else ""))
        lines.append(f"{r['scenario']:3} {r['symbol']} {r.get('qty', '')}주 주문번호 {r.get('broker_order_id')} → {state}")
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--run", choices=["T1", "T1-cancel", "T2", "T3", "T3-cancel", "T4", "T5"])
    ap.add_argument("--status", action="store_true", help="오늘 점검 주문 접수·체결·취소 상태")
    ap.add_argument("--dry-run", action="store_true", help="관문 판정만, 증권사로 보내지 않음")
    ap.add_argument("--candidates", help="자료팀 tech_candidates.csv 경로 (T1·T2·T3·T5 종목 자동 선택)")
    ap.add_argument("--rank", type=int, default=1, help="후보 순위 (1순위 불가 시 2)")
    ap.add_argument("--symbol", help="종목 직접 지정 (후보 파일보다 우선)")
    ap.add_argument("--operator", default="")
    from .kis_session import add_key_args, make_client
    add_key_args(ap)
    a = ap.parse_args(argv)
    records = load_records()
    if a.status:
        kis = make_client(a)
        for line in status_lines(records, kis.inquire_daily_orders(datetime.now().strftime("%Y%m%d"))) or ["점검 주문 기록 없음"]:
            print(line)
        return 0
    if a.check or not a.run:
        st = trigger_status(records)
        print(json.dumps(st, ensure_ascii=False, indent=2))
        print("→ 점검용 주문 실행 필요" if st["run_tech_orders"] else "→ 점검용 주문 불필요")
        return 0
    symbol = a.symbol or pick_symbol(a.run, load_candidates(a.candidates) if a.candidates else None, a.rank)
    print(run_scenario(a.run, make_client(a), Journal(journal_path()), symbol, a.operator, records,
                       dry_run=a.dry_run))
    return 0


if __name__ == "__main__":
    from .common import run_cli
    raise SystemExit(run_cli(main))

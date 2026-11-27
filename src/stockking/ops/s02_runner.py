"""S02 주문 실행기 (모의투자 전용, 국내만 — D-033). COMMON_SPEC §6 타임라인.

python -m stockking.ops.s02_runner plan   --bars data/bars.csv --universe data/universe.txt \
                                          --markets data/markets.csv --index data/index.csv   # T일 18:30
python -m stockking.ops.s02_runner submit --operator 대표      # T+1 08:40 장전 동시호가 제출
python -m stockking.ops.s02_runner cancel --operator 대표      # T+1 15:15 지정가(LDAY) 잔량 취소
python -m stockking.ops.s02_runner sync                        # T+1 15:45 체결 반영(가상 장부 갱신)
python -m stockking.ops.s02_runner verify --bars ... (plan 과 같은 입력)   # C2 신호 일치 확인

가상 장부(var/s02/state.json): 모의 계좌 금액과 관계없이 운용 기준 자금(15만 원, D-032)으로
현금·보유·실현손익을 관리하고, 사이징·리스크 한도는 이 장부 기준으로 계산한다.
입력 파일 규격(자료팀): bars = data/bars.py CSV(수정주가), universe = 종목코드 한 줄씩,
markets = symbol,market(KOSPI|KOSDAQ), index = 지수 일봉 CSV(symbol 칸에 KOSPI200 / KOSDAQ150).
"""

from __future__ import annotations

import argparse
import csv
import json
import os
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .. import config
from ..audit import Journal
from ..config import TradingMode
from ..data import load_bars_csv
from ..domain import AccountSnapshot, OrderRequest, OrderStatus, OrderType, Position, Side
from ..execution import OrderRouter
from ..indicators import sma
from ..krx import marketable_sell_limit
from ..market import Market, market_of
from ..risk import KillSwitch, RiskGate
from ..risk.stages import limits_for, operating_capital, resolve_stage
from ..sizing import order_target
from ..strategy.d005_sizer import ONE_SHARE_OVER, d005_size
from ..strategy.s02 import STRATEGY_ID, HeldPosition, S02Params, build_order_plan, write_order_plan
from .common import journal_path
from .tech_orders import assert_paper_only, load_records

STATE_DIR = Path("var/s02")
PLAN_DIR = Path("var/order_plan")
INDEX_OF = {"KOSPI": "KOSPI200", "KOSDAQ": "KOSDAQ150"}


# ---------------- 가상 장부 ----------------
def load_state(path: Path = STATE_DIR / "state.json") -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    cap = operating_capital()
    return {"starting_cash": cap, "cash": cap, "realized_pnl": 0, "positions": {}, "last_exits": {}, "peak_equity": cap}


def save_state(state: dict, path: Path = STATE_DIR / "state.json") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)  # 원자적 교체 — 기록 중 다운돼도 이전 상태 보존


def held_positions(state: dict) -> list[HeldPosition]:
    return [HeldPosition(s, p["qty"], p["init_stop"], p["holding_days"]) for s, p in sorted(state["positions"].items())]


def book_equity(state: dict, prices: dict[str, int]) -> int:
    return state["cash"] + sum(p["qty"] * prices.get(s, p["entry_price"]) for s, p in state["positions"].items())


# ---------------- plan ----------------
def make_plan(state: dict, bars, universe: set[str], markets: dict[str, str], index_bars,
              signal_date: date, exec_date: date, params: S02Params = S02Params()):
    history: dict[str, list] = {}
    for b in sorted(bars, key=lambda x: x.date):
        if b.date <= signal_date:
            history.setdefault(b.symbol, []).append(b)
    idx_close: dict[str, list[float]] = {}
    for b in sorted(index_bars, key=lambda x: x.date):
        if b.date <= signal_date:
            idx_close.setdefault(b.symbol, []).append(float(b.close))

    def regime_on(sym: str) -> bool:
        closes = idx_close.get(INDEX_OF.get(markets.get(sym, ""), ""), [])
        ma = sma(closes, 200)
        return ma is not None and closes[-1] > ma

    kr_universe = {s for s in universe if market_of(s) is Market.KR}  # D-033: T0 국내만
    prices = {s: h[-1].close for s, h in history.items() if h}
    equity = book_equity(state, prices)
    over = sum(1 for p in state["positions"].values() if p.get("exception") == ONE_SHARE_OVER)
    taken: list[str] = []

    def sizer(sym: str, limit: int, stop: float):
        d = d005_size(limit, stop, state["starting_cash"], state["realized_pnl"], equity,
                      over + sum(1 for t in taken if t == ONE_SHARE_OVER))
        if d.qty is not None:
            taken.append(d.exception)
        return d.qty, d.exception

    exited = {s for s, d in state["last_exits"].items() if d == signal_date.isoformat()}
    return build_order_plan(signal_date, exec_date, history, kr_universe, regime_on, held_positions(state),
                            exited, params, sizer=sizer)


# ---------------- submit / cancel / sync ----------------
def kr_sell_mode() -> str:
    """국내 청산 매도 방식: market(기본, MOO) | marketable_limit (KIS 모의 시장가 미지원 시)."""
    m = os.environ.get("STOCKKING_KR_SELL_MODE", "market").strip().lower()
    if m not in ("market", "marketable_limit"):
        raise ValueError("STOCKKING_KR_SELL_MODE: market | marketable_limit")
    return m


def runner_limits():
    # 전략 주문: 손절가 필수, 청산은 장전 시장가(MOO) 허용 (COMMON_SPEC §7.1)
    return replace(limits_for(resolve_stage()), require_stop_for_buy=True, allow_market_orders=True)


def _reserve_cost(price: int, qty: int) -> int:
    from .ledger_close import PAPER_COSTS
    gross = price * qty
    return gross + PAPER_COSTS.commission(gross)


def open_buy_orders(records: list[dict], state: dict) -> list[dict]:
    """접수됐지만 아직 전량 체결·취소되지 않은 S02 매수 (재시작 후 예약 반영용)."""
    canceled = {r["client_order_id"] for r in records if r.get("event") == "s02_cancel" and r.get("accepted")}
    applied = state.get("applied", {})
    return [s for s in submitted(records)
            if s["side"] == "BUY" and s["status"] == "submitted" and s["client_order_id"] not in canceled
            and applied.get(s["client_order_id"], {}).get("qty", 0) < s["qty"]]


def submit_plan(rows: list[dict], state: dict, kis, journal: Journal, prices: dict[str, int],
                mode: TradingMode | None = None, records: list[dict] | None = None) -> list[dict]:
    """주문계획 제출. 앞서 접수된(이번 루프 + 재시작 전) 미체결 매수의 현금·종목 슬롯을 예약 반영한 뒤
    다음 주문을 관문에 넣는다 (Codex 검토 #1). 같은 신호일·종목·방향으로 이미 접수된 행은 다시 내지 않는다."""
    assert_paper_only(mode or config.resolve_mode(), kis)
    records = records or []
    router = OrderRouter(kis, RiskGate(runner_limits(), kill_switch=KillSwitch()), journal, TradingMode.PAPER)
    cap = state["starting_cash"]
    cash = state["cash"]
    positions = {s: Position(s, p["qty"], p["entry_price"]) for s, p in state["positions"].items()}
    over = {s for s, p in state["positions"].items() if p.get("exception") == ONE_SHARE_OVER}
    applied = state.get("applied", {})

    def reserve(ticker: str, qty: int, price: int, exception: str) -> None:
        nonlocal cash
        cash -= _reserve_cost(price, qty)
        held = positions.get(ticker)
        positions[ticker] = Position(ticker, (held.quantity if held else 0) + qty, price)
        if exception == ONE_SHARE_OVER:
            over.add(ticker)

    for o in open_buy_orders(records, state):   # 재시작 전 미체결 매수
        remaining = o["qty"] - applied.get(o["client_order_id"], {}).get("qty", 0)
        reserve(o["ticker"], remaining, int(o["limit_price"]), o.get("exception", ""))
    done_keys = {(s["signal_date"], s["ticker"], s["side"]) for s in submitted(records) if s["status"] == "submitted"}

    equity = book_equity(state, prices)
    sod = state.get("start_of_day_equity", equity)
    target = order_target(state["starting_cash"], state["realized_pnl"])
    results = []
    for r in sorted(rows, key=lambda x: x["side"] != "SELL"):   # 청산 먼저 (§6)
        if (r["signal_date"], r["ticker"], r["side"]) in done_keys:
            continue   # 재시작 후 같은 계획 재실행 — 중복 제출 방지
        if r["side"] == "SELL":
            if kr_sell_mode() == "market":
                o = OrderRequest(r["ticker"], Side.SELL, int(r["qty"]), OrderType.MARKET, None, STRATEGY_ID,
                                 exit_reason=r["reason_code"])
            else:   # KIS 모의가 시장가를 받지 않으면 시장성 지정가로 대체
                o = OrderRequest(r["ticker"], Side.SELL, int(r["qty"]), OrderType.LIMIT,
                                 marketable_sell_limit(prices[r["ticker"]]), STRATEGY_ID, exit_reason=r["reason_code"])
        else:
            o = OrderRequest(r["ticker"], Side.BUY, int(r["qty"]), OrderType.LIMIT, int(r["limit_price"]),
                             STRATEGY_ID, stop_price=float(r["init_stop"]), order_target=target,
                             sizing_exception=r.get("exception", ""))
        snap = AccountSnapshot(cash, dict(positions), prices, sod, principal=cap,
                               peak_equity=state.get("peak_equity"), over_exception_symbols=frozenset(over))
        routed = router.submit(o, snap, reference_price=prices.get(r["ticker"]))
        if routed.status is OrderStatus.SUBMITTED and r["side"] == "BUY":
            reserve(r["ticker"], int(r["qty"]), int(r["limit_price"]), r.get("exception", ""))
        org = ((getattr(kis, "last_response", {}) or {}).get("output") or {}).get("KRX_FWDG_ORD_ORGNO", "")
        rec = {"client_order_id": o.client_order_id, "ticker": r["ticker"], "side": r["side"],
               "order_type": r["order_type"], "qty": int(r["qty"]), "limit_price": r.get("limit_price") or None,
               "init_stop": r.get("init_stop") or None, "exception": r.get("exception", ""),
               "reason_code": r["reason_code"], "status": routed.status.value,
               "broker_order_id": routed.broker_order_id, "krx_org_no": org, "message": routed.message}
        journal.write("s02_submit", signal_date=r["signal_date"], **rec)
        results.append(rec)
    return results


def submitted(records: list[dict], signal_date: str | None = None) -> list[dict]:
    return [r for r in records if r.get("event") == "s02_submit" and (signal_date is None or r["signal_date"] == signal_date)]


def cancel_open_entries(records: list[dict], kis, journal: Journal, day: str, now: datetime | None = None,
                        mode: TradingMode | None = None) -> list[str]:
    assert_paper_only(mode or config.resolve_mode(), kis)
    now = now or datetime.now()
    if (now.hour, now.minute) < (15, 15):
        raise RuntimeError("LDAY 잔량 취소는 15:15 이후 (COMMON_SPEC §6)")
    open_ids = {r["odno"].lstrip("0"): r for r in kis.inquire_daily_orders(day)
                if not r["canceled"] and r["filled_qty"] < r["ord_qty"]}
    done = []
    for s in submitted(records):
        oid = (s.get("broker_order_id") or "").lstrip("0")
        if s["order_type"] == "LDAY" and oid in open_ids:
            c = kis.cancel_order(s["broker_order_id"], s["ticker"], 0, krx_org_no=s.get("krx_org_no"))
            journal.write("s02_cancel", client_order_id=s["client_order_id"], broker_order_id=s["broker_order_id"],
                          cancel_odno=c.broker_order_id, accepted=c.accepted, message=c.message)
            done.append(s["ticker"])
    return done


def sync_fills(state: dict, records: list[dict], broker_rows: list[dict], day: date, costs,
               params: S02Params = S02Params()) -> dict:
    """당일 체결 → 가상 장부 반영. 주문별 '누적 체결 − 이미 반영한 양'만 반영한다(부분체결·재조회 안전, Codex 검토 #2).
    보유일수 +1 은 하루 한 번, 기존 보유분에만(진입일 = 0)."""
    by_odno = {r["odno"].lstrip("0"): r for r in broker_rows}
    applied = state.setdefault("applied", {})   # client_order_id -> {qty, gross, comm, tax}
    if state.get("last_sync_date") != day.isoformat():
        for p in state["positions"].values():
            p["holding_days"] += 1
        state["last_sync_date"] = day.isoformat()
    for s in submitted(records):
        b = by_odno.get((s.get("broker_order_id") or "").lstrip("0"))
        if not b:
            continue
        cum_qty = int(b["filled_qty"])
        cum_gross = int(round(b["avg_price"] * cum_qty))
        prev = applied.get(s["client_order_id"], {"qty": 0, "gross": 0, "comm": 0, "tax": 0})
        dq, dg = cum_qty - prev["qty"], cum_gross - prev["gross"]
        if dq <= 0:
            continue
        cum_comm = costs.commission(cum_gross)
        dc = cum_comm - prev["comm"]
        pos = state["positions"].get(s["ticker"])
        if s["side"] == "BUY":
            state["cash"] -= dg + dc
            if pos:
                pos["buy_comm"] = pos.get("buy_comm", 0) + dc   # I1: 실현 순손익에 넣을 매수 수수료
                new_qty = pos["qty"] + dq
                pos["entry_price"] = round((pos["entry_price"] * pos["qty"] + dg) / new_qty, 2)
                pos["qty"] = new_qty
                pos["init_stop"] = round(pos["entry_price"] * (1 - params.stop_pct), 2)
            else:
                px = round(dg / dq, 2)
                state["positions"][s["ticker"]] = {"qty": dq, "entry_price": px, "entry_date": day.isoformat(),
                                                   "init_stop": round(px * (1 - params.stop_pct), 2),  # 체결가 기준(§10)
                                                   "holding_days": 0, "exception": s.get("exception", ""),
                                                   "buy_comm": dc}
            cum_tax = 0
        else:
            cum_tax = costs.sell_charges(cum_gross, cum_qty)
            dt = cum_tax - prev["tax"]
            state["cash"] += dg - dc - dt
            if pos:
                # I1(매매팀 S02_IMPL_CHECK): 실현 순손익 = 매도대금 − 매수대금 − 매수·매도 수수료 − 매도세
                buy_comm_alloc = round(pos.get("buy_comm", 0) * min(dq, pos["qty"]) / pos["qty"]) if pos["qty"] else 0
                pos["buy_comm"] = pos.get("buy_comm", 0) - buy_comm_alloc
                state["realized_pnl"] += round(dg - pos["entry_price"] * dq) - dc - dt - buy_comm_alloc
                pos["qty"] -= dq
                if pos["qty"] <= 0:
                    del state["positions"][s["ticker"]]
                    state["last_exits"][s["ticker"]] = day.isoformat()
        applied[s["client_order_id"]] = {"qty": cum_qty, "gross": cum_gross, "comm": cum_comm, "tax": cum_tax}
    return state


# ---------------- C2 신호 일치 ----------------
def compare_signals(plan_rows: list, submitted_rows: list[dict]) -> dict:
    """같은 입력으로 다시 만든 주문계획 ↔ 실제 제출 기록. 종목·방향·수량·지정가가 모두 같아야 일치."""
    key = lambda t, sd, q, lp: (t, sd, int(q), int(lp) if lp not in (None, "") else None)  # noqa: E731
    plan = sorted(key(r.ticker, r.side, r.qty, r.limit_price) for r in plan_rows if r.qty)
    sent = sorted(key(s["ticker"], s["side"], s["qty"], s["limit_price"]) for s in submitted_rows)
    return {"plan": plan, "submitted": sent, "match": plan == sent,
            "missing": sorted(set(plan) - set(sent)), "extra": sorted(set(sent) - set(plan))}


def read_plan(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _inputs(a):
    bars = load_bars_csv(a.bars)
    universe = {ln.strip().zfill(6) for ln in Path(a.universe).read_text(encoding="utf-8").split() if ln.strip()}
    with open(a.markets, encoding="utf-8") as fh:
        markets = {r["symbol"].zfill(6): r["market"].upper() for r in csv.DictReader(fh)}
    idx = []
    with open(a.index, encoding="utf-8") as fh:
        from ..data import Bar
        for r in csv.DictReader(fh):
            idx.append(Bar(date.fromisoformat(r["date"]), r["symbol"], *(int(float(r[k])) for k in
                                                                        ("open", "high", "low", "close")), 0))
    return bars, universe, markets, idx


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["plan", "submit", "cancel", "sync", "verify"])
    for k in ("bars", "universe", "markets", "index"):
        ap.add_argument(f"--{k}")
    ap.add_argument("--signal-date", default=date.today().isoformat())
    ap.add_argument("--exec-date", default=(date.today() + timedelta(days=1)).isoformat())
    ap.add_argument("--operator", default="")
    from .kis_session import add_key_args, make_client
    add_key_args(ap)
    a = ap.parse_args(argv)
    state, sd, ed = load_state(), date.fromisoformat(a.signal_date), date.fromisoformat(a.exec_date)
    journal = Journal(journal_path())

    if a.action in ("plan", "verify"):
        rows = make_plan(state, *_inputs(a), sd, ed)
        if a.action == "plan":
            out = write_order_plan(rows, PLAN_DIR, signal_date=sd)
            journal.write("s02_plan", signal_date=sd.isoformat(), rows=[asdict(r) for r in rows])
            print(f"주문계획 {len(rows)}건 → {out}")
            return 0
        rep = compare_signals(rows, submitted(load_records(), sd.isoformat()))
        out = Path("var/reconcile") / f"signal_{sd:%Y%m%d}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"date": sd.isoformat(), **rep}, ensure_ascii=False, indent=2, default=str),
                       encoding="utf-8")
        print(f"C2 신호 일치: {'일치' if rep['match'] else '불일치'} (누락 {rep['missing']}, 초과 {rep['extra']})")
        return 0 if rep["match"] else 1

    if a.action == "submit":
        if not a.operator:
            ap.error("--operator 필요")
        plan_path = PLAN_DIR / f"order_plan_{sd:%Y%m%d}.csv"
        if not plan_path.exists():
            print(f"중단: 주문계획 파일 없음 ({plan_path})")
            return 2
        rows = read_plan(plan_path)
        today_kst = datetime.now(timezone(timedelta(hours=9))).date().isoformat()
        if any(row.get("signal_date") != sd.isoformat() or row.get("exec_date") != today_kst for row in rows):
            print(f"중단: 주문계획 날짜 불일치 (신호일 {sd}, 실행일 {today_kst} 필요)")
            return 2
        if not rows:
            print("주문계획 0건")
            return 0
        kis = make_client(a)
        from ..risk.stages import capital_error
        bal = kis.inquire_balance()
        if (err := capital_error(bal.get("cash_d2") or bal["cash"], state["starting_cash"])):
            print(f"중단: {err}")
            return 2
        prices = {r["ticker"]: kis.inquire_price(r["ticker"])["base_price"] for r in rows}
        state["start_of_day_equity"] = book_equity(state, prices)
        res = submit_plan(rows, state, kis, journal, prices, records=load_records())
        save_state(state)
        for r in res:
            print(f"  {r['side']} {r['ticker']} {r['qty']}주 → {r['status']} {r['message']}")
        return 0
    kis = make_client(a)
    if a.action == "cancel":
        done = cancel_open_entries(load_records(), kis, journal, datetime.now().strftime("%Y%m%d"))
        print(f"LDAY 잔량 취소 {len(done)}건: {done}")
        return 0
    if a.action == "sync":
        from .ledger_close import PAPER_COSTS
        records = load_records()
        state = sync_fills(state, records, kis.inquire_daily_orders(datetime.now().strftime("%Y%m%d")),
                           date.today(), PAPER_COSTS)
        journal.write("s02_sync", applied=state["applied"], cash=state["cash"], positions=state["positions"])
        state["peak_equity"] = max(state.get("peak_equity", 0), state["cash"] + sum(
            p["qty"] * p["entry_price"] for p in state["positions"].values()))
        save_state(state)
        print(f"가상 장부: 현금 {state['cash']:,}원, 보유 {list(state['positions'])}, 실현손익 {state['realized_pnl']:,}원")
        return 0
    return 2


if __name__ == "__main__":
    from .common import run_cli
    raise SystemExit(run_cli(main))

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
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta
from pathlib import Path

from .. import config
from ..audit import Journal
from ..config import TradingMode
from ..data import load_bars_csv
from ..domain import AccountSnapshot, OrderRequest, OrderStatus, OrderType, Position, Side
from ..execution import OrderRouter
from ..indicators import sma
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
def runner_limits():
    # 전략 주문: 손절가 필수, 청산은 장전 시장가(MOO) 허용 (COMMON_SPEC §7.1)
    return replace(limits_for(resolve_stage()), require_stop_for_buy=True, allow_market_orders=True)


def submit_plan(rows: list[dict], state: dict, kis, journal: Journal, prices: dict[str, int],
                mode: TradingMode | None = None) -> list[dict]:
    assert_paper_only(mode or config.resolve_mode(), kis)
    router = OrderRouter(kis, RiskGate(runner_limits(), kill_switch=KillSwitch()), journal, TradingMode.PAPER)
    cap = state["starting_cash"]
    positions = {s: Position(s, p["qty"], p["entry_price"]) for s, p in state["positions"].items()}
    over = frozenset(s for s, p in state["positions"].items() if p.get("exception") == ONE_SHARE_OVER)
    equity = book_equity(state, prices)
    sod = state.get("start_of_day_equity", equity)
    snap = AccountSnapshot(state["cash"], positions, prices, sod, principal=cap, peak_equity=state.get("peak_equity"),
                           over_exception_symbols=over)
    target = order_target(state["starting_cash"], state["realized_pnl"])
    results = []
    for r in sorted(rows, key=lambda x: x["side"] != "SELL"):   # 청산 먼저 (§6)
        if r["side"] == "SELL":
            o = OrderRequest(r["ticker"], Side.SELL, int(r["qty"]), OrderType.MARKET, None, STRATEGY_ID)
        else:
            o = OrderRequest(r["ticker"], Side.BUY, int(r["qty"]), OrderType.LIMIT, int(r["limit_price"]),
                             STRATEGY_ID, stop_price=float(r["init_stop"]), order_target=target,
                             sizing_exception=r.get("exception", ""))
        routed = router.submit(o, snap, reference_price=prices.get(r["ticker"]))
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
                          accepted=c.accepted, message=c.message)
            done.append(s["ticker"])
    return done


def sync_fills(state: dict, records: list[dict], broker_rows: list[dict], day: date, costs,
               params: S02Params = S02Params()) -> dict:
    """당일 체결 → 가상 장부 반영. 보유일수 +1 은 기존 보유분에만(진입일 = 0)."""
    by_odno = {r["odno"].lstrip("0"): r for r in broker_rows}
    done = set(state.setdefault("synced_orders", []))
    if state.get("last_sync_date") != day.isoformat():   # 같은 날 두 번 실행해도 보유일수는 한 번만 증가
        for p in state["positions"].values():
            p["holding_days"] += 1
        state["last_sync_date"] = day.isoformat()
    for s in submitted(records):
        b = by_odno.get((s.get("broker_order_id") or "").lstrip("0"))
        if not b or b["filled_qty"] <= 0 or s["client_order_id"] in done:
            continue
        qty, px = int(b["filled_qty"]), int(round(b["avg_price"]))
        gross = qty * px
        comm = costs.commission(gross)
        if s["side"] == "BUY":
            state["cash"] -= gross + comm
            state["positions"][s["ticker"]] = {"qty": qty, "entry_price": px, "entry_date": day.isoformat(),
                                               "init_stop": round(px * (1 - params.stop_pct), 2),  # 체결가 기준 재계산(§10)
                                               "holding_days": 0, "exception": s.get("exception", "")}
        else:
            pos = state["positions"].get(s["ticker"])
            tax = costs.sell_charges(gross, qty)
            state["cash"] += gross - comm - tax
            if pos:
                state["realized_pnl"] += (px - pos["entry_price"]) * qty - comm - tax
                pos["qty"] -= qty
                if pos["qty"] <= 0:
                    del state["positions"][s["ticker"]]
                    state["last_exits"][s["ticker"]] = day.isoformat()
        done.add(s["client_order_id"])
    state["synced_orders"] = sorted(done)
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
            out = write_order_plan(rows, PLAN_DIR) if rows else None
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

    kis = make_client(a)
    if a.action == "submit":
        if not a.operator:
            ap.error("--operator 필요")
        plan_path = PLAN_DIR / f"order_plan_{sd:%Y%m%d}.csv"
        rows = read_plan(plan_path) if plan_path.exists() else []
        prices = {r["ticker"]: kis.inquire_price(r["ticker"])["base_price"] for r in rows}
        state["start_of_day_equity"] = book_equity(state, prices)
        res = submit_plan(rows, state, kis, journal, prices)
        save_state(state)
        for r in res:
            print(f"  {r['side']} {r['ticker']} {r['qty']}주 → {r['status']} {r['message']}")
        return 0
    if a.action == "cancel":
        done = cancel_open_entries(load_records(), kis, journal, datetime.now().strftime("%Y%m%d"))
        print(f"LDAY 잔량 취소 {len(done)}건: {done}")
        return 0
    if a.action == "sync":
        from .ledger_close import PAPER_COSTS
        records = load_records()
        state = sync_fills(state, records, kis.inquire_daily_orders(datetime.now().strftime("%Y%m%d")),
                           date.today(), PAPER_COSTS)
        journal.write("s02_sync", synced=state["synced_orders"], cash=state["cash"], positions=state["positions"])
        state["peak_equity"] = max(state.get("peak_equity", 0), state["cash"] + sum(
            p["qty"] * p["entry_price"] for p in state["positions"].values()))
        save_state(state)
        print(f"가상 장부: 현금 {state['cash']:,}원, 보유 {list(state['positions'])}, 실현손익 {state['realized_pnl']:,}원")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

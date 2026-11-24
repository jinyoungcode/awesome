"""회계팀 장부 양식 v0.2 자동 작성 + 장부·잔고 대사 (D-024 C1).

근거: teams/05-finance/ledger/README.md v0.2 (claude/stock-king-05-finance 브랜치)
- trades.csv / positions.csv / daily_pnl.csv 를 같은 필드명으로 작성. 런타임 산출물은
  var/ledger/<mode>/<YYYY>/ (gitignore) — 회계팀 검증 후 회계팀이 teams/05-finance/ledger/ 로 옮긴다.
- book: 감사 로그의 증권사 접수 주문번호와 매칭되면 AUTO, 아니면 MANUAL (D-029)
- 리스크 한도용 수치는 AUTO 만 사용
- 국내 주식만 (미국·환율 필드는 PR #2 병합 후 채움: 지금은 currency=KRW, fx_rate=1)

대사 방식 (system book vs broker)
- 장 시작 전 스냅샷(open_state.json): 증권사 잔고(D+2 예수금·보유수량)를 시스템 장부의 시작점으로 저장
- 장 마감 후: 시작점 + 당일 체결(수수료·세금 포함 net) = 시스템 기대 현금·수량
  → 증권사 마감 잔고와 1원·1주 단위 비교. 하나라도 다르면 recon_status=DIFF
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from .backtest.costs import CostModel

TRADES_FIELDS = ["trade_id", "trade_date", "settle_date", "account", "mode", "book", "market", "currency", "ticker",
                 "name", "side", "qty", "price", "gross_amount", "commission", "sell_tax", "sec_fee", "finra_taf",
                 "other_fee", "net_amount", "fx_rate", "net_amount_krw", "order_id", "strategy_id", "reason_code",
                 "note"]
POSITIONS_FIELDS = ["date", "account", "mode", "book", "ticker", "name", "market", "currency", "qty", "avg_cost",
                    "close_price", "market_value", "fx_rate", "market_value_krw", "unrealized_pnl_krw", "weight"]
DAILY_FIELDS = ["date", "account", "mode", "book", "start_equity_krw", "cash_flow_krw", "end_equity_krw", "krw_cash",
                "usd_cash", "usdkrw_rate", "realized_pnl_krw", "unrealized_pnl_krw", "fx_pnl_krw", "commission_krw",
                "sell_tax_krw", "us_reg_fee_krw", "dividend_net_krw", "daily_return_twr", "cum_return_twr",
                "peak_equity_krw", "drawdown", "us_tax_reserve_krw", "broker_equity_krw", "recon_diff_krw",
                "recon_status", "note"]


def settle_date(trade_day: date, holidays: set[date] = frozenset()) -> date:
    """국내 T+2 결제일 (주말·휴장일 제외). 휴장일 캘린더는 자료팀 제공 예정."""
    d, n = trade_day, 0
    while n < 2:
        d += timedelta(days=1)
        if d.weekday() < 5 and d not in holidays:
            n += 1
    return d


def journal_index(records: list[dict]) -> dict[str, dict]:
    """증권사 주문번호(앞자리 0 제거) → {strategy_id, reason_code, client_order_id}"""
    orders = {r["order"]["client_order_id"]: r["order"] for r in records if r.get("event") == "order_request"}
    tech = {r.get("client_order_id") for r in records if r.get("event") == "tech_order"}
    idx = {}
    for r in records:
        if r.get("event") == "broker_response" and r.get("accepted") and r.get("broker_order_id"):
            cid = r["client_order_id"]
            o = orders.get(cid, {})
            sid = o.get("strategy_id", "")
            idx[str(r["broker_order_id"]).lstrip("0")] = {
                "client_order_id": cid, "strategy_id": sid,
                "reason_code": "T_TECH" if (sid == "T_TECH" or cid in tech) else ""}
    return idx


def trade_rows(day: date, broker_orders: list[dict], jidx: dict[str, dict], costs: CostModel, mode: str,
               account: str, market_of: dict[str, str] | None = None) -> list[dict]:
    """체결된 주문 → trades.csv 행. 수수료·세금은 체결내역 값이 없으면 비용모델 추정(note 표시)."""
    rows = []
    for i, o in enumerate(sorted(broker_orders, key=lambda x: int(x["odno"] or 0))):  # 주문번호 숫자순
        qty = int(o["filled_qty"])
        if qty <= 0:
            continue
        price = int(round(o["avg_price"]))
        gross = qty * price
        comm = o.get("commission")
        tax = o.get("sell_tax")
        est = comm is None or tax is None
        comm = costs.commission(gross) if comm is None else int(comm)
        tax = (costs.sell_tax(gross) if o["side"] == "sell" else 0) if tax is None else int(tax)
        net = (gross - comm - tax) if o["side"] == "sell" else -(gross + comm)
        meta = jidx.get(o["odno"].lstrip("0"))
        rows.append({
            "trade_id": f"{day:%Y%m%d}-{i + 1:04d}", "trade_date": day.isoformat(),
            "settle_date": settle_date(day).isoformat(), "account": account, "mode": mode,
            "book": "AUTO" if meta else "MANUAL", "market": (market_of or {}).get(o["symbol"], "KRX"),
            "currency": "KRW", "ticker": o["symbol"], "name": o.get("name", ""), "side": o["side"].upper(),
            "qty": qty, "price": price, "gross_amount": gross, "commission": comm, "sell_tax": tax,
            "sec_fee": 0, "finra_taf": 0, "other_fee": 0, "net_amount": net, "fx_rate": 1, "net_amount_krw": net,
            "order_id": o["odno"], "strategy_id": (meta or {}).get("strategy_id", ""),
            "reason_code": (meta or {}).get("reason_code", ""),
            "note": f"수수료·세금 추정({costs.source})" if est else ""})
    return rows


@dataclass
class ReconResult:
    status: str                 # OK / DIFF
    cash_expected: int
    cash_broker: int
    qty_diffs: dict[str, tuple[int, int]]   # 종목: (기대, 증권사)
    equity_broker: int
    diff_krw: int


def reconcile(open_state: dict, trades: list[dict], close_balance: dict) -> ReconResult:
    """시작점 + 당일 체결 = 마감 기대값 → 증권사 마감 잔고와 비교 (1원·1주 단위)."""
    cash = int(open_state["cash_d2"]) + sum(int(t["net_amount_krw"]) for t in trades)
    qty = {s: int(q) for s, q in open_state["qty"].items()}
    for t in trades:
        qty[t["ticker"]] = qty.get(t["ticker"], 0) + (t["qty"] if t["side"] == "BUY" else -t["qty"])
    broker_qty = {s: d["qty"] for s, d in close_balance.get("details", {}).items()}
    diffs = {s: (qty.get(s, 0), broker_qty.get(s, 0)) for s in set(qty) | set(broker_qty)
             if qty.get(s, 0) != broker_qty.get(s, 0)}
    cash_b = int(close_balance["cash_d2"])
    diff = cash_b - cash
    return ReconResult("OK" if diff == 0 and not diffs else "DIFF", cash, cash_b, diffs,
                       int(close_balance["total_equity"]), diff)


def position_rows(day: date, balance: dict, mode: str, account: str, manual_symbols: set[str],
                  market_of: dict[str, str] | None = None) -> list[dict]:
    eq = int(balance["total_equity"]) or 1
    out = []
    for s, d in sorted(balance.get("details", {}).items()):
        mv = int(d["market_value"]) or d["qty"] * d["close_price"]
        out.append({"date": day.isoformat(), "account": account, "mode": mode,
                    "book": "MANUAL" if s in manual_symbols else "AUTO", "ticker": s, "name": d.get("name", ""),
                    "market": (market_of or {}).get(s, "KRX"), "currency": "KRW", "qty": d["qty"],
                    "avg_cost": d["avg_cost"], "close_price": d["close_price"], "market_value": mv, "fx_rate": 1,
                    "market_value_krw": mv, "unrealized_pnl_krw": math.floor(mv - d["avg_cost"] * d["qty"]),
                    "weight": round(mv / eq, 6)})
    return out


def daily_rows(day: date, mode: str, account: str, open_state: dict, trades: list[dict], positions: list[dict],
               balance: dict, recon: ReconResult, prev: dict | None) -> list[dict]:
    """book 별 1행. AUTO/MANUAL 각각 체결·보유분으로 나눠 집계. 현금은 AUTO 에 귀속(전용 계좌 원칙, D-030)."""
    rows = []
    for book in ("AUTO", "MANUAL"):
        bt = [t for t in trades if t["book"] == book]
        bp = [p for p in positions if p["book"] == book]
        if book == "MANUAL" and not bt and not bp:
            continue
        pos_val = sum(p["market_value_krw"] for p in bp)
        cash = recon.cash_broker if book == "AUTO" else 0
        end = cash + pos_val
        start = (prev or {}).get(book, {}).get("end_equity_krw") or open_state.get("equity", {}).get(book, end)
        start = int(start)
        peak = max(int((prev or {}).get(book, {}).get("peak_equity_krw") or start), end)
        rows.append({
            "date": day.isoformat(), "account": account, "mode": mode, "book": book, "start_equity_krw": start,
            "cash_flow_krw": 0, "end_equity_krw": end, "krw_cash": cash, "usd_cash": 0, "usdkrw_rate": "",
            "realized_pnl_krw": "", "unrealized_pnl_krw": sum(p["unrealized_pnl_krw"] for p in bp),
            "fx_pnl_krw": 0, "commission_krw": sum(t["commission"] for t in bt),
            "sell_tax_krw": sum(t["sell_tax"] for t in bt), "us_reg_fee_krw": 0, "dividend_net_krw": 0,
            "daily_return_twr": round(end / start - 1, 8) if start else "", "cum_return_twr": "",
            "peak_equity_krw": peak, "drawdown": round(end / peak - 1, 8) if peak else "", "us_tax_reserve_krw": 0,
            "broker_equity_krw": recon.equity_broker if book == "AUTO" else "",
            "recon_diff_krw": recon.diff_krw if book == "AUTO" else "",
            "recon_status": recon.status if book == "AUTO" else "",
            "note": ("수량 불일치 " + json.dumps(recon.qty_diffs, ensure_ascii=False)) if recon.qty_diffs and book == "AUTO" else ""})
    return rows


def append_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerows(rows)

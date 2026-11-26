"""장부 작성·잔고 대사 (D-024 C1). 장 시작 전 스냅샷 → 장 마감 후 장부 작성·대사.

python -m stockking.ops.ledger_close --open            # 08:30 장 시작 전: 증권사 잔고를 시작점으로 저장
python -m stockking.ops.ledger_close --close           # 15:40 장 마감 후: trades/positions/daily_pnl 작성 + 대사

계좌는 별칭만 기록(기본 KIS-PAPER-01). 계좌번호·키는 장부에 남기지 않는다.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path

from ..backtest.costs import CostModel
from ..ledger import (DAILY_FIELDS, POSITIONS_FIELDS, TRADES_FIELDS, append_csv, daily_rows, journal_index,
                      position_rows, reconcile, trade_rows)
from .common import Checklist
from .kis_session import add_key_args, make_client
from .tech_orders import load_records

# 회계팀 COST_MODEL v0.2.4 국내 기본(D-045: 현 계좌 영업점 요율 0.147% → 0.15% 보수) — 실측(C-2)으로 확정 예정
PAPER_COSTS = CostModel.kr_v024(slippage_bps=0)


def ledger_dir(mode: str, day: date) -> Path:
    return Path("var/ledger") / mode / f"{day:%Y}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--open", action="store_true")
    g.add_argument("--close", action="store_true")
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    ap.add_argument("--account", default="KIS-PAPER-01")
    ap.add_argument("--mode", default="paper", choices=["paper"])  # 실전 장부는 T0 승인 후 추가
    add_key_args(ap)
    a = ap.parse_args(argv)
    day = datetime.strptime(a.date, "%Y%m%d").date()
    d = ledger_dir(a.mode, day)
    state_path = d / f"open_state_{a.date}.json"
    kis = make_client(a)
    bal = kis.inquire_balance()

    if a.open:
        from ..risk.stages import _auto_requested, fix_auto_capital, operating_capital
        if _auto_requested():   # D-046: 1일차 첫 실행 시점의 주문 가능 금액 전액으로 고정
            print(f"운용 금액 auto: {fix_auto_capital(bal['cash_d2'] or bal['cash']):,}원 (var/operating_capital.json)")
        state = {"date": a.date, "cash_d2": bal["cash_d2"], "equity": {"AUTO": bal["total_equity"]},
                 "principal": operating_capital(),   # D-046: 장부 원금 = 운용 금액
                 "qty": {s: v["qty"] for s, v in bal["details"].items()}}
        d.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"시작점 저장: {state_path} (D+2 예수금 {bal['cash_d2']:,}원, 보유 {len(state['qty'])}종목)")
        return 0

    if not state_path.exists():
        print(f"시작점 파일이 없습니다: {state_path} — 장 시작 전에 --open 을 먼저 실행하세요")
        return 2
    open_state = json.loads(state_path.read_text(encoding="utf-8"))
    trades = trade_rows(day, kis.inquire_daily_orders(a.date), journal_index(load_records()), PAPER_COSTS,
                        a.mode, a.account)
    manual = {t["ticker"] for t in trades if t["book"] == "MANUAL"}
    positions = position_rows(day, bal, a.mode, a.account, manual)
    recon = reconcile(open_state, trades, bal)
    prev_path = d / "last_daily.json"
    prev = json.loads(prev_path.read_text(encoding="utf-8")) if prev_path.exists() else None
    daily = daily_rows(day, a.mode, a.account, open_state, trades, positions, bal, recon, prev)
    append_csv(d / "trades.csv", TRADES_FIELDS, trades)
    append_csv(d / "positions.csv", POSITIONS_FIELDS, positions)
    append_csv(d / "daily_pnl.csv", DAILY_FIELDS, daily)
    prev_path.write_text(json.dumps({r["book"]: r for r in daily}, ensure_ascii=False, default=str), encoding="utf-8")
    Path("var/reconcile").mkdir(parents=True, exist_ok=True)
    (Path("var/reconcile") / f"ledger_{a.date}.json").write_text(json.dumps(
        {"date": a.date, "recon_status": recon.status, "cash_expected": recon.cash_expected,
         "cash_broker": recon.cash_broker, "qty_diffs": recon.qty_diffs, "diff_krw": recon.diff_krw},
        ensure_ascii=False, indent=2), encoding="utf-8")

    cl = Checklist(f"장부·잔고 대사 {a.date}")
    cl.check("현금(D+2 예수금) 1원 단위 일치", recon.diff_krw == 0,
             f"기대 {recon.cash_expected:,} / 증권사 {recon.cash_broker:,} / 차이 {recon.diff_krw:,}원")
    cl.check("보유 수량 일치", not recon.qty_diffs, json.dumps(recon.qty_diffs, ensure_ascii=False))
    cl.check("수동(MANUAL) 체결 없음 — 있으면 별도 집계(D-029)", not manual, f"{sorted(manual)}")
    print(f"장부: {d}/trades.csv ({len(trades)}건), positions.csv ({len(positions)}종목), daily_pnl.csv")
    if any("추정" in t["note"] for t in trades):
        print("  ※ 수수료·세금은 비용모델 추정값 — 현금 차이가 수수료 반올림에서 나는지 회계팀 C-2 실측으로 확인")
    return cl.finish()


if __name__ == "__main__":
    from .common import run_cli
    raise SystemExit(run_cli(main))

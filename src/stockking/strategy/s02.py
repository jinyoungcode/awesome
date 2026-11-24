"""S02 상승추세 눌림목 반등(MR-PB) — 신호 생성 골격.

근거: teams/04-trading/strategies/S02_MR_PULLBACK.md v0.1, COMMON_SPEC.md §3~§5, §8, §11~§13, §15
(claude/stock-king-04-trading 브랜치). 매매팀이 15만 원 기준 수치를 확정하면 파라미터만 바꾼다.

이 모듈은 '무엇을 사고팔지'만 정한다. 수량(D-005 사이징·D-021/D-025 1주 예외)은 주입된 sizer 가
정하고(PR #2 sizing.py 병합 후 연결), 주문 전 리스크 관문은 OrderRouter 가 맡는다.
"""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

from ..data import Bar
from ..indicators import ret, rsi_wilder, sma
from ..krx import floor_to_tick

STRATEGY_ID = "S02"


@dataclass(frozen=True)
class S02Params:
    rsi_entry: float = 10
    time_stop_days: int = 5
    stop_pct: float = 0.08
    rsi_len: int = 2
    trend_ma: int = 200
    exit_ma: int = 5
    crash_ret1d: float = -0.10
    entry_limit_mult: float = 1.00
    max_positions: int = 6
    max_new_per_day: int = 3
    total_slots: int = 5            # COMMON_SPEC §15.1: 전 전략 합산 동시 보유 5종목


@dataclass(frozen=True)
class HeldPosition:
    symbol: str
    qty: int
    init_stop: float
    holding_days: int               # 진입일 = 0
    strategy_id: str = STRATEGY_ID


@dataclass
class PlanRow:
    """COMMON_SPEC §12.1 order_plan 스키마 + exception 표시(§15.1)."""
    signal_date: str
    exec_date: str
    strategy_id: str
    ticker: str
    side: str
    order_type: str
    qty: int | None
    limit_price: int | None
    init_stop: float | None
    risk_krw: float | None
    reason_code: str
    rank: int | None = None
    exception: str = ""
    notes: list[str] = field(default_factory=list)


# sizer(ticker, limit_price, stop_ref) -> (qty | None, exception_flag)
Sizer = Callable[[str, int, float], tuple[int | None, str]]


def _closes(bars: list[Bar]) -> list[float]:
    return [float(b.close) for b in bars]


def exit_signals(held: list[HeldPosition], history: dict[str, list[Bar]], p: S02Params,
                 forced: dict[str, str] | None = None) -> list[tuple[HeldPosition, str]]:
    """§5 청산 판정 — 첫 번째로 맞는 사유 하나. forced = {종목: X_ADMIN|X_KILL} (COMMON_SPEC §8, 최우선)."""
    out = []
    for h in sorted(held, key=lambda x: x.symbol):
        if h.strategy_id != STRATEGY_ID:
            continue
        if forced and h.symbol in forced:
            out.append((h, forced[h.symbol]))
            continue
        closes = _closes(history.get(h.symbol, []))
        if not closes:
            continue  # 거래정지 등 — 데이터 없으면 판정 보류
        c = closes[-1]
        ma = sma(closes, p.exit_ma)
        if c <= h.init_stop:
            out.append((h, "X_STOP"))
        elif ma is not None and c > ma:
            out.append((h, "X_TP"))
        elif h.holding_days >= p.time_stop_days:
            out.append((h, "X_TIME"))
    return out


def entry_candidates(history: dict[str, list[Bar]], universe: set[str], regime_on: Callable[[str], bool],
                     held_any: set[str], exited_prev_day: set[str], p: S02Params) -> list[dict]:
    """§3 E1~E7 통과 종목을 순위대로 (RSI 오름차순 → ADV20 내림차순 → 종목코드)."""
    cands = []
    for sym in sorted(universe):                                   # E1
        bars = history.get(sym, [])
        closes = _closes(bars)
        if not closes or not regime_on(sym):                       # E2
            continue
        ma200 = sma(closes, p.trend_ma)
        if ma200 is None or not closes[-1] > ma200:                # E3
            continue
        r = rsi_wilder(closes, p.rsi_len)
        if r is None or not r < p.rsi_entry:                       # E4
            continue
        r1 = ret(closes, 1)
        if r1 is None or not r1 > p.crash_ret1d:                   # E5
            continue
        if sym in held_any or sym in exited_prev_day:              # E6, E7
            continue
        adv20 = sum(b.close * b.volume for b in bars[-20:]) / min(20, len(bars))  # 거래대금 필드 수령 전 근사
        cands.append({"symbol": sym, "rsi": r, "adv20": adv20, "close": bars[-1].close})
    cands.sort(key=lambda c: (c["rsi"], -c["adv20"], c["symbol"]))
    return cands


def build_order_plan(signal_date: date, exec_date: date, history: dict[str, list[Bar]], universe: set[str],
                     regime_on: Callable[[str], bool], held: list[HeldPosition], exited_prev_day: set[str],
                     p: S02Params = S02Params(), sizer: Sizer | None = None,
                     forced: dict[str, str] | None = None, data_qc_ok: bool = True) -> list[PlanRow]:
    """COMMON_SPEC §11 순서: 강제청산 → 청산 → 진입 후보 → 사이징. 결정론적."""
    sd, ed = signal_date.isoformat(), exec_date.isoformat()
    rows = [PlanRow(sd, ed, STRATEGY_ID, h.symbol, "SELL", "MOO", h.qty, None, None, None, code)
            for h, code in exit_signals(held, history, p, forced)]
    if not data_qc_ok:   # §6: 데이터 품질 실패 시 신규 진입 없음(청산은 생성)
        return rows
    n_after_exit = len(held) - len(rows)
    slots_left = min(p.max_positions, p.total_slots - n_after_exit)
    n_new = max(0, min(p.max_new_per_day, slots_left))
    held_syms = {h.symbol for h in held}
    rank = 0
    for c in entry_candidates(history, universe, regime_on, held_syms, exited_prev_day, p):
        if rank >= n_new:
            break
        limit = floor_to_tick(int(c["close"] * p.entry_limit_mult))
        stop_ref = limit * (1 - p.stop_pct)
        qty, exc = (sizer(c["symbol"], limit, stop_ref) if sizer else (None, ""))
        row = PlanRow(sd, ed, STRATEGY_ID, c["symbol"], "BUY", "LDAY", qty, limit, round(stop_ref, 2),
                      None if qty is None else round((limit - stop_ref) * qty, 2), "E_S02_PB", rank + 1, exc)
        if sizer and qty is None:
            continue   # 사이징 불가(1주 예외 한도 초과 등) → 다음 순위 후보
        if not sizer:
            row.notes.append("qty 미정: D-005 sizer 연결 대기(PR #2)")
        rows.append(row)
        rank += 1
    return rows


PLAN_FIELDS = ["signal_date", "exec_date", "strategy_id", "ticker", "side", "order_type", "qty",
               "limit_price", "init_stop", "risk_krw", "reason_code", "rank", "exception"]


def write_order_plan(rows: list[PlanRow], out_dir: str | Path = "var/order_plan") -> Path:
    out = Path(out_dir) / f"order_plan_{rows[0].signal_date.replace('-', '') if rows else 'empty'}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=PLAN_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if v is None else v) for k, v in asdict(r).items()})
    return out

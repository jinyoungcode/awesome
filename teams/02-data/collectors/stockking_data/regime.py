"""국면 필터 상태·복귀 예상 (D-048, S02 COMMON_SPEC §4: 지수 종가 > SMA200 이면 ON).

- regime_status: T일과 전일의 ON/OFF, 전환 여부
- outlook: 시나리오별로 '지수 종가 > 200일 평균'이 처음 성립하는 미래 거래일 수와 날짜
  (미래 경로를 가정해 이어 붙이고 매일 SMA200을 다시 계산하는 단순 시뮬레이션)
  날짜는 **주말만 제외한 근사**이며 공휴일만큼 늦어질 수 있다.
"""

from __future__ import annotations

from datetime import date, timedelta

SMA_N = 200
SCENARIOS = {               # 이름: 월간(21거래일) 수익률
    "횡보(지금 수준 유지)": 0.0,
    "완만한 반등(월 +3%)": 0.03,
    "보통 반등(월 +5%)": 0.05,
    "강한 반등(월 +10%)": 0.10,
}
MAX_DAYS = 750


def sma(xs: list[float], n: int = SMA_N) -> float | None:
    return sum(xs[-n:]) / n if len(xs) >= n else None


def regime_status(closes: list[float]) -> dict:
    s_t, s_p = sma(closes), sma(closes[:-1])
    on = s_t is not None and closes[-1] > s_t
    prev_on = s_p is not None and closes[-2] > s_p
    return {"close": round(closes[-1], 2), "sma200": round(s_t, 2) if s_t else None, "on": on,
            "prev_on": prev_on, "changed": on != prev_on,
            "gap_pct": round((closes[-1] / s_t - 1) * 100, 2) if s_t else None}


def next_weekdays(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            out.append(d)
    return out


def days_to_within(closes: list[float], monthly: float, pct: float = 0.01, max_days: int = MAX_DAYS) -> int | None:
    """200일 평균이 지수의 (1+pct) 이내로 좁혀지는 첫 거래일 수. 횡보 시나리오는 평균이 지수와 '같아지기만'
    하고 넘지 못할 수 있어(최근 200일이 모두 같은 값이면 평균 = 지수), 이 값을 함께 본다."""
    path = list(closes)
    daily = (1 + monthly) ** (1 / 21)
    for k in range(0, max_days + 1):
        if k:
            path.append(path[-1] * daily)
        s = sma(path)
        if s is not None and s <= path[-1] * (1 + pct):
            return k
    return None


def days_to_on(closes: list[float], monthly: float, max_days: int = MAX_DAYS) -> tuple[int | None, float | None, float | None]:
    """(거래일 수, 그날 지수, 그날 SMA200). 이미 ON이면 0."""
    path = list(closes)
    if sma(path) is not None and path[-1] > sma(path):
        return 0, path[-1], sma(path)
    daily = (1 + monthly) ** (1 / 21)
    for k in range(1, max_days + 1):
        path.append(path[-1] * daily)
        s = sma(path)
        if s is not None and path[-1] > s:
            return k, path[-1], s
    return None, None, None


def outlook(series: list[tuple[str, float]], scenarios: dict[str, float] | None = None) -> dict:
    closes = [c for _, c in series]
    last = date.fromisoformat(series[-1][0])
    cal = next_weekdays(last, MAX_DAYS)
    res = {"as_of": series[-1][0], **regime_status(closes), "scenarios": []}
    for name, m in (scenarios or SCENARIOS).items():
        k, lvl, s = days_to_on(closes, m)
        w = days_to_within(closes, m)
        res["scenarios"].append({
            "within_1pct_trading_days": w, "within_1pct_approx_date": (cal[w - 1].isoformat() if w else res["as_of"]) if w is not None else None,
            "scenario": name, "monthly_return": m,
            "trading_days": k, "approx_date": (cal[k - 1].isoformat() if k else res["as_of"]) if k is not None else None,
            "index_at_cross": round(lvl, 2) if lvl else None, "sma200_at_cross": round(s, 2) if s else None,
        })
    return res

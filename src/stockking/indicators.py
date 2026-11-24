"""지표 (매매팀 COMMON_SPEC §5 정의 그대로). 입력은 과거→현재 순 리스트, 마지막 값이 T일."""

from __future__ import annotations

RSI_WARMUP = 50  # COMMON_SPEC §5: 워밍업 최소 50봉


def sma(xs: list[float], n: int) -> float | None:
    return sum(xs[-n:]) / n if n > 0 and len(xs) >= n else None


def rsi_wilder(closes: list[float], n: int = 2, warmup: int = RSI_WARMUP) -> float | None:
    """Wilder RSI. 첫 평균 = 최초 n개 변화량 단순평균, 이후 avg = (avg·(n−1) + x)/n.
    avg_loss=0 → 100, avg_gain=avg_loss=0 → 50. 봉 수가 warmup 미만이면 None."""
    if len(closes) < max(warmup, n + 1):
        return None
    diffs = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0.0) for d in diffs]
    losses = [max(-d, 0.0) for d in diffs]
    ag, al = sum(gains[:n]) / n, sum(losses[:n]) / n
    for g, l in zip(gains[n:], losses[n:]):
        ag, al = (ag * (n - 1) + g) / n, (al * (n - 1) + l) / n
    if al == 0:
        return 50.0 if ag == 0 else 100.0
    return 100.0 - 100.0 / (1.0 + ag / al)


def ret(closes: list[float], n: int = 1) -> float | None:
    return closes[-1] / closes[-1 - n] - 1 if len(closes) > n and closes[-1 - n] else None

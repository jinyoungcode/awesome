"""S03 ETF 듀얼모멘텀 백테스트용 ETF 데이터 (D-048 ③, S03 명세 §2·§4·§7·§8).

필요 데이터
- ETF 일봉(원가격): 069500 KODEX 200, 229200 KODEX 코스닥150, 114260 KODEX 국고채3년, (V3) 132030 KODEX 골드선물(H)
- 분배금 이력(지급기준일·주당 분배금) → **TR 가격**(분배금 재투자) 계산
- 229200 상장(2015-10) 이전: KOSDAQ150 지수 − 연 0.25% 보수로 이어 붙인 proxy
- 벤치마크: KOSPI200 TR 지수 (KRX 개별지수 시세 추이 CSV)

원천
- 일봉: KIS 기간별 시세(FHKST03010100, 원주가 FID_ORG_ADJ_PRC=1) — ETF도 같은 TR
- 분배금: KRX 정보데이터시스템 [ETF 분배금 지급 현황] CSV 수동 다운로드 (KIS 배당 API는 ⚠ 미확인)
- 지수: KIS 업종 기간별 시세 또는 KRX CSV
"""

from __future__ import annotations

import csv
import io
from datetime import date, timedelta
from pathlib import Path

from .constituents import _decode

S03_ETFS = {"069500": "KODEX 200", "229200": "KODEX 코스닥150", "114260": "KODEX 국고채3년", "132030": "KODEX 골드선물(H)"}
PROXY_FEE = 0.0025          # 229200 상장 전 proxy: 지수 − 연 0.25%
TRADING_DAYS = 252


def fetch_etf_bars(q, symbols: list[str], start: date, end: date, log=print) -> list[dict]:
    """원가격(분배금·분할 미반영) 일봉. 분할은 KIS 수정주가와 비교해 별도 점검."""
    out = []
    for i, s in enumerate(symbols, 1):
        rows = q.daily_bars(s, start, end, adjusted=False)
        out += rows
        log(f"  [{i}/{len(symbols)}] {s} {S03_ETFS.get(s, '')} {len(rows)}행 {rows[0]['date'] if rows else '-'}~{rows[-1]['date'] if rows else '-'}")
    return out


def read_distributions(path: Path) -> dict[str, dict[str, float]]:
    """KRX 분배금 CSV → {종목코드: {기준일(YYYY-MM-DD): 주당 분배금}}.
    열 이름: 종목코드(또는 단축코드) / 기준일·지급기준일·배당기준일 / 주당분배금·분배금액 (자동 탐색)."""
    rows = list(csv.reader(io.StringIO(_decode(Path(path).read_bytes()))))
    h = [c.strip() for c in rows[0]]

    def find(*keys):
        for i, c in enumerate(h):
            if any(k in c for k in keys):
                return i
        raise ValueError(f"분배금 CSV에 {keys} 열이 없습니다: {h}")
    ci, di, vi = find("종목코드", "단축코드"), find("기준일"), find("주당분배금", "분배금액", "분배금")
    out: dict[str, dict[str, float]] = {}
    for r in rows[1:]:
        if len(r) <= max(ci, di, vi) or not r[di].strip():
            continue
        code = r[ci].strip().zfill(6)
        d = r[di].strip().replace("/", "-").replace(".", "-")
        if len(d) == 8 and d.isdigit():
            d = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        out.setdefault(code, {})[d] = out.get(code, {}).get(d, 0.0) + float(r[vi].replace(",", "") or 0)
    return out


def tr_series(bars: list[dict], dists: dict[str, float]) -> list[dict]:
    """TR 가격(분배금 재투자). 국내 T+2 결제라 **분배락일 = 지급기준일 직전 거래일**.
    분배락일에 계수 × (1 + 분배금 / 락 전일 종가). tr_close = close × 누적계수 (첫날 1)."""
    bars = sorted(bars, key=lambda b: b["date"])
    dates = [b["date"] for b in bars]
    ex_amt: dict[int, float] = {}
    for d, amt in dists.items():
        i = max((k for k, x in enumerate(dates) if x < d), default=None)   # 기준일 직전 거래일 = 분배락일
        if i is not None and i > 0:
            ex_amt[i] = ex_amt.get(i, 0.0) + amt
    factor, out = 1.0, []
    for i, b in enumerate(bars):
        if i in ex_amt and bars[i - 1]["close"]:
            factor *= 1 + ex_amt[i] / bars[i - 1]["close"]
        out.append({**b, "tr_factor": round(factor, 8), "tr_close": round(b["close"] * factor, 4)})
    return out


def proxy_from_index(index_rows: list[dict], listing_first: dict, fee: float = PROXY_FEE) -> list[dict]:
    """상장 전 구간: 지수 수익률 − 연 fee 로 ETF 가격을 거꾸로 이어 붙인다(상장 첫날 종가에 맞춤)."""
    idx = sorted(index_rows, key=lambda r: r["date"])
    pre = [r for r in idx if r["date"] < listing_first["date"]]
    if not pre:
        return []
    anchor = next((r for r in idx if r["date"] >= listing_first["date"]), None)
    if anchor is None:
        return []
    daily_fee = (1 - fee) ** (1 / TRADING_DAYS)
    out = []
    level = listing_first["tr_close"] if "tr_close" in listing_first else listing_first["close"]
    nxt = anchor
    for r in reversed(pre):
        level = level / (nxt["close"] / r["close"]) / daily_fee
        out.append({"date": r["date"], "symbol": listing_first["symbol"], "close": round(level, 4), "tr_close": round(level, 4),
                    "open": round(level, 4), "high": round(level, 4), "low": round(level, 4), "volume": 0, "value": 0,
                    "tr_factor": 1.0, "proxy": True})
        nxt = r
    return list(reversed(out))

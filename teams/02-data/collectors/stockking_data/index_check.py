"""지수 일봉 점검 (D-047 대응): KIS 모의 지수값이 정상인지 대조.

1) 내부 점검: 하루 등락률 |r| > 8% 인 날 목록 (KOSPI200 가격제한·서킷브레이커 고려 시 매우 드묾)
2) KIS 재조회: 의심일 + 표본 5일을 **하루짜리 구간**으로 다시 조회해 index.csv 값과 비교
   (페이지 넘김·구간 합치기 오류인지, KIS 원천값 자체 문제인지 구분)
3) KRX 대조(선택): KRX 정보데이터시스템 '개별지수 시세 추이' CSV(일자·종가)와 비교
"""

from __future__ import annotations

import csv
import io
from datetime import date
from pathlib import Path

from .constituents import _decode
from .s02_inputs import KOSPI200_CODE

JUMP = 0.08


def read_index_csv(path: Path) -> dict[str, list[tuple[str, float]]]:
    out: dict[str, list[tuple[str, float]]] = {}
    with path.open(encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            out.setdefault(r["symbol"], []).append((r["date"], float(r["close"])))
    return {k: sorted(v) for k, v in out.items()}


def read_krx_index_csv(path: Path) -> dict[str, float]:
    """KRX '개별지수 시세 추이' CSV → {YYYY-MM-DD: 종가}. 열 이름: 일자/종가 (인코딩 자동)."""
    rows = list(csv.reader(io.StringIO(_decode(Path(path).read_bytes()))))
    h = [c.strip() for c in rows[0]]
    di = next(i for i, c in enumerate(h) if "일자" in c or c.lower() == "date")
    ci = next(i for i, c in enumerate(h) if c in ("종가", "현재지수", "close") or "종가" in c)
    out = {}
    for r in rows[1:]:
        if len(r) <= max(di, ci) or not r[di].strip():
            continue
        d = r[di].strip().replace("/", "-").replace(".", "-")
        if len(d) == 8 and d.isdigit():
            d = f"{d[:4]}-{d[4:6]}-{d[6:]}"
        out[d] = float(r[ci].replace(",", ""))
    return out


def jumps(series: list[tuple[str, float]]) -> list[dict]:
    out = []
    for (d0, c0), (d1, c1) in zip(series, series[1:]):
        if c0 and abs(c1 / c0 - 1) > JUMP:
            out.append({"date": d1, "prev_date": d0, "prev": c0, "close": c1, "ret_pct": round((c1 / c0 - 1) * 100, 2)})
    return out


def sample_dates(series: list[tuple[str, float]], n: int = 5) -> list[str]:
    if not series:
        return []
    idx = sorted({round(i * (len(series) - 1) / (n - 1)) for i in range(n)})
    return [series[i][0] for i in idx]


def check(index_csv: Path, q=None, kosdaq150_code: str | None = None,
          krx: dict[str, Path] | None = None) -> dict:
    data = read_index_csv(index_csv)
    codes = {"KOSPI200": KOSPI200_CODE, "KOSDAQ150": kosdaq150_code}
    report: dict = {"index_csv": str(index_csv), "rule": f"|하루 등락률| > {JUMP:.0%}"}
    for name, series in data.items():
        j = jumps(series)
        closes = [c for _, c in series]
        sma200 = sum(closes[-200:]) / 200 if len(closes) >= 200 else None
        sec: dict = {"rows": len(series), "first": series[0], "last": series[-1],
                     "sma200": round(sma200, 2) if sma200 else None, "jumps": j}
        targets = sorted(set(sample_dates(series)) | {x["date"] for x in j} | {x["prev_date"] for x in j})
        by_date = dict(series)
        if q is not None and codes.get(name):
            refetch = []
            for d in targets:
                dd = date.fromisoformat(d)
                _, rows = q.index_bars(codes[name], dd, dd)
                kis1 = rows[0]["close"] if rows else None
                refetch.append({"date": d, "index_csv": by_date.get(d), "kis_single_day": kis1,
                                "match": kis1 is not None and abs(kis1 - by_date.get(d, 0)) < 0.01})
            sec["kis_refetch"] = refetch
        if krx and krx.get(name):
            ref = read_krx_index_csv(krx[name])
            cmp_ = [{"date": d, "index_csv": by_date.get(d), "krx": ref.get(d),
                     "diff_pct": round((by_date[d] / ref[d] - 1) * 100, 3) if d in ref and d in by_date else None}
                    for d in targets]
            sec["krx_compare"] = cmp_
            sec["krx_max_abs_diff_pct"] = max((abs(c["diff_pct"]) for c in cmp_ if c["diff_pct"] is not None), default=None)
        report[name] = sec
    return report

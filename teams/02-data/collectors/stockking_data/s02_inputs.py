"""S02 실행기 입력 파일 4종 생성 (teams/03-dev/PAPER_RUNBOOK.md §3-1, COMMON_SPEC §3 U_T).

출력 (--out 디렉터리, 저장소 밖):
  bars.csv      date,symbol,open,high,low,close,volume,value   수정주가, 300거래일+ (value=거래대금, 원천값)
  universe.txt  T일 U_T 통과 종목코드 한 줄씩
  markets.csv   symbol,market (KOSPI/KOSDAQ)
  index.csv     date,symbol,open,high,low,close  (KOSPI200/KOSDAQ150, 200일+)
  universe_report.csv  종목별 U1~U8 판정과 탈락 사유 (검토용)
  manifest.json        건수·점검 결과·한계 (검토용)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .store import write_csv

KOSPI200_CODE = "2001"
KOSDAQ150_CANDIDATES = ("2203", "3003")   # ⚠ KIS 업종코드 미확인 → 지수명으로 검증해 선택

MIN_PRICE = 2_000
MIN_ADV20 = 3_000_000_000
MIN_LISTING_DAYS = 250
MAX_MISSING_250D = 5
BAR_LOOKBACK_DAYS = 520     # 달력일 → 약 350거래일
INDEX_LOOKBACK_DAYS = 420   # 달력일 → 약 280거래일

# D-027 점검용 주문 대상 가격대 (teams/04-trading/PAPER_3DAY_CHECK.md §4)
# D-044: 운용 금액 기준 비율. 목표 = 운용 금액 ÷ 5
#   T1·T2 = 목표의 90~110% (15만 원이면 27,000~33,000원)
#   T5(1주 예외, 목표 초과) = 목표의 110% 초과 ~ 125% (15만 원이면 33,000~37,500원, D-025 상한 운용 금액 30% 이내)
DEFAULT_CAPITAL = 150_000
TECH_RATIOS = {"T1": (0.90, 1.10), "T5": (1.10, 1.25)}
TECH_TOP_N = 5


def tech_ranges(capital: int) -> dict[str, tuple[int, int]]:
    target = capital / 5
    return {role: (round(target * lo), round(target * hi)) for role, (lo, hi) in TECH_RATIOS.items()}

# KIS iscd_stat_cls_code: 51 관리종목, 52 투자위험, 53 투자경고, 54 투자주의, 58 거래정지, 59 단기과열 ⚠ 재확인
BLOCK_STATUS = {"51": "관리종목", "52": "투자위험", "53": "투자경고", "54": "투자주의환기", "58": "거래정지", "59": "단기과열"}
BLOCK_WARN = {"02": "투자경고", "03": "투자위험"}


@dataclass
class Verdict:
    symbol: str
    market: str
    passed: bool = True
    reasons: list[str] = field(default_factory=list)
    adv20: int = 0
    price: int = 0
    bars: int = 0
    missing_250: int = 0

    def fail(self, why: str) -> None:
        self.passed = False
        self.reasons.append(why)


def valid_ohlc(b: dict) -> bool:
    return b["low"] <= min(b["open"], b["close"]) and max(b["open"], b["close"]) <= b["high"] and b["low"] > 0 and b["volume"] >= 0


def resolve_kosdaq150(q, start: date, end: date, override: str | None) -> tuple[str, list[dict]]:
    for code in ([override] if override else KOSDAQ150_CANDIDATES):
        try:
            name, rows = q.index_bars(code, start, end)
        except Exception:  # noqa: BLE001 - 다음 후보 시도
            continue
        if rows and ("150" in name or override):
            return code, rows
    raise RuntimeError("KOSDAQ150 지수 코드를 찾지 못했습니다. KIS 업종코드표 확인 후 --kosdaq150-code 로 지정하세요")


NON_COMMON_NAME_MARKERS = ("스팩", "SPAC", "리츠", "REIT", "우B", "우C")


def judge(symbol: str, market: str, bars: list[dict], status: dict | None, T: date, calendar: list[str],
          name: str = "") -> Verdict:
    v = Verdict(symbol, market)
    if any(m in name for m in NON_COMMON_NAME_MARKERS) or name.endswith("우"):
        v.fail(f"U2 보통주 아님(종목명 '{name}')")
    v.bars = len(bars)
    by_date = {b["date"]: b for b in bars}
    t = T.isoformat()
    if not symbol.endswith("0"):
        v.fail("U2 보통주 아님(우선주 등)")
    if t not in by_date:
        v.fail("T일 일봉 없음")
        return v
    if len(bars) < MIN_LISTING_DAYS:
        v.fail(f"U3 상장 경과 거래일 부족({len(bars)}<{MIN_LISTING_DAYS})")
    v.price = status["price"] if status and status.get("price") else by_date[t]["close"]
    if v.price < MIN_PRICE:
        v.fail(f"U4 주가 {v.price:,}원 < {MIN_PRICE:,}원")
    last20 = [d for d in calendar if d <= t][-20:]
    vals = [by_date[d]["value"] for d in last20 if d in by_date]
    v.adv20 = sum(vals) // 20 if vals else 0
    if v.adv20 < MIN_ADV20:
        v.fail(f"U5 20일 평균 거래대금 {v.adv20/1e8:,.1f}억 < 30억")
    if status:
        if status["status_code"] in BLOCK_STATUS:
            v.fail(f"U6/U7 {BLOCK_STATUS[status['status_code']]}")
        if status["warn_code"] in BLOCK_WARN:
            v.fail(f"U6 {BLOCK_WARN[status['warn_code']]}")
        if status["liquidation"]:
            v.fail("U6 정리매매")
        if status["short_overheat"]:
            v.fail("U6 단기과열")
        if status["halted"]:
            v.fail("U7 거래정지")
    else:
        v.fail("U6/U7 상태 조회 실패")
    last250 = [d for d in calendar if d <= t][-250:]
    listed_from = bars[0]["date"] if bars else t
    v.missing_250 = sum(1 for d in last250 if d >= listed_from and d not in by_date)
    if v.missing_250 > MAX_MISSING_250D:
        v.fail(f"U8 최근 250일 결측 {v.missing_250}일 > {MAX_MISSING_250D}")
    return v


def build(q, T: date, kospi200: list[str], kosdaq150: list[str], out: Path,
          kosdaq150_code: str | None = None, extra_symbols: list[str] | None = None, log=print,
          names: dict[str, str] | None = None, capital: int = DEFAULT_CAPITAL, status_now: bool | None = None) -> dict:
    """status_now: 종목 상태(U6/U7)를 지금 조회해 T일 상태로 쓸지. 기본은 T가 오늘일 때만.
    자정을 넘겨 T(직전 거래일) 파일을 만들 때, 그 사이 거래일이 없으면 True로 지정해도 된다."""
    check_status = (T == date.today()) if status_now is None else status_now
    names = names or {}
    out.mkdir(parents=True, exist_ok=True)
    istart = T - timedelta(days=INDEX_LOOKBACK_DAYS)
    k2_name, k2 = q.index_bars(KOSPI200_CODE, istart, T)
    if not k2 or (k2_name and "200" not in k2_name):
        raise RuntimeError(f"KOSPI200 지수 확인 실패 (코드 {KOSPI200_CODE}, 이름 '{k2_name}')")
    kq_code, kq = resolve_kosdaq150(q, istart, T, kosdaq150_code)
    calendar = [r["date"] for r in k2]
    if calendar[-1] != T.isoformat():
        raise RuntimeError(f"{T}는 거래일이 아니거나 지수 데이터가 아직 없습니다 (마지막 {calendar[-1]})")
    index_rows = [{**r, "symbol": "KOSPI200"} for r in k2] + [{**r, "symbol": "KOSDAQ150"} for r in kq]

    markets = {s: "KOSPI" for s in kospi200} | {s: "KOSDAQ" for s in kosdaq150}
    symbols = sorted(set(markets) | set(extra_symbols or []))
    bstart = T - timedelta(days=BAR_LOOKBACK_DAYS)
    all_bars: list[dict] = []
    verdicts: list[Verdict] = []
    bad_ohlc: list[str] = []
    cache = out / "_cache"
    cache.mkdir(parents=True, exist_ok=True)
    for i, s in enumerate(symbols, 1):
        cf = cache / f"{s}.json"
        cached = None
        if cf.exists():
            try:
                cached = json.loads(cf.read_text(encoding="utf-8"))
                if cached.get("T") != T.isoformat() or cached.get("status_checked") != check_status:
                    cached = None
            except (ValueError, OSError):
                cached = None
        if cached:
            bars, st = cached["bars"], cached["status"]
            tag = "캐시"
        else:
            bars = q.daily_bars(s, bstart, T, adjusted=True)
            st = None
            if check_status:
                try:
                    st = q.price_status(s)
                except Exception as e:  # noqa: BLE001 - 상태 조회 실패는 종목 탈락으로 처리
                    log(f"    {s} 상태 조회 실패: {e}")
            tmp = cf.with_suffix(".tmp")
            tmp.write_text(json.dumps({"T": T.isoformat(), "status_checked": check_status, "bars": bars, "status": st},
                                      ensure_ascii=False), encoding="utf-8")
            tmp.replace(cf)
            tag = "조회"
        good = [b for b in bars if valid_ohlc(b)]
        bad_ohlc += [f"{b['symbol']} {b['date']}" for b in bars if not valid_ohlc(b)]
        all_bars += good
        if s in markets:
            verdicts.append(judge(s, markets[s], good, st, T, calendar, names.get(s, "")))
        log(f"  [{i}/{len(symbols)}] {s} {tag} 일봉 {len(good)}행 (API 호출 {q.calls}회, 재시도 {getattr(q, 'retries', 0)}회)")

    universe = sorted(v.symbol for v in verdicts if v.passed)
    ranges = tech_ranges(capital)
    tech = pick_tech_candidates(verdicts, ranges)
    write_csv(out / "bars.csv", ["date", "symbol", "open", "high", "low", "close", "volume", "value"],
              sorted(all_bars, key=lambda b: (b["date"], b["symbol"])))
    (out / "universe.txt").write_text("".join(f"{s}\n" for s in universe), encoding="utf-8")
    write_csv(out / "markets.csv", ["symbol", "market"], [{"symbol": s, "market": m} for s, m in sorted(markets.items())])
    write_csv(out / "index.csv", ["date", "symbol", "open", "high", "low", "close"], index_rows)
    write_csv(out / "universe_report.csv", ["symbol", "market", "passed", "price", "adv20", "bars", "missing_250", "reasons"],
              [{**v.__dict__, "reasons": "; ".join(v.reasons)} for v in verdicts])
    write_csv(out / "tech_candidates.csv", ["role", "rank", "symbol", "market", "price", "adv20"], tech)
    manifest = {
        "T": T.isoformat(), "source": "KIS Open API (모의투자 도메인 시세)", "adj": "KIS 수정주가(FID_ORG_ADJ_PRC=0)",
        "constituents": {"KOSPI200": len(kospi200), "KOSDAQ150": len(kosdaq150)},
        "universe_passed": len(universe), "bars_rows": len(all_bars),
        "excluded_by_rule": exclusion_counts(verdicts),
        "index_rows": {"KOSPI200": len(k2), "KOSDAQ150": len(kq), "kosdaq150_code": kq_code},
        "bad_ohlc_excluded": bad_ohlc[:50], "bad_ohlc_count": len(bad_ohlc),
        "status_checked": check_status, "api_calls": q.calls, "api_retries": getattr(q, "retries", 0),
        "tech_pick": {role: next((r["symbol"] for r in tech if r["role"] == role), None) for role in ranges},
        "tech_ranges": {"operating_capital": capital, "target": capital // 5, **{k: list(v) for k, v in ranges.items()}},
        "limitations": [
            "구성종목은 T일 현재 목록(시점별 이력 아님) — 3일 모의 용도로는 충분, 백테스트용 아님",
            "수정주가는 KIS 제공값(자체 수정계수 af-v1 아님, DATA_DICTIONARY §4) — 모의 단계 임시",
            "U6/U7 상태는 실행 시점 기준. T가 오늘이 아니면 상태 점검 생략(전 종목 '상태 조회 실패'로 탈락)",
            "T+1 거래정지 예정 공시(U7 후단)는 미반영",
        ],
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def pick_tech_candidates(verdicts: list[Verdict], ranges: dict[str, tuple[int, int]] | None = None) -> list[dict]:
    """D-027 T1/T5 후보: 상태 이상(U6/U7) 없고 T일 일봉이 있는 종목 중 가격대 안, 20일 평균 거래대금 상위 순.
    U3~U5·U8 탈락은 무관(점검 주문은 전략 유니버스 밖이어도 됨). 1순위를 지정 종목으로 쓴다."""
    out = []
    for role, (lo, hi) in (ranges or tech_ranges(DEFAULT_CAPITAL)).items():
        ok = [v for v in verdicts
              if lo <= v.price <= hi and v.price > 0
              and not any(r.startswith(("U6", "U7", "T일")) for r in v.reasons)]
        ok.sort(key=lambda v: -v.adv20)
        out += [{"role": role, "rank": i, "symbol": v.symbol, "market": v.market, "price": v.price, "adv20": v.adv20}
                for i, v in enumerate(ok[:TECH_TOP_N], 1)]
    return out


def exclusion_counts(verdicts: list[Verdict]) -> dict[str, int]:
    """제외 사유별 건수 (한 종목이 여러 사유면 각각 집계). 매매팀 S02_IMPL_CHECK I2 요청."""
    counts: dict[str, int] = {}
    for v in verdicts:
        for r in v.reasons:
            key = r.split(" ")[0] if r.startswith("U") else r
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))

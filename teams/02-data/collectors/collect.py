#!/usr/bin/env python3
"""자료팀 수집 도구 실행 진입점.

사용 예 (저장소 루트에서):
  python teams/02-data/collectors/collect.py check
  python teams/02-data/collectors/collect.py minute --kospi200 k200.csv --kosdaq150 kq150.csv
  python teams/02-data/collectors/collect.py minute-backfill --days 260 --max-days 15
  python teams/02-data/collectors/collect.py s02-inputs --kospi200 k200.csv --kosdaq150 kq150.csv --out ../s02_inputs

필요 환경변수 (프로그램팀과 동일, 값은 절대 저장소에 커밋 금지):
  KIS_PAPER_APP_KEY, KIS_PAPER_APP_SECRET, KIS_PAPER_ACCOUNT_NO
선택: STOCKKING_DATA_DIR (기본 ~/stockking-data)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))
# 프로그램팀 패키지(stockking)는 **설치된 것(.venv, pip install -e)을 우선** 쓴다 — 토큰 파일 캐시 등 최신 kis.py.
# 설치돼 있지 않을 때만 이 저장소의 src를 뒤에 붙인다.
try:
    import stockking  # noqa: F401
except ImportError:
    sys.path.append(str(REPO / "src"))

from stockking_data.capital import resolve_capital  # noqa: E402
from stockking_data.constituents import read_constituent_names, read_constituents  # noqa: E402
from stockking_data.kis_quotes import KisQuotes  # noqa: E402
from stockking_data import minute_job, s02_inputs  # noqa: E402
from stockking_data.store import _refuse_inside_git, add_lines, data_root, read_lines  # noqa: E402


def _client(live_quotes: bool = False):
    import stockking
    print(f"stockking 패키지: {Path(stockking.__file__).parent}", file=sys.stderr)
    if live_quotes:  # D-041: 실전 앱키, 시세 조회 전용 (주문 코드 없음, 별도 키 파일)
        from stockking_data.live_quotes import KisLiveQuoteClient, load_live_quote_keys
        return KisQuotes(KisLiveQuoteClient(*load_live_quote_keys()))
    from stockking.broker.kis import KisPaperClient, KisPaperCredentials
    return KisQuotes(KisPaperClient(KisPaperCredentials.from_env()))


def _symbols(a, root: Path) -> list[str]:
    reg = minute_job.registry_path(root)
    new = []
    for p in (a.kospi200, a.kosdaq150):
        if p:
            new += read_constituents(p)
    add_lines(reg, sorted(set(new)))
    syms = read_lines(reg)
    if not syms:
        sys.exit("적재 대상 종목이 없습니다. 처음 한 번은 --kospi200/--kosdaq150 구성종목 파일을 지정하세요")
    return syms


def _etf_bars(a, q) -> int:
    from stockking_data import etf
    from stockking_data.store import write_csv
    out = Path(a.out).expanduser()
    _refuse_inside_git(out)
    out.mkdir(parents=True, exist_ok=True)
    syms = [s.strip().zfill(6) for s in a.symbols.split(",") if s.strip()]
    bars = etf.fetch_etf_bars(q, syms, a.start, a.end)
    dists = etf.read_distributions_many([Path(x.strip()) for x in a.dist.split(",") if x.strip()]) if a.dist else {}
    if not a.dist:
        print("[경고] 분배금 CSV(--dist)가 없어 TR 가격 = 원가격입니다. S03 신호(§4)는 TR 가격 기준이므로 백테스트 전 반드시 보완", file=sys.stderr)
    rows, summary = [], {}
    for s in syms:
        b = [x for x in bars if x["symbol"] == s]
        tr = etf.tr_series(b, dists.get(s, {}))
        proxy = []
        if s == "229200" and tr and not a.kosdaq150_index and tr[0]["date"] > "2010-01-04":
            # KRX CSV가 없으면 KIS에서 KOSDAQ150 지수(2010-01~상장일)를 받아 proxy 생성
            first = date.fromisoformat(tr[0]["date"])
            code, rows_k = s02_inputs.resolve_kosdaq150(q, max(a.start, date(2010, 1, 4)), first, None)
            print(f"  KOSDAQ150 지수(KIS 코드 {code}) {len(rows_k)}행으로 229200 상장 전 proxy 생성", file=sys.stderr)
            proxy = etf.proxy_from_index(rows_k, tr[0])
        if s == "229200" and a.kosdaq150_index and tr:
            from stockking_data.index_check import read_index_csv, read_krx_index_ohlc
            p = Path(a.kosdaq150_index)
            try:
                idx = [{"date": d, "close": c} for d, c in read_index_csv(p).get("KOSDAQ150", [])]
            except KeyError:
                idx = read_krx_index_ohlc(p)
            proxy = etf.proxy_from_index(idx, tr[0])
        rows += [{**x, "proxy": x.get("proxy", False)} for x in proxy + tr]
        summary[s] = {"name": etf.S03_ETFS.get(s, ""), "rows": len(tr), "proxy_rows": len(proxy),
                      "first": tr[0]["date"] if tr else None, "last": tr[-1]["date"] if tr else None,
                      "distributions": len(dists.get(s, {})), "tr_factor_last": tr[-1]["tr_factor"] if tr else None}
    write_csv(out / "etf_bars.csv", ["date", "symbol", "open", "high", "low", "close", "volume", "value",
                                     "tr_factor", "tr_close", "proxy"], sorted(rows, key=lambda r: (r["symbol"], r["date"])))
    (out / "etf_manifest.json").write_text(json.dumps({"start": a.start.isoformat(), "end": a.end.isoformat(),
                                                       "dist_file": a.dist, "symbols": summary, "api_calls": q.calls},
                                                      ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="자료팀 KIS 시세 수집 (조회 전용)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    chk = sub.add_parser("check", help="인증·시세·지수코드 확인")
    chk.add_argument("--live-quotes", action="store_true", help="D-041 실전 앱키 시세 전용 클라이언트로 확인")
    for name in ("minute", "minute-backfill", "s02-inputs"):
        p = sub.add_parser(name)
        p.add_argument("--kospi200")
        p.add_argument("--kosdaq150")
        p.add_argument("--date", type=date.fromisoformat, default=date.today())
        if name in ("minute", "minute-backfill"):
            p.add_argument("--live-quotes", action="store_true",
                           help="D-041 실전 앱키 시세 전용(약 14건/초). 키 파일 ~/.config/stockking/secrets_live_quotes.env")
        if name == "minute-backfill":
            p.add_argument("--days", type=int, default=260, help="오늘부터 거슬러 올라갈 평일 수")
            p.add_argument("--max-days", type=int, default=None, help="이번 실행에서 채울 최대 일수")
        if name == "s02-inputs":
            p.add_argument("--out", required=True)
            p.add_argument("--kosdaq150-code")
            p.add_argument("--extra-symbols", default="", help="보유 종목 등 추가 일봉 대상, 쉼표 구분")
            p.add_argument("--status-now", action="store_true",
                           help="자정 이후 직전 거래일(--date) 파일을 만들 때, 지금 조회한 종목 상태를 그날 상태로 사용")
            p.add_argument("--capital", type=int, default=None,
                           help="모의 운용 금액(D-044/D-046). 점검 주문 후보 가격대 = 운용 금액÷5 기준 비율. "
                                "기본: STOCKKING_OPERATING_CAPITAL(숫자 또는 auto → operating_capital.json), 없으면 150000")
    for p in sub.choices.values():
        p.add_argument("--interval", type=float, default=None,
                       help="호출 간격(초). 모의 기본 1.0. 0.5까지 낮춰도 한도 초과 시 자동 재시도")
    vi = sub.add_parser("verify-index", help="D-047: index.csv 지수값 점검(급변일·KIS 하루 재조회·KRX 대조)")
    vi.add_argument("--index-csv", required=True)
    vi.add_argument("--krx-kospi200", help="KRX 개별지수 시세 추이 CSV (코스피 200)")
    vi.add_argument("--krx-kosdaq150", help="KRX 개별지수 시세 추이 CSV (코스닥 150)")
    vi.add_argument("--kosdaq150-code", help="manifest.json index_rows.kosdaq150_code 값")
    vi.add_argument("--no-refetch", action="store_true", help="KIS 재조회 없이 파일만 점검")
    vi.add_argument("--interval", type=float, default=None)
    ik = sub.add_parser("index-from-krx", help="대체안: KRX 공표 지수 CSV 2개로 S02 index.csv 생성")
    ik.add_argument("--krx-kospi200", required=True)
    ik.add_argument("--krx-kosdaq150", required=True)
    ik.add_argument("--out", required=True, help="만들 index.csv 경로 (기존 파일은 index_kis.csv로 보관)")
    ro = sub.add_parser("regime-outlook", help="D-048: 국면 필터(지수 > 200일 평균) 복귀 예상 시점")
    ro.add_argument("--index-csv", required=True)
    eb = sub.add_parser("etf-bars", help="D-048 ③ S03 ETF 일봉(원가격) + 분배금 → TR 가격")
    eb.add_argument("--out", required=True, help="출력 폴더")
    eb.add_argument("--start", type=date.fromisoformat, default=date(2009, 1, 1))
    eb.add_argument("--end", type=date.fromisoformat, default=date.today())
    eb.add_argument("--symbols", default="069500,229200,114260,132030")
    eb.add_argument("--dist", help="분배금 CSV(SEIBro 분배금지급현황 등, isin 또는 종목코드 열). 여러 개는 쉼표로. 없으면 TR=원가격, 경고")
    eb.add_argument("--kosdaq150-index", help="229200 상장 전 proxy용 KOSDAQ150 지수 CSV(index.csv 형식 또는 KRX CSV). 없으면 KIS에서 받음")
    eb.add_argument("--interval", type=float, default=None)
    tc = sub.add_parser("tech-candidates", help="D-027 점검 주문 후보만 다시 만들기 (입력 폴더 _cache 사용, KIS 호출 없음)")
    tc.add_argument("--inputs", required=True, help="s02-inputs 출력 폴더 (예: ...\\s02_inputs\\2026-11-27)")
    tc.add_argument("--capital", type=int, default=None, help="운용 금액. 기본: STOCKKING_OPERATING_CAPITAL(숫자/auto)")
    a = ap.parse_args(argv)

    if a.cmd == "tech-candidates":
        capital, src = (a.capital, "--capital") if a.capital else resolve_capital(repo_root=REPO)
        print(f"운용 금액 {capital:,}원 (출처: {src})", file=sys.stderr)
        r = s02_inputs.rebuild_tech_from_cache(Path(a.inputs).expanduser(), capital)
        for x in r["rows"]:
            print(f"  {x['role']} #{x['rank']} {x['symbol']} {x['market']} 가격 {x['price']:,} × {x['q']}주 = {x['order_amount']:,}원"
                  f" (ADV20 {x['adv20'] / 1e8:,.0f}억) {'' if x['in_band'] else '[범위 밖 대체]'}")
        return 0

    if a.cmd == "regime-outlook":
        from stockking_data.index_check import read_index_csv
        from stockking_data.regime import outlook
        idx = Path(a.index_csv).expanduser()
        rep = {nm: outlook(series) for nm, series in read_index_csv(idx).items()}
        (idx.parent / "regime_outlook.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        for nm, r in rep.items():
            print(f"{nm} ({r['as_of']}): 종가 {r['close']:,} / 200일 평균 {r['sma200']:,} ({r['gap_pct']:+.2f}%) → {'ON' if r['on'] else 'OFF'}")
            for sc in r["scenarios"]:
                when = (f"{sc['trading_days']}거래일 후 ≈ {sc['approx_date']}" if sc["trading_days"] is not None
                        else f"750거래일 안에 '초과'는 안 됨 (평균과 1% 이내 접근: {sc['within_1pct_approx_date'] or '없음'})")
                print(f"   - {sc['scenario']}: {when}" + (f" (지수 {sc['index_at_cross']:,} > 평균 {sc['sma200_at_cross']:,})" if sc["index_at_cross"] else ""))
        print("※ 날짜는 주말만 제외한 근사(공휴일만큼 늦어질 수 있음). 상세:", idx.parent / "regime_outlook.json")
        return 0

    if a.cmd == "index-from-krx":
        from stockking_data.index_check import index_csv_from_krx
        out = Path(a.out).expanduser()
        _refuse_inside_git(out.parent)
        if out.exists():
            out.replace(out.with_name("index_kis.csv"))
        r = index_csv_from_krx(Path(a.krx_kospi200), Path(a.krx_kosdaq150), out)
        print(json.dumps(r, ensure_ascii=False))
        if min(r["KOSPI200"], r["KOSDAQ150"]) < s02_inputs.MIN_INDEX_ROWS:
            print(f"[실패] 지수 {s02_inputs.MIN_INDEX_ROWS}행 미만 — KRX 조회 기간을 늘려 다시 받으세요", file=sys.stderr)
            return 2
        return 0

    if a.cmd == "verify-index":
        from stockking_data.index_check import check
        idx = Path(a.index_csv).expanduser()
        kq_code = a.kosdaq150_code
        mf = idx.parent / "manifest.json"
        if not kq_code and mf.exists():
            kq_code = (json.loads(mf.read_text(encoding="utf-8")).get("index_rows") or {}).get("kosdaq150_code")
        q = None if a.no_refetch else _client()
        if q is not None and a.interval is not None:
            q._c.min_interval_sec = max(0.05, a.interval)
        rep = check(idx, q, kq_code, {"KOSPI200": a.krx_kospi200 and Path(a.krx_kospi200),
                                      "KOSDAQ150": a.krx_kosdaq150 and Path(a.krx_kosdaq150)})
        out = idx.parent / "index_check.json"
        out.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        for name in ("KOSPI200", "KOSDAQ150"):
            sec = rep.get(name) or {}
            print(f"{name}: {sec.get('rows')}행, 처음 {sec.get('first')}, 마지막 {sec.get('last')}, 200일 평균 {sec.get('sma200')}, "
                  f"8% 초과 급변 {len(sec.get('jumps', []))}일, "
                  f"KIS 하루 재조회 불일치 {sum(1 for r in sec.get('kis_refetch', []) if not r['match'])}건, "
                  f"KRX 최대 오차 {sec.get('krx_max_abs_diff_pct')}%")
        print(f"상세: {out}")
        return 0

    q = _client(getattr(a, "live_quotes", False))
    if a.cmd == "etf-bars":
        if a.interval is not None:
            q._c.min_interval_sec = max(0.05, a.interval)
        return _etf_bars(a, q)
    if a.interval is not None:
        q._c.min_interval_sec = max(0.05, a.interval)
    if a.cmd == "check":
        st = q.price_status("005930")
        name, rows = q.index_bars(s02_inputs.KOSPI200_CODE, date.today().replace(day=1), date.today())
        print(json.dumps({"005930": st, "KOSPI200": {"name": name, "rows": len(rows)}}, ensure_ascii=False, indent=2))
        return 0

    if a.cmd == "s02-inputs":
        if not a.kospi200:
            sys.exit("--kospi200 구성종목 파일이 필요합니다 (--kosdaq150 생략 시 KOSPI200만으로 유니버스 구성)")
        out = Path(a.out).expanduser()
        _refuse_inside_git(out)
        capital, cap_src = (a.capital, "--capital") if a.capital else resolve_capital(repo_root=REPO)
        print(f"운용 금액 {capital:,}원 (출처: {cap_src})", file=sys.stderr)
        k2 = read_constituent_names(a.kospi200)
        kq = read_constituent_names(a.kosdaq150) if a.kosdaq150 else {}
        m = s02_inputs.build(q, a.date, sorted(k2), sorted(kq), out,
                             a.kosdaq150_code, [s.strip().zfill(6) for s in a.extra_symbols.split(",") if s.strip()],
                             names=k2 | kq, capital=capital, status_now=True if a.status_now else None)
        print(json.dumps(m, ensure_ascii=False, indent=2))
        return 0

    root = data_root()
    syms = _symbols(a, root)
    if a.cmd == "minute":
        r = minute_job.collect_day(q, root, a.date, syms)
        print(json.dumps({**r, "empty": len(r.get("empty", []))}, ensure_ascii=False))
    else:
        for r in minute_job.backfill(q, root, syms, a.date, a.days, a.max_days):
            print(json.dumps({**r, "empty": len(r.get("empty", []))}, ensure_ascii=False))
    print(f"API 호출 {q.calls}회, 데이터 경로 {root}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as e:  # 입력 검증 실패 등: 메시지와 함께 실패 코드 2
        print(f"[실패] {e}", file=sys.stderr)
        raise SystemExit(2)

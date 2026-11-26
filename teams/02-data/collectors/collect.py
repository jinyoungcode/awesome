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
    a = ap.parse_args(argv)

    q = _client(getattr(a, "live_quotes", False))
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
    raise SystemExit(main())

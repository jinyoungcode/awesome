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
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path[:0] = [str(HERE), str(REPO / "src")]

from stockking_data.constituents import read_constituent_names, read_constituents  # noqa: E402
from stockking_data.kis_quotes import KisQuotes  # noqa: E402
from stockking_data import minute_job, s02_inputs  # noqa: E402
from stockking_data.store import _refuse_inside_git, add_lines, data_root, read_lines  # noqa: E402


def _client():
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
    sub.add_parser("check", help="인증·시세·지수코드 확인")
    for name in ("minute", "minute-backfill", "s02-inputs"):
        p = sub.add_parser(name)
        p.add_argument("--kospi200")
        p.add_argument("--kosdaq150")
        p.add_argument("--date", type=date.fromisoformat, default=date.today())
        if name == "minute-backfill":
            p.add_argument("--days", type=int, default=260, help="오늘부터 거슬러 올라갈 평일 수")
            p.add_argument("--max-days", type=int, default=None, help="이번 실행에서 채울 최대 일수")
        if name == "s02-inputs":
            p.add_argument("--out", required=True)
            p.add_argument("--kosdaq150-code")
            p.add_argument("--extra-symbols", default="", help="보유 종목 등 추가 일봉 대상, 쉼표 구분")
    a = ap.parse_args(argv)

    q = _client()
    if a.cmd == "check":
        st = q.price_status("005930")
        name, rows = q.index_bars(s02_inputs.KOSPI200_CODE, date.today().replace(day=1), date.today())
        print(json.dumps({"005930": st, "KOSPI200": {"name": name, "rows": len(rows)}}, ensure_ascii=False, indent=2))
        return 0

    if a.cmd == "s02-inputs":
        if not (a.kospi200 and a.kosdaq150):
            sys.exit("--kospi200, --kosdaq150 구성종목 파일이 필요합니다")
        out = Path(a.out).expanduser()
        _refuse_inside_git(out)
        k2, kq = read_constituent_names(a.kospi200), read_constituent_names(a.kosdaq150)
        m = s02_inputs.build(q, a.date, sorted(k2), sorted(kq), out,
                             a.kosdaq150_code, [s.strip().zfill(6) for s in a.extra_symbols.split(",") if s.strip()],
                             names=k2 | kq)
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

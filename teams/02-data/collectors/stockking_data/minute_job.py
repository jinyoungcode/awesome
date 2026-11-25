"""국내 1분봉 일일 적재 + 과거 백필 (MINUTE_SOURCES.md §3, 대표 결정 2026-11-25 '오늘부터 매일 쌓기').

저장 (STORAGE.md):
  <data_root>/raw/kis_minute_kr/date=YYYY-MM-DD/<batch>.jsonl.gz    원천 응답 (append-only)
  <data_root>/clean/minute_kr/date=YYYY-MM-DD/<symbol>.csv.gz       date,symbol,time,open,high,low,close,volume,cum_value
  <data_root>/clean/minute_kr/date=YYYY-MM-DD/_done.txt             완료 종목 (재실행 시 건너뜀 → 중단 후 재시작 안전)
  <data_root>/clean/minute_kr/date=YYYY-MM-DD/_HOLIDAY              휴장일 표식
  <data_root>/registry/kr_symbols.txt                                적재 대상 종목 (추가만, 삭제 없음)
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

from .store import add_lines, append_raw, read_lines, write_csv_gz

FIELDS = ["date", "symbol", "time", "open", "high", "low", "close", "volume", "cum_value"]
HOLIDAY_PROBE = 3


def registry_path(root: Path) -> Path:
    return root / "registry" / "kr_symbols.txt"


def collect_day(q, root: Path, day: date, symbols: list[str], log=print) -> dict:
    ddir = root / "clean" / "minute_kr" / f"date={day.isoformat()}"
    if (ddir / "_HOLIDAY").exists():
        return {"date": day.isoformat(), "status": "holiday"}
    done_path = ddir / "_done.txt"
    done = set(read_lines(done_path))
    todo = [s for s in symbols if s not in done]
    batch = datetime.now().strftime("%Y%m%dT%H%M%S")
    raw_path = root / "raw" / "kis_minute_kr" / f"date={day.isoformat()}" / f"{batch}.jsonl.gz"
    empty_streak, saved, empty = 0, 0, []
    for i, s in enumerate(todo, 1):
        raws, rows = q.minute_bars(s, day)
        append_raw(raw_path, raws, batch)
        if rows:
            write_csv_gz(ddir / f"{s}.csv.gz", FIELDS, rows)
            saved += 1
            empty_streak = 0
        else:
            empty.append(s)
            empty_streak += 1
            if not done and saved == 0 and empty_streak >= HOLIDAY_PROBE:
                ddir.mkdir(parents=True, exist_ok=True)
                (ddir / "_HOLIDAY").write_text("첫 종목들 분봉 없음 → 휴장일로 판단\n", encoding="utf-8")
                return {"date": day.isoformat(), "status": "holiday"}
        add_lines(done_path, [s])  # 빈 결과(정지·상폐 등)도 시도 완료로 기록
        if i % 50 == 0:
            log(f"  {day} 분봉 {i}/{len(todo)} (API 호출 {q.calls}회)")
    return {"date": day.isoformat(), "status": "ok", "saved": saved, "empty": empty, "skipped_done": len(done)}


def weekdays_back(end: date, days: int) -> list[date]:
    out, d = [], end
    while len(out) < days:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return out


def backfill(q, root: Path, symbols: list[str], end: date, days: int, max_days: int | None, log=print) -> list[dict]:
    """오래된 날짜부터 채운다 (KIS 보관 기간(약 1년) 밖으로 먼저 사라지는 날짜를 우선 확보)."""
    results = []
    for d in sorted(weekdays_back(end, days)):
        ddir = root / "clean" / "minute_kr" / f"date={d.isoformat()}"
        if (ddir / "_HOLIDAY").exists() or set(symbols) <= set(read_lines(ddir / "_done.txt")):
            continue
        log(f"백필 {d} 시작")
        results.append(collect_day(q, root, d, symbols, log))
        if max_days and len(results) >= max_days:
            break
    return results

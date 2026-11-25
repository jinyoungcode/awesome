"""수집 데이터 저장 (STORAGE.md: 운영 머신 로컬, 저장소 밖).

- 기본 경로: 환경변수 STOCKKING_DATA_DIR, 없으면 ~/stockking-data
- raw: 원천 응답 그대로 jsonl.gz, append-only (덮어쓰기 금지)
- clean: 정리된 CSV(.csv.gz), 날짜 파티션
- 시세 재배포 금지 약관 → 이 경로를 Git 저장소 안에 두지 않는다
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def data_root() -> Path:
    root = Path(os.environ.get("STOCKKING_DATA_DIR") or Path.home() / "stockking-data").expanduser()
    _refuse_inside_git(root)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _refuse_inside_git(root: Path) -> None:
    p = root.resolve()
    for parent in [p, *p.parents]:
        if (parent / ".git").exists():
            raise RuntimeError(f"데이터 경로가 Git 저장소 안입니다({parent}). 시세 재배포 금지 약관 → 저장소 밖 경로를 쓰세요")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def append_raw(path: Path, records: list[dict], batch_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "at", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps({"batch_id": batch_id, "collected_at": now_iso(), **r}, ensure_ascii=False) + "\n")


def write_csv_gz(path: Path, fields: list[str], rows: list[dict]) -> None:
    """원자적 쓰기(임시 파일 → 교체). clean 계층은 재생성 가능하므로 교체 허용."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(buf.getvalue(), encoding="utf-8")
    tmp.replace(path)


def read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def add_lines(path: Path, items: list[str]) -> None:
    """종목 레지스트리: 한 번 들어온 종목은 지우지 않는다(편출·상폐 종목도 계속 적재 → 생존편향 완화)."""
    have = set(read_lines(path))
    new = [i for i in items if i not in have]
    if new:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.writelines(f"{i}\n" for i in new)

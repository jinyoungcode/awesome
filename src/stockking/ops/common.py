"""운영 스크립트 공통: 단계별 PASS/FAIL 기록, 감사 로그 경로."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

JOURNAL_DIR = Path("var/journal")


def journal_path(day: date | None = None) -> Path:
    return JOURNAL_DIR / f"{(day or date.today()):%Y%m%d}.jsonl"


@dataclass
class Checklist:
    title: str
    results: list[tuple[str, bool, str]] = field(default_factory=list)

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.results.append((name, bool(ok), detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""), flush=True)
        return bool(ok)

    @property
    def passed(self) -> bool:
        return bool(self.results) and all(ok for _, ok, _ in self.results)

    def finish(self) -> int:
        n_ok = sum(ok for _, ok, _ in self.results)
        print(f"\n== {self.title}: {'통과' if self.passed else '미통과'} ({n_ok}/{len(self.results)}) ==")
        return 0 if self.passed else 1


def die(msg: str) -> None:
    print(f"중단: {msg}", file=sys.stderr)
    raise SystemExit(2)

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


def run_cli(main) -> int:
    """운영 명령 공통 실행: 예상된 오류(토큰 제한·키 없음·한도 초과 등)는 traceback 대신 한글 안내 후 종료코드 2."""
    try:
        return main()
    except KeyboardInterrupt:
        print("\n중단: 사용자가 Ctrl+C 로 멈췄습니다. 재시작 전에 'python -m stockking.ops.startup_check' 를 실행하세요.",
              file=sys.stderr)
        return 130
    except (RuntimeError, PermissionError, ValueError, OSError) as e:
        print(f"\n중단: {e}", file=sys.stderr)
        msg = str(e)
        if "1분" in msg or "EGW00133" in msg or "403" in msg:
            print("  → 토큰 발급 제한입니다. 1분 기다렸다 같은 명령을 다시 실행하세요.", file=sys.stderr)
        elif "비밀값" in msg:
            print("  → 비밀값 파일을 확인하세요: notepad \"$env:APPDATA\\stockking\\secrets.env\"", file=sys.stderr)
        return 2

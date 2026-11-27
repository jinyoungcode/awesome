"""운영 스크립트 공통: 단계별 PASS/FAIL 기록, 감사 로그 경로."""

from __future__ import annotations

import atexit
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from ..paths import layout_error, var_root

def journal_dir() -> Path:
    """감사 로그 폴더 — 호출 시점의 계좌 프로필 기준(var\\<프로필>\\journal, 호환 모드 var\\journal)."""
    return var_root() / "journal"


def journal_path(day: date | None = None) -> Path:
    return journal_dir() / f"{(day or date.today()):%Y%m%d}.jsonl"


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


def fix_capital_logged(orderable: int, operator: str = "", command: str = "") -> int:
    """운용 금액 auto 고정 + 새로 고정했으면 감사 로그(capital_fixed: 새 값·이전 값·사유) 기록 (D-046·D-049)."""
    from ..audit import Journal
    from ..risk.stages import fix_auto_capital_detail
    capital, prev = fix_auto_capital_detail(orderable)
    if prev is not None:
        Journal(journal_path()).write("capital_fixed", capital=capital, previous_capital=prev.get("previous_capital"),
                                      previous_fixed_on=prev.get("previous_fixed_on"), reason=prev.get("reason"),
                                      operator=operator, command=command)
        was = f"{prev['previous_capital']:,}원 → " if prev.get("previous_capital") else ""
        print(f"  운용 금액 새로 고정: {was}{capital:,}원 (사유: {prev.get('reason')}) — 감사 로그 기록")
    return capital


def die(msg: str) -> None:
    print(f"중단: {msg}", file=sys.stderr)
    raise SystemExit(2)


def _pid_alive(pid: int) -> bool:
    """윈도우는 os.kill(pid, 0) 이 프로세스를 종료시킬 수 있어 OpenProcess 로 확인한다."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(k32.GetExitCodeProcess(h, ctypes.byref(code))) and code.value == 259   # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def acquire_account_lock() -> Path | None:
    """D-049: 같은 계좌 프로필의 주문 가능 프로세스는 하나만. 호환 모드에서는 잠그지 않는다(기존 동작 유지).
    비정상 종료로 남은 잠금은 PID 가 살아 있지 않으면 넘겨받는다."""
    from ..config import account_profile
    prof = account_profile()
    if prof is None:
        return None
    lock = var_root() / ".lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                held = json.loads(lock.read_text(encoding="utf-8")).get("pid", 0)
            except (OSError, ValueError):
                held = 0
            if held == os.getpid():
                return lock
            if _pid_alive(int(held)):
                raise RuntimeError(f"{prof} 계좌 프로그램이 이미 실행 중입니다(PID {held}) — 그 창의 작업이 끝난 뒤 "
                                   "다시 실행하세요(D-049: 계좌당 주문 프로세스 1개)")
            lock.unlink(missing_ok=True)   # 남은 잠금 해제
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"pid": os.getpid(), "profile": prof}, fh)
        atexit.register(_release_lock, lock)
        return lock
    raise RuntimeError(f"잠금 파일을 만들 수 없습니다: {lock}")


def _release_lock(lock: Path) -> None:
    try:
        if json.loads(lock.read_text(encoding="utf-8")).get("pid") == os.getpid():
            lock.unlink()
    except (OSError, ValueError):
        pass


def run_cli(main) -> int:
    """운영 명령 공통 실행: 예상된 오류(토큰 제한·키 없음·한도 초과 등)는 traceback 대신 한글 안내 후 종료코드 2."""
    try:
        if (err := layout_error()):
            raise RuntimeError(err)
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

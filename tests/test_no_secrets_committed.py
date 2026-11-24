"""저장소 안에 비밀값처럼 보이는 문자열이 들어가지 않았는지 검사 (최소 안전망)."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = [
    re.compile(r"(?i)(app_?key|app_?secret|access_token)\s*[=:]\s*['\"]?[A-Za-z0-9+/]{20,}"),
    re.compile(r"PS[A-Za-z0-9]{30,}"),   # KIS appkey 형태 추정
    re.compile(r"-----BEGIN (RSA |EC )?PRIVATE KEY-----"),
]


def tracked_files():
    try:
        out = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    except Exception:
        return []
    return [ROOT / p for p in out.splitlines()]


def test_no_env_file_tracked():
    assert not any(p.name == ".env" for p in tracked_files())


def test_no_secret_like_strings():
    hits = []
    for p in tracked_files():
        if p.suffix in {".png", ".jpg", ".pdf"} or not p.is_file() or p.name == Path(__file__).name:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        hits += [f"{p.relative_to(ROOT)}: {m.group(0)[:30]}" for pat in PATTERNS for m in pat.finditer(text)]
    assert not hits, hits

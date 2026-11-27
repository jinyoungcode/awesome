"""지수 구성종목 파일 읽기.

원천: KRX 정보데이터시스템 [지수 > 지수구성종목] 화면에서 내려받은 CSV (SOURCES.md: 소량·수동 허용).
- KOSPI200, KOSDAQ150 각각 내려받아 지정. 인코딩(EUC-KR/UTF-8)과 열 이름이 바뀌어도
  '6자리 종목코드' 열을 자동으로 찾는다.
"""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path

_CODE = re.compile(r"^[0-9A-Z]{6}$")


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError("구성종목 파일 인코딩을 읽을 수 없습니다")


def read_constituents(path: str | Path) -> list[str]:
    return sorted(read_constituent_names(path))


def read_constituent_names(path: str | Path) -> dict[str, str]:
    """{종목코드: 종목명}. 종목명 열이 없으면 빈 문자열."""
    text = _decode(Path(path).read_bytes())
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        raise ValueError(f"빈 파일: {path}")
    header, body = rows[0], rows[1:]
    col = name_col = None
    for i, h in enumerate(header):
        if col is None and ("종목코드" in h or h.strip().lower() in ("code", "symbol", "isu_srt_cd")):
            col = i
        if name_col is None and ("종목명" in h or h.strip().lower() in ("name", "isu_abbrv")):
            name_col = i
    if col is None:  # 헤더 없는 단순 목록
        body, col = rows, 0
    out: dict[str, str] = {}
    for r in body:
        if len(r) > col:
            c = r[col].strip().strip('"').zfill(6)
            if _CODE.match(c):
                out[c] = r[name_col].strip() if name_col is not None and len(r) > name_col else ""
    if not out:
        raise ValueError(f"종목코드를 찾지 못했습니다: {path}")
    return out

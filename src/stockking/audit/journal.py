"""감사 로그(append-only JSONL). 주문 요청·리스크 판단·브로커 응답·체결을 모두 남긴다.

회계팀 정산(대사)과 사고 분석의 1차 원천 자료다. 비밀값은 절대 기록하지 않는다.
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path

_FORBIDDEN_KEYS = {"appkey", "appsecret", "app_key", "app_secret", "authorization", "access_token", "token"}


def _default(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if is_dataclass(obj):
        return asdict(obj)
    return str(obj)


def _scrub(value):
    if isinstance(value, dict):
        return {k: ("****" if k.lower() in _FORBIDDEN_KEYS else _scrub(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


class Journal:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None
        self.records: list[dict] = []

    def write(self, event: str, **payload) -> dict:
        record = {"ts": datetime.now().isoformat(), "event": event,
                  **_scrub(json.loads(json.dumps(payload, default=_default)))}
        self.records.append(record)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

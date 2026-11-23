#!/usr/bin/env python3
"""GPT 협업 도구 — 질문과 참고 문서를 GPT에 보내고, 대화 기록을 docs/gpt-consults/ 에 남긴다.

사용법:
  python3 tools/gpt_consult.py --topic "조직안 검토" --question "..." [--file 경로 ...] [--model 모델]

API 키는 코드/환경변수에 두지 않는다. 클라우드 세션의 네트워크 프록시가 api.openai.com 요청에 키를 주입한다.
(로컬에서 직접 쓸 때만 OPENAI_API_KEY 환경변수를 사용)
"""
import argparse
import datetime
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "docs" / "gpt-consults"
DEFAULT_MODEL = os.environ.get("GPT_MODEL", "gpt-5.5")

SYSTEM = (
    "너는 '나는야 주식왕' 자동 트레이딩 프로젝트에 공동 자문으로 참여한 GPT다. "
    "여의도에서 20년 이상 일한 주식 운용·리스크·시스템 전문가의 관점으로 답한다. "
    "Claude(총괄운영팀)가 정리한 문서를 검토하고, 동의/반대/보완 의견을 근거와 함께 구체적으로 낸다. "
    "확실하지 않은 사실(세율, 약관, API 사양 등)은 '확인 필요'라고 표시한다. 한국어로 답한다."
)


def call_gpt(model: str, user_text: str) -> str:
    body = json.dumps({"model": model, "instructions": SYSTEM, "input": user_text}).encode()
    req = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + os.environ.get("OPENAI_API_KEY", "proxy-injected"),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        sys.exit(f"[GPT 호출 실패] HTTP {e.code}: {detail}")
    parts = [
        c.get("text", "")
        for item in data.get("output", [])
        if item.get("type") == "message"
        for c in item.get("content", [])
        if c.get("type") == "output_text"
    ]
    return "\n".join(parts).strip()


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--topic", required=True)
    p.add_argument("--question", required=True)
    p.add_argument("--file", action="append", default=[], help="참고 문서 경로(여러 개 가능)")
    p.add_argument("--model", default=DEFAULT_MODEL)
    a = p.parse_args()

    ctx = []
    for f in a.file:
        path = pathlib.Path(f)
        ctx.append(f"### 참고 문서: {f}\n\n{path.read_text(encoding='utf-8')}")
    user_text = a.question + ("\n\n---\n\n" + "\n\n---\n\n".join(ctx) if ctx else "")

    answer = call_gpt(a.model, user_text)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9)))
    slug = re.sub(r"[^0-9A-Za-z가-힣]+", "-", a.topic).strip("-")[:40]
    out = LOG_DIR / f"{now:%Y-%m-%d-%H%M}-{slug}.md"
    out.write_text(
        f"# GPT 협의: {a.topic}\n\n- 일시: {now:%Y-%m-%d %H:%M} KST\n- 모델: {a.model}\n"
        f"- 참고 문서: {', '.join(a.file) or '없음'}\n\n## 질문 (Claude → GPT)\n\n{a.question}\n\n"
        f"## GPT 답변\n\n{answer}\n",
        encoding="utf-8",
    )
    print(answer)
    print(f"\n[기록 저장] {out.relative_to(ROOT)}", file=sys.stderr)


if __name__ == "__main__":
    main()

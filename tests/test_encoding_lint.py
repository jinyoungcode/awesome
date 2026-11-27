"""Windows(cp949) 에서 한글 파일이 깨지지 않도록, src·tests 의 텍스트 파일 입출력은 encoding 을 반드시 명시한다.

대상: open(), Path.open(), Path.read_text(), Path.write_text(). 바이너리 모드('b')는 제외.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TESTS = ROOT / "tests"
TEXT_IO = {"read_text", "write_text", "open"}


def _mode(call: ast.Call, is_builtin: bool) -> str | None:
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            return kw.value.value
    pos = 1 if is_builtin else 0
    if len(call.args) > pos and isinstance(call.args[pos], ast.Constant) and isinstance(call.args[pos].value, str):
        return call.args[pos].value
    return None


def find_missing_encoding(root: Path = SRC) -> list[str]:
    bad = []
    for py in sorted(root.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            if isinstance(f, ast.Name) and f.id == "open":
                is_builtin, name = True, "open"
            elif isinstance(f, ast.Attribute) and f.attr in TEXT_IO:
                is_builtin, name = False, f.attr
                if isinstance(f.value, ast.Name) and f.value.id in {"os", "urllib", "request", "webbrowser"}:
                    continue
            else:
                continue
            if name == "open" and "b" in (_mode(node, is_builtin) or ""):
                continue
            if name == "read_bytes" or any(kw.arg == "encoding" for kw in node.keywords):
                continue
            bad.append(f"{py.relative_to(root)}:{node.lineno} {name}()")
    return bad


def test_src_text_io_declares_utf8_encoding():
    assert find_missing_encoding(SRC) == []


def test_tests_text_io_declares_utf8_encoding():
    # 대표 PC(Windows) 실측: tests 의 인코딩 미지정 read_text 가 한글 감사 로그에서 cp949 오류
    assert find_missing_encoding(TESTS) == []


def test_lint_catches_missing_encoding(tmp_path):
    (tmp_path / "m.py").write_text(
        "from pathlib import Path\n"
        "Path('a').read_text()\n"
        "open('a')\n"
        "Path('a').open('rb')\n"
        "Path('a').write_text('x', encoding='utf-8')\n", encoding="utf-8")
    assert find_missing_encoding(tmp_path) == ["m.py:2 read_text()", "m.py:3 open()"]

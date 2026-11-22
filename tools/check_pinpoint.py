"""Exercise source-refinement guards with independently constructed changes."""

import difflib
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pinpoint import Index, _predicate_entry, classify_line
from selector import parse_unified_diff


def make_patch(path, original, edited):
    unified = difflib.unified_diff(original.splitlines(), edited.splitlines(),
                                   f"a/{path}", f"b/{path}", lineterm="")
    return "\n".join([f"diff --git a/{path} b/{path}", *unified]) + "\n"


def check():
    path = "sqlglot/check.py"
    original = "def f(items):\n    if items and len(items) == 1:\n        return 42\n    return 0\n"
    assert classify_line(path, original, 2, "    if items or len(items) == 1:") == "exact"
    assert classify_line(path, original, 2, "    for item in items:") is None
    assert classify_line(path, original, 2, "        if not items:") is None
    assert classify_line(path, original, 2, "    if (items := []):") is None

    original = ("def f(value):\n"
                "    if (\n"
                "        value\n"
                "        and (count := len(value)) > 1\n"
                "        and count < 5\n"
                "    ):\n"
                "        return count\n")
    edited = original.replace("and count < 5", "or count < 5")
    assert _predicate_entry(original, edited, 5) == 3
    patch = make_patch(path, original, edited)
    change, = parse_unified_diff(patch)
    index = Index({path: original}, {path: {"3": "AwA="}}, set(), None)
    assert index.narrow(patch, change) == {3}
    assert index.narrow(patch.replace("and count < 5", "and count < 6"), change) is None

    reassigned = edited.replace("or count < 5", "or (extra := count) < 5")
    assert _predicate_entry(original, reassigned, 5) is None
    assert _predicate_entry(original, edited.replace("or count < 5", "or (yield count)"), 5) is None

    original = 'def f():\n    return """first\n    second\n    """\n'
    edited = original.replace("    second", "    # New wording\n    second")
    patch = make_patch(path, original, edited)
    change, = parse_unified_diff(patch)
    assert Index({path: original}, {}, set(), None).narrow(patch, change) is None


if __name__ == "__main__":
    check()
    print("Pinpoint source-refinement guards passed.")

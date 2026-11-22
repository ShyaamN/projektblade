"""Regression: a Python edit that cannot compile must not schedule nothing."""

from __future__ import annotations

import ast
import difflib
import json
import sys
from pathlib import Path
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import selector  # noqa: E402


def load():
    tests = json.loads((ROOT / "sample" / "tests.json").read_text(encoding="utf-8"))["tests"]
    id_to_number = {t["id"]: t["number"] for t in tests}
    all_numbers = {t["number"] for t in tests}
    file_to_numbers: dict[str, list[int]] = {}
    for test in tests:
        file_to_numbers.setdefault(test["file"], []).append(test["number"])
    impact = selector.ImpactMaps.load(set(id_to_number))
    pinpoint = selector.PinpointIndex.load(
        selector.PINPOINT_PATH, id_to_number, selector.PINNED_SHA
    )
    if pinpoint is None or not impact.map_ok:
        raise SystemExit("maps unavailable")
    coverage = selector.CoverageIndex.from_data(pinpoint.primary, integer_lines=True)
    return pinpoint, coverage, impact, id_to_number, all_numbers, file_to_numbers


def patch_for(path: str, original: str, edited: str) -> str:
    diff = difflib.unified_diff(
        original.splitlines(),
        edited.splitlines(),
        f"a/{path}",
        f"b/{path}",
        lineterm="",
    )
    return "\n".join([f"diff --git a/{path} b/{path}", *diff]) + "\n"


def choose(patch, loaded):
    pinpoint, coverage, impact, ids, numbers, files = loaded
    return selector.select(
        patch, coverage, impact, ids, numbers, files, pinpoint=pinpoint
    )


def main() -> int:
    loaded = load()
    pinpoint = loaded[0]
    numbers = loaded[4]
    path = "sqlglot/__init__.py"
    original = pinpoint.sources[path]
    broken = original.replace("import logging\n", "import logging def\n", 1)
    if broken == original:
        raise SystemExit("could not plant a syntax error")
    syntax = patch_for(path, original, broken)
    selected = choose(syntax, loaded)
    assert selected.force_all and selected.numbers == numbers, selected.reasons
    assert selected.reasons[0] == "python_syntax"

    raises = original.replace("import logging\n", "raise RuntimeError('test')\nimport logging\n", 1)
    compile(raises, path, "exec")
    selected_import = choose(patch_for(path, original, raises), loaded)
    assert selected_import.force_all and selected_import.numbers == numbers, selected_import.reasons

    test_path = "tests/test_parser.py"
    test_source = zlib.decompress(pinpoint.test_sources[test_path]).decode("utf-8")
    method = next(
        node for node in ast.walk(ast.parse(test_source))
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    )
    lines = test_source.splitlines()
    first_line = method.body[0].lineno - 1
    prefix = lines[first_line][:len(lines[first_line]) - len(lines[first_line].lstrip())]
    lines.insert(first_line, prefix + "raise AssertionError('test body mutation')")
    body = choose(patch_for(test_path, test_source, "\n".join(lines)), loaded)
    assert not body.force_all and body.numbers == set(loaded[5][test_path]), body.reasons
    collection = choose(
        patch_for(test_path, test_source, "raise RuntimeError('collection')\n" + test_source),
        loaded,
    )
    assert collection.force_all, collection.reasons

    workers = [[0], [1]]
    selector.retain_within_slack(
        workers, {0, 2, 3}, {0: {"ms": 10}, 1: {"ms": 3}, 2: {"ms": 7}, 3: {"ms": 8}},
    )
    assert workers == [[0], [1, 2]]

    ordinary = (ROOT / "sample" / "commits" / "2jrq.patch").read_text(encoding="utf-8")
    routine = choose(ordinary, loaded)
    assert not routine.force_all, routine.reasons
    assert 0 < len(routine.numbers) < len(numbers)

    mixed = ordinary + syntax
    widened = choose(mixed, loaded)
    assert widened.force_all and widened.numbers == numbers, widened.reasons

    bad = syntax.replace("import logging\n", "import missing\n", 1)
    context = choose(bad, loaded)
    assert context.force_all and context.reasons[0] == "python_context"

    print(
        f"syntax={len(selected.numbers)} routine={len(routine.numbers)} "
        f"mixed={len(widened.numbers)} context={context.reasons[0]}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

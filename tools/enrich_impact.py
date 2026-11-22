#!/usr/bin/env python3
"""Enrich maps/impact.json with dump/load callers and AST function ranges."""

from __future__ import annotations

import ast
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PINNED_SHA = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"


def function_ranges(path: Path) -> list[dict]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append(
                {
                    "name": node.name,
                    "start": node.lineno,
                    "end": getattr(node, "end_lineno", node.lineno) or node.lineno,
                    "kind": type(node).__name__,
                }
            )
    return out


def main() -> int:
    sqlglot = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "vendor" / "sqlglot")
    impact_path = Path(sys.argv[2] if len(sys.argv) > 2 else ROOT / "maps" / "impact.json")
    tests = json.loads((ROOT / "sample" / "tests.json").read_text(encoding="utf-8"))["tests"]
    file_to_ids = defaultdict(list)
    for t in tests:
        file_to_ids[t["file"]].append(t["id"])

    impact = json.loads(impact_path.read_text(encoding="utf-8"))

    serde_files = []
    for path in (sqlglot / "tests").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if re.search(r"\.dump\(|Expr\.load|import sqlglot\.serde|from sqlglot import serde", text):
            rel = path.relative_to(sqlglot).as_posix()
            if rel in file_to_ids:
                serde_files.append(rel)

    # Executor TPC tests also break when serde corrupts expression graphs.
    for extra in ("tests/test_executor.py",):
        if extra in file_to_ids and extra not in serde_files:
            serde_files.append(extra)

    ranges = {}
    for rel in impact.get("modules", {}).values():
        path = sqlglot / rel
        if path.exists():
            ranges[rel] = function_ranges(path)

    impact["serde_test_files"] = sorted(serde_files)
    impact["function_ranges"] = ranges
    impact["pinned_sha"] = impact.get("pinned_sha") or PINNED_SHA
    impact_path.write_text(json.dumps(impact, separators=(",", ":")), encoding="utf-8")
    print(
        f"enriched {impact_path}: serde_files={len(serde_files)} "
        f"range_files={len(ranges)}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

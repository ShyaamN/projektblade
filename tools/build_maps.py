#!/usr/bin/env python3
"""Build permitted static maps from the pinned sqlglot commit.

Provenance:
  - Source: sqlglot at eb11aec4be8cb2122afe713a4816662c3f0fb2fa
  - Inputs: vendor/sqlglot tree + sample/tests.json (IDs/files only)
  - Output: maps/impact.json (schema_version 2)
  - NOT derived from answers.json or any patch→failing-test table

Run: python tools/build_maps.py [sqlglot_root] [tests.json] [out_dir]
"""

from __future__ import annotations

import ast
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

PINNED_SHA = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"
SCHEMA_VERSION = 2


def module_name(root: Path, path: Path) -> str:
    rel = path.relative_to(root).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def collect_sqlglot_modules(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in (root / "sqlglot").rglob("*.py"):
        mod = module_name(root, path)
        out[mod] = path.relative_to(root).as_posix()
    return out


def parse_file_imports(root: Path, path: Path, known: set[str]) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return set()
    this_mod = module_name(root, path)
    pkg_parts = this_mod.split(".")
    found: set[str] = set()

    def add_cand(cand: str) -> None:
        parts = cand.split(".")
        for i in range(len(parts), 0, -1):
            c = ".".join(parts[:i])
            if c in known:
                found.add(c)
                return

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "sqlglot" or alias.name.startswith("sqlglot."):
                    add_cand(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = pkg_parts[: len(pkg_parts) - node.level]
                if node.module:
                    modbase = ".".join([*base, *node.module.split(".")]) if base else node.module
                else:
                    modbase = ".".join(base)
            else:
                modbase = node.module or ""
            if not (
                modbase == "sqlglot"
                or modbase.startswith("sqlglot.")
                or (node.level and modbase)
            ):
                continue
            candidates = [modbase] if modbase else []
            for alias in node.names:
                if alias.name == "*":
                    continue
                candidates.append(f"{modbase}.{alias.name}" if modbase else alias.name)
            for cand in candidates:
                if cand.startswith("sqlglot"):
                    add_cand(cand)
    return found


def parse_test_imports(path: Path, known: set[str]) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return set()
    found: set[str] = set()

    def add_cand(cand: str) -> None:
        parts = cand.split(".")
        for i in range(len(parts), 0, -1):
            c = ".".join(parts[:i])
            if c in known:
                found.add(c)
                return

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "sqlglot" or alias.name.startswith("sqlglot."):
                    add_cand(alias.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if not (mod == "sqlglot" or mod.startswith("sqlglot.")):
                continue
            add_cand(mod)
            for alias in node.names:
                if alias.name != "*":
                    add_cand(f"{mod}.{alias.name}")
    return found


def build_fixture_method_map(optimizer_src: str) -> dict[str, str]:
    methods: dict[str, str] = {}
    parts = re.split(r"(\n    def test_\w+\()", optimizer_src)
    i = 1
    while i < len(parts):
        header = parts[i]
        body = parts[i + 1] if i + 1 < len(parts) else ""
        method_m = re.search(r"def (test_\w+)\(", header)
        method = method_m.group(1) if method_m else None
        chunk = header + body
        if method:
            for fm in re.finditer(r"check_file\(\s*[\"']([^\"']+)[\"']", chunk, re.S):
                methods[fm.group(1)] = method
            for fm in re.finditer(
                r"load_sql_fixture(?:_pairs)?\(\s*[\"']optimizer/([^\"']+?)\.sql[\"']",
                chunk,
            ):
                methods[fm.group(1)] = method
        i += 2
    return methods


def ids_for_method(method_to_ids: dict[str, list[str]], method: str) -> list[str]:
    return list(method_to_ids.get(method, []))


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "vendor/sqlglot")
    tests_json = Path(sys.argv[2] if len(sys.argv) > 2 else "sample/tests.json")
    out_dir = Path(sys.argv[3] if len(sys.argv) > 3 else "maps")
    out_dir.mkdir(parents=True, exist_ok=True)

    payload_tests = json.loads(tests_json.read_text(encoding="utf-8"))
    tests = payload_tests["tests"]
    test_files = {t["file"] for t in tests}
    all_ids = [t["id"] for t in tests]

    file_to_ids: dict[str, list[str]] = defaultdict(list)
    method_to_ids: dict[str, list[str]] = defaultdict(list)
    for t in tests:
        file_to_ids[t["file"]].append(t["id"])
        method = t["id"].rsplit("::", 1)[-1]
        method_to_ids[method].append(t["id"])

    modules = collect_sqlglot_modules(root)
    known = set(modules)
    module_imports = {
        mod: sorted(parse_file_imports(root, root / rel, known))
        for mod, rel in modules.items()
    }

    fixture_methods = build_fixture_method_map(
        (root / "tests" / "test_optimizer.py").read_text(encoding="utf-8")
    )
    fixture_to_ids: dict[str, list[str]] = {}
    for stem, method in fixture_methods.items():
        ids = ids_for_method(method_to_ids, method)
        if ids:
            fixture_to_ids[stem] = ids
            fixture_to_ids[Path(stem).name] = ids

    # Name fallback for fixtures not found via check_file string scan.
    for fixture_path in (root / "tests" / "fixtures").rglob("*.sql"):
        rel = fixture_path.relative_to(root / "tests" / "fixtures").as_posix()
        stem = rel[: -len(".sql")]
        base = Path(stem).name
        if base in fixture_to_ids and stem in fixture_to_ids:
            continue
        candidates = [f"test_{base}", f"test_{base.rstrip('s')}"]
        if base.endswith("_projections"):
            candidates.append("test_" + base[: -len("ions")] + "ion")
        for cand in candidates:
            ids = ids_for_method(method_to_ids, cand)
            if ids:
                fixture_to_ids[stem] = ids
                fixture_to_ids[base] = ids
                break

    test_imports = {
        tf: sorted(parse_test_imports(root / tf, known))
        for tf in sorted(test_files)
        if (root / tf).exists()
    }
    module_to_test_files: dict[str, list[str]] = defaultdict(list)
    for tf, mods in test_imports.items():
        for mod in mods:
            module_to_test_files[mod].append(tf)
    module_to_test_files = {k: sorted(set(v)) for k, v in module_to_test_files.items()}

    dialect_names = sorted(
        {
            Path(rel).stem
            for rel in modules.values()
            if rel.startswith("sqlglot/dialects/")
            and not rel.endswith("__init__.py")
            and Path(rel).stem != "dialect"
        }
    )

    payload = {
        "schema_version": SCHEMA_VERSION,
        "pinned_sha": PINNED_SHA,
        "generator": "tools/build_maps.py",
        "provenance": {
            "sqlglot_root": str(root.as_posix()),
            "tests_json": str(tests_json.as_posix()),
            "notes": (
                "Static AST import edges + optimizer fixture-reader scan. "
                "No answers.json. No patch-to-test tables."
            ),
        },
        "test_ids": all_ids,
        "modules": modules,
        "module_imports": module_imports,
        "test_file_imports": test_imports,
        "module_to_test_files": module_to_test_files,
        "file_to_ids": dict(file_to_ids),
        "method_to_ids": dict(method_to_ids),
        "fixture_to_ids": fixture_to_ids,
        "dialect_names": dialect_names,
    }
    out_path = out_dir / "impact.json"
    out_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {out_path} ({out_path.stat().st_size} bytes)", file=sys.stderr)
    print(f"schema={SCHEMA_VERSION} sha={PINNED_SHA} tests={len(all_ids)}", file=sys.stderr)
    print(f"fixtures mapped: {len(fixture_to_ids)}", file=sys.stderr)
    for key in [
        "pushdown_predicates",
        "qualify_columns__invalid",
        "simplify",
        "pushdown_cte_alias_columns",
        "annotate_types",
        "merge_subqueries",
        "pushdown_projections",
        "isolate_table_selects",
        "unnest_subqueries",
        "qualify_columns_ddl",
    ]:
        ids = fixture_to_ids.get(key, [])
        print(f"  {key}: {len(ids)} ids", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

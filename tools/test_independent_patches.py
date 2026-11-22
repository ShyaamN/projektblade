"""Compare two selectors on fresh SQLGlot edits with verified pytest outcomes."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory, gettempdir

from test_source_guard import patch_for


ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / "vendor/sqlglot"
RUN_SELECTOR = """
import sys
from pathlib import Path
import selector
selector.DATA = Path(sys.argv[1])
selector.main()
"""


CASES = (
    ("missing_import", "sqlglot/expressions/core.py", "import datetime\n",
     "import projektblade_unavailable_module\nimport datetime\n",
     "tests/test_parser.py::TestParser::test_union", "projektblade_unavailable_module"),
    ("fixture_directive", "tests/fixtures/optimizer/annotate_types.sql",
     "# dialect: spark", "# dialect: projektblade_unavailable_dialect",
     "tests/test_optimizer.py::TestOptimizer::test_annotate_types", "projektblade_unavailable_dialect"),
    ("comment_in_sql_template", "sqlglot/generators/duckdb.py",
     "        WITH rand AS (SELECT :random_expr AS r),",
     "        # projektblade holdout text\n        WITH rand AS (SELECT :random_expr AS r),",
     "tests/dialects/test_duckdb.py::TestDuckDB::test_min_max", "ParseError"),
    ("expression_required_argument", "sqlglot/expressions/ddl.py",
     '        "referenced_table": False,', '        "referenced_table": True,',
     "tests/dialects/test_duckdb.py::TestDuckDB::test_create_trigger", "referenced_table"),
    ("show_handler_target", "sqlglot/parsers/mysql.py",
     '"CREATE TABLE": _show_parser("CREATE TABLE", target=True),',
     '"CREATE TABLE": _show_parser("CREATE TABLE", target=False),',
     "tests/dialects/test_mysql.py::TestMySQL::test_show_create_qualified", "FAILED"),
    ("isolated_test_assertion", "tests/test_parser.py",
     '        self.assertEqual(expr.sql(dialect="clickhouse"), single_union)\n',
     '        self.assertEqual(expr.sql(dialect="clickhouse"), single_union)\n'
     '        assert False, "projektblade holdout assertion"\n',
     "tests/test_parser.py::TestParser::test_union", "projektblade holdout assertion"),
    ("filtered_fixture_trivia", "tests/fixtures/optimizer/annotate_types.sql",
     "# dialect: spark", "-- project note, filtered by the fixture reader\n# dialect: spark",
     "tests/test_optimizer.py::TestOptimizer::test_annotate_types", None),
    ("parser_small_node_limit", "sqlglot/parser.py",
     "if self.max_nodes > -1:", "if self.max_nodes > 3:",
     "tests/dialects/test_athena.py::TestAthena::test_max_nodes", "ParseError not raised"),
    ("invalid_source_encoding", "sqlglot/expressions/core.py",
     '"""sqlglot expressions core - base classes, traits, operators, and helpers."""',
     '# coding: projektblade_unavailable_codec\n'
     '"""sqlglot expressions core - base classes, traits, operators, and helpers."""',
     "tests/test_parser.py::TestParser::test_union", "unknown encoding"),
)


def selection(root, data):
    run = subprocess.run(
        [sys.executable, "-B", "-c", RUN_SELECTOR, str(data)], cwd=root,
        capture_output=True, text=True, check=True, timeout=60,
    )
    return {row["commit"]: row["workers"] for row in map(json.loads, run.stdout.splitlines())}


def check_pytest(root, node):
    env = {
        **os.environ, "PYTHONPATH": str(root), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "SKIP_INTEGRATION": "0", "PYTHON_CPU_COUNT": "2", "OPENBLAS_NUM_THREADS": "1",
    }
    return subprocess.run(
        [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", node],
        cwd=root, env=env, capture_output=True, text=True, timeout=65,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default="e525db5321a7acbe93c0994981f1047e929ba35f")
    by_case = {case[0]: case for case in CASES}
    parser.add_argument("--case", action="append", choices=by_case)
    parser.add_argument("--mixed", action="append", choices=by_case, nargs=2)
    args = parser.parse_args()
    singles = args.case or (list(by_case) if not args.mixed else [])
    groups = {key: [by_case[key]] for key in singles}
    for keys in args.mixed or ():
        pair = [by_case[key] for key in keys]
        if pair[0][1] == pair[1][1]:
            parser.error("A mixed regression requires two distinct source files")
        groups["mixed__" + "__".join(keys)] = pair
    test_path = ROOT / "sample/tests.json"
    tests = json.loads(test_path.read_text(encoding="utf-8"))["tests"]
    by_id = {test["id"]: test["number"] for test in tests}
    ms = {test["number"]: round(test["seconds"] * 1000) for test in tests}

    def cost(workers):
        return (max((sum(ms[n] for n in w) for w in workers), default=0) + 2000 * len(workers)) / 1000

    with TemporaryDirectory(prefix="projektblade-holdout-") as temp:
        tmp = Path(temp).resolve()
        if tmp.parent != Path(gettempdir()).resolve():
            raise RuntimeError("Unexpected scratch location")
        data = tmp / "data"
        (data / "commits").mkdir(parents=True)
        shutil.copyfile(test_path, data / "tests.json")
        baseline = tmp / "baseline"
        for relative in ("selector.py", "pinpoint.py", "maps/impact.json", "maps/pinpoint.marshal.gz",
                         "maps/coverage.json.gz"):
            target = baseline / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(subprocess.check_output(
                ["git", "show", f"{args.baseline}:{relative}"], cwd=ROOT
            ))
        checkout = tmp / "checkout"
        for relative in ("sqlglot", "tests"):
            shutil.copytree(UPSTREAM / relative, checkout / relative,
                            ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
        originals = {}
        for key, cases in groups.items():
            edits = []
            for _, relative, old, new, _, _ in cases:
                path = checkout / relative
                originals[path] = path.read_bytes()
                original = path.read_text(encoding="utf-8")
                assert old in original, key
                edits.append(patch_for(relative, original, original.replace(old, new, 1)))
            (data / "commits" / f"{key}.patch").write_text("".join(edits), encoding="utf-8")
        before = selection(baseline, data)
        after = selection(ROOT, data)
        for key, cases in groups.items():
            for _, _, _, _, node, _ in cases:
                clean = check_pytest(checkout, node)
                assert clean.returncode == 0, clean.stdout + clean.stderr
            try:
                for _, relative, old, new, _, _ in cases:
                    path = checkout / relative
                    original = path.read_text(encoding="utf-8")
                    path.write_text(original.replace(old, new, 1), encoding="utf-8")
                for name, _, _, _, node, expected in cases:
                    broken = check_pytest(checkout, node)
                    report = broken.stdout + broken.stderr
                    number = by_id[node]
                    previously_selected = any(number in worker for worker in before[key])
                    currently_selected = any(number in worker for worker in after[key])
                    if expected is None:
                        assert broken.returncode == 0, report
                        if len(cases) == 1:
                            assert not after[key], key
                        outcome = "pass -> pass"
                    else:
                        assert broken.returncode and expected in report, report
                        assert currently_selected, f"Omitted failing test: {node}"
                        outcome = "pass -> fail"
                    print(f"{key}/{name}: {outcome}; previous inclusion={previously_selected}, "
                          f"current inclusion={currently_selected}", flush=True)
            finally:
                for _, relative, _, _, _, _ in cases:
                    path = checkout / relative
                    path.write_bytes(originals[path])
            print(f"{key}: schedule {cost(before[key]):.3f}s -> {cost(after[key]):.3f}s", flush=True)


if __name__ == "__main__":
    main()

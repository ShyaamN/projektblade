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
    args = parser.parse_args()
    test_path = ROOT / "sample/tests.json"
    tests = json.loads(test_path.read_text(encoding="utf-8"))["tests"]
    by_id = {test["id"]: test["number"] for test in tests}
    ms = {test["number"]: round(test["seconds"] * 1000) for test in tests}
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
        for key, relative, old, new, _, _ in CASES:
            original = (checkout / relative).read_text(encoding="utf-8")
            assert original.count(old) >= 1, key
            updated = original.replace(old, new, 1)
            patch = patch_for(relative, original, updated)
            (data / "commits" / f"{key}.patch").write_text(patch, encoding="utf-8")
        before = selection(baseline, data)
        after = selection(ROOT, data)
        for key, relative, old, new, node, expected in CASES:
            target = checkout / relative
            original = target.read_bytes()
            clean = check_pytest(checkout, node)
            assert clean.returncode == 0, clean.stdout + clean.stderr
            updated = original.decode("utf-8").replace("\r\n", "\n").replace(old, new, 1)
            try:
                target.write_text(updated, encoding="utf-8")
                broken = check_pytest(checkout, node)
            finally:
                target.write_bytes(original)
            report = broken.stdout + broken.stderr
            assert broken.returncode and expected in report, report
            number = by_id[node]
            scheduled = lambda groups: number in {n for group in groups for n in group}
            cost = lambda groups: (max((sum(ms[n] for n in w) for w in groups), default=0)
                                   + 2000 * len(groups)) / 1000
            print(f"{key}: pass -> fail; previous inclusion={scheduled(before[key])}, "
                  f"current inclusion={scheduled(after[key])}; "
                  f"schedule {cost(before[key]):.3f}s -> {cost(after[key]):.3f}s", flush=True)
            assert scheduled(after[key]), f"Omitted failing test: {node}"


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Read-only audit of current sample schedules. Labels are evaluation only."""

import argparse
import ast
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "sample"
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))

import selector
from score import evaluate_run


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def loads(workers, tests):
    return [sum(tests[n]["ms"] for n in worker) for worker in workers if worker]


def cost(workers, tests):
    bins = loads(workers, tests)
    return max(bins, default=0) + 2000 * len(bins)


def emit(outputs):
    return ("\n".join(json.dumps(row) for row in outputs) + "\n").encode()


def select_with_sources(diff, cov, impact, ids, numbers, files):
    sources = defaultdict(set)
    exact = set()
    changes = selector.parse_unified_diff(diff) or []
    for change in changes:
        if change.old_path:
            exact.update(cov.tests_for_lines(change.old_path, change.old_lines, neighbors=False))

    def record(method, name):
        def call(*args, **kwargs):
            selected = method(*args, **kwargs)
            sources[name].update(selected)
            return selected
        return call

    def routes(changes, impact):
        picked = original_routes(changes, impact)
        if picked is not None:
            sources["pool routes"].update(ids[test_id] for test_id in picked if test_id in ids)
        return picked

    original_routes = selector._pool_route_ids
    with (
        patch.object(cov, "tests_for_lines", record(cov.tests_for_lines, "line seed")),
        patch.object(cov, "tests_for_functions", record(cov.tests_for_functions, "function seed")),
        patch.object(selector, "_pool_route_ids", routes),
    ):
        result = selector.select(diff, cov, impact, ids, numbers, files)
    with patch.object(selector, "_pool_route_ids", return_value=set()):
        no_routes = selector.select(diff, cov, impact, ids, numbers, files)
    return result, no_routes.numbers, sources, exact, changes


def neutral_import_comment_paths(diff, changes):
    """Recognize plain comments added between imports, with unchanged parsed AST."""
    neutral = set()
    paths = {
        change.old_path for change in changes
        if change.old_path and change.old_path == change.new_path
        and change.old_path.startswith("sqlglot/") and change.old_path.endswith(".py")
        and not change.removed_bodies and change.added_bodies
    }
    path = None
    prior = None
    old_line = None
    original = []
    additions = defaultdict(list)
    rejected = set()
    for line in diff.splitlines():
        header = selector.DIFF_FILE_RE.match(line)
        if header:
            path = header[1]
            old_line = None
            if path in paths:
                original = (ROOT / "vendor/sqlglot" / path).read_text(encoding="utf-8").splitlines()
            continue
        if path not in paths:
            continue
        if header := selector.HUNK_RE.match(line):
            old_line = int(header[1])
            prior = None
        elif old_line is not None and line.startswith(" "):
            if not 0 < old_line <= len(original) or original[old_line - 1] != line[1:]:
                rejected.add(path)
            old_line += 1
            prior = line[1:]
        elif old_line is not None and line.startswith("+"):
            following = original[old_line - 1] if 0 < old_line <= len(original) else ""
            if (
                prior and prior.startswith(("import ", "from "))
                and following.startswith(("import ", "from "))
                and old_line > 2
                and re.fullmatch(r"\+#[ ]+[A-Za-z][A-Za-z ,.-]*", line)
                and not re.search(r"(?i)noqa|type|coding|pragma|fmt|lint|pylint|ruff|pyright|mypy", line)
            ):
                additions[path].append((old_line - 1, line[1:]))
            else:
                rejected.add(path)
        elif old_line is not None:
            rejected.add(path)
    for path, edits in additions.items():
        if path in rejected:
            continue
        base = (ROOT / "vendor/sqlglot" / path).read_text(encoding="utf-8").splitlines()
        updated = base[:]
        for index, line in sorted(edits, reverse=True):
            updated.insert(index, line)
        if ast.dump(ast.parse("\n".join(base))) == ast.dump(ast.parse("\n".join(updated))):
            neutral.add(path)
    return neutral


def executor_fixture_activity(executor_ids):
    """Map executor node IDs to pinned fixture cases whose execute metadata is truthy."""
    base = ROOT / "vendor/sqlglot"
    tree = ast.parse((base / "tests/test_executor.py").read_text(encoding="utf-8"))
    methods = {node.name: node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
    fixtures = {}
    for node in ast.walk(methods["setUpClass"]):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Attribute):
            continue
        for call in ast.walk(node.value):
            if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                and call.func.id == "load_sql_fixture_pairs"):
                fixtures[target.attr] = ast.literal_eval(call.args[0])
    result = {}
    for test_id in executor_ids:
        method = methods[test_id.rsplit("::", 1)[-1]]
        keys = {
            node.attr for node in ast.walk(method)
            if isinstance(node, ast.Attribute) and node.attr in fixtures
        }
        if len(keys) != 1:
            continue
        fixture = fixtures[keys.pop()]
        text = (base / "tests/fixtures" / fixture).read_text(encoding="utf-8")
        statements = "\n".join(
            line for line in text.splitlines() if line and not line.startswith("--")
        ).split(";")
        active = 0
        # Mirror load_sql_fixture_pairs and mp_execute. "false" is a truthy string.
        pairs = len(statements) // 2
        for i in range(0, pairs * 2, 2):
            meta = {}
            for line in statements[i].strip().splitlines():
                if not line.startswith("#"):
                    break
                key, _, val = line.lstrip("#").partition(":")
                meta[key.strip()] = val.strip()
            active += bool(meta.get("execute"))
        result[test_id] = (fixture, pairs, active)
    return result


def base_optimizer_worker_calls(activity):
    """Read-only replay of the optimizer stage of enabled pinned fixture cases."""
    base = ROOT / "vendor/sqlglot"
    sys.path.insert(0, str(base))
    from sqlglot import parse_one
    from sqlglot.optimizer import optimize
    from tests.helpers import TPCDS_SCHEMA, TPCH_SCHEMA, load_sql_fixture_pairs

    schemas = {"optimizer/tpc-ds/tpc-ds.sql": TPCDS_SCHEMA,
               "optimizer/tpc-h/tpc-h.sql": TPCH_SCHEMA}
    prefix = base.as_posix() + "/"
    result = {}
    for test_id, (fixture, _, active) in sorted(activity.items()):
        calls = set()
        case_calls = set()
        traced = 0

        def trace(frame, event, arg):
            filename = frame.f_code.co_filename.replace(chr(92), "/")
            if event == "call" and filename.startswith(prefix + "sqlglot/optimizer/"):
                case_calls.add((filename[len(prefix):], frame.f_code.co_name))
            return None

        for meta, sql, _ in load_sql_fixture_pairs(fixture):
            if not meta.get("execute"):
                continue
            expr = parse_one(sql)
            case_calls.clear()
            previous = sys.gettrace()
            sys.settrace(trace)
            try:
                optimize(expr, schema=schemas[fixture], leave_tables_isolated=True, dialect=None)
            finally:
                sys.settrace(previous)
            if ("sqlglot/optimizer/optimizer.py", "optimize") not in case_calls:
                raise RuntimeError(f"Optimizer entry missing from fixture trace for {test_id}")
            calls.update(case_calls)
            traced += 1
        if traced != active:
            raise RuntimeError(f"Incomplete fixture replay for {test_id}: {traced}/{active}")
        result[test_id] = calls
    return result


def isolated_bool_assignment(changes, impact):
    """Recognize a body-local boolean literal replacement inside one optimizer rule."""
    if len(changes) != 1:
        return None
    c = changes[0]
    if not (
        c.old_path and c.old_path == c.new_path
        and c.old_path.startswith("sqlglot/optimizer/")
        and len(c.old_lines) == len(c.added_bodies) == len(c.removed_bodies) == 1
        and not c.structural
    ):
        return None
    if (len(c.removed_bodies[0]) - len(c.removed_bodies[0].lstrip())
        != len(c.added_bodies[0]) - len(c.added_bodies[0].lstrip())):
        return None
    try:
        before = ast.parse(c.removed_bodies[0].strip()).body[0]
        after = ast.parse(c.added_bodies[0].strip()).body[0]
    except (SyntaxError, IndexError):
        return None
    if not isinstance(before, ast.Assign) or not isinstance(after, ast.Assign):
        return None
    for statement in (before, after):
        for node in ast.walk(statement):
            if isinstance(node, ast.Constant) and type(node.value) is bool:
                node.value = False
    if ast.dump(before) != ast.dump(after):
        return None
    body_line = next(iter(c.old_lines))
    pinned = (ROOT / "vendor/sqlglot" / c.old_path).read_text(encoding="utf-8").splitlines()
    if not 0 < body_line <= len(pinned) or pinned[body_line - 1] != c.removed_bodies[0]:
        return None
    scopes = [
        r for r in impact.function_ranges.get(c.old_path, ())
        if r["kind"] == "FunctionDef" and r["start"] < body_line <= r["end"]
    ]
    if not scopes:
        return None
    return c.old_path, min(scopes, key=lambda r: r["end"] - r["start"])["name"]


def variants(diff, changes, impact, ids, selected, no_routes, exact, activity, worker_calls):
    executor = {ids[test_id] for test_id in impact.pool_routes["executor_ids"]}
    neutral = neutral_import_comment_paths(diff, changes)
    remaining = [change for change in changes if change.old_path not in neutral]
    other_routes = {ids[test_id] for test_id in selector._pool_route_ids(remaining, impact)}
    protected = no_routes | exact
    neutral_drops = (selected & executor) - protected - other_routes
    eligible = any(
        p and p.startswith(("sqlglot/optimizer/", "sqlglot/executor/"))
        and impact.path_to_module.get(p) in impact.pool_routes["executor_deps"]
        for c in changes for p in (c.old_path, c.new_path)
    )
    closure_drops = (selected & executor) - protected if eligible else set()
    inactive = {ids[test_id] for test_id, (_, _, active) in activity.items() if active == 0}
    python_changes = [c for c in remaining if (c.old_path or "").endswith(".py")]
    has_only_optimizer_or_executor_bodies = bool(python_changes) and all(
        c.old_path == c.new_path
        and c.old_path.startswith(("sqlglot/optimizer/", "sqlglot/executor/"))
        and c.old_lines and c.added_bodies and not c.structural
        and all(
            any(r["start"] < line <= r["end"] and r["kind"] == "FunctionDef"
                for r in impact.function_ranges.get(c.old_path, ()))
            for line in c.old_lines
        )
        for c in python_changes
    )
    inactive_drops = closure_drops & inactive if has_only_optimizer_or_executor_bodies else set()
    result = {
        "Prune executor routes triggered solely by neutral import comments": neutral_drops,
        "Also prune inactive worker fixtures on optimizer/executor body edits": neutral_drops | inactive_drops,
        "Reject module-closure executor routes without a direct seed": closure_drops,
    }
    if worker_calls is not None:
        target = isolated_bool_assignment(changes, impact)
        unreached = {
            ids[test_id] for test_id, calls in worker_calls.items() if target not in calls
        } if target else set()
        result["Also prune unreached optimizer bodies after pinned fixture replay"] = (
            neutral_drops | inactive_drops | (closure_drops & unreached)
        )
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay-base-optimizer", action="store_true",
                        help="Replay only the pinned optimizer stage of enabled pool fixture cases")
    args = parser.parse_args()
    initial_sha = hashlib.sha256((ROOT / "selector.py").read_bytes()).hexdigest()
    raw_tests = read_json(SAMPLE / "tests.json")["tests"]
    tests = {t["number"]: {**t, "ms": selector.duration_ms(t["seconds"])} for t in raw_tests}
    ids = {t["id"]: n for n, t in tests.items()}
    numbers = set(tests)
    files = defaultdict(list)
    for n, test in tests.items():
        files[test["file"]].append(n)
    cov = selector.CoverageIndex.load()
    impact = selector.ImpactMaps.load(set(ids))
    if not cov.ok or not impact.map_ok or list(ids) != cov.test_ids:
        raise RuntimeError(f"Cannot audit an invalid map: {cov.error}, {impact.map_error}")
    if read_json(selector.IMPACT_PATH)["test_ids"] != list(ids):
        raise RuntimeError("Impact test ID order mismatch")
    activity = executor_fixture_activity(impact.pool_routes["executor_ids"])
    worker_calls = base_optimizer_worker_calls(activity) if args.replay_base_optimizer else None
    answers = read_json(SAMPLE / "answers.json")["commits"]
    outputs = []
    details = []
    trials = defaultdict(list)
    removed = defaultdict(dict)
    for file in sorted((SAMPLE / "commits").glob("*.patch")):
        diff = file.read_text(encoding="utf-8")
        result, no_routes, sources, exact, changes = select_with_sources(
            diff, cov, impact, ids, numbers, files
        )
        workers = selector.schedule([tests[n] for n in result.numbers])
        outputs.append({"commit": file.stem, "workers": workers})
        details.append((file.stem, workers, no_routes, sources, exact, result.force_all))
        for name, drops in variants(
            diff, changes, impact, ids, result.numbers, no_routes, exact, activity, worker_calls
        ).items():
            pruned = selector.schedule([tests[n] for n in result.numbers - drops])
            trials[name].append({"commit": file.stem, "workers": pruned})
            if drops:
                removed[name][file.stem] = (sorted(drops), cost(workers, tests), cost(pruned, tests))
    baseline = evaluate_run(emit(outputs), raw_tests, answers)
    if not baseline["ok"]:
        raise RuntimeError(f"Scorer rejected baseline: {baseline}")
    if initial_sha != hashlib.sha256((ROOT / "selector.py").read_bytes()).hexdigest():
        raise RuntimeError("Selector changed during audit. Rerun against the latest revision.")
    print(f"Selector SHA-256: {initial_sha}")
    print(f"Baseline: {baseline['simulated_milliseconds']/1000:.3f} s, "
          f"{len(baseline['missed_commits'])} misses")
    print("\nPinned executor case counts:")
    for test_id, (fixture, pairs, active) in sorted(activity.items()):
        print(f"- `{test_id}` uses `{fixture}`. {active}/{pairs} cases enter the executor.")
    print("\nCounterfactuals with rescheduling:")
    for name, trial in trials.items():
        evaluation = evaluate_run(emit(trial), raw_tests, answers)
        if not evaluation["ok"]:
            raise RuntimeError(f"Scorer rejected counterfactual: {evaluation}")
        print(f"- {name}. {evaluation['simulated_milliseconds']/1000:.3f} s, "
              f"{len(evaluation['missed_commits'])} misses.")
        for commit, (drops, before, after) in removed[name].items():
            dropped = ", ".join(f"`{tests[n]['id']}`" for n in drops)
            print(f"  - {commit} {before/1000:.3f} to {after/1000:.3f} s. Removed {dropped}.")
            for n in set(drops) & set(answers[commit]["failing"]):
                print(f"    - Missed `{tests[n]['id']}`")
    print("\nFailure-only schedules for the requested cost drivers (offline reference):")
    for commit in ("2jrq", "3utz", "5y72", "66ks", "rszg"):
        failure_workers = selector.schedule([tests[n] for n in answers[commit]["failing"]])
        print(f"- {commit} {cost(failure_workers, tests)/1000:.3f} s "
              f"(makespan {max(loads(failure_workers, tests), default=0)/1000:.3f} s).")
    for commit, workers, no_routes, sources, exact, force_all in details:
        if cost(workers, tests) <= 4000:
            continue
        bins = loads(workers, tests)
        print(f"\n### {commit} - makespan {max(bins)/1000:.3f} s, {len(bins)} workers, "
              f"cost {cost(workers, tests)/1000:.3f} s ({sum(map(len, workers))} selected)")
        print("\n| Worker | Test ID | Test (s) | Selection source | Exact old line | "
              "Fixed schedule saving if dropped (s) | Labeled failure |")
        print("|---:|---|---:|---|---|---:|---|")
        for worker_index, (worker, load) in enumerate(zip(workers, bins, strict=True)):
            if load != max(bins):
                continue
            for n in worker:
                origin = [label for label, hits in sources.items() if n in hits]
                only = not force_all and n in sources["pool routes"] and n not in no_routes
                if only:
                    origin = ["pool only"]
                elif not origin or (n in no_routes and origin == ["pool routes"]):
                    origin.append("fixture, test-file or fallback")
                saving = "-"
                if only:
                    pruned = [[m for m in w if m != n] for w in workers]
                    saving = f"{(cost(workers, tests) - cost(pruned, tests))/1000:.3f}"
                print(
                    f"| {worker_index + 1} | `{tests[n]['id']}` | {tests[n]['ms']/1000:.3f} | "
                    f"{', '.join(origin)} | {'yes' if n in exact else 'no'} | {saving} | "
                    f"{'yes' if n in answers[commit]['failing'] else 'no'} |"
                )


if __name__ == "__main__":
    main()

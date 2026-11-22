#!/usr/bin/env python3
"""Offline sample audit. Keep this evaluator and sample labels out of the image."""

from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from contextvars import ContextVar
import hashlib
import json
from pathlib import Path
import re
import sys
from threading import Lock, gettrace as get_thread_trace, settrace as set_thread_trace
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = "tests/fixtures/"
SOURCE = ROOT / "vendor/sqlglot/sqlglot"
SOURCE_PREFIX = SOURCE.as_posix() + "/"
ACTIVE = ContextVar("active_test", default=None)
CHILD_DIRECTORY = None
POOL_LINES = defaultdict(set)
POOL_LOCK = Lock()


class Evidence(NamedTuple):
    kind: str
    paths: tuple[str, ...]


def evidence_for(changes, coverage, function_ranges):
    """Strongest observed base coverage, independent of selector seed policy."""
    evidence = {}
    for change in changes:
        path = change.old_path
        if path is None:
            continue
        bucket = coverage.file_lines.get(path, {})
        exact = {
            n for line in change.old_lines for n in bucket.get(line, ())
        }
        spans = set()
        for anchor in change.old_lines or change.context_lines:
            containing = [
                (r["start"], r["end"])
                for r in function_ranges.get(path, ())
                if r["kind"] in {"FunctionDef", "AsyncFunctionDef"}
                and r["start"] <= anchor <= r["end"]
            ]
            if containing:
                spans.add(min(containing, key=lambda span: span[1] - span[0]))
        functions = {
            n for line, numbers in bucket.items()
            if any(start <= line <= end for start, end in spans)
            for n in numbers
        }
        file_hits = {n for numbers in bucket.values() for n in numbers}
        fixtures = coverage.fixtures.get(path[len(FIXTURE_ROOT):], ()) if path.startswith(
            FIXTURE_ROOT
        ) else ()
        for kind, hits in (
            ("exact changed-line", exact),
            ("enclosing-function", functions),
            ("file-level", file_hits),
            ("fixture", fixtures),
        ):
            for n in hits:
                evidence.setdefault(n, defaultdict(set))[kind].add(path)
    return evidence


def strongest(evidence):
    for kind in ("exact changed-line", "enclosing-function", "fixture", "file-level"):
        if kind in evidence:
            return Evidence(kind, tuple(sorted(evidence[kind])))
    return Evidence("none", ())


class DiscardStaticFiles(set):
    """Disable the static test-file sink while preserving all seed logic."""

    def add(self, value):
        pass

    def update(self, *values):
        pass


def load_selector_snapshot():
    import types

    path = ROOT / "selector.py"
    source = path.read_bytes()
    needle = b"selected_files: set[str] = set()"
    if source.count(needle) != 1:
        raise RuntimeError("Selector static sink changed. Review the offline adapter.")
    modules = []
    sys.dont_write_bytecode = True
    for name, code in (
        ("static_gap_baseline", source),
        ("static_gap_without_suites", source.replace(
            needle, b"selected_files: set[str] = DiscardStaticFiles()"
        )),
    ):
        module = types.ModuleType(name)
        module.__file__ = str(path)
        module.DiscardStaticFiles = DiscardStaticFiles
        sys.modules[name] = module
        exec(compile(code, str(path), "exec"), vars(module))
        modules.append(module)
    return *modules, hashlib.sha256(source).hexdigest()


def dependency_closure(module, imports):
    seen = set()
    pending = [module]
    while pending:
        node = pending.pop()
        if node not in seen:
            seen.add(node)
            pending.extend(imports.get(node, ()))
    return seen


def pool_routes(tests, impact_raw):
    """Identify pinned test methods that send SQL or expressions to workers."""
    base = ROOT / "vendor/sqlglot"
    imports = impact_raw["module_imports"]
    modules = set(impact_raw["modules"])
    optimizer_file = "tests/test_optimizer.py"
    executor_file = "tests/test_executor.py"
    routes = {}
    executor_ids = set()
    tag = re.compile(r"^#\s*dialect:\s*(\w+)\s*$", re.M)
    for file in (optimizer_file, executor_file):
        tree = ast.parse((base / file).read_text(encoding="utf-8"))
        aliases = {
            item.asname or item.name: f"{node.module}.{item.name}"
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module
            for item in node.names
        }
        numbers = {test["id"].rsplit("::", 1)[-1]: n for n, test in tests.items()
                   if test["file"] == file}
        for method in ast.walk(tree):
            if not isinstance(method, ast.FunctionDef) or method.name not in numbers:
                continue
            n = numbers[method.name]
            for call in ast.walk(method):
                if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
                    continue
                if not isinstance(call.func.value, ast.Name) or call.func.value.id != "self":
                    continue
                if file == executor_file and call.func.attr == "_mp_execute":
                    executor_ids.add(n)
                if file != optimizer_file or call.func.attr != "check_file":
                    continue
                fixture, target = call.args[:2]
                dialects, dependencies = routes.setdefault(n, (set(), set()))
                if isinstance(fixture, ast.Constant) and isinstance(fixture.value, str):
                    path = base / FIXTURE_ROOT / "optimizer" / f"{fixture.value}.sql"
                    dialects.update(tag.findall(path.read_text(encoding="utf-8")))
                name = ast.unparse(target)
                root, _, rest = name.partition(".")
                qualified = aliases.get(root, root) + ("." + rest if rest else "")
                if qualified == "sqlglot.optimizer.optimize":
                    qualified = "sqlglot.optimizer.optimizer.optimize"
                if qualified == "qualify_then_canonicalize":
                    dependencies.update(dependency_closure("sqlglot.optimizer.qualify", imports))
                    dependencies.update(dependency_closure("sqlglot.optimizer.canonicalize", imports))
                while "." in qualified:
                    qualified = qualified.rpartition(".")[0]
                    if qualified in modules:
                        dependencies.update(dependency_closure(qualified, imports))
                        break
    return routes, executor_ids, dependency_closure("sqlglot.executor", imports)


def envelope(changes, impact, routes, executor_ids, executor_deps):
    """Add tests by base-source pool routes, independent of sample labels."""
    picked = set()
    for change in changes:
        for path in {change.old_path, change.new_path} - {None}:
            if not path.startswith("sqlglot/"):
                continue
            module = impact.path_to_module.get(path)
            if path == "sqlglot/serde.py":
                # Expr.__reduce__ uses serde during expression payload transport.
                picked.update(routes)
                picked.update(executor_ids)
            elif path.startswith(("sqlglot/optimizer/", "sqlglot/executor/")):
                picked.update(n for n, (_, deps) in routes.items() if module in deps)
                if module in executor_deps or path.startswith("sqlglot/executor/"):
                    picked.update(executor_ids)
            else:
                parts = path.split("/")
                if len(parts) == 3 and parts[1] in {"dialects", "parsers", "generators", "typing"}:
                    dialect = Path(path).stem
                    picked.update(n for n, (dialects, _) in routes.items() if dialect in dialects)
    return picked


def cost(workers, tests):
    loads = [sum(tests[n]["ms"] for n in worker) for worker in workers if worker]
    return max(loads, default=0) + 2000 * len(loads)


def schedule_bound(numbers, tests):
    durations = [tests[n]["ms"] for n in numbers]
    if not durations:
        return 0
    total, longest = sum(durations), max(durations)
    return min(
        max(longest, (total + k - 1) // k) + 2000 * k
        for k in range(1, min(len(durations), 8) + 1)
    )


def trace_source(frame, event, arg):
    record = ACTIVE.get()
    if record is None or event != "call":
        return None
    filename = frame.f_code.co_filename.replace("\\", "/")
    if not filename.startswith(SOURCE_PREFIX):
        return None
    path = "sqlglot/" + filename[len(SOURCE_PREFIX):]

    def record_line(frame, event, arg):
        if event == "line":
            record[path].add(frame.f_lineno)
        return record_line

    return record_line


def init_child(directory, initializer, initargs):
    global CHILD_DIRECTORY
    CHILD_DIRECTORY = Path(directory)
    sys.settrace(trace_source)
    set_thread_trace(trace_source)
    if initializer is not None:
        initializer(*initargs)


def traced_job(node, fn, args, kwargs):
    record = defaultdict(set)
    token = ACTIVE.set(record)
    try:
        return fn(*args, **kwargs)
    finally:
        ACTIVE.reset(token)
        if CHILD_DIRECTORY is not None:
            import os

            # One process writes each shard. Include every job, even on error.
            with (CHILD_DIRECTORY / f"{os.getpid()}.jsonl").open("a", encoding="utf-8") as out:
                out.write(json.dumps([node, {p: sorted(lines) for p, lines in record.items()}]) + "\n")
        else:
            with POOL_LOCK:
                for p, lines in record.items():
                    POOL_LINES[node, p].update(lines)


def collect_pool_gap(node_ids):
    """Run explicitly selected tests and print parent/worker coverage as JSON."""
    import contextlib
    import os
    import tempfile
    from unittest.mock import patch

    os.environ.setdefault("SKIP_INTEGRATION", "0")
    sys.path.insert(0, str(SOURCE.parent))
    os.chdir(SOURCE.parent)
    import pytest

    current_node = ContextVar("current_node", default=None)
    parent_hits = defaultdict(set)

    class Plugin:
        @pytest.hookimpl(hookwrapper=True, tryfirst=True)
        def pytest_runtest_protocol(self, item, nextitem):
            node_token = current_node.set(item.nodeid)
            lines = defaultdict(set)
            trace_token = ACTIVE.set(lines)
            try:
                yield
            finally:
                ACTIVE.reset(trace_token)
                current_node.reset(node_token)
                for path, hits in lines.items():
                    parent_hits[item.nodeid, path].update(hits)

    with tempfile.TemporaryDirectory(prefix="blade-pool-trace-") as directory:
        class TracedProcessPool(ProcessPoolExecutor):
            def __init__(self, *args, initializer=None, initargs=(), **kwargs):
                super().__init__(*args, initializer=init_child,
                                 initargs=(directory, initializer, initargs), **kwargs)

            def submit(self, fn, /, *args, **kwargs):
                node = current_node.get()
                if node is None:
                    return super().submit(fn, *args, **kwargs)
                return super().submit(traced_job, node, fn, args, kwargs)

        class TracedThreadPool(ThreadPoolExecutor):
            def submit(self, fn, /, *args, **kwargs):
                node = current_node.get()
                if node is None:
                    return super().submit(fn, *args, **kwargs)
                return super().submit(traced_job, node, fn, args, kwargs)

        # Keep hooks installed across items and before pytest imports the suite.
        previous = sys.gettrace()
        previous_thread = get_thread_trace()
        sys.settrace(trace_source)
        set_thread_trace(trace_source)
        try:
            with (patch("concurrent.futures.ProcessPoolExecutor", TracedProcessPool),
                  patch("concurrent.futures.ThreadPoolExecutor", TracedThreadPool),
                  contextlib.redirect_stdout(sys.stderr)):
                result = pytest.main(
                    ["-q", "-p", "no:cacheprovider", *node_ids], plugins=[Plugin()]
                )
        finally:
            sys.settrace(previous)
            set_thread_trace(previous_thread)
        child_hits = defaultdict(set)
        jobs = Counter()
        for shard in Path(directory).glob("*.jsonl"):
            for line in shard.read_text(encoding="utf-8").splitlines():
                node, lines = json.loads(line)
                jobs[node] += 1
                for path, hits in lines.items():
                    child_hits[node, path].update(hits)

    def by_node(records):
        result = defaultdict(dict)
        for (node, path), lines in sorted(records.items()):
            result[node][path] = sorted(lines)
        return result

    print(json.dumps({
        "pytest_exit": int(result),
        "child_jobs": jobs,
        "parent_lines": by_node(parent_hits),
        "child_lines": by_node(child_hits),
        "thread_lines": by_node(POOL_LINES),
    }, separators=(",", ":")))
    return int(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="store_true", help="Emit the full Markdown table")
    parser.add_argument("--trace-pool", nargs="+", metavar="NODE_ID",
                        help="Run selected pinned-base tests with worker attribution")
    args = parser.parse_args()
    if args.trace_pool:
        sys.dont_write_bytecode = True
        return collect_pool_gap(args.trace_pool)
    base, without_suites, sha = load_selector_snapshot()
    tests = {
        t["number"]: {**t, "ms": base.duration_ms(t["seconds"])}
        for t in json.loads((ROOT / "sample/tests.json").read_text(encoding="utf-8"))["tests"]
    }
    ids = {t["id"]: n for n, t in tests.items()}
    files = defaultdict(list)
    for n, test in tests.items():
        files[test["file"]].append(n)
    coverage = base.CoverageIndex.load()
    impact = base.ImpactMaps.load(set(ids))
    impact_raw = json.loads((ROOT / "maps/impact.json").read_text(encoding="utf-8"))
    routes, executor_ids, executor_deps = pool_routes(tests, impact_raw)
    if not coverage.ok or coverage.test_ids != list(ids) or not impact.map_ok:
        raise RuntimeError(f"Map validation failed: {coverage.error}, {impact.map_error}")
    answers = json.loads((ROOT / "sample/answers.json").read_text(encoding="utf-8"))["commits"]
    patches = sorted((ROOT / "sample/commits").glob("*.patch"))
    if {p.stem for p in patches} != answers.keys():
        raise RuntimeError("Sample patches and labels differ")
    variants = ("current", "without suites", "route envelope", "sample-only none adds",
                "sample-only minimum adds", "failure-only reference")
    totals = Counter()
    misses = defaultdict(dict)
    categories = Counter()
    rows = []
    commit_rows = []
    probe_rows = []
    seed_overhead = []
    fixed_seed_bound = 0
    for patch_path in patches:
        commit = patch_path.stem
        patch = patch_path.read_text(encoding="utf-8")
        changes = base.parse_unified_diff(patch)
        if not changes:
            raise RuntimeError(f"Could not parse {patch_path.name}")
        seed = without_suites.select(patch, coverage, impact, ids, set(tests), files).numbers
        current = base.select(patch, coverage, impact, ids, set(tests), files).numbers
        additions = envelope(changes, impact, routes, executor_ids, executor_deps)
        if not seed <= current:
            raise RuntimeError(f"Static removal unexpectedly increased selection for {commit}")
        signals = evidence_for(changes, coverage, impact.function_ranges)
        failing = set(answers[commit]["failing"])
        fixed_seed_bound += schedule_bound(seed | failing, tests)
        none = {n for n in failing if not signals.get(n)}
        costs = []
        for name, picked in zip(
            variants, (current, seed, seed | additions, seed | none, seed | failing, failing)
        ):
            elapsed = cost(base.schedule([tests[n] for n in picked]), tests)
            costs.append(elapsed)
            totals[name] += elapsed
            if omitted := failing - picked:
                misses[name][commit] = sorted(tests[n]["id"] for n in omitted)
        probe_group = (seed | additions) & executor_ids
        if probe_group:
            after_probes = (seed | additions) - (probe_group - failing)
            after_cost = cost(base.schedule([tests[n] for n in after_probes]), tests)
            probe_rows.append((commit, costs[2] - after_cost, probe_group))
        paths = sorted({c.new_path or c.old_path for c in changes})
        commit_rows.append((commit, paths, len(failing), costs, len(additions - seed)))
        seed_overhead.append((costs[-2] - costs[-1], commit, seed - failing))
        for n in sorted(failing):
            evidence = strongest(signals.get(n, {}))
            categories[evidence.kind] += 1
            rows.append((commit, tests[n]["id"], evidence, n in seed, n in seed | additions))
    print(f"Selector SHA-256 `{sha}`")
    print(f"Coverage map `{base.COV_GZ_PATH.name}` with {len(coverage.file_lines)} source files")
    print(f"Commits {len(commit_rows)}, failures {len(rows)}, classifications {dict(categories)}")
    print(f"Base-derived pool routes: {len(routes)} optimizer methods, {len(executor_ids)} executor methods")
    for name in variants:
        count = len(misses[name])
        simulated = totals[name] + 300_000 * count
        print(f"{name}: execution={totals[name]/1000:.3f}s misses={count} simulated={simulated/1000:.3f}s")
        for commit, missed in sorted(misses[name].items()):
            for test_id in missed:
                print(f"  {commit} {test_id}")
    print(f"Fixed seed plus failures, any-scheduler lower bound: {fixed_seed_bound/1000:.3f}s")
    print("Largest remaining costs above failure-only schedules, with the three longest seed extras:")
    for excess, commit, extra in sorted(seed_overhead, reverse=True)[:5]:
        longest = sorted(extra, key=lambda n: (-tests[n]["ms"], n))[:3]
        print(f"  {commit} excess={excess/1000:.3f}s "
              + ", ".join(f"{tests[n]['id']} ({tests[n]['ms']/1000:.3f}s)" for n in longest))
    print("Executor pool probe upper bounds (labels model a conclusive pass on every passing probe):")
    for commit, ceiling, group in probe_rows:
        print(f"  {commit} saves at most {ceiling/1000:.3f}s; "
              f"group's recorded elapsed={sum(tests[n]['ms'] for n in group)/1000:.3f}s; "
              + ", ".join(tests[n]["id"] for n in sorted(group)))
    if args.report:
        print("\n| Commit | Labeled failing test | Strongest map evidence | Changed path with evidence | Seed selected | Envelope selected |")
        print("|---|---|---|---|---|---|")
        for commit, test_id, signal, in_seed, in_envelope in rows:
            paths = ", ".join(f"`{p}`" for p in signal.paths) or "-"
            print(f"| {commit} | `{test_id}` | {signal.kind} | {paths} | {'yes' if in_seed else 'no'} | {'yes' if in_envelope else 'no'} |")
        print("\n| Commit | Changed paths | Labeled failures | Current (s) | Seed (s) | Envelope (s) | Added tests |")
        print("|---|---|---:|---:|---:|---:|---:|")
        for commit, paths, count, costs, added in commit_rows:
            print(f"| {commit} | {', '.join(paths)} | {count} | " +
                  " | ".join(f"{ms/1000:.3f}" for ms in costs[:3]) + f" | {added} |")


if __name__ == "__main__":
    raise SystemExit(main())

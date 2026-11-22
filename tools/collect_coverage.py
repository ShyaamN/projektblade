#!/usr/bin/env python3
"""Collect per-test line coverage + fixture reads from pinned sqlglot.

Optimized tracer: string-prefix checks only (no Path.resolve per line).
"""

from __future__ import annotations

import atexit
import gzip
import json
import os
import sys
import threading
import time
from collections import defaultdict
from contextvars import ContextVar
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SQLGLOT = ROOT / "vendor" / "sqlglot"
TESTS_JSON = ROOT / "sample" / "tests.json"
OUT_DIR = ROOT / "maps"
PINNED_SHA = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"

# These tests stress the tokenizer/parser with huge inputs; profiling every
# bytecode line makes them time out. We skip tracing them and rely on
# file/subsystem fallbacks for those rare patches.
SKIP_TRACE_SUBSTRINGS = (
    "test_max_nodes",
    "test_max_tokens",
)

# Spawned workers re-import this module. These globals are filled in the child.
ACTIVE: ContextVar = ContextVar("blade_active_lines", default=None)
CHILD_DIR: Path | None = None
PENDING_NODE: str | None = None
PENDING_RECORD: dict | None = None
_PATH_CACHE: dict[str, str | None] = {}


def rel_for(filename: str) -> str | None:
    """Map a code object's filename onto a repo path under sqlglot/."""
    cached = _PATH_CACHE.get(filename)
    if cached is not None or filename in _PATH_CACHE:
        return cached
    sqlglot_dir = str((SQLGLOT / "sqlglot").resolve()).replace("\\", "/")
    norm = filename.replace("\\", "/")
    rel = None
    if norm.startswith(sqlglot_dir):
        candidate = "sqlglot" + norm[len(sqlglot_dir) :]
        if candidate.endswith(".py"):
            rel = candidate
    elif "/sqlglot/" in norm:
        idx = norm.rfind("/sqlglot/")
        candidate = norm[idx + 1 :]
        if candidate.startswith("sqlglot/") and candidate.endswith(".py"):
            rel = candidate
    _PATH_CACHE[filename] = rel
    return rel


def child_trace(frame, event, arg):  # noqa: ANN001
    """Line tracer installed in pool workers. Stays quiet outside sqlglot/."""
    if event != "call":
        return None
    rel = rel_for(frame.f_code.co_filename)
    if rel is None:
        return None

    def line(frame, event, arg):  # noqa: ANN001
        if event == "line":
            record = ACTIVE.get()
            if record is None:
                record = PENDING_RECORD
            if record is not None:
                record[rel].add(frame.f_lineno)
        return line

    return line


def flush_pending() -> None:
    """Write the previous job, including pickle work that ran after it returned."""
    global PENDING_NODE, PENDING_RECORD
    if not PENDING_NODE or PENDING_RECORD is None or CHILD_DIR is None:
        PENDING_NODE = None
        PENDING_RECORD = None
        return
    payload = {
        path: sorted(lines) for path, lines in PENDING_RECORD.items()
    }
    with (CHILD_DIR / f"{os.getpid()}.jsonl").open("a", encoding="utf-8") as out:
        out.write(json.dumps([PENDING_NODE, payload]) + "\n")
    PENDING_NODE = None
    PENDING_RECORD = None


def init_pool_child(directory, initializer, initargs):  # noqa: ANN001
    """Pool worker start: trace sqlglot lines and keep the caller's initializer."""
    global CHILD_DIR
    CHILD_DIR = Path(directory)
    atexit.register(flush_pending)
    sys.settrace(child_trace)
    threading.settrace(child_trace)
    if initializer is not None:
        initializer(*initargs)


def traced_pool_job(node, fn, args, kwargs):  # noqa: ANN001
    """Run one submitted job and leave its record open for the result pickle."""
    global PENDING_NODE, PENDING_RECORD
    flush_pending()
    record: dict = defaultdict(set)
    PENDING_NODE = node
    PENDING_RECORD = record
    token = ACTIVE.set(record)
    try:
        return fn(*args, **kwargs)
    finally:
        # Result pickling happens after this function returns. PENDING_RECORD
        # stays set so those serde lines still attribute to this node.
        ACTIVE.reset(token)


def main() -> int:
    os.environ.setdefault("SKIP_INTEGRATION", "0")
    sys.path.insert(0, str(SQLGLOT))

    import builtins
    import io
    import pytest

    tests_meta = json.loads(TESTS_JSON.read_text(encoding="utf-8"))["tests"]
    id_to_number = {t["id"]: t["number"] for t in tests_meta}
    wanted = set(id_to_number)

    fixtures_root = str((SQLGLOT / "tests" / "fixtures").resolve())
    fixtures_root_alt = fixtures_root.replace("\\", "/")
    sqlglot_repo = str(SQLGLOT.resolve())
    sqlglot_repo_alt = sqlglot_repo.replace("\\", "/")

    line_map: dict[str, dict[int, set[int]]] = defaultdict(lambda: defaultdict(set))
    fixture_map: dict[str, set[int]] = defaultdict(set)

    state = {"num": None, "matched": 0, "collected": 0, "skipped_trace": 0}
    started = time.time()
    current_node: ContextVar = ContextVar("blade_node", default=None)
    submitted: dict[str, int] = defaultdict(int)

    def profiler(frame, event, arg):  # noqa: ANN001
        if event != "line":
            return profiler
        num = state["num"]
        if num is None:
            return profiler
        rel = rel_for(frame.f_code.co_filename)
        if rel is None:
            return profiler
        line_map[rel][frame.f_lineno].add(num)
        return profiler

    real_open = builtins.open

    def tracked_open(file, *args, **kwargs):
        handle = real_open(file, *args, **kwargs)
        num = state["num"]
        if num is None:
            return handle
        try:
            path = str(Path(file).resolve()).replace("\\", "/")
            if path.startswith(fixtures_root_alt):
                rel = path[len(fixtures_root_alt) :].lstrip("/")
                fixture_map[rel].add(num)
        except Exception:
            pass
        return handle

    builtins.open = tracked_open
    io.open = tracked_open  # type: ignore[attr-defined]

    class Plugin:
        @pytest.hookimpl(hookwrapper=True, tryfirst=True)
        def pytest_runtest_protocol(self, item, nextitem):  # noqa: N802
            token = current_node.set(item.nodeid.replace("\\", "/"))
            try:
                yield
            finally:
                current_node.reset(token)

        def pytest_runtest_setup(self, item):  # noqa: N802
            nodeid = item.nodeid.replace("\\", "/")
            num = id_to_number.get(nodeid)
            state["num"] = num
            if num is None:
                return
            if any(s in nodeid for s in SKIP_TRACE_SUBSTRINGS):
                state["skipped_trace"] += 1
                state["num"] = num  # still attribute fixtures if any
                return
            # settrace emits 'line' events; setprofile does not.
            # Optimizer tests submit work to threads — install thread tracer too.
            sys.settrace(profiler)
            threading.settrace(profiler)

        def pytest_runtest_teardown(self, item, nextitem):  # noqa: N802
            sys.settrace(None)
            threading.settrace(None)
            state["collected"] += 1
            if state["num"] is not None:
                state["matched"] += 1
            state["num"] = None
            if state["matched"] and state["matched"] % 50 == 0:
                print(
                    f"progress matched={state['matched']}/{len(wanted)} "
                    f"files={len(line_map)} lines={sum(len(v) for v in line_map.values())} "
                    f"elapsed={time.time() - started:.0f}s",
                    file=sys.stderr,
                    flush=True,
                )

    from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

    class TracedProcessPool(ProcessPoolExecutor):
        def __init__(self, *args, initializer=None, initargs=(), **kwargs):
            super().__init__(
                *args,
                initializer=init_pool_child,
                initargs=(str(trace_dir), initializer, initargs),
                **kwargs,
            )

        def submit(self, fn, /, *args, **kwargs):
            node = current_node.get()
            if node is None:
                submitted["<unattributed>"] += 1
                return super().submit(fn, *args, **kwargs)
            submitted[node] += 1
            return super().submit(traced_pool_job, node, fn, args, kwargs)

    class TracedThreadPool(ThreadPoolExecutor):
        def submit(self, fn, /, *args, **kwargs):
            node = current_node.get()
            if node is None:
                return super().submit(fn, *args, **kwargs)
            submitted[node] += 1
            return super().submit(traced_pool_job, node, fn, args, kwargs)

    print(f"collecting coverage for {len(wanted)} labeled tests", file=sys.stderr)
    # PYTHONPATH so spawned interpreters can import the pinned tree.
    prior = os.environ.get("PYTHONPATH", "")
    os.environ["PYTHONPATH"] = (
        str(SQLGLOT) + (os.pathsep + prior if prior else "")
    )
    import tempfile
    from unittest.mock import patch

    trace_root = tempfile.TemporaryDirectory(prefix="blade-pool-trace-")
    trace_dir = Path(trace_root.name)
    # High timeout: parser stress tests and traced pool jobs are heavy.
    targets = sys.argv[1:] or [str(SQLGLOT / "tests")]
    with patch("concurrent.futures.ProcessPoolExecutor", TracedProcessPool), patch(
        "concurrent.futures.ThreadPoolExecutor", TracedThreadPool
    ):
        code = pytest.main(
            [
                *targets,
                "-q",
                "--tb=line",
                "-p",
                "no:cacheprovider",
                "--timeout=600",
                "-p",
                "no:cov",
                f"--rootdir={SQLGLOT}",
            ],
            plugins=[Plugin()],
        )
    child_jobs: dict[str, int] = defaultdict(int)
    for shard in trace_dir.glob("*.jsonl"):
        for line in shard.read_text(encoding="utf-8").splitlines():
            node, lines = json.loads(line)
            node = str(node).replace("\\", "/")
            child_jobs[node] += 1
            num = id_to_number.get(node)
            if num is None:
                continue
            for path, hits in lines.items():
                for lineno in hits:
                    line_map[path][int(lineno)].add(num)
    trace_root.cleanup()
    print(
        f"pytest exit={code} matched={state['matched']} "
        f"collected={state['collected']} skipped_trace={state['skipped_trace']}",
        file=sys.stderr,
    )

    compact = {
        path: {str(line): sorted(nums) for line, nums in sorted(lines.items())}
        for path, lines in sorted(line_map.items())
    }
    fixtures_compact = {k: sorted(v) for k, v in sorted(fixture_map.items())}

    ordered_ids = [t["id"] for t in tests_meta]
    submitted_n = sum(submitted.values())
    completed_n = sum(child_jobs.values())
    missing_jobs = sorted(
        node for node, count in submitted.items()
        if node != "<unattributed>" and child_jobs.get(node, 0) < count
    )
    print(
        f"pool submitted={submitted_n} completed={completed_n} "
        f"unattributed={submitted.get('<unattributed>', 0)} "
        f"nodes_short={len(missing_jobs)}",
        file=sys.stderr,
    )
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    partial = bool(sys.argv[1:])
    cov_path = OUT_DIR / ("coverage.smoke.json.gz" if partial else "coverage.child.json.gz")
    payload = {
        "schema_version": 1,
        "format": "coverage.json.gz",
        "pinned_sha": PINNED_SHA,
        "test_ids": ordered_ids,
        "test_count": len(wanted),
        "matched_tests": state["matched"],
        "skipped_trace": state["skipped_trace"],
        "files": compact,
        "fixtures": fixtures_compact,
        "elapsed_seconds": round(time.time() - started, 1),
        "child_jobs": completed_n,
        "child_jobs_submitted": submitted_n,
    }
    with gzip.open(cov_path, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"))
    print(
        f"wrote {cov_path} ({cov_path.stat().st_size} bytes) "
        f"files={len(compact)} fixtures={len(fixtures_compact)}",
        file=sys.stderr,
    )
    summary = {
        "matched_tests": state["matched"],
        "wanted_tests": len(wanted),
        "files": len(compact),
        "fixtures": len(fixtures_compact),
        "bytes": cov_path.stat().st_size,
        "elapsed_seconds": payload["elapsed_seconds"],
        "pytest_exit": code,
        "skipped_trace": state["skipped_trace"],
        "child_jobs": completed_n,
        "child_jobs_submitted": submitted_n,
        "unattributed_submits": submitted.get("<unattributed>", 0),
        "nodes_short": missing_jobs[:20],
        "partial": partial,
    }
    (OUT_DIR / "coverage_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    return 0 if state["matched"] > len(wanted) * 0.8 else 1


if __name__ == "__main__":
    raise SystemExit(main())

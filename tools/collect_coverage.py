#!/usr/bin/env python3
"""Collect per-test line coverage + fixture reads from pinned sqlglot.

Optimized tracer: string-prefix checks only (no Path.resolve per line).
"""

from __future__ import annotations

import gzip
import json
import os
import sys
import threading
import time
from collections import defaultdict
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


def main() -> int:
    os.environ.setdefault("SKIP_INTEGRATION", "0")
    sys.path.insert(0, str(SQLGLOT))

    import builtins
    import io
    import pytest

    tests_meta = json.loads(TESTS_JSON.read_text(encoding="utf-8"))["tests"]
    id_to_number = {t["id"]: t["number"] for t in tests_meta}
    wanted = set(id_to_number)

    sqlglot_dir = str((SQLGLOT / "sqlglot").resolve())
    # Normalize for Windows path compares against co_filename.
    sqlglot_dir_alt = sqlglot_dir.replace("\\", "/")
    fixtures_root = str((SQLGLOT / "tests" / "fixtures").resolve())
    fixtures_root_alt = fixtures_root.replace("\\", "/")
    sqlglot_repo = str(SQLGLOT.resolve())
    sqlglot_repo_alt = sqlglot_repo.replace("\\", "/")

    line_map: dict[str, dict[int, set[int]]] = defaultdict(lambda: defaultdict(set))
    fixture_map: dict[str, set[int]] = defaultdict(set)

    state = {"num": None, "matched": 0, "collected": 0, "skipped_trace": 0}
    started = time.time()
    # co_filename -> rel path or None
    path_cache: dict[str, str | None] = {}

    def rel_for(filename: str) -> str | None:
        if filename in path_cache:
            return path_cache[filename]
        norm = filename.replace("\\", "/")
        # Handle both absolute and already-normalized paths.
        if norm.startswith(sqlglot_dir_alt):
            rel = "sqlglot" + norm[len(sqlglot_dir_alt) :]
            if rel.endswith(".py"):
                path_cache[filename] = rel
                return rel
        # Sometimes co_filename is relative.
        if "/sqlglot/" in norm:
            idx = norm.rfind("/sqlglot/")
            rel = norm[idx + 1 :]
            if rel.startswith("sqlglot/") and rel.endswith(".py"):
                path_cache[filename] = rel
                return rel
        path_cache[filename] = None
        return None

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

    print(f"collecting coverage for {len(wanted)} labeled tests", file=sys.stderr)
    # High timeout: a few parser stress tests remain heavy even without tracing.
    code = pytest.main(
        [
            str(SQLGLOT / "tests"),
            "-q",
            "--tb=line",
            "-p",
            "no:cacheprovider",
            "--timeout=300",
            "-p",
            "no:cov",
        ],
        plugins=[Plugin()],
    )
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

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cov_path = OUT_DIR / "coverage.json.gz"
    payload = {
        "schema_version": 1,
        "pinned_sha": PINNED_SHA,
        "test_count": len(wanted),
        "matched_tests": state["matched"],
        "skipped_trace": state["skipped_trace"],
        "files": compact,
        "fixtures": fixtures_compact,
        "elapsed_seconds": round(time.time() - started, 1),
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
    }
    (OUT_DIR / "coverage_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    return 0 if state["matched"] > len(wanted) * 0.8 else 1


if __name__ == "__main__":
    raise SystemExit(main())

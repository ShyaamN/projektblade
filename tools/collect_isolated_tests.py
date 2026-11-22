"""Offline Linux profiling from test collection through final teardown."""

import argparse
from collections import defaultdict
import contextlib
import gzip
import io
import json
import os
from pathlib import Path
import platform
import signal
import sys
from tempfile import TemporaryDirectory


PINNED = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"


def observe(node, root, output):
    import pytest

    monitor = sys.monitoring
    tool = monitor.COVERAGE_ID
    hits = defaultdict(set)
    reads = set()
    process = False
    prefix = root.as_posix() + "/"
    sqlglot = prefix + "sqlglot/"

    def audit(event, args):
        nonlocal process
        if event in {"subprocess.Popen", "os.fork", "os.posix_spawn", "os.exec"}:
            process = True
        if event != "open" or not isinstance(args[0], (str, bytes)):
            return
        name = os.fsdecode(args[0])
        path = name.removeprefix(prefix) if name.startswith(prefix) else name.removeprefix("./")
        if path.startswith(("sqlglot/", "tests/fixtures/")):
            reads.add(path)

    def trace(code, line):
        if code.co_filename.startswith(sqlglot):
            hits[code.co_filename.removeprefix(prefix)].add(line)
        return monitor.DISABLE

    sys.addaudithook(audit)
    monitor.use_tool_id(tool, "projektblade-isolated")
    monitor.register_callback(tool, monitor.events.LINE, trace)
    monitor.set_events(tool, monitor.events.LINE)
    log = io.StringIO()
    signal.alarm(175)
    try:
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            code = pytest.main(["-q", "-p", "no:cacheprovider", "-p", "pytest_timeout",
                                "--timeout=160", node])
    finally:
        signal.alarm(0)
        monitor.set_events(tool, 0)
    if code:
        print(f"Baseline test failed: {node}\n{log.getvalue()[-1400:]}", file=sys.stderr, flush=True)
        return 1
    output.write_text(json.dumps({"lines": {path: sorted(lines) for path, lines in sorted(hits.items())},
                                  "reads": sorted(reads), "subprocess": process}, separators=(",", ":")),
                      encoding="utf-8")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--node", action="append")
    args = parser.parse_args()
    if sys.platform != "linux" or not 1 <= args.jobs <= 8:
        parser.error("Use Linux and between one and eight workers")
    # Import tooling before fork, with SQLGlot left cold in each test process.
    import pytest

    root = Path.cwd()
    sys.path.insert(0, str(root))
    all_tests = json.loads(args.inventory.read_text(encoding="utf-8"))["tests"]
    chosen = set(args.node or ())
    tests = [test for test in all_tests if not chosen or test["id"] in chosen]
    if not tests or chosen - {test["id"] for test in tests}:
        parser.error("Unknown test node")
    pending = {}
    done = failures = 0
    with TemporaryDirectory(prefix="blade-isolated-") as temp:
        workspace = Path(temp)

        def reap():
            nonlocal done, failures
            pid, status = os.wait()
            while pid not in pending:
                # As PID 1, the collector also adopts exited pytest descendants.
                pid, status = os.wait()
            node = pending.pop(pid)
            if not os.WIFEXITED(status) or os.WEXITSTATUS(status):
                failures += 1
                print(f"Incomplete profile: {node}", file=sys.stderr, flush=True)
            done += 1
            if done % 100 == 0 or done == len(tests):
                print(f"Profiled {done}/{len(tests)} tests, {failures} incomplete.", file=sys.stderr, flush=True)

        for test in tests:
            while len(pending) >= args.jobs:
                reap()
            pid = os.fork()
            if pid == 0:
                code = 1
                try:
                    code = observe(test["id"], root, workspace / f"{test['number']}.json")
                finally:
                    os._exit(code)
            pending[pid] = test["id"]
        while pending:
            reap()
        records = {}
        for test in tests:
            path = workspace / f"{test['number']}.json"
            if path.is_file():
                records[test["id"]] = json.loads(path.read_text(encoding="utf-8"))
    data = {"pinned_sha": PINNED, "python": list(sys.version_info[:2]), "platform": platform.machine(),
            "pytest": pytest.__version__, "test_ids": [test["id"] for test in all_tests], "profiles": records}
    args.output.write_bytes(gzip.compress(json.dumps(data, separators=(",", ":")).encode(), mtime=0))


if __name__ == "__main__":
    main()

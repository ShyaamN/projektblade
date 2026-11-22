"""Record pinned stress-test line reachability with one event per location."""

import gzip
import json
import os
from collections import defaultdict
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "vendor/sqlglot"


def collect():
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(BASE))
    os.chdir(BASE)
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    os.environ["SKIP_INTEGRATION"] = "0"
    import pytest

    with gzip.open(ROOT / "maps/coverage.child.json.gz", "rt", encoding="utf-8") as stream:
        coverage = json.load(stream)
    nodes = [node for node in coverage["test_ids"] if "test_max_nodes" in node]
    observations = defaultdict(lambda: defaultdict(set))
    prefix = (BASE / "sqlglot").as_posix() + "/"
    active = None
    monitor = sys.monitoring
    tool = monitor.COVERAGE_ID

    def line(code, number):
        source = code.co_filename.replace("\\", "/")
        if active is not None and source.startswith(prefix):
            path = "sqlglot/" + source.removeprefix(prefix)
            observations[active][path].add(number)
        return monitor.DISABLE

    class Observer:
        @pytest.hookimpl(hookwrapper=True, tryfirst=True)
        def pytest_runtest_protocol(self, item, nextitem):
            nonlocal active
            active = item.nodeid
            monitor.restart_events()
            monitor.set_events(tool, monitor.events.LINE)
            try:
                yield
            finally:
                monitor.set_events(tool, 0)
                active = None

    monitor.use_tool_id(tool, "projektblade-stress")
    monitor.register_callback(tool, monitor.events.LINE, line)
    try:
        result = pytest.main(["-q", "-p", "no:cacheprovider", *nodes], plugins=[Observer()])
    finally:
        monitor.set_events(tool, 0)
        monitor.register_callback(tool, monitor.events.LINE, None)
        monitor.free_tool_id(tool)
    if result or set(observations) != set(nodes):
        raise RuntimeError("Stress line collection incomplete")
    return {
        "pinned_sha": coverage["pinned_sha"],
        "nodes": {node: {path: sorted(lines) for path, lines in sorted(files.items())}
                  for node, files in sorted(observations.items())},
    }


if __name__ == "__main__":
    data = collect()
    with (ROOT / "maps/stress-lines.json").open("w", encoding="utf-8") as stream:
        json.dump(data, stream, separators=(",", ":"))
    print({node: sum(map(len, files.values())) for node, files in data["nodes"].items()})

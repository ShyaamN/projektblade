"""Supplement line coverage for pinned tests excluded from the tracer."""

from collections import defaultdict
import gzip
import json
import os
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
    from sqlglot.expressions import Expr

    with gzip.open(ROOT / "maps/coverage.child.json.gz", "rt", encoding="utf-8") as stream:
        coverage = json.load(stream)
    nodes = [node for node in coverage["test_ids"] if "test_max_nodes" in node]
    active = None
    constructed = defaultdict(set)
    original = Expr.__init__

    def initialize(self, **args):
        if active is not None:
            constructed[active].add(type(self).__name__)
        original(self, **args)

    class Observer:
        @pytest.hookimpl(hookwrapper=True, tryfirst=True)
        def pytest_runtest_protocol(self, item, nextitem):
            nonlocal active
            active = item.nodeid
            try:
                yield
            finally:
                active = None

    Expr.__init__ = initialize
    try:
        result = pytest.main(["-q", "-p", "no:cacheprovider", *nodes], plugins=[Observer()])
    finally:
        Expr.__init__ = original
    if result or set(constructed) != set(nodes):
        raise RuntimeError("Expression instance collection incomplete")
    return {"pinned_sha": coverage["pinned_sha"],
            "constructed": {node: sorted(names) for node, names in sorted(constructed.items())}}


if __name__ == "__main__":
    data = collect()
    with (ROOT / "maps/expression-instances.json").open("w", encoding="utf-8") as stream:
        json.dump(data, stream, separators=(",", ":"))
    print({node: len(names) for node, names in data["constructed"].items()})

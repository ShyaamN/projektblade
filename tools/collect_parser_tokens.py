"""Record which pinned parser tests receive comment-bearing token streams."""

import ast
import gzip
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "vendor/sqlglot"
PARSER = "sqlglot/parser.py"
METHODS = {"_parse_table_parts", "_parse_table_parts_fast"}


def collect():
    sys.path.insert(0, str(BASE))
    os.chdir(BASE)
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    os.environ["SKIP_INTEGRATION"] = "0"
    os.environ["PYTHON_CPU_COUNT"] = "2"
    import pytest
    from sqlglot.parser import Parser

    maps = ROOT / "maps"
    with gzip.open(maps / "coverage.child.json.gz", "rt", encoding="utf-8") as stream:
        coverage = json.load(stream)
    extras = json.loads((maps / "stress-lines.json").read_text(encoding="utf-8"))
    impact = json.loads((maps / "impact.json").read_text(encoding="utf-8"))
    tree = ast.parse((BASE / PARSER).read_text(encoding="utf-8"))
    methods = [fn for fn in ast.walk(tree) if isinstance(fn, ast.FunctionDef) and fn.name in METHODS]
    lines = {line for fn in methods for line in range(fn.lineno + 1, fn.end_lineno + 1)}
    files = coverage["files"][PARSER]
    nodes = {coverage["test_ids"][i] for line in lines for i in files.get(str(line), ())}
    nodes.update(node for node, paths in extras["nodes"].items() if lines.intersection(paths.get(PARSER, ())))
    pool = impact["pool_routes"]
    routed = set(pool["executor_ids"]) | {route["id"] for route in pool["routes"]}
    nodes -= routed

    active = None
    fixture_scope = None
    observed = set()
    has_comments = set()
    shared_comment_files = set()
    shared_global_comments = False
    original = Parser._parse

    def parse(self, parse_method, raw_tokens, sql=None):
        nonlocal shared_global_comments
        if active is not None:
            observed.add(active)
        if any(token.comments for token in raw_tokens):
            if active is not None:
                has_comments.add(active)
            if fixture_scope in {"class", "module", "package"} and active is not None:
                shared_comment_files.add(active.split("::", 1)[0])
            elif fixture_scope == "session" or active is None:
                shared_global_comments = True
        return original(self, parse_method, raw_tokens, sql)

    class Observer:
        @pytest.hookimpl(hookwrapper=True, tryfirst=True)
        def pytest_runtest_protocol(self, item, nextitem):
            nonlocal active
            active = item.nodeid
            try:
                yield
            finally:
                active = None

        @pytest.hookimpl(hookwrapper=True, tryfirst=True)
        def pytest_fixture_setup(self, fixturedef, request):
            nonlocal fixture_scope
            previous = fixture_scope
            fixture_scope = request.scope
            try:
                yield
            finally:
                fixture_scope = previous

    Parser._parse = parse
    try:
        result = pytest.main(["-q", "-p", "no:cacheprovider", *sorted(nodes)], plugins=[Observer()])
    finally:
        Parser._parse = original
    if result or not observed:
        raise RuntimeError("Parser token observation failed")
    comment_free = [] if shared_global_comments else sorted(
        node for node in observed - has_comments if node.split("::", 1)[0] not in shared_comment_files
    )
    return {"pinned_sha": coverage["pinned_sha"], "test_ids": coverage["test_ids"],
            "comment_free": comment_free, "tested": len(nodes), "observed": len(observed),
            "shared_global_comments": shared_global_comments}


if __name__ == "__main__":
    data = collect()
    with (ROOT / "maps/parser-tokens.json").open("w", encoding="utf-8") as stream:
        json.dump(data, stream, separators=(",", ":"))
    print(f"Observed {data['observed']}/{data['tested']} candidate tests. "
          f"Recorded {len(data['comment_free'])} comment-free tests. "
          f"Session comment input: {data['shared_global_comments']}.")

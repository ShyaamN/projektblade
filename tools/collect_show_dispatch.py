"""Record which tests use each pinned SHOW parser dispatch entry."""

import gzip
import json
import os
from pathlib import Path
import sys
from collections import defaultdict


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "vendor/sqlglot"


def collect():
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(BASE))
    os.chdir(BASE)
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    os.environ["SKIP_INTEGRATION"] = "0"
    import pytest
    from sqlglot.parser import Parser

    with gzip.open(ROOT / "maps/coverage.child.json.gz", "rt", encoding="utf-8") as stream:
        coverage = json.load(stream)
    source = (BASE / "sqlglot/parser.py").read_text(encoding="utf-8")
    lookup = [i for i, line in enumerate(source.splitlines(), 1)
              if "self._find_parser(self.SHOW_PARSERS, self.SHOW_TRIE)" in line]
    if len(lookup) != 1:
        raise RuntimeError("Pinned SHOW dispatch site changed")
    nodes = {
        coverage["test_ids"][n]
        for n in coverage["files"]["sqlglot/parser.py"][str(lookup[0])]
    }
    active = None
    observed = set()
    keys = defaultdict(lambda: defaultdict(set))
    original = Parser._find_parser

    def find_parser(self, parsers, trie):
        result = original(self, parsers, trie)
        if active is not None and parsers is self.SHOW_PARSERS:
            observed.add(active)
            for owner in type(self).__mro__:
                table = owner.__dict__.get("SHOW_PARSERS")
                if table is None:
                    continue
                path = owner.__module__.replace(".", "/") + ".py"
                keys[path]
                if result is not None:
                    for key, handler in table.items():
                        if handler is result:
                            keys[path][key].add(active)
        return result

    class Observer:
        @pytest.hookimpl(hookwrapper=True, tryfirst=True)
        def pytest_runtest_protocol(self, item, nextitem):
            nonlocal active
            active = item.nodeid
            try:
                yield
            finally:
                active = None

    Parser._find_parser = find_parser
    try:
        result = pytest.main(["-q", "-p", "no:cacheprovider", *sorted(nodes)], plugins=[Observer()])
    finally:
        Parser._find_parser = original
    if result or not observed:
        raise RuntimeError("SHOW dispatch collection failed")
    return {
        "pinned_sha": coverage["pinned_sha"],
        "test_ids": coverage["test_ids"],
        "keys": {path: {key: sorted(ids) for key, ids in sorted(bucket.items())}
                 for path, bucket in sorted(keys.items())},
        "unobserved": sorted(nodes - observed),
    }


if __name__ == "__main__":
    data = collect()
    with (ROOT / "maps/show-dispatch.json").open("w", encoding="utf-8") as stream:
        json.dump(data, stream, separators=(",", ":"))
    print(f"Observed {sum(map(len, data['keys'].values()))} dispatch entries. "
          f"Unobserved candidates: {data['unobserved']}")

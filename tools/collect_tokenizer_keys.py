"""Trace the selected keys at the pinned tokenizer's dictionary lookup sites."""

from collections import defaultdict
import gzip
import json
import os
from pathlib import Path
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "vendor/sqlglot"
FILE = "sqlglot/tokenizer_core.py"


def collect():
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(BASE))
    os.chdir(BASE)
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    os.environ["SKIP_INTEGRATION"] = "0"
    import pytest
    from sqlglot.dialects.snowflake import Snowflake

    with gzip.open(ROOT / "maps/coverage.child.json.gz", "rt", encoding="utf-8") as stream:
        coverage = json.load(stream)
    source = (BASE / FILE).read_text(encoding="utf-8")
    lookup_lines = {
        i for i, line in enumerate(source.splitlines(), 1)
        if 'spec = self.numeric_escapes.get("0" if is_octal else peek)' in line
    }
    if len(lookup_lines) != 2:
        raise RuntimeError("Pinned tokenizer lookup sites changed")
    nodes = sorted({
        coverage["test_ids"][n] for line in lookup_lines
        for n in coverage["files"][FILE].get(str(line), ())
    })
    observed = set()
    keys = defaultdict(set)
    active = None
    source_file = (BASE / FILE).as_posix()

    def trace(frame, event, arg):
        if event == "line" and active and frame.f_lineno in lookup_lines:
            local = frame.f_locals
            observed.add(active)
            if local["self"].numeric_escapes == Snowflake.Tokenizer.NUMERIC_ESCAPES:
                keys["0" if local["is_octal"] else local["peek"]].add(active)
        return trace

    def enter(frame, event, arg):
        if event == "call" and frame.f_code.co_filename.replace(chr(92), "/") == source_file:
            return trace
        return None

    class Observer:
        @pytest.hookimpl(hookwrapper=True, tryfirst=True)
        def pytest_runtest_protocol(self, item, nextitem):
            nonlocal active
            active = item.nodeid
            try:
                yield
            finally:
                active = None

    sys.settrace(enter)
    threading.settrace(enter)
    try:
        result = pytest.main(["-q", "-p", "no:cacheprovider", *nodes], plugins=[Observer()])
    finally:
        sys.settrace(None)
        threading.settrace(None)
    if result or not observed:
        raise RuntimeError("Tokenizer operand collection failed")
    return {
        "pinned_sha": coverage["pinned_sha"],
        "test_ids": coverage["test_ids"],
        "keys": {key: sorted(items) for key, items in sorted(keys.items())},
        "unobserved": sorted(set(nodes) - observed),
    }


if __name__ == "__main__":
    result = collect()
    print("Keys observed:", sorted(result["keys"]), "unobserved candidates:", result["unobserved"])
    with (ROOT / "maps/tokenizer-observations.json").open("w", encoding="utf-8") as stream:
        json.dump(result, stream, separators=(",", ":"))

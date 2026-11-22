"""Package pinned source and complete-call coverage for the selector."""

import ast
import base64
import gzip
import json
import marshal
from pathlib import Path
import subprocess
import struct


ROOT = Path(__file__).resolve().parents[1]
PINNED = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"


def main():
    base = ROOT / "vendor/sqlglot"
    head = subprocess.check_output(["git", "-C", str(base), "rev-parse", "HEAD"], text=True).strip()
    if head != PINNED or subprocess.check_output(
        ["git", "-C", str(base), "status", "--porcelain", "--untracked-files=no"], text=True
    ).strip():
        raise RuntimeError("Coverage map requires an unchanged pinned checkout")
    maps = ROOT / "maps"
    with gzip.open(maps / "coverage.child.json.gz", "rt", encoding="utf-8") as stream:
        coverage = json.load(stream)
    summary = json.loads((maps / "coverage_summary.json").read_text(encoding="utf-8"))
    if (
        coverage["pinned_sha"] != PINNED
        or summary["matched_tests"] != coverage["test_count"]
        or summary["partial"]
        or summary["pytest_exit"]
        or summary["unattributed_submits"]
        or coverage["child_jobs_submitted"] != summary["child_jobs_submitted"]
        or coverage["child_jobs"] != summary["child_jobs"]
    ):
        raise RuntimeError("Child coverage is incomplete or misattributed")
    # Scope this optimization to loaded modules. Env, packaging, native-source
    # and tests remain in the ordinary selection path.
    sources = {
        p: (base / p).read_text(encoding="utf-8")
        for p in coverage["files"]
        if p.startswith("sqlglot/") and (base / p).is_file()
    }
    preserve = set(summary["nodes_short"])
    preserve.update(test for test in coverage["test_ids"] if "test_max_nodes" in test)
    lines = {}
    operator_lines = {}
    for path, source in sources.items():
        tree = ast.parse(source)
        keep = {
            str(node.test.lineno if isinstance(node, ast.If) else node.lineno)
            for node in ast.walk(tree)
            if isinstance(node, (ast.If, ast.Assign))
        }
        lines[path] = {
            line: base64.b64encode(struct.pack(f"<{len(hits)}H", *hits)).decode()
            for line, hits in coverage["files"][path].items() if line in keep
        }
        checked = sorted({
            node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Compare) and isinstance(node.left, ast.Attribute)
            and node.left.attr in {"normalization_strategy", "version"}
            and len(node.ops) == 1
        })
        if checked:
            operator_lines[path] = checked
    with gzip.open(maps / "coverage.json.gz", "rt", encoding="utf-8") as stream:
        primary = json.load(stream)
    if primary["pinned_sha"] != PINNED or primary["test_ids"] != coverage["test_ids"]:
        raise RuntimeError("Parent coverage inventory mismatch")
    primary["files"] = {
        path: {int(line): hits for line, hits in bucket.items()}
        for path, bucket in primary["files"].items()
    }
    packaged = {
        "schema": 3,
        "pinned_sha": PINNED,
        "test_ids": coverage["test_ids"],
        "sources": sources,
        "lines": lines,
        "preserve": sorted(preserve),
        "primary": primary,
        "operator_lines": operator_lines,
    }
    observations = maps / "tokenizer-observations.json"
    if observations.exists():
        tokenizer = json.loads(observations.read_text(encoding="utf-8"))
        if tokenizer["pinned_sha"] != PINNED or tokenizer["test_ids"] != coverage["test_ids"]:
            raise RuntimeError("Tokenizer observation inventory mismatch")
        number = {test: n for n, test in enumerate(coverage["test_ids"])}
        packaged["tokenizer"] = {
            "unobserved": sorted(number[test] for test in tokenizer["unobserved"]),
            "keys": {key: sorted(number[test] for test in tests) for key, tests in tokenizer["keys"].items()},
        }
    data = marshal.dumps(packaged, 4, allow_code=False)
    (maps / "pinpoint.marshal.gz").write_bytes(gzip.compress(data, mtime=0))


if __name__ == "__main__":
    main()

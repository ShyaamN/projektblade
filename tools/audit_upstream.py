"""Evaluate selectors on reversions of upstream fixes, without contest answers."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

from test_independent_patches import ROOT, RUN_SELECTOR, UPSTREAM, patch_for, selection


ARTIFACTS = ("selector.py", "pinpoint.py", "maps/impact.json", "maps/coverage.json.gz",
             "maps/pinpoint.marshal.gz")


PYTEST_DRIVER = """
import json, sys
from pathlib import Path
import pytest

class Result:
    def __init__(self):
        self.failures = set()

    def pytest_runtest_logreport(self, report):
        if report.failed:
            self.failures.add(report.nodeid)

    def pytest_collectreport(self, report):
        if report.failed:
            self.failures.add(report.nodeid)

result = Result()
exit_code = pytest.main(["-q", "-p", "no:cacheprovider", *sys.argv[2:]], plugins=[result])
Path(sys.argv[1]).write_text(json.dumps(sorted(result.failures)), encoding="utf-8")
raise SystemExit(exit_code)
"""


def run_tests(checkout, report, paths):
    env = {**os.environ, "PYTHONPATH": str(checkout), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
           "SKIP_INTEGRATION": "0", "PYTHON_CPU_COUNT": "2", "OPENBLAS_NUM_THREADS": "1"}
    # A driver file guarded as __main__ also supports optimizer subprocesses.
    runner = checkout / "_pytest_audit.py"
    runner.write_text('if __name__ == "__main__":\n' + ''.join(
        '    ' + line + '\n' for line in PYTEST_DRIVER.splitlines() if line
    ), encoding="utf-8")
    run = subprocess.run([sys.executable, "-B", str(runner), str(report), *paths], cwd=checkout,
                         env=env, capture_output=True, text=True, timeout=125)
    if run.returncode not in (0, 1, 2) or not report.is_file():
        raise RuntimeError(run.stdout[-2400:] + run.stderr[-2400:])
    return set(json.loads(report.read_text(encoding="utf-8")))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("patch_dir", type=Path)
    parser.add_argument("--baseline", default="669d6b5")
    parser.add_argument("--all-file-tests", action="store_true")
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--patch", action="append")
    parser.add_argument("--probe-node", action="append", default=[])
    args = parser.parse_args()
    inputs = ROOT / "sample/tests.json"
    metadata = json.loads(inputs.read_text(encoding="utf-8"))
    inventory = metadata["tests"]
    skipped = [f"--deselect={node}" for node in metadata.get("skipped", ())]
    ids = {test["id"]: test["number"] for test in inventory}
    times = {test["number"]: round(test["seconds"] * 1000) for test in inventory}

    def seconds(schedule):
        return (max((sum(times[n] for n in worker) for worker in schedule), default=0)
                + 2000 * len(schedule)) / 1000

    with TemporaryDirectory(prefix="projektblade-upstream-audit-") as directory:
        tmp = Path(directory)
        data = tmp / "data"
        (data / "commits").mkdir(parents=True)
        shutil.copyfile(inputs, data / "tests.json")
        baseline = tmp / "baseline"
        historical = subprocess.check_output(
            ["git", "ls-tree", "-r", "--name-only", args.baseline], cwd=ROOT, text=True,
        ).splitlines()
        for path in ARTIFACTS:
            if path not in historical:
                continue
            target = baseline / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(subprocess.check_output(["git", "show", f"{args.baseline}:{path}"], cwd=ROOT))
        checkout = tmp / "checkout"
        for path in ("sqlglot", "tests"):
            shutil.copytree(UPSTREAM / path, checkout / path, ignore=shutil.ignore_patterns("__pycache__"))
        for patch in sorted(args.patch_dir.resolve().glob("*.patch")):
            if args.patch and patch.stem not in args.patch:
                continue
            text = patch.read_text(encoding="utf-8-sig")
            parts = re.split(r"^diff --git a/(\S+) b/\1\n", text, flags=re.M)
            files = dict(zip(parts[1::2], parts[2::2]))
            sources = [path for path in files if path.startswith("sqlglot/") and path.endswith(".py")]
            test_files = [path for path in files if path.startswith("tests/") and path.endswith(".py")]
            if not sources or not test_files or any(not (checkout / path).is_file() for path in sources + test_files):
                print(f"{patch.stem}: no actionable source and test-file pair", flush=True)
                continue
            original = {path: (checkout / path).read_bytes() for path in sources}
            command = ["git", "apply", "-R", *(f"--include={path}" for path in sources), str(patch)]
            apply = subprocess.run(command, cwd=checkout, capture_output=True, text=True)
            if apply.returncode:
                print(f"{patch.stem}: reverse patch does not apply to pin", flush=True)
                continue
            try:
                diff = "".join(patch_for(path, content.decode("utf-8"),
                                         (checkout / path).read_text(encoding="utf-8"))
                               for path, content in original.items())
                (data / "commits" / f"{patch.stem}.patch").write_text(diff, encoding="utf-8")
                targets = []
                for path in test_files:
                    names = set(re.findall(r"^(?:\+[^\n]*?|@@[^\n]*?)\bdef (test_\w+)\(", files[path], re.M))
                    target_nodes = [node for node in ids if node.startswith(path + "::")
                                    and node.rsplit("::", 1)[-1].split("[", 1)[0] in names]
                    targets.extend(target_nodes if target_nodes and not args.all_file_tests else [path])
                targets.extend(args.probe_node)
                failures = (set() if args.plan_only else run_tests(
                    checkout, tmp / f"{patch.stem}-broken.json", [*targets, *skipped, "-k", "not test_max_nodes"],
                ))
            finally:
                for path, content in original.items():
                    (checkout / path).write_bytes(content)
            if failures:
                baseline_failures = run_tests(checkout, tmp / f"{patch.stem}-clean.json", sorted(failures))
                failures -= baseline_failures
            before = selection(baseline, data)[patch.stem]
            after = selection(ROOT, data)[patch.stem]
            for label, workers in (("before", before), ("after", after)):
                scheduled = {n for worker in workers for n in worker}
                omitted = sorted(node for node in failures if ids.get(node) not in scheduled)
                print(f"{patch.stem}: {label} {seconds(workers):.3f}s, {len(failures)} verified failures, "
                      f"omitted={omitted}", flush=True)
            if args.plan_only:
                explain = subprocess.run([sys.executable, "-B", "-c", RUN_SELECTOR, str(data)],
                                         cwd=ROOT, capture_output=True, text=True, check=True)
                info = next(row for row in explain.stderr.splitlines() if row.startswith(patch.stem + ":"))
                critical = max(after, key=lambda worker: sum(times[n] for n in worker), default=[])
                slow = sorted(critical, key=times.__getitem__, reverse=True)[:4]
                print(f"  {info}\n  Critical: {[(inventory[n]['id'], times[n]) for n in slow]}", flush=True)


if __name__ == "__main__":
    main()

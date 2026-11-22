"""Record pinned SQLGlot modules loaded by each test file in a fresh process."""

from concurrent.futures import ThreadPoolExecutor
import gzip
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "vendor/sqlglot"
PINNED = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"
PROBE = """
import importlib, json, pathlib, sys
from collections import defaultdict
path = pathlib.PurePosixPath(sys.argv[1])
root = pathlib.Path.cwd()
prefix = (root / 'sqlglot').as_posix() + '/'
lines = defaultdict(set)
monitor = sys.monitoring
tool = monitor.COVERAGE_ID

def on_line(code, number):
    source = code.co_filename.replace(chr(92), '/')
    if source.startswith(prefix):
        lines['sqlglot/' + source.removeprefix(prefix)].add(number)
    return monitor.DISABLE

monitor.use_tool_id(tool, 'projektblade-cold')
monitor.register_callback(tool, monitor.events.LINE, on_line)
monitor.set_events(tool, monitor.events.LINE)
importlib.import_module(".".join(path.with_suffix("").parts))
monitor.set_events(tool, 0)
seen = []
for name, mod in sorted(sys.modules.items()):
    path = getattr(mod, "__file__", None)
    if path and (name == "sqlglot" or name.startswith("sqlglot.") or name.startswith("tests.")):
        target = pathlib.Path(path).resolve()
        if target.is_relative_to(root):
            seen.append(target.relative_to(root).as_posix())
print("BLADE_IMPORTS:" + json.dumps([seen, {path: sorted(hits) for path, hits in lines.items()}]))
"""


def main():
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=BASE, text=True).strip()
    if sha != PINNED or subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"], cwd=BASE, text=True
    ).strip():
        raise RuntimeError("Import collection requires the untouched pinned checkout")
    tests = json.loads((ROOT / "sample/tests.json").read_text(encoding="utf-8"))["tests"]
    paths = sorted({test["file"] for test in tests})
    env = {
        **os.environ, "PYTHONPATH": str(BASE), "SKIP_INTEGRATION": "0",
        "PYTHONDONTWRITEBYTECODE": "1", "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1",
    }

    def capture(path):
        try:
            result = subprocess.run(
                [sys.executable, "-B", "-c", PROBE, path], cwd=BASE, env=env,
                text=True, capture_output=True, timeout=40,
            )
        except subprocess.TimeoutExpired:
            return path, None
        if result.returncode:
            return path, None
        for line in result.stdout.splitlines():
            if line.startswith("BLADE_IMPORTS:"):
                return path, json.loads(line.removeprefix("BLADE_IMPORTS:"))
        return path, None

    with ThreadPoolExecutor(max_workers=4) as pool:
        captured = dict(pool.map(capture, paths))
    payload = {
        "pinned_sha": PINNED, "test_ids": [test["id"] for test in tests],
        "python": list(sys.version_info[:2]), "platform": sys.platform,
    }
    (ROOT / "maps/cold-imports.json").write_text(
        json.dumps({**payload, "imports": {path: value[0] if value else None for path, value in captured.items()}},
                   separators=(",", ":")) + "\n", encoding="utf-8"
    )
    profiles = {path: value[1] if value else None for path, value in captured.items()}
    raw = json.dumps({**payload, "lines": profiles}, separators=(",", ":")).encode()
    (ROOT / "maps/cold-lines.json.gz").write_bytes(gzip.compress(raw, mtime=0))
    print(f"Recorded {sum(v is not None for v in captured.values())}/{len(paths)} test files.")
    print("Unobserved:", [p for p, v in captured.items() if v is None])


if __name__ == "__main__":
    main()

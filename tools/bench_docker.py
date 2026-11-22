"""Score three local Docker runs with the public sample and contest limits."""

import argparse
from decimal import Decimal, ROUND_HALF_UP
import json
from pathlib import Path
from statistics import median
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from score import evaluate_run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image")
    parser.add_argument("--verify", choices=("0", "1"))
    args = parser.parse_args()
    sample = ROOT / "sample"
    tests = json.loads((sample / "tests.json").read_text(encoding="utf-8"))["tests"]
    answers = json.loads((sample / "answers.json").read_text(encoding="utf-8"))["commits"]
    launcher = (
        'import resource,runpy,sys;sys.path.insert(0,"/app");'
        'runpy.run_path("/app/selector.py",run_name="__main__");'
        'r=[resource.getrusage(who) for who in (resource.RUSAGE_SELF,resource.RUSAGE_CHILDREN)];'
        'print("MEASURED_CPU",sum(q.ru_utime+q.ru_stime for q in r),file=sys.stderr)'
    )
    verifier_env = [] if args.verify is None else ["--env", f"BLADE_VERIFY={args.verify}"]
    command = [
        "docker", "run", "--rm", "--platform", "linux/amd64", "--network", "none",
        "--cpus", "2", "--memory", "4096m", "--memory-swap", "4096m", "--pids-limit", "256",
        "--read-only", "--tmpfs", "/tmp:rw,size=64m,mode=1777", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--user", "65534:65534", "--env", "HOME=/tmp",
        *verifier_env,
        "--mount", f"type=bind,source={sample / 'commits'},target=/data/commits,readonly",
        "--mount", f"type=bind,source={sample / 'tests.json'},target=/data/tests.json,readonly",
        "--entrypoint", "python", args.image, "-c", launcher,
    ]
    totals = []
    for _ in range(3):
        run = subprocess.run(command, capture_output=True, timeout=180)
        if run.returncode:
            raise RuntimeError(run.stderr.decode(errors="replace"))
        if len(run.stdout) > 2 * 1024 * 1024:
            raise RuntimeError("Contest stdout limit exceeded")
        score = evaluate_run(run.stdout, tests, answers)
        if not score["ok"]:
            raise RuntimeError(score)
        cpu = Decimal(run.stderr.decode().rsplit("MEASURED_CPU ", 1)[1].strip())
        simulated = Decimal(score["simulated_milliseconds"]) / 1000
        total = simulated + cpu
        totals.append(total)
        print(f"simulated={simulated:.3f} CPU={cpu:.6f} total={total:.6f} "
              f"misses={score['missed_commits']}", flush=True)
    middle = median(totals)
    print(f"median={middle:.6f} rounded={middle.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Run selector → output.jsonl and score against sample answers (eval only)."""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output.jsonl"
sys.path.insert(0, str(ROOT))


def main() -> int:
    out = OUT.open("w", encoding="utf-8", newline="\n")
    old = sys.stdout
    sys.stdout = out
    try:
        runpy.run_path(str(ROOT / "selector.py"), run_name="__main__")
    finally:
        sys.stdout = old
        out.close()

    from score import evaluate_run

    tests = json.loads((ROOT / "sample" / "tests.json").read_text(encoding="utf-8"))["tests"]
    by_number = {t["number"]: t for t in tests}
    answers = json.loads((ROOT / "sample" / "answers.json").read_text(encoding="utf-8"))[
        "commits"
    ]
    evaluation = evaluate_run(OUT.read_bytes(), tests, answers)
    if not evaluation["ok"]:
        print(f"Rejected: {evaluation['code']} line={evaluation.get('line')}")
        return 1

    ms = evaluation["simulated_milliseconds"]
    misses = evaluation["missed_commits"]
    print(f"Simulated: {ms // 1000}.{ms % 1000:03d} s")
    print(f"Misses:    {len(misses)}")
    for commit in misses:
        selected: set[int] = set()
        for line in OUT.read_text(encoding="utf-8").splitlines():
            obj = json.loads(line)
            if obj["commit"] == commit:
                for worker in obj["workers"]:
                    selected.update(worker)
                break
        failing = answers[commit]["failing"]
        missed_nums = [n for n in failing if n not in selected]
        print(f"  missed {commit}:")
        for n in missed_nums:
            t = by_number[n]
            print(f"    {n} {t['id']}")

    lines = {
        json.loads(line)["commit"]: json.loads(line)
        for line in OUT.read_text(encoding="utf-8").splitlines()
    }
    for commit in sorted(answers):
        obj = lines[commit]
        n = sum(len(w) for w in obj["workers"])
        fail = answers[commit]["failing"]
        covered = all(f in {x for w in obj["workers"] for x in w} for f in fail)
        flag = "OK" if covered else "MISS"
        print(f"{commit}: sel={n:4d} fail={len(fail)} {flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

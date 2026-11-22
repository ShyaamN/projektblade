#!/usr/bin/env python3
"""Ablate candidate sources on the coverage index that scored 261.783s.

That index is maps/covmeta.json + covpostings.bin (first coverage collect).
Sample labels are used only to name excess vs a failure-only schedule.
"""

from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import selector  # noqa: E402
from score import evaluate_run  # noqa: E402

FOCUS = ("5y72", "66ks", "rszg")
FULL = {"old", "context", "func", "static", "neighbors", "file"}
DROPS = {
    "minus_old_lines": FULL - {"old"},
    "minus_context": FULL - {"context"},
    "minus_enclosing_functions": FULL - {"func"},
    "minus_static_suites": FULL - {"static"},
}


def load_bin_index() -> selector.CoverageIndex:
    meta = json.loads((ROOT / "maps" / "covmeta.json").read_text(encoding="utf-8"))
    postings = list(
        struct.unpack(f"<{meta['postings_count']}H", (ROOT / "maps" / "covpostings.bin").read_bytes())
    )
    idx = selector.CoverageIndex()
    for name, entries in meta["files"].items():
        bucket: dict[int, list[int]] = {}
        for entry in entries:
            start, count = entry["start"], entry["count"]
            bucket[int(entry["line"])] = postings[start : start + count]
        idx.file_lines[name] = bucket
    idx.fixtures = {k: list(v) for k, v in meta.get("fixtures", {}).items()}
    idx.ok = True
    return idx


def emit(cov, impact, tests, by_number, id_to_number, all_numbers, file_to_numbers, sources) -> bytes:
    lines = []
    for patch_path in sorted((ROOT / "sample" / "commits").glob("*.patch")):
        patch = patch_path.read_text(encoding="utf-8", errors="replace")
        selection = selector.select(
            patch, cov, impact, id_to_number, all_numbers, file_to_numbers, sources
        )
        if selection.force_all:
            workers = selector.schedule(tests)
        else:
            chosen = [by_number[n] for n in selection.numbers if n in by_number]
            workers = selector.schedule(chosen)
        lines.append(
            json.dumps({"commit": patch_path.stem, "workers": workers}, separators=(",", ":"))
        )
    return ("\n".join(lines) + "\n").encode()


def commit_costs(stdout: bytes, tests, answers) -> dict[str, int]:
    # Reuse scorer arithmetic via a one-commit answers slice is awkward; compute here.
    by = {t["number"]: selector.duration_ms(t["seconds"]) for t in tests}
    costs = {}
    for line in stdout.decode().splitlines():
        obj = json.loads(line)
        workers = [w for w in obj["workers"] if w]
        if not workers:
            costs[obj["commit"]] = 0
            continue
        costs[obj["commit"]] = (
            max(sum(by[n] for n in w) for w in workers) + 2000 * len(workers)
        )
    return costs


def main() -> int:
    tests = json.loads((ROOT / "sample" / "tests.json").read_text(encoding="utf-8"))["tests"]
    tests_rt = [
        {**t, "ms": selector.duration_ms(t["seconds"])} for t in tests
    ]
    by_number = {t["number"]: t for t in tests_rt}
    id_to_number = {t["id"]: t["number"] for t in tests_rt}
    file_to_numbers: dict[str, list[int]] = {}
    for t in tests_rt:
        file_to_numbers.setdefault(t["file"], []).append(t["number"])
    answers = json.loads((ROOT / "sample" / "answers.json").read_text(encoding="utf-8"))[
        "commits"
    ]
    impact = selector.ImpactMaps.load(set(id_to_number))
    cov = load_bin_index()

    base = emit(
        cov, impact, tests_rt, by_number, id_to_number, set(by_number), file_to_numbers, FULL
    )
    evaluation = evaluate_run(base, tests, answers)
    print(
        f"baseline_bin simulated={evaluation['simulated_milliseconds']/1000:.3f} "
        f"misses={evaluation['missed_commits']}"
    )
    base_costs = commit_costs(base, tests, answers)
    for name, sources in DROPS.items():
        out = emit(
            cov, impact, tests_rt, by_number, id_to_number, set(by_number), file_to_numbers, sources
        )
        ev = evaluate_run(out, tests, answers)
        costs = commit_costs(out, tests, answers)
        print(
            f"{name}: simulated={ev['simulated_milliseconds']/1000:.3f} "
            f"misses={ev['missed_commits']}"
        )
        for commit in FOCUS:
            delta = base_costs[commit] - costs[commit]
            print(
                f"  {commit}: base={base_costs[commit]/1000:.3f} "
                f"ablated={costs[commit]/1000:.3f} drop={delta/1000:.3f}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

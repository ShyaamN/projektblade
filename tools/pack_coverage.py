#!/usr/bin/env python3
"""Pack coverage.json.gz into a compact runtime index for selector.py.

Produces maps/covindex.bin + maps/covmeta.json readable by nobody.
Format is designed for fast line→test lookups under CPU budget.
"""

from __future__ import annotations

import gzip
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PINNED_SHA = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"


def main() -> int:
    src = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "maps" / "coverage.json.gz")
    out_dir = Path(sys.argv[2] if len(sys.argv) > 2 else ROOT / "maps")
    with gzip.open(src, "rt", encoding="utf-8") as fh:
        raw = json.load(fh)

    files = raw["files"]
    fixtures = raw.get("fixtures", {})

    # Build sorted file table and flat postings.
    # For each file: list of (line, start, count) into a global uint16 test array.
    file_names = sorted(files)
    postings: list[int] = []
    file_index = {}
    for name in file_names:
        line_entries = []
        for line_s, tests in sorted(files[name].items(), key=lambda kv: int(kv[0])):
            line = int(line_s)
            start = len(postings)
            # tests are already numbers 0..1619
            for t in tests:
                postings.append(int(t))
            line_entries.append((line, start, len(tests)))
        file_index[name] = line_entries

    meta = {
        "schema_version": 1,
        "pinned_sha": raw.get("pinned_sha", PINNED_SHA),
        "matched_tests": raw.get("matched_tests"),
        "files": {
            name: [{"line": line, "start": start, "count": count} for line, start, count in entries]
            for name, entries in file_index.items()
        },
        "fixtures": fixtures,
        "postings_count": len(postings),
    }
    meta_path = out_dir / "covmeta.json"
    bin_path = out_dir / "covpostings.bin"
    meta_path.write_text(json.dumps(meta, separators=(",", ":")), encoding="utf-8")
    with bin_path.open("wb") as fh:
        fh.write(struct.pack(f"<{len(postings)}H", *postings))
    print(
        f"wrote {meta_path} ({meta_path.stat().st_size}) "
        f"{bin_path} ({bin_path.stat().st_size}) files={len(file_names)}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

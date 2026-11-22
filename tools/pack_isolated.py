"""Pack isolated Linux pytest observations into deduplicated test-number bitmaps."""
import argparse
import gzip
import json
import marshal
from pathlib import Path


def records(stream):
    decoder = json.JSONDecoder()
    buffer = ""
    marker = '"profiles":{'
    while marker not in buffer:
        chunk = stream.read(131072)
        if not chunk:
            raise ValueError("Missing profiles")
        buffer += chunk
    header, buffer = buffer.split(marker, 1)
    yield json.loads(header[:-1] + "}")
    while True:
        if buffer.startswith("}"):
            if (buffer + stream.read()).strip() != "}}":
                raise ValueError("Truncated or trailing profile data")
            return
        try:
            name, end = decoder.raw_decode(buffer)
            if buffer[end:end+1] != ":":
                raise ValueError("Missing record")
            record, end = decoder.raw_decode(buffer, end + 1)
            if len(buffer) <= end:
                raise ValueError("Missing record delimiter")
        except ValueError:
            chunk = stream.read(524288)
            if not chunk:
                raise
            buffer += chunk
            continue
        yield name, record
        buffer = buffer[end:]
        if buffer.startswith(","):
            buffer = buffer[1:]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    lines, fixtures = {}, {}
    observed = set()
    children = set()
    with gzip.open(args.profile, "rt", encoding="utf-8") as stream:
        rows = records(stream)
        header = next(rows)
        inventory = header["test_ids"]
        ids = {name: n for n, name in enumerate(inventory)}
        for name, record in rows:
            n = ids[name]
            if n in observed:
                raise ValueError("Duplicate profile")
            observed.add(n)
            if record["subprocess"]:
                children.add(n)
            bit = 1 << n
            for path, hits in record["lines"].items():
                bucket = lines.setdefault(path, {})
                for line in hits:
                    bucket[line] = bucket.get(line, 0) | bit
            for path in record["reads"]:
                if path.startswith("tests/fixtures/"):
                    fixtures[path] = fixtures.get(path, 0) | bit
    missing = set(ids.values()) - observed
    if not observed or header["python"] != [3, 13] or header["platform"] != "x86_64":
        raise ValueError("Incomplete or incompatible trace")
    args.output.write_bytes(gzip.compress(marshal.dumps(
        (header["pinned_sha"], inventory, lines, fixtures, sorted(missing | children)), 4
    ), mtime=0))
    print(f"{len(observed)} observed, {len(missing)} missing, {len(children)} process tests, "
          f"{len(lines)} source files, {len(fixtures)} fixture files, {args.output.stat().st_size} bytes")


if __name__ == "__main__":
    main()

"""ProjektBLADE selector — coverage + static hybrid.

Coverage (pinned-base line hits, function/file expansion) is the primary
narrowing signal. Static envelopes catch thread-executed optimizer paths,
import-time dialect attributes, and serde callers that base coverage misses.
"""

from __future__ import annotations

import ast
import gzip
import json
import math
import re
import struct
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from pinpoint import Index as PinpointIndex
from pinpoint import python_risk
from pinpoint import unpack_lines

DATA = Path("/data")
APP_DIR = Path(__file__).resolve().parent
if not DATA.exists():
    DATA = APP_DIR / "sample"

IMPACT_PATH = APP_DIR / "maps" / "impact.json"
COV_GZ_PATH = APP_DIR / "maps" / "coverage.json.gz"
COV_META_PATH = APP_DIR / "maps" / "covmeta.json"
COV_BIN_PATH = APP_DIR / "maps" / "covpostings.bin"
PINPOINT_PATH = APP_DIR / "maps" / "pinpoint.marshal.gz"
PINNED_SHA = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"

DIFF_FILE_RE = re.compile(r"^diff --git a/(\S+) b/(\S+)\s*$")
HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
SHARED_TEST_HELPERS = {
    "tests/helpers.py",
    "tests/conftest.py",
    "tests/gen_fixtures.py",
}
COMMENT_OR_BLANK = re.compile(r"^\s*(#.*)?$")
CONTROL_FLOW = re.compile(r"\b(if|elif|else|for|while|return|raise|and|or|not|try|except)\b")


def duration_ms(seconds: float) -> int:
    return math.floor(seconds * 1000 + 0.5)


def schedule(tests: list[dict], max_workers: int = 8) -> list[list[int]]:
    if not tests:
        return []
    # Tie-break on test number so Linux and Windows emit the same workers.
    items = sorted(tests, key=lambda t: (-t["ms"], t["number"]))
    best_workers = None
    best_cost = None
    for worker_count in range(1, min(max_workers, len(items)) + 1):
        workers: list[list[int]] = [[] for _ in range(worker_count)]
        loads = [0] * worker_count
        for test in items:
            i = loads.index(min(loads))
            workers[i].append(test["number"])
            loads[i] += test["ms"]
        cost = max(loads) + 2000 * worker_count
        if best_cost is None or cost < best_cost:
            best_cost = cost
            best_workers = workers
    assert best_workers is not None
    return [w for w in best_workers if w]


def retain_within_slack(workers: list[list[int]], candidates: set[int], tests: dict[int, dict]) -> None:
    """Keep more originally plausible tests without changing worker cost."""
    if len(workers) < 2 or not candidates:
        return
    scheduled = {n for worker in workers for n in worker}
    loads = [sum(tests[n]["ms"] for n in worker) for worker in workers]
    limit = max(loads)
    for n in sorted(candidates - scheduled, key=lambda n: (tests[n]["ms"], n)):
        worker = loads.index(min(loads))
        duration = tests[n]["ms"]
        if loads[worker] + duration > limit:
            break
        workers[worker].append(n)
        loads[worker] += duration


@dataclass
class FileChange:
    old_path: str | None
    new_path: str | None
    old_lines: set[int] = field(default_factory=set)
    context_lines: set[int] = field(default_factory=set)
    added_bodies: list[str] = field(default_factory=list)
    removed_bodies: list[str] = field(default_factory=list)
    structural: bool = False


def parse_unified_diff(patch: str) -> list[FileChange] | None:
    changes: list[FileChange] = []
    current: FileChange | None = None
    old_line = 0
    new_line = 0
    for line in patch.splitlines():
        m = DIFF_FILE_RE.match(line)
        if m:
            old_p, new_p = m.group(1), m.group(2)
            if ".." in old_p or ".." in new_p:
                return None
            current = FileChange(old_path=old_p, new_path=new_p)
            changes.append(current)
            continue
        if line.startswith("diff --git "):
            return None
        if current is None:
            continue
        if line.startswith("--- "):
            rest = line[4:].split("\t", 1)[0].strip()
            if rest.startswith("a/"):
                current.old_path = rest[2:]
            elif rest == "/dev/null":
                current.old_path = None
            continue
        if line.startswith("+++ "):
            rest = line[4:].split("\t", 1)[0].strip()
            if rest.startswith("b/"):
                current.new_path = rest[2:]
            elif rest == "/dev/null":
                current.new_path = None
            continue
        hm = HUNK_RE.match(line)
        if hm:
            old_line = int(hm.group(1))
            new_line = int(hm.group(3))
            continue
        if line.startswith("\\"):
            continue
        if line.startswith("+"):
            if line.startswith("+++"):
                continue
            body = line[1:]
            current.added_bodies.append(body)
            if body.lstrip().startswith(("import ", "from ", "class ", "def ", "@")):
                current.structural = True
            new_line += 1
        elif line.startswith("-"):
            if line.startswith("---"):
                continue
            body = line[1:]
            current.removed_bodies.append(body)
            current.old_lines.add(old_line)
            if body.lstrip().startswith(("import ", "from ", "class ", "def ", "@")):
                current.structural = True
            old_line += 1
        elif line.startswith(" "):
            current.context_lines.add(old_line)
            old_line += 1
            new_line += 1
    return changes


def unresolved_path(
    path: str,
    cov: CoverageIndex,
    impact: ImpactMaps,
    file_to_numbers: dict[str, list[int]],
) -> str | None:
    """Reason to run the full suite, or None when this path is a known input."""
    if path in SHARED_TEST_HELPERS or path.endswith("/conftest.py"):
        return f"shared_test_infra:{path}"
    if path.startswith("tests/fixtures/") and path.endswith(".sql"):
        rel = path[len("tests/fixtures/") :]
        stem = rel[: -len(".sql")]
        base = Path(stem).name
        known = (
            cov.fixtures.get(rel)
            or cov.fixtures.get(stem)
            or cov.fixtures.get(base)
            or impact.fixture_to_ids.get(stem)
            or impact.fixture_to_ids.get(base)
        )
        if not known:
            return f"unknown_fixture:{path}"
        return None
    if path.startswith("tests/") and path.endswith(".py"):
        if path not in file_to_numbers:
            return f"unknown_test_file:{path}"
        return None
    if not path.startswith("sqlglot/"):
        return f"unknown_path:{path}"
    if path.endswith(".py") and impact.path_to_module.get(path) is None:
        return f"unknown_sqlglot_module:{path}"
    return None


def is_nonexecutable_edit(change: FileChange) -> bool:
    """True when every +/- body is blank or # comment (coverage-empty safe skip)."""
    bodies = change.added_bodies + change.removed_bodies
    if not bodies:
        return False
    return all(COMMENT_OR_BLANK.match(b) is not None for b in bodies)


class PackedCoverageFiles(Mapping):
    def __init__(self, files):
        self.files = files
        self.cache = {}

    def __getitem__(self, path):
        if path not in self.cache:
            self.cache[path] = unpack_lines(self.files[path])
        return self.cache[path]

    def __iter__(self):
        return iter(self.files)

    def __len__(self):
        return len(self.files)

    def __contains__(self, path):
        return path in self.files


class CoverageIndex:
    def __init__(self) -> None:
        self.ok = False
        self.error = ""
        self.file_lines: dict[str, dict[int, list[int]]] = {}
        self.fixtures: dict[str, list[int]] = {}

    format_name: str = ""
    file_count: int = 0
    test_ids: list[str] = []
    pinned_sha: str = ""

    @classmethod
    def load(cls) -> CoverageIndex:
        """Load the single shipped map: maps/coverage.json.gz."""
        idx = cls()
        idx.format_name = "coverage.json.gz"
        if not COV_GZ_PATH.exists():
            idx.error = "missing_coverage_json_gz"
            return idx
        try:
            with gzip.open(COV_GZ_PATH, "rt", encoding="utf-8") as fh:
                raw = json.load(fh)
        except Exception as exc:  # noqa: BLE001
            idx.error = f"coverage_unreadable:{exc}"
            return idx
        return cls.from_data(raw)

    @classmethod
    def from_data(cls, raw: dict, *, integer_lines: bool = False) -> CoverageIndex:
        idx = cls()
        idx.pinned_sha = str(raw.get("pinned_sha", ""))
        idx.test_ids = list(raw.get("test_ids") or [])
        idx.format_name = str(raw.get("format") or "coverage.json.gz")
        if idx.pinned_sha != PINNED_SHA:
            idx.error = "coverage_sha_mismatch"
            return idx
        if raw.get("packed_files"):
            idx.file_lines = PackedCoverageFiles(raw["files"])
        elif integer_lines:
            idx.file_lines = raw.get("files", {})
        else:
            idx.file_lines = {
                name: {int(line): nums for line, nums in lines.items()}
                for name, lines in raw.get("files", {}).items()
            }
        idx.fixtures = raw.get("fixtures", {})
        idx.file_count = len(idx.file_lines)
        if not idx.file_lines:
            idx.error = "coverage_empty"
            return idx
        if not idx.test_ids:
            idx.error = "coverage_missing_test_ids"
            return idx
        idx.ok = True
        return idx

    def tests_for_lines(
        self, path: str, lines: set[int], neighbors: bool = True
    ) -> set[int]:
        bucket = self.file_lines.get(path)
        if not bucket or not lines:
            return set()
        out: set[int] = set()
        for line in lines:
            cands = (line - 1, line, line + 1) if neighbors else (line,)
            for cand in cands:
                nums = bucket.get(cand)
                if nums:
                    out.update(nums)
        return out

    def enclosing_functions(
        self, path: str, lines: set[int], ranges: list[dict]
    ) -> list[tuple[int, int]]:
        touched: list[tuple[int, int]] = []
        for rng in ranges:
            if rng.get("kind") == "ClassDef":
                continue
            start, end = int(rng["start"]), int(rng["end"])
            if any(start <= ln <= end for ln in lines):
                touched.append((start, end))
        touched.sort(key=lambda se: se[1] - se[0])
        return touched

    def tests_for_functions(
        self, path: str, lines: set[int], ranges: list[dict]
    ) -> set[int]:
        bucket = self.file_lines.get(path)
        if not bucket:
            return set()
        out: set[int] = set()
        touched = self.enclosing_functions(path, lines, ranges)
        if not touched:
            return out
        start, end = touched[0]
        for ln in range(start, end + 1):
            nums = bucket.get(ln)
            if nums:
                out.update(nums)
        return out

    def tests_for_file(self, path: str) -> set[int]:
        bucket = self.file_lines.get(path)
        if not bucket:
            return set()
        out: set[int] = set()
        for nums in bucket.values():
            out.update(nums)
        return out


@dataclass
class ImpactMaps:
    map_ok: bool
    map_error: str
    file_to_ids: dict[str, list[str]]
    fixture_to_ids: dict[str, list[str]]
    dialect_names: set[str]
    path_to_module: dict[str, str]
    module_to_test_files: dict[str, list[str]]
    function_ranges: dict[str, list[dict]]
    serde_test_files: list[str]
    pool_routes: dict

    @classmethod
    def load(cls, runtime_ids: set[str]) -> ImpactMaps:
        if not IMPACT_PATH.exists():
            return cls(False, "missing impact", {}, {}, set(), {}, {}, {}, [], {})
        raw = json.loads(IMPACT_PATH.read_text(encoding="utf-8"))
        test_ids = set(raw.get("test_ids", []))
        map_ok, map_error = True, ""
        if int(raw.get("schema_version", 0)) < 2:
            map_ok, map_error = False, "impact_schema"
        elif raw.get("pinned_sha") not in ("", PINNED_SHA):
            map_ok, map_error = False, "impact_sha_mismatch"
        elif set(raw.get("test_ids") or []) != runtime_ids:
            map_ok, map_error = False, "impact_id_set_mismatch"
        modules = raw.get("modules", {})
        return cls(
            map_ok=map_ok,
            map_error=map_error,
            file_to_ids=raw.get("file_to_ids", {}),
            fixture_to_ids=raw.get("fixture_to_ids", {}),
            dialect_names=set(raw.get("dialect_names", [])),
            path_to_module={v: k for k, v in modules.items()},
            module_to_test_files=raw.get("module_to_test_files", {}),
            function_ranges=raw.get("function_ranges", {}),
            serde_test_files=list(raw.get("serde_test_files", [])),
            pool_routes=raw.get("pool_routes") or {},
        )


def _nested_tooling_class(ranges: list[dict], lines: set[int]) -> bool:
    """True when changed lines sit inside Tokenizer/Parser/Generator class body."""
    names = {"Tokenizer", "Parser", "Generator"}
    for rng in ranges:
        if rng.get("kind") != "ClassDef" or rng.get("name") not in names:
            continue
        start, end = int(rng["start"]), int(rng["end"])
        if any(start <= ln <= end for ln in lines):
            return True
    return False


def dialect_from_path(path: str) -> str | None:
    parts = path.split("/")
    if len(parts) >= 3 and parts[0] == "sqlglot" and parts[1] in {
        "dialects",
        "parsers",
        "generators",
        "typing",
    }:
        name = Path(parts[-1]).stem
        if name not in {"__init__", "dialect", "parser", "generator"}:
            return name
    return None


@dataclass
class Selection:
    numbers: set[int] = field(default_factory=set)
    force_all: bool = False
    reasons: list[str] = field(default_factory=list)
    slack_candidates: set[int] = field(default_factory=set)


def select(
    patch: str,
    cov: CoverageIndex,
    impact: ImpactMaps,
    id_to_number: dict[str, int],
    all_numbers: set[int],
    file_to_numbers: dict[str, list[int]],
    sources: set[str] | None = None,
    pinpoint: PinpointIndex | None = None,
) -> Selection:
    # Ablation toggles. Production keeps every source.
    use = sources or {"old", "context", "func", "static", "neighbors", "file"}
    result = Selection()
    changes = parse_unified_diff(patch)
    if changes is None or not changes:
        result.force_all = True
        result.reasons.append("diff_parse_failed")
        result.numbers = set(all_numbers)
        return result

    validated: dict[str, str] = {}
    import_changes: set[str] = set()
    if pinpoint is not None:
        # Reconstruct and compile before any empty or narrowed selection.
        reason = python_risk(patch, changes, pinpoint, validated, import_changes)
        if reason:
            result.force_all = True
            result.reasons.append(reason)
            result.numbers = set(all_numbers)
            return result
        if import_changes:
            for path in sorted(import_changes):
                for test_file in pinpoint.import_test_files[path]:
                    result.numbers.update(file_to_numbers.get(test_file, ()))
                result.reasons.append(f"python_import_operation:{path}")
            # For shared imports, retain all tests rather than rely on small
            # gaps in the observed files or environment-dependent imports.
            if len(result.numbers) * 100 >= len(all_numbers) * 95:
                result.force_all = True
                result.numbers = set(all_numbers)
                return result
            changes = [change for change in changes if change.old_path not in import_changes]
            if not changes:
                return result

    identity = [change for change in changes if _none_identity_edit(change)] if len(changes) == 1 else []
    if identity:
        result.reasons.append(f"none_identity:{len(identity)}")
        if len(identity) == len(changes):
            return result
        changes = [change for change in changes if change not in identity]

    if pinpoint is not None:
        broad = []
        allow_equivalence = len(changes) == 1
        for change in changes:
            if change.old_path == "sqlglot/serde.py" or (
                change.old_path == change.new_path
                and change.old_path
                and not change.old_path.startswith(("sqlglot/optimizer/", "sqlglot/typing/"))
                and is_nonexecutable_edit(change)
                and _pool_route_ids([change], impact) == set()
            ):
                broad.append(change)
                continue
            hit = pinpoint.narrow(patch, change, allow_equivalence=allow_equivalence, validated=validated)
            if hit is None:
                broad.append(change)
                continue
            result.numbers.update(hit)
            result.reasons.append(f"verified_source:{change.old_path}:{len(hit)}")
        if len(broad) != len(changes):
            # The child collector explicitly records exceptions. Preserve any
            # such tests that the original envelope would have kept.
            original = select(patch, cov, impact, id_to_number, all_numbers, file_to_numbers, sources)
            result.numbers.update(original.numbers & pinpoint.preserve)
            if not original.force_all:
                result.slack_candidates.update(original.numbers)
        changes = broad
        if not changes:
            return result

    selected_files: set[str] = set()

    def add_files(files: set[str], reason: str) -> None:
        if "static" not in use:
            result.reasons.append(f"skip_{reason}")
            return
        selected_files.update(files)
        result.reasons.append(reason)

    for change in changes:
        paths = {p for p in (change.old_path, change.new_path) if p}
        if not paths:
            result.force_all = True
            result.reasons.append("empty_paths")
            break

        # Unknown paths and fixtures force the full suite before a comment-only
        # hunk is allowed to skip line coverage.
        for path in sorted(paths):
            reason = unresolved_path(path, cov, impact, file_to_numbers)
            if reason:
                result.force_all = True
                result.reasons.append(reason)
                break
        if result.force_all:
            break

        # Non-executable comment/blank-only edit: skip coverage selection, but
        # still apply static envelopes for optimizer/typing hubs.
        if is_nonexecutable_edit(change) and all(
            path.startswith("sqlglot/") and path.endswith(".py") for path in paths
        ):
            result.reasons.append(f"nonexec:{next(iter(paths))}")
            for path in paths:
                if path.startswith("sqlglot/optimizer/"):
                    add_files(
                        {
                            "tests/test_optimizer.py",
                            "tests/test_executor.py",
                            "tests/test_optimize_cross_joins.py",
                        },
                        "static:optimizer_nonexec",
                    )
                elif path.startswith("sqlglot/typing/"):
                    add_files({"tests/test_optimizer.py"}, "static:typing_nonexec")
                    d = dialect_from_path(path)
                    if d:
                        selected_files.add(f"tests/dialects/test_{d}.py")
            continue

        for path in sorted(paths):
            if path in SHARED_TEST_HELPERS or path.endswith("/conftest.py"):
                result.force_all = True
                result.reasons.append(f"shared_test_infra:{path}")
                break

            if path.startswith("tests/fixtures/") and path.endswith(".sql"):
                rel = path[len("tests/fixtures/") :]
                stem = rel[: -len(".sql")]
                base = Path(stem).name
                nums = (
                    cov.fixtures.get(rel)
                    or cov.fixtures.get(stem)
                    or cov.fixtures.get(base)
                )
                if nums:
                    result.numbers.update(nums)
                    result.reasons.append(f"cov_fixture:{base}")
                    continue
                ids = impact.fixture_to_ids.get(stem) or impact.fixture_to_ids.get(base)
                if ids:
                    for i in ids:
                        n = id_to_number.get(i)
                        if n is not None:
                            result.numbers.add(n)
                    result.reasons.append(f"static_fixture:{base}")
                    continue
                result.force_all = True
                result.reasons.append(f"unknown_fixture:{path}")
                break

            if path.startswith("tests/") and path.endswith(".py"):
                nums = file_to_numbers.get(path)
                if nums:
                    result.numbers.update(nums)
                    result.reasons.append(f"test_file:{path}")
                else:
                    result.force_all = True
                    result.reasons.append(f"unknown_test_file:{path}")
                continue

            if not path.startswith("sqlglot/"):
                result.force_all = True
                result.reasons.append(f"unknown_path:{path}")
                break

            ranges = impact.function_ranges.get(path, [])
            # Ordinary in-function replacements seed from changed old lines only.
            # Unchanged diff context is not a coverage seed. Pure insertions use
            # the enclosing function at the insertion point. Function-span
            # coverage is a fallback for empty hits and control-flow edits.
            # Expand to the enclosing function only when a control-flow word
            # was added or removed. A word that stays on the line (an `if`
            # whose condition changed) is an ordinary line seed.
            removed_kw = {
                kw for b in change.removed_bodies for kw in CONTROL_FLOW.findall(b)
            }
            added_kw = {
                kw for b in change.added_bodies for kw in CONTROL_FLOW.findall(b)
            }
            control_flow = removed_kw != added_kw
            pure_insert = not change.old_lines and any(
                COMMENT_OR_BLANK.match(b) is None for b in change.added_bodies
            )
            in_function = bool(
                cov.enclosing_functions(path, set(change.old_lines), ranges)
            ) if change.old_lines else False
            ordinary_replace = bool(change.old_lines) and in_function and not control_flow

            if ordinary_replace and "old" in use:
                anchor = set(change.old_lines)
                use_neighbors = False
                use_func_now = "func" in use and False  # fallback only if hit empty
            elif pure_insert and "context" in use:
                anchor = set(change.context_lines)
                use_neighbors = False
                use_func_now = "func" in use
            else:
                anchor = set()
                if "old" in use:
                    anchor |= change.old_lines
                if "context" in use:
                    anchor |= change.context_lines
                use_neighbors = "neighbors" in use
                use_func_now = "func" in use

            enclosing = cov.enclosing_functions(path, anchor, ranges) if cov.ok else []
            class_body = not enclosing
            hit: set[int] = set()
            nested_tooling = _nested_tooling_class(ranges, anchor or change.old_lines or change.context_lines)
            if cov.ok and path in cov.file_lines and anchor:
                hit |= cov.tests_for_lines(path, anchor, neighbors=use_neighbors)
                if use_func_now:
                    hit |= cov.tests_for_functions(path, anchor, ranges)
                if ordinary_replace and not hit and "func" in use:
                    hit |= cov.tests_for_functions(path, anchor, ranges)
                    result.reasons.append(f"func_fallback:{path}")
                # Whole-file coverage for class and module body added tests
                # that caught no sample failure and cost 7.252s. Changed lines
                # and enclosing functions still seed. Nested tooling classes
                # stay on their own line hits.
                result.numbers.update(hit)
                result.reasons.append(
                    f"cov:{path}:{len(hit)}:{'class' if class_body else 'func'}"
                )

            dialect = dialect_from_path(path)
            kind = path.split("/")[1] if "/" in path else ""

            if path == "sqlglot/serde.py" or path.startswith("sqlglot/serde"):
                for tf in impact.serde_test_files:
                    selected_files.add(tf)
                result.reasons.append("static:serde_callers")
            elif path.startswith("sqlglot/optimizer/"):
                # Prefer coverage hits for optimizer methods; always include the
                # optimizer suite (thread gap) but skip executor unless sparse.
                files = {"tests/test_optimizer.py", "tests/test_optimize_cross_joins.py"}
                if len(hit) < 30:
                    files.add("tests/test_executor.py")
                add_files(files, "static:optimizer")
            elif path.startswith("sqlglot/executor/"):
                files = {"tests/test_executor.py"}
                if len(hit) < 30:
                    files.add("tests/test_optimizer.py")
                add_files(files, "static:executor")
            elif path.startswith("sqlglot/typing/"):
                add_files({"tests/test_optimizer.py"}, "static:typing")
                if dialect:
                    selected_files.add(f"tests/dialects/test_{dialect}.py")
            elif class_body and dialect and dialect in impact.dialect_names:
                files = {f"tests/dialects/test_{dialect}.py"}
                # Nested Tokenizer/Parser/Generator class attrs rarely need the
                # full optimizer suite; top-level Dialect attrs often do.
                if nested_tooling:
                    pass  # dialect suite only
                elif kind == "dialects" or len(hit) < 15:
                    files.add("tests/test_optimizer.py")
                add_files(files, f"static:class_body:{dialect}")
            elif class_body and path in {
                "sqlglot/dialects/dialect.py",
                "sqlglot/dialects/__init__.py",
            }:
                add_files(
                    {
                        "tests/dialects/test_dialect.py",
                        "tests/test_optimizer.py",
                    },
                    "static:base_dialect_class",
                )
            elif not hit:
                if dialect and dialect in impact.dialect_names:
                    files = {f"tests/dialects/test_{dialect}.py"}
                    if kind in {"parsers", "dialects", "typing"}:
                        files.add("tests/test_optimizer.py")
                    if kind == "parsers":
                        files.update(
                            {"tests/test_parser.py", "tests/test_expressions.py"}
                        )
                    elif kind == "generators":
                        files.update(
                            {"tests/test_generator.py", "tests/test_transpile.py"}
                        )
                    add_files(files, f"static:dialect:{dialect}")
                elif path.startswith("sqlglot/expressions/"):
                    add_files(
                        {
                            "tests/test_expressions.py",
                            "tests/test_parser.py",
                            "tests/test_optimizer.py",
                            "tests/test_generator.py",
                        },
                        "static:expressions",
                    )
                elif path in {"sqlglot/generator.py", "sqlglot/transforms.py"}:
                    add_files(
                        {
                            "tests/test_generator.py",
                            "tests/test_transpile.py",
                            "tests/test_optimizer.py",
                            "tests/dialects/test_dialect.py",
                        },
                        "static:generator_core",
                    )
                elif path.endswith(".py") and impact.path_to_module.get(path) is None:
                    result.force_all = True
                    result.reasons.append(f"unknown_sqlglot_module:{path}")
                    break
                elif pinpoint is not None and "static" in use:
                    exposed = (pinpoint.import_test_files or {}).get(path)
                    if not exposed:
                        result.force_all = True
                        result.reasons.append(f"unmapped_executable_edit:{path}")
                        break
                    add_files(set(exposed), f"static:import_exposure:{path}")
            elif dialect and kind == "parsers":
                # Parser method edits: dialect suite + optimizer (not full
                # parser/expressions files — those dominate cost).
                add_files(
                    {
                        f"tests/dialects/test_{dialect}.py",
                        "tests/test_optimizer.py",
                    },
                    f"static:parser:{dialect}",
                )

        if result.force_all:
            break

    if result.force_all:
        result.numbers = set(all_numbers)
        return result

    routed = _pool_route_ids(changes, impact)
    if routed is not None:
        seeds = set(result.numbers)
        for test_id in _suppressed_executor_ids(patch, changes, impact):
            number = id_to_number.get(test_id)
            if number is not None and number not in seeds:
                routed.discard(test_id)
    # The recursive reference selection still models the original pool
    # envelope. Actual decisions can fall back when all refined seeds are empty.
    if routed is None or (pinpoint is not None and not routed and not result.numbers):
        for path in selected_files:
            nums = file_to_numbers.get(path)
            if nums:
                result.numbers.update(nums)
        if routed is None:
            return result
    else:
        # Preserve other static evidence where it fits the existing schedule.
        for path in selected_files:
            result.slack_candidates.update(file_to_numbers.get(path, ()))

    missing = [test_id for test_id in routed if test_id not in id_to_number]
    if missing:
        result.force_all = True
        result.reasons.append(f"pool_route_id_mismatch:{len(missing)}")
        result.numbers = set(all_numbers)
        return result
    result.numbers.update(id_to_number[test_id] for test_id in routed)
    result.reasons.append(f"pool_routes:{len(routed)}")
    return result


# Pinned assignments of Context._table are None or a Table, and Table does not
# define __eq__, so identity and equality with None have the same result.
_NONE_IDENTITY = {("sqlglot/executor/context.py", "self._table")}
_NONE_COMPARE = re.compile(r"^(?P<left>.*?) (?P<op>is not|is|==|!=) None\b(?P<right>.*)$")


def _canonicalize_none_compare(body: str) -> str | None:
    match = _NONE_COMPARE.match(body.strip())
    if match is None:
        return None
    op = "==" if match.group("op") in {"is", "=="} else "!="
    return f"{match.group('left')} {op} None{match.group('right')}"


def _none_identity_edit(change: FileChange) -> bool:
    """True when the edit cannot change a None test for a vetted operand."""
    path = change.old_path
    if (
        path != change.new_path
        or len(change.removed_bodies) != 1
        or len(change.added_bodies) != 1
        or change.removed_bodies[0] == change.added_bodies[0]
    ):
        return False
    removed = _canonicalize_none_compare(change.removed_bodies[0])
    added = _canonicalize_none_compare(change.added_bodies[0])
    if removed is None or removed != added:
        return False
    match = _NONE_COMPARE.match(change.removed_bodies[0].strip())
    if match is None:
        return False
    left = match.group("left").rstrip()
    return any(
        left == operand or left.endswith(operand) and not left[: -len(operand)].endswith(("_",))
        and (len(left) == len(operand) or not left[-len(operand) - 1].isalnum())
        for allowed, operand in _NONE_IDENTITY
        if allowed == path
    )


_PLAIN_COMMENT = re.compile(r"#[ ]+[A-Za-z][A-Za-z ,.-]*\Z")
_DIRECTIVE = re.compile(r"(?i)noqa|type|coding|pragma|fmt|lint|pylint|ruff|pyright|mypy")


def _neutral_import_comment_paths(patch: str, changes: list[FileChange]) -> set[str]:
    """Plain comments inserted between imports. The parsed tree would not change."""
    candidates = {
        change.old_path
        for change in changes
        if change.old_path
        and change.old_path == change.new_path
        and change.old_path.startswith("sqlglot/")
        and change.old_path.endswith(".py")
        and not change.removed_bodies
        and change.added_bodies
    }
    accepted: set[str] = set()
    rejected: set[str] = set()
    path: str | None = None
    hunk: list[str] = []

    def finish_hunk() -> None:
        if path not in candidates or not hunk:
            return
        for index, line in enumerate(hunk):
            if not line.startswith("+"):
                continue
            body = line[1:]
            previous = next(
                (hunk[pos][1:] for pos in range(index - 1, -1, -1) if hunk[pos].startswith(" ")),
                "",
            )
            nxt = next(
                (hunk[pos][1:] for pos in range(index + 1, len(hunk)) if hunk[pos].startswith(" ")),
                "",
            )
            plain = (
                previous.startswith(("import ", "from "))
                and nxt.startswith(("import ", "from "))
                and _PLAIN_COMMENT.fullmatch(body) is not None
                and _DIRECTIVE.search(body) is None
            )
            if plain:
                accepted.add(path)
            else:
                rejected.add(path)

    for line in patch.splitlines():
        header = DIFF_FILE_RE.match(line)
        if header:
            finish_hunk()
            path = header.group(1)
            hunk = []
            continue
        if HUNK_RE.match(line):
            finish_hunk()
            hunk = []
            continue
        if line.startswith(("+++", "---")):
            continue
        if path in candidates and line.startswith(("+", "-", " ")):
            hunk.append(line)
    finish_hunk()
    return {item for item in accepted if item not in rejected}


def _in_function(impact: ImpactMaps, path: str, line: int) -> bool:
    return any(
        row.get("kind") == "FunctionDef" and row["start"] < line <= row["end"]
        for row in impact.function_ranges.get(path, ())
    )


def _optimizer_body_edits(changes: list[FileChange], impact: ImpactMaps) -> bool:
    python_changes = [change for change in changes if (change.old_path or "").endswith(".py")]
    if not python_changes:
        return False
    for change in python_changes:
        path = change.old_path
        if (
            path != change.new_path
            or not path
            or not path.startswith(("sqlglot/optimizer/", "sqlglot/executor/"))
            or not change.old_lines
            or not change.added_bodies
            or change.structural
        ):
            return False
        if not all(_in_function(impact, path, line) for line in change.old_lines):
            return False
    return True


def _isolated_bool_target(changes: list[FileChange], impact: ImpactMaps) -> str | None:
    """One boolean literal inside an optimizer assignment, as path:function."""
    if len(changes) != 1:
        return None
    change = changes[0]
    path = change.old_path
    if not (
        path
        and path == change.new_path
        and path.startswith("sqlglot/optimizer/")
        and len(change.old_lines) == len(change.added_bodies) == len(change.removed_bodies) == 1
        and not change.structural
    ):
        return None
    removed, added = change.removed_bodies[0], change.added_bodies[0]
    if len(removed) - len(removed.lstrip()) != len(added) - len(added.lstrip()):
        return None
    try:
        before = ast.parse(removed.strip()).body[0]
        after = ast.parse(added.strip()).body[0]
    except (SyntaxError, IndexError):
        return None
    if not isinstance(before, ast.Assign) or not isinstance(after, ast.Assign):
        return None
    for statement in (before, after):
        for node in ast.walk(statement):
            if isinstance(node, ast.Constant) and type(node.value) is bool:
                node.value = False
    if ast.dump(before) != ast.dump(after):
        return None
    body_line = next(iter(change.old_lines))
    scopes = [
        row
        for row in impact.function_ranges.get(path, ())
        if row.get("kind") == "FunctionDef" and row["start"] < body_line <= row["end"]
    ]
    if not scopes:
        return None
    name = min(scopes, key=lambda row: row["end"] - row["start"])["name"]
    return f"{path}:{name}"


def _suppressed_executor_ids(
    patch: str, changes: list[FileChange], impact: ImpactMaps
) -> set[str]:
    """Executor pool ids the pinned fixtures never exercise for this diff.

    Line seeds and serde routes are left in place by the caller. Missing
    activity data suppresses nothing.
    """
    data = impact.pool_routes or {}
    executor_ids = set(data.get("executor_ids") or [])
    activity = data.get("executor_activity") or {}
    calls = data.get("executor_calls") or {}
    if not executor_ids or not activity:
        return set()
    deps = set(data.get("executor_deps") or [])
    eligible = any(
        path
        and path.startswith(("sqlglot/optimizer/", "sqlglot/executor/"))
        and impact.path_to_module.get(path) in deps
        for change in changes
        for path in (change.old_path, change.new_path)
    )
    neutral = _neutral_import_comment_paths(patch, changes)
    remaining = [change for change in changes if change.old_path not in neutral]
    drop: set[str] = set()
    if neutral and eligible:
        kept = _pool_route_ids(remaining, impact) or set()
        drop |= executor_ids - kept
    if eligible and _optimizer_body_edits(remaining, impact):
        drop |= {test_id for test_id, active in activity.items() if active == 0}
    target = _isolated_bool_target(changes, impact)
    if target and eligible and calls:
        drop |= {test_id for test_id, names in calls.items() if target not in names}
    return drop & executor_ids


def _pool_route_ids(changes: list[FileChange], impact: ImpactMaps) -> set[str] | None:
    """Pinned-base tests that send work to spawned pools. None if the map lacks routes."""
    data = impact.pool_routes
    routes = data.get("routes") if data else None
    if not routes:
        return None
    executor_ids = set(data.get("executor_ids") or [])
    executor_deps = set(data.get("executor_deps") or [])
    picked: set[str] = set()
    for change in changes:
        for path in {p for p in (change.old_path, change.new_path) if p}:
            if not path.startswith("sqlglot/"):
                continue
            module = impact.path_to_module.get(path)
            if path == "sqlglot/serde.py" or path.startswith("sqlglot/serde"):
                picked.update(route["id"] for route in routes)
                picked.update(executor_ids)
                continue
            if path.startswith(("sqlglot/optimizer/", "sqlglot/executor/")):
                if module:
                    picked.update(
                        route["id"] for route in routes if module in route.get("deps", [])
                    )
                if (module and module in executor_deps) or path.startswith("sqlglot/executor/"):
                    picked.update(executor_ids)
                continue
            dialect = dialect_from_path(path)
            if dialect:
                picked.update(
                    route["id"]
                    for route in routes
                    if dialect in route.get("dialects", [])
                )
    return picked


def main() -> None:
    tests_raw = json.loads((DATA / "tests.json").read_text(encoding="utf-8"))["tests"]
    tests = [
        {
            "number": t["number"],
            "file": t["file"],
            "id": t["id"],
            "seconds": t["seconds"],
            "ms": duration_ms(t["seconds"]),
        }
        for t in tests_raw
    ]
    by_number = {t["number"]: t for t in tests}
    id_to_number = {t["id"]: t["number"] for t in tests}
    all_numbers = set(by_number)
    file_to_numbers: dict[str, list[int]] = {}
    for t in tests:
        file_to_numbers.setdefault(t["file"], []).append(t["number"])

    runtime_ids = [t["id"] for t in tests_raw]
    impact = ImpactMaps.load(set(id_to_number))
    pinpoint = PinpointIndex.load(PINPOINT_PATH, id_to_number, PINNED_SHA)
    cov = (
        CoverageIndex.from_data(pinpoint.primary, integer_lines=True)
        if pinpoint is not None and pinpoint.primary is not None
        else CoverageIndex.load()
    )
    fallback = cov.error or impact.map_error or "none"
    if cov.ok and cov.test_ids != runtime_ids:
        cov.ok = False
        fallback = "coverage_test_id_order_mismatch"
    # Impact map references use pytest IDs, while the coverage artifacts above
    # already enforce ordered IDs for their integer postings.
    if not cov.ok or not impact.map_ok:
        # Full suite on every commit. select() still runs, but main overrides.
        fallback = fallback if fallback != "none" else "map_invalid"
    print(
        f"map format={cov.format_name} schema=coverage.json.gz "
        f"files={cov.file_count} sha={cov.pinned_sha or PINNED_SHA} "
        f"fallback={fallback}",
        file=sys.stderr,
    )
    full_schedule = schedule(tests)
    commits = sorted((DATA / "commits").glob("*.patch"))
    total = 0
    for patch_path in commits:
        patch = patch_path.read_text(encoding="utf-8", errors="replace")
        if not cov.ok or not impact.map_ok:
            selection = Selection(numbers=set(all_numbers), force_all=True, reasons=[fallback])
        else:
            selection = select(
                patch, cov, impact, id_to_number, all_numbers, file_to_numbers, pinpoint=pinpoint
            )
        if selection.force_all:
            workers = full_schedule
            n = len(tests)
        else:
            selected = [by_number[n] for n in sorted(selection.numbers) if n in by_number]
            workers = schedule(selected)
            retain_within_slack(workers, selection.slack_candidates, by_number)
            n = sum(map(len, workers))
        print(
            json.dumps(
                {"commit": patch_path.stem, "workers": workers},
                separators=(",", ":"),
            )
        )
        total += n
        print(
            f"{patch_path.stem}: selected={n} force_all={selection.force_all} "
            f"reasons={','.join(selection.reasons[:5]) or 'none'}",
            file=sys.stderr,
        )
    print(f"selected {total} test-slots across {len(commits)} commits", file=sys.stderr)


if __name__ == "__main__":
    main()

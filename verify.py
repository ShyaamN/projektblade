"""Verify small test selections in a bounded child on the pinned Linux tree."""

from __future__ import annotations

import builtins
import importlib
import importlib.machinery
import io
import os
from pathlib import Path
import re
import select
import signal
import sys
import time

from pinpoint import _patched_source


_HEADER = re.compile(r"diff --git a/([\w./-]+) b/([\w./-]+)")
_INDEX = re.compile(r"index [0-9a-f]+\.\.[0-9a-f]+(?: 100644)?")
_HUNK = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@.*")
_MAX_PROBE_CPU = 0.60
_TOTAL_PROBE_CPU = 3.0
_MAX_PROBE_WALL = 2.0
_TOTAL_PROBE_WALL = 12.0


def _overlays(root: Path, patch: str) -> dict[str, bytes] | None:
    """Accept complete, same-path text hunks; leave other patches to selection."""
    if len(patch) > 1_000_000 or "\ufffd" in patch or not patch.endswith("\n"):
        return None
    lines = iter(patch[:-1].split("\n"))
    overlays = {}
    line = next(lines, None)
    while line is not None:
        header = _HEADER.fullmatch(line)
        if not header or header[1] != header[2]:
            return None
        path = header[1]
        if any(part in {"", ".", ".."} for part in path.split("/")):
            return None
        if not (
            path.startswith("sqlglot/") and path.endswith(".py")
            or path.startswith("tests/fixtures/") and path.endswith((".sql", ".json"))
        ):
            return None
        old_header = next(lines, "")
        if _INDEX.fullmatch(old_header):
            old_header = next(lines, "")
        if old_header != f"--- a/{path}" or next(lines, None) != f"+++ b/{path}":
            return None

        line = next(lines, None)
        hunks = 0
        shift = 0
        while line is not None and (hunk := _HUNK.fullmatch(line)):
            old_count, new_count = int(hunk[2] or "1"), int(hunk[4] or "1")
            old_start = int(hunk[1]) - bool(old_count)
            new_start = int(hunk[3]) - bool(new_count)
            if new_start != old_start + shift:
                return None
            shift += new_count - old_count
            hunks += 1
            while old_count or new_count:
                body = next(lines, "")
                if not body or body[0] not in " +-":
                    return None
                old_count -= body[0] != "+"
                new_count -= body[0] != "-"
                if min(old_count, new_count) < 0:
                    return None
            line = next(lines, None)
        if not hunks:
            return None

        filename = root / path
        if str(filename) in overlays or filename.resolve() != filename:
            return None
        source = filename.read_bytes().decode("utf-8")
        if not source.endswith("\n") or "\n".join(source.splitlines()) + "\n" != source:
            return None
        patched = _patched_source(path, source, patch)
        if patched is None:
            return None
        overlays[str(filename)] = patched.encode("utf-8")
    return overlays or None


class _PatchedLoader(importlib.machinery.SourceFileLoader):
    def __init__(self, name, path, overlay):
        super().__init__(name, path)
        self.overlay = overlay

    def get_code(self, fullname):
        code = compile(self.overlay.files[self.path], self.path, "exec", dont_inherit=True)
        self.overlay.seen.add(self.path)
        return code


class _Overlay:
    def __init__(self, root, files):
        self.files = files
        self.modules = {
            str(Path(path).relative_to(root).with_suffix("")).replace("/", ".").removesuffix(".__init__")
            for path in files if path.endswith(".py")
        }
        self.seen = set()
        self.invalid = False

    def find_spec(self, fullname, path=None, target=None):
        if fullname not in self.modules:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path, target)
        if spec is not None and spec.origin in self.files:
            spec.loader = _PatchedLoader(fullname, spec.origin, self)
            return spec
        return None

    def _path(self, file):
        if isinstance(file, (int, type(None))):
            return None
        if isinstance(file, (str, bytes, os.PathLike)):
            return os.path.abspath(os.fsdecode(file))
        return None

    def redirect(self, original):
        def open_file(file, mode="r", buffering=-1, encoding=None, errors=None,
                      newline=None, closefd=True, opener=None):
            path = self._path(file)
            if path not in self.files:
                return original(file, mode, buffering, encoding, errors, newline, closefd, opener)
            if mode not in {"r", "rt", "rb", "br", "tr"} or opener or not closefd:
                self.invalid = True
                raise OSError("unsupported access to patched source")
            self.seen.add(path)
            stream = io.BytesIO(self.files[path])
            stream.name = file
            if "b" in mode:
                return stream
            return io.TextIOWrapper(stream, encoding, errors, newline)

        return open_file

    def audit(self, event, args):
        if event in {"subprocess.Popen", "os.system", "os.fork", "os.forkpty",
                     "os.posix_spawn", "os.exec"}:
            self.invalid = True
            raise OSError("verification child cannot start another process")
        if event == "open" and self._path(args[0]) in self.files:
            self.invalid = True
            raise OSError("open bypassed patched source")


class _Verdicts:
    def __init__(self, pytest, node_ids):
        self.report_type = pytest.TestReport
        self.expected = set(node_ids)
        self.collected = []
        self.phases = {node_id: set() for node_id in node_ids}
        self.bad = set()
        self.collection_error = False

    def pytest_collection_finish(self, session):
        self.collected = [item.nodeid for item in session.items]

    def pytest_collectreport(self, report):
        self.collection_error |= report.failed

    def pytest_runtest_logreport(self, report):
        node_id = report.nodeid
        if node_id not in self.expected:
            self.collection_error = True
        elif not report.passed or hasattr(report, "wasxfail"):
            self.bad.add(node_id)
        elif type(report) is self.report_type:
            if report.when in self.phases[node_id]:
                self.bad.add(node_id)
            self.phases[node_id].add(report.when)

    def passed_mask(self, node_ids):
        if self.collection_error or sorted(self.collected) != sorted(node_ids):
            return 0
        required = {"setup", "call", "teardown"}
        return sum(1 << i for i, node_id in enumerate(node_ids)
                   if node_id not in self.bad and self.phases[node_id] == required)


def _run_child(pytest, root, files, node_ids):
    os.chdir(root)
    sys.path.insert(0, str(root))
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    os.environ["SKIP_INTEGRATION"] = "0"
    overlay = _Overlay(root, files)
    sys.meta_path.insert(0, overlay)
    builtins.open = overlay.redirect(builtins.open)
    io.open = overlay.redirect(io.open)
    sys.addaudithook(overlay.audit)
    if node_ids is None:
        for name in sorted(overlay.modules):
            importlib.import_module(name)
        return int(not overlay.invalid and overlay.seen == files.keys())
    verdicts = _Verdicts(pytest, node_ids)
    status = pytest.main(
        ["-q", "--tb=no", "-p", "no:cacheprovider", *node_ids], plugins=[verdicts]
    )
    if status not in (0, 1) or overlay.invalid or overlay.seen != files.keys():
        return 0
    return verdicts.passed_mask(node_ids)


class Verifier:
    def __init__(self, root: Path):
        self.root = root
        self.cpu_left = _TOTAL_PROBE_CPU
        self.deadline = time.monotonic() + _TOTAL_PROBE_WALL
        self.pytest = None

    def import_ok(self, patch):
        """Return True on a clean import, False on error, None for unsupported diffs."""
        try:
            files = _overlays(self.root, patch)
            if not files or any(not path.endswith(".py") for path in files):
                return None
            return bool(self._probe(files, None, 0.75, 2.0))
        except (ImportError, OSError, UnicodeError, ValueError):
            return None

    def passes(self, patch: str, node_ids: list[str], potential_seconds: float) -> set[str]:
        """Each returned ID has a complete, explicit pass on this exact patch."""
        cpu_limit = min(_MAX_PROBE_CPU, self.cpu_left, potential_seconds / 4)
        wall_limit = min(_MAX_PROBE_WALL, self.deadline - time.monotonic())
        if cpu_limit < 0.15 or wall_limit < 0.3:
            return set()
        try:
            files = _overlays(self.root, patch)
            if files is None:
                return set()
            if self.pytest is None:
                import pytest
                from _pytest.config import default_plugins

                # These pinned pytest modules are independent of the patched checkout.
                for plugin in default_plugins:
                    importlib.import_module(f"_pytest.{plugin}")
                self.pytest = pytest
            mask = self._probe(files, node_ids, cpu_limit, wall_limit)
        except (ImportError, OSError, UnicodeError, ValueError):
            return set()
        return {node_id for i, node_id in enumerate(node_ids) if mask & (1 << i)}

    def _probe(self, files, node_ids, cpu_limit, wall_limit):
        reader, writer = os.pipe()
        try:
            pid = os.fork()
        except OSError:
            os.close(reader)
            os.close(writer)
            raise

        if pid == 0:
            try:
                signal.signal(signal.SIGPROF, signal.SIG_DFL)
                signal.setitimer(signal.ITIMER_PROF, cpu_limit)
                os.close(reader)
                with open(os.devnull, "rb+") as devnull:
                    for fd in (0, 1, 2):
                        os.dup2(devnull.fileno(), fd)
                result = _run_child(self.pytest, self.root, files, node_ids)
                os.write(writer, bytes([result]))
            except BaseException:
                os._exit(1)
            os._exit(0)

        os.close(writer)
        result = b""
        try:
            ready, _, _ = select.select([reader], [], [], wall_limit)
            if ready:
                result = os.read(reader, 2)
            else:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        finally:
            os.close(reader)
            _, status, usage = os.wait4(pid, 0)
            self.cpu_left -= usage.ru_utime + usage.ru_stime
        return result[0] if status == 0 and len(result) == 1 else 0

"""Exercise the fail-closed verifier in the Linux contest image."""

import difflib
import os
from pathlib import Path
import tempfile
import unittest

from verify import Verifier


MODULE = "sqlglot/value.py"
FIXTURE = "tests/fixtures/value.json"
TESTS = """\
from pathlib import Path
import os
import pytest
from sqlglot.value import value

def test_pass():
    assert value == 2

def test_fail():
    assert value == 1

def test_skip():
    pytest.skip(str(value))

@pytest.mark.xfail
def test_xfail():
    assert value == 1

def test_subtest(subtests):
    with subtests.test():
        assert value == 1

def test_fixture():
    assert Path('tests/fixtures/value.json').read_text() == '2\\n'

def test_bypass():
    try:
        os.close(os.open('sqlglot/value.py', os.O_RDONLY))
    except OSError:
        pass
    assert value == 2

def test_timeout():
    while True:
        pass
"""


def patch(path, old, new):
    hunk = difflib.unified_diff(old.splitlines(True), new.splitlines(True), f"a/{path}", f"b/{path}")
    return f"diff --git a/{path} b/{path}\nindex 82a0caf..0505c07 100644\n" + "".join(hunk)


@unittest.skipUnless(hasattr(os, "fork"), "requires the Linux verifier")
class VerifierTest(unittest.TestCase):
    def setUp(self):
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        self.root = Path(work.name)
        (self.root / "sqlglot").mkdir()
        (self.root / "sqlglot/__init__.py").touch()
        (self.root / MODULE).write_text("value = 1\n", encoding="utf-8")
        (self.root / "tests/fixtures").mkdir(parents=True)
        (self.root / FIXTURE).write_text("1\n", encoding="utf-8")
        (self.root / "test_probe.py").write_text(TESTS, encoding="utf-8")
        self.patch = patch(MODULE, "value = 1\n", "value = 2\n")

    def probe(self, names, change=None):
        ids = [f"test_probe.py::test_{name}" for name in names]
        return Verifier(self.root).passes(self.patch if change is None else change, ids, 3.0)

    def test_only_explicit_passes(self):
        observed = self.probe(["pass", "fail", "skip", "xfail", "subtest"])
        self.assertEqual(observed, {"test_probe.py::test_pass"})

    def test_patched_fixture_and_source(self):
        change = self.patch + patch(FIXTURE, "1\n", "2\n")
        self.assertEqual(self.probe(["fixture"], change), {"test_probe.py::test_fixture"})
        self.assertEqual((self.root / FIXTURE).read_text(), "1\n")

    def test_unsupported_and_timeout_keep_all(self):
        self.assertFalse(self.probe(["pass"], self.patch.replace("--- a/sqlglot", "--- a/other")))
        self.assertFalse(self.probe(["pass", "bypass"]))
        self.assertFalse(self.probe(["pass", "timeout"]))


if __name__ == "__main__":
    unittest.main()

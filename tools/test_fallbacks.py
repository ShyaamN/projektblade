#!/usr/bin/env python3
"""Regression checks for REVIEW.md correctness risks (no answers.json)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import selector  # noqa: E402


def load_maps():
    tests = json.loads((ROOT / "sample" / "tests.json").read_text(encoding="utf-8"))["tests"]
    all_ids = {t["id"] for t in tests}
    maps = selector.ImpactMaps.load(ROOT / "maps" / "impact.json", all_ids)
    assert maps.map_ok, maps.map_error
    return maps, all_ids


def test_comment_not_empty():
    maps, all_ids = load_maps()
    patch = (ROOT / "sample" / "commits" / "9u5k.patch").read_text(encoding="utf-8")
    result = selector.select_ids(patch, maps, all_ids)
    assert not result.force_all or len(result.ids) == len(all_ids)
    assert len(result.ids) > 0, "comment-only must not return empty selection"


def test_unknown_path_forces_all_in_mixed_patch():
    maps, all_ids = load_maps()
    # Known fixture + unknown top-level file in one patch.
    patch = """diff --git a/tests/fixtures/optimizer/simplify.sql b/tests/fixtures/optimizer/simplify.sql
index 111..222 100644
--- a/tests/fixtures/optimizer/simplify.sql
+++ b/tests/fixtures/optimizer/simplify.sql
@@ -1,1 +1,1 @@
-SELECT 1;
+SELECT 2;
diff --git a/weird/top.py b/weird/top.py
new file mode 100644
index 000..111
--- /dev/null
+++ b/weird/top.py
@@ -0,0 +1,1 @@
+print('x')
"""
    result = selector.select_ids(patch, maps, all_ids)
    assert result.force_all
    assert result.ids == all_ids
    assert any(r.startswith("unknown_path:") for r in result.reasons)


def test_unknown_fixture_forces_all():
    maps, all_ids = load_maps()
    patch = """diff --git a/tests/fixtures/optimizer/not_a_real_fixture_zz.sql b/tests/fixtures/optimizer/not_a_real_fixture_zz.sql
index 111..222 100644
--- a/tests/fixtures/optimizer/not_a_real_fixture_zz.sql
+++ b/tests/fixtures/optimizer/not_a_real_fixture_zz.sql
@@ -1,1 +1,1 @@
-A
+B
"""
    result = selector.select_ids(patch, maps, all_ids)
    assert result.force_all
    assert result.ids == all_ids
    assert any("unknown_fixture" in r for r in result.reasons)


def test_shared_helper_forces_all():
    maps, all_ids = load_maps()
    patch = """diff --git a/tests/helpers.py b/tests/helpers.py
index 111..222 100644
--- a/tests/helpers.py
+++ b/tests/helpers.py
@@ -1,1 +1,1 @@
-x = 1
+x = 2
"""
    result = selector.select_ids(patch, maps, all_ids)
    assert result.force_all
    assert result.ids == all_ids


def main() -> int:
    test_comment_not_empty()
    test_unknown_path_forces_all_in_mixed_patch()
    test_unknown_fixture_forces_all()
    test_shared_helper_forces_all()
    print("fallback regressions: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

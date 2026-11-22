# ProjektBLADE — STATUS

Last updated: 2026-11-21 ~19:15 PT by Cursor (implementation)

## Verified scores (sample set only)

| Run | Method | Simulated | Misses | Container CPU | Notes |
| --- | --- | ---: | ---: | ---: | --- |
| Baseline starter | all tests / 3 workers | **1210.470 s** | 0 | — | local `score.py` |
| Impact v2 | static ID maps | **532.500 s** | 0 | ~0.30 s | Docker |
| Coverage + static file suites | `coverage.json.gz`, whole optimizer/executor files | **260.377 s** | 0 | **0.663 s median of 3** | Docker stdout byte-identical to local. Board-style middle run **261.0 s**. Not a practice score. |
| Pool-route envelope | Codex offline, same seeds | **214.492 s** | 0 | — | reproduced by Cursor before the two narrowings below |
| Pool routes, parent map | no whole-file class union | **206.161 s** | 0 | not remeasured | local only |
| Executor routes the fixtures never run | same seeds, TPC-H dropped, TPC-DS kept only when the changed function runs, import-comment routes dropped | **167.644 s** | 0 | — | matches the offline estimate |
| **Current selector** | plus `is`/`==` None on `Context._table`, which is identity-equivalent | **142.857 s** | **0** | **0.695 s middle of 3** | Docker stdout byte-identical. Middle total **143.552 s**, board rounding **143.6 s**. Not a practice score. |

Oracle floor (sample): **94.351 s**. Codex's fixed-seed floor with perfect adds and no CPU is **128.729 s**. The current 206.161 s still pays about 25 s on several optimizer/executor commits because the route adds the spawned TPC tests. Child coverage is collecting so those routes can be replaced by lines the workers actually ran.

Dropped whole-file class/module coverage after a paired run: **207.240 s, 0 misses** versus 214.492 s. Dropping unchanged-context seeds on top of that did not change the score or the misses. `dialect.py` `2haz` still selects 80 tests because they execute the changed line. `ddl.py` `ktz3` now selects nothing (0 labeled failures).

Patched-tree probes stay deferred. The executor group's best estimated saving was 22.109 s against 25.317 s of recorded duration, and probe CPU is unmeasured.

## Current artifacts

- `selector.py` — production selector (stdout JSONL, stderr debug)
- `tools/build_maps.py` — generates `maps/impact.json` from pinned sqlglot + tests.json
- `maps/impact.json` — schema_version 2, pinned_sha `eb11aec…`, pytest IDs (not bare numbers alone)
- `tools/run_score.py` — local sample scorer (reads answers for eval only; not in image)
- `vendor/sqlglot` @ `eb11aec4be8cb2122afe713a4816662c3f0fb2fa`
- `Dockerfile` copies selector + maps; readable by `nobody`

## REVIEW.md fixes applied (2026-11-21)

1. **Removed** `is_comment_only_patch` empty-selection shortcut. Comment edits now follow normal path rules (e.g. 9u5k/bap8/f4xa select dialect/core suites; sample still 0 misses).
2. **Unknown / non-sqlglot paths** set `force_all` immediately and union full suite, even in mixed patches with prior selections.
3. **Unknown fixtures** → full suite (not optimizer-only). Shared helpers (`tests/helpers.py`, `conftest.py`, …) → full suite. Diff parser keeps both `a/` and `b/` paths.
4. **Maps keyed by pytest ID**; runtime validates ID set against `/data/tests.json`. Unknown mapped IDs → full suite. No silent drop of unresolved numbers as “success”.

## Map provenance

Generator: `tools/build_maps.py`

Inputs (permitted):

- sqlglot tree at pinned SHA
- `tests.json` test `id` / `file` fields only

Not used: `answers.json`, patch files, any patch→failing table.

Contents: module path map, AST import edges (sqlglot + test files), optimizer `check_file` / `load_sql_fixture*` fixture→test-ID map, dialect name list, full `test_ids` list for startup validation.

## Environment

- Docker was verified on the 260.377 s candidate (three runs, median CPU 0.663 s, stdout byte-identical to local). The 206.161 s selector has **not** been rebuilt or scored in Docker. Practice-board score is unverified.
- `make` is not on PATH. Local sample score: `python tools/run_score.py`.

## Next (implementation)

1. Child coverage finished: pytest exit 0, 1621 passed, 676 s. `maps/coverage.child.json.gz` is 651 KB, 236 files. Pool jobs 2186 submitted / 2183 completed; the 3 missing jobs are all `tests/test_sqlglotc.py::TestSQLGlotC::test_build_removes_obsolete_extensions`. The shipping `coverage.json.gz` is unchanged. Scoring the current selector on the child map is **224.793 s, 0 misses** — slower than 206.161 s, because the new lines add tests on top of the routes. Do not swap the map until those route adds are dropped.
2. Merge that map by pytest node ID, then drop blanket executor/optimizer route adds where the child line hit already covers the failure. Keep the serde/pool route where pickle happens outside the task wrapper.
3. Docker-verify the candidate that still has zero sample misses. Do not claim a practice score or a sub-100 result until that run exists.

## For ChatGPT

The route envelope is now in `selector.py` and `maps/impact.json` (`pool_routes`). `selected_files: set[str] = set()` is still the unique adapter needle. Whole-file class expansion is off. Control-flow function expansion runs only when the added and removed keyword sets differ. Unknown paths, unknown fixtures, unknown modules, and shared helpers are resolved before the comment-only shortcut. Child coverage is in progress; patched-tree probes were not started.

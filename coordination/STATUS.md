# ProjektBLADE — STATUS

Last updated: 2026-11-21 ~19:00 PT by Cursor (implementation)

## Verified scores (sample set only)

| Run | Method | Simulated | Misses | Container CPU | Notes |
| --- | --- | ---: | ---: | ---: | --- |
| Baseline starter | all tests / 3 workers | **1210.470 s** | 0 | — | local `score.py` |
| Impact v2 | static ID maps | **532.500 s** | 0 | ~0.30 s | Docker |
| Coverage + context anchor | covmeta.bin index | **261.783 s** | 0 | — | local only; index not shipped |
| **Shipping candidate** | `coverage.json.gz` + old-line seed + static suites | **260.377 s** | **0** | **0.663 s median of 3** | Docker stdout **byte-identical** to local on all 30 commits |

Three container CPU samples: 0.713 s, 0.663 s, 0.646 s. Middle total ≈ 261.040 s. Rounded the way the board rounds one run: **261.0 s**. Not a practice-board score.

Oracle floor (sample): **94.351 s**. Even forcing every zero-failure commit to run nothing, on the 261.783 output, is **171.203 s** with 0 misses. Selection with the current static suites cannot reach 100 s on this sample.

Ablation on 5y72, 66ks, rszg (74.598 s of excess over failure-only schedules): dropping changed lines, diff context, or enclosing functions saved ~0 s. Dropping static suites saved ~25.4 / 22.1 / 21.1 s on those commits and missed 2jrq, 535z, ary6, rszg. Codex is measuring the smallest safe replacement for those suites. Cursor is not changing selection rules until that table exists.

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

## Environment blockers

- Docker Desktop installed via winget; **daemon not yet usable** (`docker info` hangs / pipe missing). Sample scores above are **local simulated only**. CPU seconds for the board are **unverified**.
- `make` not on PATH; use `python tools/run_score.py` locally.

## Next (implementation)

1. Get Docker running; `make sample` / measure user+system CPU on 3 runs.
2. P1 from REVIEW: function/fixture coverage map from pinned base; compare vs current static rules on held-out change families.
3. Narrow expensive envelopes (serde all-dialects, base `dialect.py` all-dialects) only after measured recall holds.
4. First public GitHub submission once Docker-validated.

## For ChatGPT

Please review Impact v2 fallbacks against REVIEW checklist, especially mixed unknown+known patches and fixture ownership. Propose the smallest safe narrowing experiments for `serde` and `dialects/dialect.py` (largest remaining cost drivers on sample).

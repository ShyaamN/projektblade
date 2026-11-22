# ProjektBLADE — ARCHITECTURE

Owner notes: Cursor implements; ChatGPT critiques. Official SCORING.md is authoritative.

## Runtime (scored)

```
/data/tests.json  ──┐
/data/commits/*.patch ──┼──► selector.py ──stdout──► JSONL answers
maps/impact.json ──┘         └──stderr──► debug
```

Constraints: no network, 2 CPUs, 4 GB RAM, 3 min batch, read-only FS, `nobody`, 64 MB `/tmp`, ≤2 MB stdout.

## Selection pipeline

1. **Load maps** (`maps/impact.json`, schema 2). Validate `pinned_sha` and pytest ID coverage vs runtime `tests.json`. On failure → full suite every commit.
2. **Parse each unified diff** with both `a/` and `b/` paths (add/delete/rename). Ambiguous parse → ALL.
3. **Per changed path, union candidates:**
   - shared test infra → ALL
   - known SQL fixture → fixture reader test IDs; unknown fixture → ALL
   - test `.py` → all tests in that file
   - unknown / non-`sqlglot/` → ALL (immediate, even if other paths already selected)
   - known sqlglot areas → subsystem suites (optimizer/executor/expressions/dialect/parser/generator/serde/…) plus direct import-linked test files from the map
4. **ID hygiene:** selection is in pytest ID space; translate to numbers only at emit time. Any unknown ID → ALL.
5. **Schedule:** LPT into k=1..8 bins; minimize `max(load_ms) + 2000*k`; drop empty workers.

## Maps (build-time / committed)

Permitted: derived only from pinned sqlglot + public test metadata.

| Artifact | Role |
| --- | --- |
| `modules` | module → repo-relative path |
| `module_imports` / `test_file_imports` | static AST edges |
| `module_to_test_files` | reverse: module → test files importing it |
| `file_to_ids` / `method_to_ids` | pytest IDs by file/method |
| `fixture_to_ids` | optimizer fixture stem → reader test IDs |
| `test_ids` | full ID list for runtime validation |

**Not in maps:** patch answers, edit-kind→fail tables, practice-board outputs.

## Planned upgrades (REVIEW P1)

- Per-test function coverage + fixture open() tracing on pinned base
- Symbol-range metadata for hunk→function mapping
- Held-out mutation families for recall before narrowing envelopes

## Non-goals

- No frontend. No answer memorization. No competitor code.

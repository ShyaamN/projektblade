# Pool coverage and static selection experiments — 2026-11-21

Run `python -B tools/static_gap.py --report` from the repository root to reproduce the tables. The evaluator snapshots the current selector in memory and disables only its static test-file sink, including direct additions. It preserves Cursor's coverage seeds, fixture mapping and existing fallbacks. It loads the thread-aware gzip artifact, uses Cursor's scheduler and counts 300 seconds once per missed commit. All measured totals are simulated on the 30 local samples. The evaluator and the labels stay offline.

Evidence categories are exclusive. Exact means a changed old line, without context or adjacent lines. Enclosing-function uses the innermost function in the impact map for each changed line, with old context as the anchor for insertion-only diffs. File-level means some execution in that changed file. Fixture means a recorded read of the changed fixture's exact path. If evidence overlaps across files, use exact, function, fixture, then file-level. The seed column shows actual selection after removing static suites, which is a separate question from whether a test has any evidence in the map. The cost breakdown excludes miss penalties per row. The summary includes them.

## Collection experiment

The optimizer constructs a `ProcessPoolExecutor(mp_context=get_context("spawn"))` in `TestOptimizer.setUpClass` and submits `parse_and_optimize` from `check_file`. The executor tests create a second spawned process pool in `_mp_execute`. Timing the parent tracer earlier can fix old threads. It cannot instrument another interpreter. See `vendor/sqlglot/tests/test_optimizer.py` lines 37, 110–112 and 225–270 and `tests/test_executor.py` lines 57–68 and 175–191.

`python -B tools/static_gap.py --trace-pool tests/test_optimizer.py::TestOptimizer::test_eliminate_joins` is a runnable focused collector. It installs hooks before pytest loads tests, installs the line tracer again at child startup through the process-pool initializer, passes the active pytest node ID with each submission, and attributes results by ID across reusable workers. Parent tracing spans setup, call and teardown. Thread pool submissions similarly carry their own node ID. The existing pool initializer still runs. Child shards are written to a temporary directory and merged after the run. JSON on stdout contains separate parent, worker-thread and child-process lines. It does not alter the project maps. The focused run passed with 59 subtests and returned 40 child jobs. Old line 71 of `eliminate_joins.py` was absent in the parent map and present in the child map.

For Cursor's full collector, merge the node-ID postings into the existing map with pytest ID validation. Track submitted versus completed jobs and record missing or skipped IDs by name. Use task ownership instead of a process-global current test. Keep the pool routes as protection for missing jobs, setup failures and unsupported work. The proof of concept covers work inside the task. Implicit result pickling runs after the wrapper returns and argument unpickling can occur before it starts. For serde, add an explicit serialization subphase in the collector that records the expression result's `__reduce__`/pickle round trip under the same node ID, or keep the static pool route as a guard. Validate that the helper does not change test outcomes. Preserve broad handling of import-time work and the two untraced stress tests. No full collection was run in this audit.

Accept a narrower envelope only after child postings recover the affected methods, the same scorer-compatible image produces zero sample misses, and independent cases exercise subprocess tasks, serde transport, dialect initialization and mixed patches. Static fixture dialect tags omit programmatic dialect choices and inherited dialect behavior. Expand those edges with measured worker coverage or fall back when unresolved. The current comment/blank test also runs before conservative path classification. Move path and fixture validation ahead of it so mixed patches preserve the unknown-path fallback. Absence of a changed-line hit alone cannot justify an empty selection.

## Targeted pytest decision

Evaluate only the two executor pool methods as a single proposed group. Even with every passing probe recognized, the best observed rescheduling saving is 22.109 seconds for one sample commit. Their recorded durations sum to 25.317 seconds, before starting pytest or applying a patch. The duration sum is a proxy, not measured CPU. Given the CPU-heavy process pool and the small margin, drop this experiment for now. If later evidence shows the group runs under the applicable savings ceiling in total user plus system CPU, keep all unprobed tests and remove only tests with a conclusive pass on an isolated patched tree. Retain failures, errors, timeouts, subtest failures, missing reports and ambiguous runs.

## Script output

```text
Selector SHA-256 `9af91d65a4837f077b0a63c530801522050650399178243875ead3cf9047de3c`
Coverage map `coverage.json.gz` with 230 source files
Commits 30, failures 73, classifications {'exact changed-line': 39, 'none': 6, 'file-level': 16, 'fixture': 11, 'enclosing-function': 1}
Base-derived pool routes: 22 optimizer methods, 2 executor methods
current: execution=260.377s misses=0 simulated=260.377s
without suites: execution=107.819s misses=5 simulated=1607.819s
  2jrq tests/test_executor.py::TestExecutor::test_execute_tpcds
  2jrq tests/test_optimizer.py::TestOptimizer::test_eliminate_joins
  2jrq tests/test_optimizer.py::TestOptimizer::test_optimize
  2jrq tests/test_optimizer.py::TestOptimizer::test_tpcds
  2jrq tests/test_optimizer.py::TestOptimizer::test_tpch
  3utz tests/test_optimizer.py::TestOptimizer::test_eliminate_ctes
  3utz tests/test_optimizer.py::TestOptimizer::test_eliminate_joins
  3utz tests/test_optimizer.py::TestOptimizer::test_eliminate_subqueries
  3utz tests/test_optimizer.py::TestOptimizer::test_normalize_identifiers
  3utz tests/test_optimizer.py::TestOptimizer::test_optimize
  3utz tests/test_optimizer.py::TestOptimizer::test_optimize_joins
  3utz tests/test_optimizer.py::TestOptimizer::test_pushdown_cte_alias_columns
  3utz tests/test_optimizer.py::TestOptimizer::test_pushdown_predicates
  3utz tests/test_optimizer.py::TestOptimizer::test_qualify_tables
  3utz tests/test_optimizer.py::TestOptimizer::test_quote_identifiers
  3utz tests/test_optimizer.py::TestOptimizer::test_tpcds
  3utz tests/test_optimizer.py::TestOptimizer::test_tpch
  3utz tests/test_optimizer.py::TestOptimizer::test_unnest_subqueries
  535z tests/test_optimizer.py::TestOptimizer::test_simplify
  ary6 tests/test_optimizer.py::TestOptimizer::test_optimize
  ary6 tests/test_optimizer.py::TestOptimizer::test_quote_identifiers
  rszg tests/test_optimizer.py::TestOptimizer::test_optimize
route envelope: execution=214.492s misses=0 simulated=214.492s
sample-only none adds: execution=128.334s misses=3 simulated=1028.334s
  2jrq tests/test_optimizer.py::TestOptimizer::test_eliminate_joins
  2jrq tests/test_optimizer.py::TestOptimizer::test_optimize
  3utz tests/test_optimizer.py::TestOptimizer::test_eliminate_ctes
  3utz tests/test_optimizer.py::TestOptimizer::test_eliminate_joins
  3utz tests/test_optimizer.py::TestOptimizer::test_eliminate_subqueries
  3utz tests/test_optimizer.py::TestOptimizer::test_normalize_identifiers
  3utz tests/test_optimizer.py::TestOptimizer::test_optimize
  3utz tests/test_optimizer.py::TestOptimizer::test_optimize_joins
  3utz tests/test_optimizer.py::TestOptimizer::test_pushdown_cte_alias_columns
  3utz tests/test_optimizer.py::TestOptimizer::test_pushdown_predicates
  3utz tests/test_optimizer.py::TestOptimizer::test_qualify_tables
  3utz tests/test_optimizer.py::TestOptimizer::test_quote_identifiers
  3utz tests/test_optimizer.py::TestOptimizer::test_tpcds
  3utz tests/test_optimizer.py::TestOptimizer::test_tpch
  3utz tests/test_optimizer.py::TestOptimizer::test_unnest_subqueries
  rszg tests/test_optimizer.py::TestOptimizer::test_optimize
sample-only minimum adds: execution=128.729s misses=0 simulated=128.729s
failure-only reference: execution=94.351s misses=0 simulated=94.351s
Fixed seed plus failures, any-scheduler lower bound: 128.729s
Largest remaining costs above failure-only schedules, with the three longest seed extras:
  2haz excess=7.885s tests/test_optimizer.py::TestOptimizer::test_scope (2.533s), tests/test_optimizer.py::TestOptimizer::test_qualify_columns (1.406s), tests/test_optimizer.py::TestOptimizer::test_optimize_in_nulls (1.331s)
  ktz3 excess=6.965s tests/test_optimizer.py::TestOptimizer::test_qualify_columns (1.406s), tests/dialects/test_snowflake.py::TestSnowflake::test_snowflake (0.662s), tests/test_serde.py::TestSerde::test_serde (0.506s)
  ary6 excess=4.398s tests/test_optimizer.py::TestOptimizer::test_scope (2.533s), tests/test_optimizer.py::TestOptimizer::test_annotate_funcs (1.963s), tests/dialects/test_snowflake.py::TestSnowflake::test_snowflake (0.662s)
  66ks excess=3.345s tests/test_executor.py::TestExecutor::test_subqueries (0.186s), tests/test_executor.py::TestExecutor::test_scalar_functions (0.169s), tests/test_executor.py::TestExecutor::test_correlated_exists_over_scalar_aggregate (0.086s)
  2qn5 excess=2.536s tests/dialects/test_duckdb.py::TestDuckDB::test_duckdb (0.287s), tests/dialects/test_dialect.py::TestDialect::test_time (0.155s), tests/dialects/test_bigquery.py::TestBigQuery::test_week (0.051s)
Executor pool probe upper bounds (labels model a conclusive pass on every passing probe):
  2jrq saves at most 0.000s; group's recorded elapsed=25.317s; tests/test_executor.py::TestExecutor::test_execute_tpcds, tests/test_executor.py::TestExecutor::test_execute_tpch
  3utz saves at most 0.000s; group's recorded elapsed=25.317s; tests/test_executor.py::TestExecutor::test_execute_tpcds, tests/test_executor.py::TestExecutor::test_execute_tpch
  5y72 saves at most 20.401s; group's recorded elapsed=25.317s; tests/test_executor.py::TestExecutor::test_execute_tpcds, tests/test_executor.py::TestExecutor::test_execute_tpch
  66ks saves at most 22.109s; group's recorded elapsed=25.317s; tests/test_executor.py::TestExecutor::test_execute_tpcds, tests/test_executor.py::TestExecutor::test_execute_tpch
  rszg saves at most 17.449s; group's recorded elapsed=25.317s; tests/test_executor.py::TestExecutor::test_execute_tpcds, tests/test_executor.py::TestExecutor::test_execute_tpch
```

| Commit | Labeled failing test | Strongest map evidence | Changed path with evidence | Seed selected | Envelope selected |
|---|---|---|---|---|---|
| 2jrq | `tests/test_executor.py::TestExecutor::test_execute_subqueries` | exact changed-line | `sqlglot/optimizer/eliminate_joins.py` | yes | yes |
| 2jrq | `tests/test_executor.py::TestExecutor::test_execute_tpcds` | none | - | no | yes |
| 2jrq | `tests/test_optimizer.py::TestOptimizer::test_eliminate_joins` | file-level | `sqlglot/optimizer/eliminate_joins.py` | no | yes |
| 2jrq | `tests/test_optimizer.py::TestOptimizer::test_optimize` | file-level | `sqlglot/optimizer/eliminate_joins.py` | no | yes |
| 2jrq | `tests/test_optimizer.py::TestOptimizer::test_optimize_special_joins` | exact changed-line | `sqlglot/optimizer/eliminate_joins.py` | yes | yes |
| 2jrq | `tests/test_optimizer.py::TestOptimizer::test_pushdown_projections_prunes_non_self_referencing_ctes` | exact changed-line | `sqlglot/optimizer/eliminate_joins.py` | yes | yes |
| 2jrq | `tests/test_optimizer.py::TestOptimizer::test_tpcds` | none | - | no | yes |
| 2jrq | `tests/test_optimizer.py::TestOptimizer::test_tpch` | none | - | no | yes |
| 2vdj | `tests/test_optimizer.py::TestOptimizer::test_pushdown_predicates` | fixture | `tests/fixtures/optimizer/pushdown_predicates.sql` | yes | yes |
| 3utz | `tests/dialects/test_databricks.py::TestDatabricks::test_optimize` | exact changed-line | `sqlglot/serde.py` | yes | yes |
| 3utz | `tests/dialects/test_teradata.py::TestTeradata::test_execute` | exact changed-line | `sqlglot/serde.py` | yes | yes |
| 3utz | `tests/dialects/test_teradata.py::TestTeradata::test_hex_character_string` | exact changed-line | `sqlglot/serde.py` | yes | yes |
| 3utz | `tests/dialects/test_teradata.py::TestTeradata::test_named` | exact changed-line | `sqlglot/serde.py` | yes | yes |
| 3utz | `tests/dialects/test_teradata.py::TestTeradata::test_named_attribute_order` | exact changed-line | `sqlglot/serde.py` | yes | yes |
| 3utz | `tests/dialects/test_teradata.py::TestTeradata::test_title` | exact changed-line | `sqlglot/serde.py` | yes | yes |
| 3utz | `tests/test_executor.py::TestExecutor::test_execute_tpcds` | exact changed-line | `sqlglot/serde.py` | yes | yes |
| 3utz | `tests/test_executor.py::TestExecutor::test_execute_tpch` | exact changed-line | `sqlglot/serde.py` | yes | yes |
| 3utz | `tests/test_expressions.py::TestExprs::test_ref_type_builder` | exact changed-line | `sqlglot/serde.py` | yes | yes |
| 3utz | `tests/test_optimizer.py::TestOptimizer::test_canonicalize` | exact changed-line | `sqlglot/serde.py` | yes | yes |
| 3utz | `tests/test_optimizer.py::TestOptimizer::test_canonicalize_internal_names` | exact changed-line | `sqlglot/serde.py` | yes | yes |
| 3utz | `tests/test_optimizer.py::TestOptimizer::test_eliminate_ctes` | file-level | `sqlglot/serde.py` | no | yes |
| 3utz | `tests/test_optimizer.py::TestOptimizer::test_eliminate_joins` | file-level | `sqlglot/serde.py` | no | yes |
| 3utz | `tests/test_optimizer.py::TestOptimizer::test_eliminate_subqueries` | file-level | `sqlglot/serde.py` | no | yes |
| 3utz | `tests/test_optimizer.py::TestOptimizer::test_isolate_table_selects` | exact changed-line | `sqlglot/serde.py` | yes | yes |
| 3utz | `tests/test_optimizer.py::TestOptimizer::test_merge_subqueries` | exact changed-line | `sqlglot/serde.py` | yes | yes |
| 3utz | `tests/test_optimizer.py::TestOptimizer::test_normalize` | exact changed-line | `sqlglot/serde.py` | yes | yes |
| 3utz | `tests/test_optimizer.py::TestOptimizer::test_normalize_identifiers` | file-level | `sqlglot/serde.py` | no | yes |
| 3utz | `tests/test_optimizer.py::TestOptimizer::test_optimize` | file-level | `sqlglot/serde.py` | no | yes |
| 3utz | `tests/test_optimizer.py::TestOptimizer::test_optimize_joins` | file-level | `sqlglot/serde.py` | no | yes |
| 3utz | `tests/test_optimizer.py::TestOptimizer::test_pushdown_cte_alias_columns` | file-level | `sqlglot/serde.py` | no | yes |
| 3utz | `tests/test_optimizer.py::TestOptimizer::test_pushdown_predicates` | file-level | `sqlglot/serde.py` | no | yes |
| 3utz | `tests/test_optimizer.py::TestOptimizer::test_pushdown_projection` | exact changed-line | `sqlglot/serde.py` | yes | yes |
| 3utz | `tests/test_optimizer.py::TestOptimizer::test_qualify_columns` | exact changed-line | `sqlglot/serde.py` | yes | yes |
| 3utz | `tests/test_optimizer.py::TestOptimizer::test_qualify_columns__with_invisible` | exact changed-line | `sqlglot/serde.py` | yes | yes |
| 3utz | `tests/test_optimizer.py::TestOptimizer::test_qualify_order_by_source_columns` | exact changed-line | `sqlglot/serde.py` | yes | yes |
| 3utz | `tests/test_optimizer.py::TestOptimizer::test_qualify_tables` | file-level | `sqlglot/serde.py` | no | yes |
| 3utz | `tests/test_optimizer.py::TestOptimizer::test_quote_identifiers` | file-level | `sqlglot/serde.py` | no | yes |
| 3utz | `tests/test_optimizer.py::TestOptimizer::test_simplify` | exact changed-line | `sqlglot/serde.py` | yes | yes |
| 3utz | `tests/test_optimizer.py::TestOptimizer::test_tpcds` | file-level | `sqlglot/serde.py` | no | yes |
| 3utz | `tests/test_optimizer.py::TestOptimizer::test_tpch` | file-level | `sqlglot/serde.py` | no | yes |
| 3utz | `tests/test_optimizer.py::TestOptimizer::test_unnest_subqueries` | file-level | `sqlglot/serde.py` | no | yes |
| 3utz | `tests/test_serde.py::TestSerde::test_custom_expression` | exact changed-line | `sqlglot/serde.py` | yes | yes |
| 3utz | `tests/test_serde.py::TestSerde::test_meta` | exact changed-line | `sqlglot/serde.py` | yes | yes |
| 3utz | `tests/test_serde.py::TestSerde::test_recursion` | exact changed-line | `sqlglot/serde.py` | yes | yes |
| 3utz | `tests/test_serde.py::TestSerde::test_serde` | exact changed-line | `sqlglot/serde.py` | yes | yes |
| 3utz | `tests/test_serde.py::TestSerde::test_type_annotations` | exact changed-line | `sqlglot/serde.py` | yes | yes |
| 535z | `tests/test_optimizer.py::TestOptimizer::test_simplify` | none | - | no | yes |
| 6vvp | `tests/test_optimizer.py::TestOptimizer::test_qualify_columns__invalid` | fixture | `tests/fixtures/optimizer/qualify_columns__invalid.sql` | yes | yes |
| 6ze8 | `tests/dialects/test_dremio.py::TestDremio::test_regexp_split` | exact changed-line | `sqlglot/parsers/dremio.py` | yes | yes |
| 986c | `tests/test_optimizer.py::TestOptimizer::test_unnest_subqueries` | fixture | `tests/fixtures/optimizer/unnest_subqueries.sql` | yes | yes |
| ary6 | `tests/dialects/test_bigquery.py::TestBigQuery::test_bigquery` | exact changed-line | `sqlglot/parsers/bigquery.py` | yes | yes |
| ary6 | `tests/dialects/test_bigquery.py::TestBigQuery::test_information_schema_roundtrip` | exact changed-line | `sqlglot/parsers/bigquery.py` | yes | yes |
| ary6 | `tests/dialects/test_bigquery.py::TestBigQuery::test_ml_functions` | exact changed-line | `sqlglot/parsers/bigquery.py` | yes | yes |
| ary6 | `tests/dialects/test_bigquery.py::TestBigQuery::test_quoted_identifier_meta` | exact changed-line | `sqlglot/parsers/bigquery.py` | yes | yes |
| ary6 | `tests/test_expressions.py::TestExprs::test_replace_tables` | exact changed-line | `sqlglot/parsers/bigquery.py` | yes | yes |
| ary6 | `tests/test_optimizer.py::TestOptimizer::test_optimize` | none | - | no | yes |
| ary6 | `tests/test_optimizer.py::TestOptimizer::test_qualify_columns` | exact changed-line | `sqlglot/parsers/bigquery.py` | yes | yes |
| ary6 | `tests/test_optimizer.py::TestOptimizer::test_qualify_tables` | enclosing-function | `sqlglot/parsers/bigquery.py` | yes | yes |
| ary6 | `tests/test_optimizer.py::TestOptimizer::test_quote_identifiers` | none | - | no | yes |
| ary6 | `tests/test_parser.py::TestParser::test_ref_type` | exact changed-line | `sqlglot/parsers/bigquery.py` | yes | yes |
| bj7g | `tests/dialects/test_clickhouse.py::TestClickhouse::test_clickhouse_values` | exact changed-line | `sqlglot/generators/clickhouse.py` | yes | yes |
| bvw2 | `tests/test_optimizer.py::TestOptimizer::test_annotate_types` | fixture | `tests/fixtures/optimizer/annotate_types.sql` | yes | yes |
| egys | `tests/test_optimizer.py::TestOptimizer::test_simplify` | fixture | `tests/fixtures/optimizer/simplify.sql` | yes | yes |
| f4bn | `tests/test_optimizer.py::TestOptimizer::test_pushdown_cte_alias_columns` | fixture | `tests/fixtures/optimizer/pushdown_cte_alias_columns.sql` | yes | yes |
| gbhk | `tests/test_optimizer.py::TestOptimizer::test_annotate_types` | fixture | `tests/fixtures/optimizer/annotate_types.sql` | yes | yes |
| j6uz | `tests/dialects/test_bigquery.py::TestBigQuery::test_bigquery` | exact changed-line | `sqlglot/parsers/bigquery.py` | yes | yes |
| j6uz | `tests/test_optimizer.py::TestOptimizer::test_annotate_funcs` | exact changed-line | `sqlglot/parsers/bigquery.py` | yes | yes |
| mthh | `tests/test_optimizer.py::TestOptimizer::test_merge_subqueries` | fixture | `tests/fixtures/optimizer/merge_subqueries.sql` | yes | yes |
| nwnx | `tests/test_optimizer.py::TestOptimizer::test_pushdown_projection` | fixture | `tests/fixtures/optimizer/pushdown_projections.sql` | yes | yes |
| rszg | `tests/test_optimizer.py::TestOptimizer::test_annotate_funcs` | exact changed-line | `sqlglot/typing/snowflake.py` | yes | yes |
| rszg | `tests/test_optimizer.py::TestOptimizer::test_optimize` | file-level | `sqlglot/optimizer/scope.py` | no | yes |
| rszg | `tests/test_optimizer.py::TestOptimizer::test_qualify_columns` | fixture | `tests/fixtures/optimizer/qualify_columns_ddl.sql` | yes | yes |
| st8f | `tests/test_optimizer.py::TestOptimizer::test_isolate_table_selects` | fixture | `tests/fixtures/optimizer/isolate_table_selects.sql` | yes | yes |

| Commit | Changed paths | Labeled failures | Current (s) | Seed (s) | Envelope (s) | Added tests |
|---|---|---:|---:|---:|---:|---:|
| 2haz | sqlglot/dialects/dialect.py | 0 | 7.885 | 7.885 | 7.885 | 0 |
| 2jrq | sqlglot/optimizer/eliminate_joins.py | 8 | 27.454 | 5.736 | 25.454 | 6 |
| 2qn5 | sqlglot/generators/clickhouse.py | 0 | 2.536 | 2.536 | 4.621 | 3 |
| 2vdj | tests/fixtures/optimizer/pushdown_predicates.sql | 1 | 2.083 | 2.083 | 2.083 | 0 |
| 3utz | sqlglot/serde.py | 37 | 27.454 | 25.454 | 25.454 | 13 |
| 4d63 | sqlglot/dialects/snowflake.py | 0 | 3.387 | 0.000 | 4.950 | 9 |
| 535z | sqlglot/dialects/tsql.py | 1 | 12.981 | 2.436 | 3.032 | 1 |
| 5y72 | sqlglot/optimizer/eliminate_subqueries.py | 0 | 27.454 | 2.050 | 25.454 | 6 |
| 66ks | sqlglot/executor/context.py | 0 | 25.454 | 3.345 | 25.454 | 2 |
| 6vvp | tests/fixtures/optimizer/qualify_columns__invalid.sql | 1 | 2.037 | 2.037 | 2.037 | 0 |
| 6ze8 | sqlglot/parsers/dremio.py | 1 | 12.675 | 2.186 | 2.186 | 0 |
| 8y7j | sqlglot/generators/clickhouse.py | 0 | 2.031 | 2.031 | 4.116 | 3 |
| 986c | sqlglot/dialects/singlestore.py, sqlglot/typing/snowflake.py, tests/fixtures/optimizer/unnest_subqueries.sql | 1 | 13.069 | 2.329 | 5.279 | 9 |
| 9u5k | sqlglot/generators/sqlite.py | 0 | 0.000 | 0.000 | 3.670 | 3 |
| ary6 | sqlglot/parsers/bigquery.py | 10 | 13.588 | 8.579 | 9.265 | 6 |
| bap8 | sqlglot/transforms.py | 0 | 0.000 | 0.000 | 0.000 | 0 |
| bj7g | sqlglot/generators/clickhouse.py | 1 | 2.241 | 2.241 | 4.326 | 3 |
| bvw2 | sqlglot/dialects/materialize.py, tests/fixtures/optimizer/annotate_types.sql | 1 | 2.061 | 2.061 | 2.061 | 0 |
| egys | tests/fixtures/optimizer/simplify.sql | 1 | 2.596 | 2.596 | 2.596 | 0 |
| f4bn | tests/fixtures/optimizer/pushdown_cte_alias_columns.sql | 1 | 2.007 | 2.007 | 2.007 | 0 |
| f4xa | sqlglot/generators/drill.py | 0 | 0.000 | 0.000 | 0.000 | 0 |
| fryt | sqlglot/parsers/dremio.py | 0 | 12.675 | 2.173 | 2.173 | 0 |
| gbav | sqlglot/parsers/singlestore.py | 0 | 2.193 | 2.144 | 2.144 | 0 |
| gbhk | tests/fixtures/optimizer/annotate_types.sql | 1 | 2.061 | 2.061 | 2.061 | 0 |
| j6uz | sqlglot/parsers/bigquery.py | 2 | 12.920 | 4.399 | 6.649 | 8 |
| ktz3 | sqlglot/expressions/ddl.py | 0 | 6.965 | 6.965 | 6.965 | 0 |
| mthh | tests/fixtures/optimizer/merge_subqueries.sql | 1 | 3.016 | 3.016 | 3.016 | 0 |
| nwnx | tests/fixtures/optimizer/pushdown_projections.sql | 1 | 2.092 | 2.092 | 2.092 | 0 |
| rszg | sqlglot/optimizer/scope.py, sqlglot/typing/snowflake.py, tests/fixtures/optimizer/qualify_columns_ddl.sql | 3 | 27.454 | 5.369 | 25.454 | 18 |
| st8f | tests/fixtures/optimizer/isolate_table_selects.sql | 1 | 2.008 | 2.008 | 2.008 | 0 |

## 2026-11-21 — Critical path audit of the 206.161-second selector

The current selector reproduces **206.161 simulated seconds with zero sample misses**. Narrow executor pool routes using what the fixtures actually execute. The combined experiment reaches **167.644 seconds with zero new misses**, a saving of **38.517 seconds**. This is an offline sample estimate. Container CPU and practice performance are unmeasured.

### 2haz and normalization strategy

All 80 selections for `2haz` come from the exact changed old line. The longest are `TestOptimizer.test_scope` (2.533 s), `TestOptimizer.test_qualify_columns` (1.406 s), `test_conditional_cross_joins` (0.407 s) and `TestOptimizer.test_optimize` (0.395 s). Every one of them reads the strategy when evaluating the comparison. There is no long-test exemption on that basis. [Dialect.case_sensitive](../vendor/sqlglot/sqlglot/dialects/dialect.py#L1270) reads the enum at [line 1280](../vendor/sqlglot/sqlglot/dialects/dialect.py#L1280), chooses a character predicate and consumes it at line 1283. Its Boolean result is consumed by [Dialect.can_quote](../vendor/sqlglot/sqlglot/dialects/dialect.py#L1307) and [qualify_columns](../vendor/sqlglot/sqlglot/optimizer/qualify_columns.py#L1361).

The distinct question is whether identity and equality return the same value. They do on actual members of the same normalization enum. [Dialect initialization](../vendor/sqlglot/sqlglot/dialects/dialect.py#L1169) assigns the class default or converts the input into that enum. A plain string or custom value can give different behavior because [NormalizationStrategy inherits from str](../vendor/sqlglot/sqlglot/dialects/dialect.py#L137). The current map records line execution without operand types. No 2haz tests were removed in this experiment.

### Executor route evidence

The 21.454-second `test_execute_tpcds` is the sole busiest-worker test for all five requested commits. It is selected solely by `executor_deps` for the optimizer changes in `2jrq` and `5y72`, and for the optimizer import comment in `rszg`. The `66ks` route also has an independent executor-path prefix rule. The `3utz` route comes from the serde special case and the exact changed-line seed. [Selector pool routing](../selector.py#L646)

- `test_execute_tpch` sends 22 fixtures, and none reaches `execute` because none has truthy `execute` metadata. It is an unnecessary worker-body route when only optimizer or executor function bodies change and the test has no other seed. The pool still parses and pickles each input in the parent, so preserve parser and serde exposure. TPC-H sits on the other worker in these schedules. [Fixture binding](../vendor/sqlglot/tests/test_executor.py#L118) · [Worker guard](../vendor/sqlglot/tests/test_executor.py#L57) · [Pool submission](../vendor/sqlglot/tests/test_executor.py#L175) · [Expression serialization](../vendor/sqlglot/sqlglot/expressions/core.py#L890)
- TPC-DS sends 99 fixtures and 57 reach `execute`. A read-only replay of the optimizer stage on those 57 pinned inputs found 378 calls to `_should_eliminate_join` and 138 hits on its changed line 71, including the first enabled fixture. It found zero calls to `_eliminate_cte` and zero hits on line 176, with zero errors. The replay uses the base source, default dialect, TPC-DS schema and the executor's `leave_tables_isolated=True`. Rule-list membership alone cannot tell those two edits apart. [Executor optimizer call](../vendor/sqlglot/sqlglot/executor/__init__.py#L76) · [Rule list](../vendor/sqlglot/sqlglot/optimizer/optimizer.py#L40) · [Join condition](../vendor/sqlglot/sqlglot/optimizer/eliminate_joins.py#L71) · [CTE rewrite](../vendor/sqlglot/sqlglot/optimizer/eliminate_subqueries.py#L157)
- Keep TPC-DS on the executor context edit. The worker executes queries through `PythonExecutor`, whose join and aggregate paths read `Context.table`. The current base trace does not establish that the exact property line is unexecuted in those children. Keep it on serde edits as well, where both expression transport and the exact seed provide evidence. [Worker call](../vendor/sqlglot/tests/test_executor.py#L57) · [Join reads](../vendor/sqlglot/sqlglot/executor/python.py#L346) · [Aggregate reads](../vendor/sqlglot/sqlglot/executor/python.py#L457) · [Context.table](../vendor/sqlglot/sqlglot/executor/context.py#L52)
- In `rszg` the closure trigger is a plain comment inserted between imports of `optimizer/scope.py`. The evaluator reconstructs the file and checks that its AST is identical. Neither executor fixture uses the separately changed Snowflake typing path or the changed optimizer fixture. Their route is removable while preserving the independent Snowflake and fixture routes. Limit this filter to non-directive comments, preserve source-sensitive tests and fall back when the diff cannot be reconstructed. [Executor fixture bindings](../vendor/sqlglot/tests/test_executor.py#L118) · [Default-dialect executor call](../vendor/sqlglot/tests/test_executor.py#L57)

### Smallest tested narrowing

Protect every existing non-pool selection and exact changed-line hit. Filter only executor pool additions. Suppress routes triggered solely by AST-identical plain comments between imports, and suppress inactive worker fixtures for verified optimizer/executor body edits. For a single indentation-preserving change to a Boolean literal inside an optimizer assignment, suppress the pool route only if complete pinned fixture replay proves the enclosing function was never called in that test's worker optimizer stage. Keep the route for any trace gaps, structural edits, edited input fixtures or other call paths into the function. A production implementation should consume a pinned-base call map from collection to avoid charging runtime CPU for that replay.

The combined rule lowers the makespan of `5y72` and `rszg`. It lowers `66ks`'s simulated cost by 0.667 seconds through worker count, while retaining the TPC-DS test. It cannot lower the makespan on `2jrq` or `3utz` without omitting `test_execute_tpcds`, a 21.454-second labeled failure on both. Their failure-only schedules already cost 25.454 seconds. There is no zero-miss rule that lowers makespan on all five under the current scoring model. Reject the broad closure-only removal, which costs 428.149 seconds including one new miss on `2jrq`, `tests/test_executor.py::TestExecutor::test_execute_tpcds`.

### Reproduction and worker tables

Run `python -B tools/critical_path.py --replay-base-optimizer`. Without the flag it skips the pinned optimizer-stage replay. The script uses the real selector and scorer in memory. It distinguishes actual line and enclosing-function seeds, tests present only because of pool routes, and exact changed old-line hits. A line seed may include context or neighboring lines, so the exact column matters. Each drop saving is measured independently on the original assignments. It removes an empty worker and its 2-second overhead, and leaves every other test on its original worker. Tied busiest workers are both included. Labels are read for scoring and annotation only.

```text
Selector SHA-256: 955e837d5368b7723eefb245e290f3123af771fd28933e88b7d3a70e11319acc
Baseline: 206.161 s, 0 misses

Pinned executor case counts:
- `tests/test_executor.py::TestExecutor::test_execute_tpcds` uses `optimizer/tpc-ds/tpc-ds.sql`. 57/99 cases enter the executor.
- `tests/test_executor.py::TestExecutor::test_execute_tpch` uses `optimizer/tpc-h/tpc-h.sql`. 0/22 cases enter the executor.

Counterfactuals with rescheduling:
- Prune executor routes triggered solely by neutral import comments. 188.712 s, 0 misses.
  - rszg 25.454 to 8.005 s. Removed `tests/test_executor.py::TestExecutor::test_execute_tpcds`, `tests/test_executor.py::TestExecutor::test_execute_tpch`.
- Also prune inactive worker fixtures on optimizer/executor body edits. 188.045 s, 0 misses.
  - 2jrq 25.454 to 25.454 s. Removed `tests/test_executor.py::TestExecutor::test_execute_tpch`.
  - 5y72 25.454 to 25.454 s. Removed `tests/test_executor.py::TestExecutor::test_execute_tpch`.
  - 66ks 25.454 to 24.787 s. Removed `tests/test_executor.py::TestExecutor::test_execute_tpch`.
  - rszg 25.454 to 8.005 s. Removed `tests/test_executor.py::TestExecutor::test_execute_tpcds`, `tests/test_executor.py::TestExecutor::test_execute_tpch`.
- Reject module-closure executor routes without a direct seed. 428.149 s, 1 misses.
  - 2jrq 25.454 to 7.413 s. Removed `tests/test_executor.py::TestExecutor::test_execute_tpcds`, `tests/test_executor.py::TestExecutor::test_execute_tpch`.
    - Missed `tests/test_executor.py::TestExecutor::test_execute_tpcds`
  - 5y72 25.454 to 5.053 s. Removed `tests/test_executor.py::TestExecutor::test_execute_tpcds`, `tests/test_executor.py::TestExecutor::test_execute_tpch`.
  - 66ks 25.454 to 3.333 s. Removed `tests/test_executor.py::TestExecutor::test_execute_tpcds`, `tests/test_executor.py::TestExecutor::test_execute_tpch`.
  - rszg 25.454 to 8.005 s. Removed `tests/test_executor.py::TestExecutor::test_execute_tpcds`, `tests/test_executor.py::TestExecutor::test_execute_tpch`.
- Also prune unreached optimizer bodies after pinned fixture replay. 167.644 s, 0 misses.
  - 2jrq 25.454 to 25.454 s. Removed `tests/test_executor.py::TestExecutor::test_execute_tpch`.
  - 5y72 25.454 to 5.053 s. Removed `tests/test_executor.py::TestExecutor::test_execute_tpcds`, `tests/test_executor.py::TestExecutor::test_execute_tpch`.
  - 66ks 25.454 to 24.787 s. Removed `tests/test_executor.py::TestExecutor::test_execute_tpch`.
  - rszg 25.454 to 8.005 s. Removed `tests/test_executor.py::TestExecutor::test_execute_tpcds`, `tests/test_executor.py::TestExecutor::test_execute_tpch`.

Failure-only schedules for the requested cost drivers (offline reference):
- 2jrq 25.454 s (makespan 21.454 s).
- 3utz 25.454 s (makespan 21.454 s).
- 5y72 0.000 s (makespan 0.000 s).
- 66ks 0.000 s (makespan 0.000 s).
- rszg 5.764 s (makespan 3.764 s).
```

#### 2haz - makespan 2.822 s, 2 workers, cost 6.822 s (80 selected)

| Worker | Test ID | Test (s) | Selection source | Exact old line | Fixed schedule saving if dropped (s) | Labeled failure |
|---:|---|---:|---|---|---:|---|
| 1 | `tests/test_optimizer.py::TestOptimizer::test_scope` | 2.533 | line seed | yes | - | no |
| 1 | `tests/test_executor.py::TestExecutor::test_execute_callable` | 0.073 | line seed | yes | - | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_qualify_columns__invalid` | 0.037 | line seed | yes | - | no |
| 1 | `tests/test_executor.py::TestExecutor::test_execute_tables` | 0.026 | line seed | yes | - | no |
| 1 | `tests/test_executor.py::TestExecutor::test_outer_joins_preserve_unmatched_rows` | 0.020 | line seed | yes | - | no |
| 1 | `tests/test_lineage.py::TestLineage::test_unpivot_with_alias_columns` | 0.013 | line seed | yes | - | no |
| 1 | `tests/test_executor.py::TestExecutor::test_execute_subqueries` | 0.011 | line seed | yes | - | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_optimize_snowflake_asof_without_on` | 0.010 | line seed | yes | - | no |
| 1 | `tests/test_executor.py::TestExecutor::test_correlated_count` | 0.009 | line seed | yes | - | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_pushdown_projections_keeps_recursive_cte_self_referenced_columns` | 0.008 | line seed | yes | - | no |
| 1 | `tests/test_lineage.py::TestLineage::test_chained_pivots` | 0.006 | line seed | yes | - | no |
| 1 | `tests/test_lineage.py::TestLineage::test_copy_flag` | 0.006 | line seed | yes | - | no |
| 1 | `tests/test_executor.py::TestExecutor::test_execute_catalog_db_table` | 0.005 | line seed | yes | - | no |
| 1 | `tests/test_lineage.py::TestLineage::test_unpivot_with_cte` | 0.005 | line seed | yes | - | no |
| 1 | `tests/test_lineage.py::TestLineage::test_chained_pivots_mixed` | 0.004 | line seed | yes | - | no |
| 1 | `tests/test_lineage.py::TestLineage::test_lineage_no_self_loops_with_multi_aliased_union_cte` | 0.004 | line seed | yes | - | no |
| 1 | `tests/test_lineage.py::TestLineage::test_lineage_union` | 0.004 | line seed | yes | - | no |
| 1 | `tests/test_lineage.py::TestLineage::test_pivot_without_alias` | 0.004 | line seed | yes | - | no |
| 1 | `tests/test_lineage.py::TestLineage::test_unpivot` | 0.004 | line seed | yes | - | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_qualify_columns_struct_field_and_source_alias` | 0.004 | line seed | yes | - | no |
| 1 | `tests/test_executor.py::TestExecutor::test_nested_table_reference` | 0.003 | line seed | yes | - | no |
| 1 | `tests/test_lineage.py::TestLineage::test_lineage_source_union` | 0.003 | line seed | yes | - | no |
| 1 | `tests/test_lineage.py::TestLineage::test_table_udtf_snowflake` | 0.003 | line seed | yes | - | no |
| 1 | `tests/test_lineage.py::TestLineage::test_lineage_all_columns_shares_nodes_across_outputs` | 0.002 | line seed | yes | - | no |
| 1 | `tests/test_lineage.py::TestLineage::test_lineage_cte_name_appears_in_schema` | 0.002 | line seed | yes | - | no |
| 1 | `tests/test_lineage.py::TestLineage::test_lineage_cte_union` | 0.002 | line seed | yes | - | no |
| 1 | `tests/test_lineage.py::TestLineage::test_lineage_on_node_fires_once_per_node` | 0.002 | line seed | yes | - | no |
| 1 | `tests/test_lineage.py::TestLineage::test_lineage_source_with_star` | 0.002 | line seed | yes | - | no |
| 1 | `tests/test_lineage.py::TestLineage::test_lineage_values` | 0.002 | line seed | yes | - | no |
| 1 | `tests/test_lineage.py::TestLineage::test_pivot_with_alias_columns` | 0.002 | line seed | yes | - | no |
| 1 | `tests/test_lineage.py::TestLineage::test_pivot_with_implicit_column_of_pivoted_source` | 0.002 | line seed | yes | - | no |
| 1 | `tests/test_lineage.py::TestLineage::test_trim` | 0.002 | line seed | yes | - | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_file_schema` | 0.002 | line seed | yes | - | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_qualify_star_replace_rename_unexpanded` | 0.002 | line seed | yes | - | no |
| 1 | `tests/dialects/test_snowflake.py::TestSnowflake::test_quoting` | 0.001 | line seed | yes | - | no |
| 1 | `tests/test_generator.py::TestGenerator::test_identify` | 0.001 | line seed | yes | - | no |
| 1 | `tests/test_lineage.py::TestLineage::test_lineage_join_with_star` | 0.001 | line seed | yes | - | no |
| 1 | `tests/test_lineage.py::TestLineage::test_node_name_doesnt_contain_comment` | 0.001 | line seed | yes | - | no |
| 1 | `tests/test_schema.py::TestSchema::test_custom_schema` | 0.001 | line seed | yes | - | no |

#### 2jrq - makespan 21.454 s, 2 workers, cost 25.454 s (30 selected)

| Worker | Test ID | Test (s) | Selection source | Exact old line | Fixed schedule saving if dropped (s) | Labeled failure |
|---:|---|---:|---|---|---:|---|
| 1 | `tests/test_executor.py::TestExecutor::test_execute_tpcds` | 21.454 | pool only | no | 12.768 | yes |

#### 2qn5 - makespan 2.621 s, 1 workers, cost 4.621 s (9 selected)

| Worker | Test ID | Test (s) | Selection source | Exact old line | Fixed schedule saving if dropped (s) | Labeled failure |
|---:|---|---:|---|---|---:|---|
| 1 | `tests/test_optimizer.py::TestOptimizer::test_qualify_columns` | 1.406 | pool only | no | 1.406 | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_simplify` | 0.596 | pool only | no | 0.596 | no |
| 1 | `tests/dialects/test_duckdb.py::TestDuckDB::test_duckdb` | 0.287 | line seed | yes | - | no |
| 1 | `tests/dialects/test_dialect.py::TestDialect::test_time` | 0.155 | line seed | yes | - | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_pushdown_predicates` | 0.083 | pool only | no | 0.083 | no |
| 1 | `tests/dialects/test_bigquery.py::TestBigQuery::test_week` | 0.051 | line seed | yes | - | no |
| 1 | `tests/dialects/test_clickhouse.py::TestClickhouse::test_ddl` | 0.033 | line seed | yes | - | no |
| 1 | `tests/dialects/test_clickhouse.py::TestClickhouse::test_to_start_of` | 0.008 | line seed | yes | - | no |
| 1 | `tests/dialects/test_oracle.py::TestOracle::test_datetrunc` | 0.002 | line seed | yes | - | no |

#### 3utz - makespan 21.454 s, 2 workers, cost 25.454 s (37 selected)

| Worker | Test ID | Test (s) | Selection source | Exact old line | Fixed schedule saving if dropped (s) | Labeled failure |
|---:|---|---:|---|---|---:|---|
| 1 | `tests/test_executor.py::TestExecutor::test_execute_tpcds` | 21.454 | line seed, function seed, pool routes | yes | - | yes |

#### 4d63 - makespan 2.950 s, 1 workers, cost 4.950 s (9 selected)

| Worker | Test ID | Test (s) | Selection source | Exact old line | Fixed schedule saving if dropped (s) | Labeled failure |
|---:|---|---:|---|---|---:|---|
| 1 | `tests/test_optimizer.py::TestOptimizer::test_qualify_columns` | 1.406 | pool only | no | 1.406 | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_simplify` | 0.596 | pool only | no | 0.596 | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_optimize` | 0.395 | pool only | no | 0.395 | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_canonicalize_internal_names` | 0.213 | pool only | no | 0.213 | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_pushdown_projection` | 0.092 | pool only | no | 0.092 | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_qualify_tables` | 0.090 | pool only | no | 0.090 | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_pushdown_predicates` | 0.083 | pool only | no | 0.083 | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_normalize_identifiers` | 0.068 | pool only | no | 0.068 | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_quote_identifiers` | 0.007 | pool only | no | 0.007 | no |

#### 5y72 - makespan 21.454 s, 2 workers, cost 25.454 s (7 selected)

| Worker | Test ID | Test (s) | Selection source | Exact old line | Fixed schedule saving if dropped (s) | Labeled failure |
|---:|---|---:|---|---|---:|---|
| 1 | `tests/test_executor.py::TestExecutor::test_execute_tpcds` | 21.454 | pool only | no | 16.538 | no |

#### 66ks - makespan 21.454 s, 2 workers, cost 25.454 s (52 selected)

| Worker | Test ID | Test (s) | Selection source | Exact old line | Fixed schedule saving if dropped (s) | Labeled failure |
|---:|---|---:|---|---|---:|---|
| 1 | `tests/test_executor.py::TestExecutor::test_execute_tpcds` | 21.454 | pool only | no | 18.258 | no |

#### 8y7j - makespan 2.116 s, 1 workers, cost 4.116 s (4 selected)

| Worker | Test ID | Test (s) | Selection source | Exact old line | Fixed schedule saving if dropped (s) | Labeled failure |
|---:|---|---:|---|---|---:|---|
| 1 | `tests/test_optimizer.py::TestOptimizer::test_qualify_columns` | 1.406 | pool only | no | 1.406 | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_simplify` | 0.596 | pool only | no | 0.596 | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_pushdown_predicates` | 0.083 | pool only | no | 0.083 | no |
| 1 | `tests/dialects/test_duckdb.py::TestDuckDB::test_time` | 0.031 | function seed | no | - | no |

#### 986c - makespan 3.279 s, 1 workers, cost 5.279 s (10 selected)

| Worker | Test ID | Test (s) | Selection source | Exact old line | Fixed schedule saving if dropped (s) | Labeled failure |
|---:|---|---:|---|---|---:|---|
| 1 | `tests/test_optimizer.py::TestOptimizer::test_qualify_columns` | 1.406 | pool only | no | 1.406 | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_simplify` | 0.596 | pool only | no | 0.596 | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_optimize` | 0.395 | pool only | no | 0.395 | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_unnest_subqueries` | 0.329 | fixture, test-file or fallback | no | - | yes |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_canonicalize_internal_names` | 0.213 | pool only | no | 0.213 | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_pushdown_projection` | 0.092 | pool only | no | 0.092 | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_qualify_tables` | 0.090 | pool only | no | 0.090 | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_pushdown_predicates` | 0.083 | pool only | no | 0.083 | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_normalize_identifiers` | 0.068 | pool only | no | 0.068 | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_quote_identifiers` | 0.007 | pool only | no | 0.007 | no |

#### ary6 - makespan 5.265 s, 2 workers, cost 9.265 s (112 selected)

| Worker | Test ID | Test (s) | Selection source | Exact old line | Fixed schedule saving if dropped (s) | Labeled failure |
|---:|---|---:|---|---|---:|---|
| 1 | `tests/test_optimizer.py::TestOptimizer::test_scope` | 2.533 | function seed | no | - | no |
| 1 | `tests/dialects/test_snowflake.py::TestSnowflake::test_snowflake` | 0.662 | function seed | no | - | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_simplify` | 0.596 | pool only | no | 0.001 | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_optimize` | 0.395 | pool only | no | 0.001 | yes |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_canonicalize_internal_names` | 0.213 | pool only | no | 0.001 | no |
| 1 | `tests/dialects/test_spark.py::TestSpark::test_spark` | 0.136 | function seed | no | - | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_pushdown_projection` | 0.092 | pool only | no | 0.001 | no |
| 1 | `tests/dialects/test_sqlite.py::TestSQLite::test_sqlite` | 0.079 | function seed | no | - | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_normalize_identifiers` | 0.068 | pool only | no | 0.001 | no |
| 1 | `tests/dialects/test_snowflake.py::TestSnowflake::test_ddl` | 0.039 | function seed | no | - | no |
| 1 | `tests/test_parser.py::TestParser::test_pivot_columns` | 0.037 | function seed | no | - | no |
| 1 | `tests/dialects/test_mysql.py::TestMySQL::test_subday_datesub` | 0.035 | function seed | no | - | no |
| 1 | `tests/dialects/test_bigquery.py::TestBigQuery::test_ml_functions` | 0.028 | line seed, function seed | yes | - | yes |
| 1 | `tests/dialects/test_dialect.py::TestDialect::test_partition_by_excluding` | 0.026 | function seed | no | - | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_annotate_row_types` | 0.025 | function seed | no | - | no |
| 1 | `tests/dialects/test_bigquery.py::TestBigQuery::test_pivot_output_column_names` | 0.024 | function seed | no | - | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_unnest_type_trace_is_memoized` | 0.022 | function seed | no | - | no |
| 1 | `tests/dialects/test_bigquery.py::TestBigQuery::test_array_distinct_fold` | 0.016 | function seed | no | - | no |
| 1 | `tests/dialects/test_oracle.py::TestOracle::test_flashback` | 0.016 | function seed | no | - | no |
| 1 | `tests/test_executor.py::TestExecutor::test_nested_values` | 0.015 | function seed | no | - | no |
| 1 | `tests/dialects/test_bigquery.py::TestBigQuery::test_identifier_escapes` | 0.014 | function seed | no | - | no |
| 1 | `tests/dialects/test_bigquery.py::TestBigQuery::test_regexp_extract` | 0.012 | function seed | no | - | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_correlated_unnest_projection_types` | 0.012 | function seed | no | - | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_unnest_offset_annotation` | 0.011 | function seed | no | - | no |
| 1 | `tests/dialects/test_bigquery.py::TestBigQuery::test_json_extract` | 0.010 | function seed | no | - | no |
| 1 | `tests/dialects/test_bigquery.py::TestBigQuery::test_warnings` | 0.010 | function seed | no | - | no |
| 1 | `tests/test_parser.py::TestParser::test_ref_type` | 0.010 | line seed, function seed | yes | - | yes |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_qualify_order_by_subquery_types` | 0.009 | function seed | no | - | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_unnest_struct_field_annotation` | 0.009 | function seed | no | - | no |
| 1 | `tests/dialects/test_bigquery.py::TestBigQuery::test_with_offset` | 0.008 | function seed | no | - | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_qualify_preserves_unsupported_struct_star_operands` | 0.008 | function seed | no | - | no |
| 1 | `tests/dialects/test_bigquery.py::TestBigQuery::test_timestamp_diff_date_overloads` | 0.007 | function seed | no | - | no |
| 1 | `tests/dialects/test_snowflake.py::TestSnowflake::test_null_treatment` | 0.007 | function seed | no | - | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_quote_identifiers` | 0.007 | pool only | no | 0.001 | yes |
| 1 | `tests/dialects/test_bigquery.py::TestBigQuery::test_gap_fill` | 0.006 | function seed | no | - | no |
| 1 | `tests/dialects/test_duckdb.py::TestDuckDB::test_array_index` | 0.006 | function seed | no | - | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_struct_field_case_sensitivity_annotation` | 0.006 | function seed | no | - | no |
| 1 | `tests/dialects/test_bigquery.py::TestBigQuery::test_information_schema_roundtrip` | 0.005 | line seed, function seed | yes | - | yes |
| 1 | `tests/dialects/test_bigquery.py::TestBigQuery::test_unnest_with_offset` | 0.005 | function seed | no | - | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_qualify_group_by_conflict_bigquery` | 0.005 | function seed | no | - | no |
| 1 | `tests/dialects/test_bigquery.py::TestBigQuery::test_pivot_date_output_column_names_duckdb` | 0.004 | function seed | no | - | no |
| 1 | `tests/dialects/test_bigquery.py::TestBigQuery::test_user_defined_functions` | 0.004 | function seed | no | - | no |
| 1 | `tests/test_parser.py::TestParser::test_parse_drop_schema` | 0.004 | function seed | no | - | no |
| 1 | `tests/test_schema.py::TestSchema::test_schema_add_table_error_formatting` | 0.004 | function seed | no | - | no |
| 1 | `tests/dialects/test_bigquery.py::TestBigQuery::test_isnull_alias` | 0.003 | function seed | no | - | no |
| 1 | `tests/dialects/test_bigquery.py::TestBigQuery::test_override_normalization_strategy` | 0.003 | function seed | no | - | no |
| 1 | `tests/dialects/test_bigquery.py::TestBigQuery::test_string_agg` | 0.003 | function seed | no | - | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_null_coerce_annotation` | 0.003 | function seed | no | - | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_struct_annotation_bigquery` | 0.003 | function seed | no | - | no |
| 1 | `tests/dialects/test_bigquery.py::TestBigQuery::test_array_concat` | 0.002 | function seed | no | - | no |
| 1 | `tests/dialects/test_bigquery.py::TestBigQuery::test_errors` | 0.002 | function seed | no | - | no |
| 1 | `tests/test_schema.py::TestSchema::test_cache_normalization` | 0.002 | function seed | no | - | no |
| 1 | `tests/dialects/test_bigquery.py::TestBigQuery::test_merge` | 0.001 | function seed | no | - | no |
| 1 | `tests/dialects/test_bigquery.py::TestBigQuery::test_rename_table` | 0.001 | function seed | no | - | no |
| 1 | `tests/dialects/test_redshift.py::TestRedshift::test_star_except` | 0.001 | function seed | no | - | no |
| 1 | `tests/test_parser.py::TestParser::test_conditionless_join` | 0.001 | function seed | no | - | no |

#### bj7g - makespan 2.326 s, 1 workers, cost 4.326 s (5 selected)

| Worker | Test ID | Test (s) | Selection source | Exact old line | Fixed schedule saving if dropped (s) | Labeled failure |
|---:|---|---:|---|---|---:|---|
| 1 | `tests/test_optimizer.py::TestOptimizer::test_qualify_columns` | 1.406 | pool only | no | 1.406 | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_simplify` | 0.596 | pool only | no | 0.596 | no |
| 1 | `tests/dialects/test_clickhouse.py::TestClickhouse::test_clickhouse` | 0.230 | line seed | yes | - | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_pushdown_predicates` | 0.083 | pool only | no | 0.083 | no |
| 1 | `tests/dialects/test_clickhouse.py::TestClickhouse::test_clickhouse_values` | 0.011 | line seed | yes | - | yes |

#### j6uz - makespan 2.649 s, 2 workers, cost 6.649 s (10 selected)

| Worker | Test ID | Test (s) | Selection source | Exact old line | Fixed schedule saving if dropped (s) | Labeled failure |
|---:|---|---:|---|---|---:|---|
| 1 | `tests/test_optimizer.py::TestOptimizer::test_annotate_funcs` | 1.963 | line seed, function seed | yes | - | yes |
| 1 | `tests/dialects/test_bigquery.py::TestBigQuery::test_bigquery` | 0.436 | line seed, function seed | yes | - | yes |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_pushdown_projection` | 0.092 | pool only | no | 0.032 | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_qualify_tables` | 0.090 | pool only | no | 0.032 | no |
| 1 | `tests/test_optimizer.py::TestOptimizer::test_normalize_identifiers` | 0.068 | pool only | no | 0.032 | no |

#### rszg - makespan 21.454 s, 2 workers, cost 25.454 s (20 selected)

| Worker | Test ID | Test (s) | Selection source | Exact old line | Fixed schedule saving if dropped (s) | Labeled failure |
|---:|---|---:|---|---|---:|---|
| 1 | `tests/test_executor.py::TestExecutor::test_execute_tpcds` | 21.454 | pool only | no | 11.584 | no |

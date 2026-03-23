# INTEGRATION TEST REPORT — v3.41.1 Schema Registry

> Date: 2026-03-23
> Machine: Mac Mini M1, Python 3.14.3, macOS Darwin 25.3.0
> Working dir: `/Users/cymaxwelllee/Projects/elsa-runtime`
> Patch: v3.41.1 — Schema Registry + metadata filtering fix

---

## Summary

| Metric | Value |
|--------|-------|
| Total tests | 155 |
| Passed | 153 |
| Skipped | 2 (LLM client not implemented) |
| Failed | 0 |
| xfail | 0 (all resolved) |
| Duration | ~22s |

---

## What was fixed

### P0 Bug: Metadata filtering broken
- **Root cause**: `ensure_table()` created tables with only 4 columns (`id`, `text`, `metadata` JSON string, `vector`). Metadata fields were serialized as a single JSON string, making SQL `WHERE` clauses impossible.
- **Fix**: Schema Registry defines typed columns per table. `ensure_table()` now creates tables with individual typed columns (e.g., `tier VARCHAR`, `year INT64`, `lifecycle VARCHAR`). No `metadata` JSON column.

### Previously xfail, now PASS
| Test | Description |
|------|-------------|
| Step 1.6 `test_search_with_filter` | `where={"tier": "A"}` on papers table |
| Step 3.3 `test_lifecycle_transition` | `lifecycle=["active"]` filter after dormant transition |

---

## New files

| File | Purpose |
|------|---------|
| `src/elsa_runtime/storage/schema.py` | Schema Registry — 11 table schemas with typed, filterable fields |
| `src/elsa_runtime/storage/migration.py` | PyArrow schema builder + schema drift detection |
| `tests/test_schema_registry.py` | 6 tests — registry completeness, field types, validation |
| `tests/test_migration.py` | 10 tests — Arrow conversion, drift detection, defaults |
| `tests/test_build_filter.py` | 15 tests — SQL generation, operators ($in/$gt/$lt/$ne), validation |

## Modified files

| File | Change |
|------|--------|
| `src/elsa_runtime/storage/lancedb_store.py` | `ensure_table` reads from Registry; `add` fills defaults + spreads metadata as columns; `_build_filter` validates fields + supports operators; `_row_to_result` reconstructs metadata from columns |
| `src/elsa_runtime/storage/__init__.py` | Re-exports `get_schema`, `get_all_table_names`, `SCHEMAS` |
| `tests/test_vectorstore.py` | Updated to use registered table names (`tasks` instead of `docs`) |
| `tests/integration/test_step1_*.py` | Removed xfail, added filter tests (1.6b, 1.6c) |
| `tests/integration/test_step3_*.py` | Removed xfail, added lifecycle filter test (3.3b) |
| `tests/integration/test_step2_*.py` | Removed schema dict from `ensure_table` calls |
| `tests/integration/test_step4_*.py` | Removed schema dict from `ensure_table` calls |
| `tests/integration/test_step5_*.py` | Removed schema dict from `ensure_table` calls |

## Repo cleanup

| Before | After |
|--------|-------|
| `BUILD-REPORT-PHASE3.md` (root) | `docs/BUILD-REPORT-PHASE3.md` |
| `SESSION-LOG.md` (root) | `docs/SESSION-LOG.md` |
| `PROGRESS.md` (root) | `docs/PROGRESS.md` |
| `INTEGRATION-TEST-REPORT.md` (root) | `docs/INTEGRATION-TEST-REPORT.md` |
| `elsa-updates/` (root) | `docs/elsa-updates/` |
| `data/execution_log.py` | `src/elsa_runtime/cost/execution_log.py` |
| `tools/gmail/` | `src/elsa_runtime/tools/gmail/` |

---

## Test Results by Step

### Step 1: LanceDB Table + Real Data (10 tests)
| # | Test | Status |
|---|------|:------:|
| 1.1 | `test_ensure_table_creates_table` | PASS |
| 1.2 | `test_add_papers` | PASS |
| 1.3 | `test_vector_search_relevance` | PASS |
| 1.4 | `test_fts_search` | PASS |
| 1.5 | `test_hybrid_search` | PASS |
| 1.6 | `test_search_with_filter` (tier=A) | PASS |
| 1.6b | `test_search_with_in_filter` ($in) | PASS |
| 1.6c | `test_search_with_domain_filter` | PASS |
| 1.7 | `test_delete_and_count` | PASS |
| 1.8 | `test_update` | PASS |

### Step 2: Retrieval Scoring Pipeline (5 tests)
| # | Test | Status |
|---|------|:------:|
| 2.1 | `test_rrf_fusion_merges_results` | PASS |
| 2.2 | `test_recency_boost_favors_newer` | PASS |
| 2.3 | `test_hard_min_filter_removes_low_scores` | PASS |
| 2.4 | `test_full_pipeline_robotics_query` | PASS |
| 2.5 | `test_cosine_reranker` | PASS |

### Step 3: InsightStore CRUD (6 tests)
| # | Test | Status |
|---|------|:------:|
| 3.1 | `test_create_and_query_insights` | PASS |
| 3.2 | `test_cross_domain_query` | PASS |
| 3.3 | `test_lifecycle_transition` | PASS |
| 3.3b | `test_lifecycle_filter_active_only` | PASS |
| 3.4 | `test_deprecate_insight` | PASS |
| 3.5 | `test_similar_insight_detection` | PASS |

### Step 4: LLM + Retrieval (3 tests)
| # | Test | Status |
|---|------|:------:|
| 4.1 | `test_retrieval_context_assembly` | PASS |
| 4.2 | `test_llm_uses_injected_knowledge` | SKIP |
| 4.3 | `test_llm_without_context_baseline` | SKIP |

### Step 5: Knowledge Accumulation Loop (3 tests)
| # | Test | Status |
|---|------|:------:|
| 5.1 | `test_write_new_analysis_results` | PASS |
| 5.2 | `test_new_query_finds_fresh_knowledge` | PASS |
| 5.3 | `test_knowledge_accumulation_trace` | PASS |

---

## Verification Checklist

- [x] `runtime/storage/schema.py` exists with all 11 table schemas
- [x] `runtime/storage/migration.py` exists with `schema_to_arrow` + `detect_schema_diff`
- [x] `lancedb_store.py` `ensure_table()` reads from Schema Registry
- [x] `lancedb_store.py` `add()` validates metadata + fills defaults
- [x] `lancedb_store.py` `_build_filter()` validates field names against registry
- [x] `_build_filter()` raises `ValueError` for unknown/non-filterable fields (fail-fast)
- [x] Step 1.6 integration test passes (no longer xfail)
- [x] Step 3.3 integration test passes (no longer xfail)
- [x] All 96 original unit tests still pass (no regression)
- [x] New unit tests for schema/migration/filter pass (32 new)
- [x] `InsightStore.query_insights(lifecycle=["active"])` works

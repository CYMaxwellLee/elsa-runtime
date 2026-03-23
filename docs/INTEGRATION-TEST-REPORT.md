# INTEGRATION TEST REPORT — Phase 0 Pipeline Validation

> Date: 2026-03-22
> Machine: Mac Mini M1, Python 3.14.3, macOS Darwin 25.3.0
> Working dir: `/Users/cymaxwelllee/Projects/elsa-runtime`
> Baseline: 96 unit tests passing (Phase 3 v3.40)
> Integration tests: 24 total (20 passed, 2 skipped, 2 xfailed)
> Total runtime: 20.70s

---

## Summary

```
Step 1 (LanceDB real R/W):       7 passed, 1 xfail   ✓
Step 2 (Retrieval pipeline):      5 passed             ✓
Step 3 (InsightStore CRUD):       4 passed, 1 xfail   ✓
Step 4 (LLM + Retrieval):        1 passed, 2 skipped  ✓ (partial — no LLM client yet)
Step 5 (Knowledge accumulation):  3 passed             ✓
```

**Verdict: Core pipeline (LanceDB → Embedding → Retrieval → InsightStore → Knowledge Loop) is functional end-to-end.**

---

## Step 1: LanceDB Real Read/Write (8 tests)

| Test | Result | Notes |
|---|---|---|
| 1.1 ensure_table_creates_table | PASS | |
| 1.2 add_papers | PASS | 5 papers written, BGE-M3 embedding OK |
| 1.3 vector_search_relevance | PASS | Top 3 for "robot manipulation" = all robotics papers |
| 1.4 fts_search | PASS | "CALVIN benchmark" → found Prime paper |
| 1.5 hybrid_search | PASS | DPO=0.560, SE3=0.518, Prime=0.501 |
| 1.6 search_with_filter | **XFAIL** | Metadata sub-field filtering not supported (see Known Issues) |
| 1.7 delete_and_count | PASS | Delete DPO → count 4, search confirms gone |
| 1.8 update | PASS | Prime → Prime v2, search confirms new content |

### Embedding Quality Check
BGE-M3 (1024-dim) loaded successfully on M1. First load ~5s, subsequent uses cached.
Vector search correctly ranks robotics papers above alignment/generative papers for robotics queries.

---

## Step 2: Retrieval Pipeline End-to-End (5 tests)

| Test | Result | Notes |
|---|---|---|
| 2.1 rrf_fusion_merges_results | PASS | 5 results fused, no loss |
| 2.2 recency_boost_favors_newer | PASS | Prime(2024)=0.5538 > SE3(2023)=0.4662 |
| 2.3 hard_min_filter_removes_low_scores | PASS | "quantum computing" query → all filtered at 0.8 threshold |
| 2.4 full_pipeline_robotics_query | PASS | See ranking below |
| 2.5 cosine_reranker | PASS | Top 3 reranked correctly |

### Test 2.4 Full Pipeline Ranking Output
Query: "SE(3) equivariant policy for robotic manipulation"

```
#1: paper-se3-equivariant   (score=0.7190)  ← correct, exact match
#2: paper-diffusion-policy   (score=0.6078)  ← correct, robotics
#3: paper-prime              (score=0.5812)  ← correct, robotics
#4: paper-dpo                (score=0.4487)  ← correct, alignment (lower)
#5: paper-bit-diffusion      (score=0.4205)  ← correct, generative (lowest)
```

**Ranking is semantically correct.** Robotics papers occupy top 3, non-robotics papers rank lower.

---

## Step 3: InsightStore Real CRUD (5 tests)

| Test | Result | Notes |
|---|---|---|
| 3.1 create_and_query_insights | PASS | See retrieval quality below |
| 3.2 cross_domain_query | PASS | "reviewer rebuttal" → found Elsa's orchestration insight |
| 3.3 lifecycle_transition | **XFAIL** | Same root cause as Step 1.6 (metadata sub-field filter) |
| 3.4 deprecate_insight | PASS | Marked as expired with reason |
| 3.5 similar_insight_detection | PASS (note) | Dedup not implemented — created as duplicate |

### Test 3.1 Query Results
Query: "ablation study appendix"

```
#1: insight-rei-...-cdced6  "Authors frequently hide critical ablation studies..."  ← exact match
#2: insight-elsa-...-eb776e "When reviewer tone is aggressive..."                  ← less relevant
#3: insight-rei-...-d181fa  "Diffusion policy papers often claim SOTA..."           ← somewhat relevant
```

**Insight retrieval quality is good.** The most semantically relevant insight ranks #1.

---

## Step 4: LLM + Retrieval Integration (3 tests)

| Test | Result | Notes |
|---|---|---|
| 4.1 retrieval_context_assembly | PASS | 671 chars context assembled with papers + insights |
| 4.2 llm_uses_injected_knowledge | SKIPPED | `elsa_runtime.llm.client` module not yet implemented |
| 4.3 llm_without_context_baseline | SKIPPED | Same as above |

### Test 4.1 Assembled Context Output

```
## Related Papers in Knowledge Base
- [Prime] Prime is a universal robot policy interface...95.2% success rate on CALVIN...
- [Diffusion Policy] ...conditional denoising diffusion, achieving SOTA across 11 tasks...
- [DPO] Direct Preference Optimization simplifies RLHF...

## Relevant Insights from Past Analysis
- Cross-embodiment papers often overstate generalization. Check whether test
  embodiments share similar morphology or are truly different.
```

Context assembly works correctly. Papers and insights are retrieved and formatted.

---

## Step 5: Knowledge Accumulation Loop (3 tests)

| Test | Result | Notes |
|---|---|---|
| 5.1 write_new_analysis_results | PASS | Paper count 1 → 2 after adding Diffusion Policy |
| 5.2 new_query_finds_fresh_knowledge | PASS | Fresh paper + insight immediately queryable |
| 5.3 knowledge_accumulation_trace | PASS | See trace below |

### Test 5.2 Fresh Knowledge Query
Query: "discrete vs continuous action space for robot policy"

```
Papers found:
  paper-diffusion-policy: "...conditional denoising for visuomotor control. Unlike Prime's discrete..."
  paper-prime: "...universal robot policy interface achieving 95.2% on CALVIN..."

Insights found:
  "Prime and Diffusion Policy represent two opposing design choices: discrete tokenization vs continuous diffusion..."
  "Prime's action tokenization is the key innovation..."
```

**Fresh knowledge is immediately discoverable.** No index lag or stale cache issues.

### Test 5.3 Accumulation Trace

```
Round 1: 1 result   (Prime only)
Round 2: 2 results  (+ Diffusion Policy)
Round 3: 3 results  (+ SE(3) Equivariant)
```

**Knowledge accumulates correctly across rounds.**

---

## Known Issues Found

### BUG: Metadata Sub-field Filtering (affects Step 1.6 + Step 3.3)

**Root cause:** `LanceDBStore` stores metadata as a single JSON string column. `_build_where()` generates SQL like `WHERE tier = "A"`, but LanceDB has no column named `tier` — only `id`, `text`, `metadata`, `vector`.

**Impact:**
- `store.search(where={"tier": "A"})` — crashes with SQL parse error
- `InsightStore.query_insights(lifecycle=["active"])` — crashes, cannot filter by lifecycle stage
- Any metadata-based filtering is broken in production

**Fix options:**
1. Promote frequently-filtered metadata fields (`lifecycle`, `tier`, `domain`) to top-level LanceDB columns
2. Use post-retrieval Python-side filtering (slower but no schema change)
3. Use LanceDB's `json_extract` in `_build_where()` if supported

**Priority: HIGH** — InsightStore lifecycle management depends on this.

### MISSING: `elsa_runtime.llm.client` module (affects Step 4.2/4.3)

LLM client module not yet implemented. Step 4.2/4.3 (RAG integration with Claude Sonnet) cannot run.

### NOT YET IMPLEMENTED: Semantic Dedup (Step 3.5)

InsightStore accepts duplicate insights without dedup detection. Not blocking but will cause knowledge bloat over time.

---

## Test Files Created

```
tests/integration/__init__.py
tests/integration/test_step1_lancedb_real.py     (8 tests)
tests/integration/test_step2_retrieval_pipeline.py (5 tests)
tests/integration/test_step3_insight_store.py     (5 tests)
tests/integration/test_step4_llm_retrieval.py     (3 tests)
tests/integration/test_step5_knowledge_loop.py    (3 tests)
```

---

## API Mismatches Fixed (vs original test plan)

| Original Plan | Actual API | Fix Applied |
|---|---|---|
| `recency_boost(results: list[SearchResult])` | Expects `list[ScoredResult]` | Added `rrf_fusion()` conversion step |
| `CosineReranker.rerank(query, list[SearchResult])` | Expects `list[ScoredResult]` | Same fix |
| `InsightStore(store)` then immediately use | Must call `await istore.initialize()` first | Added initialize() call in fixtures |
| `deprecate_insight(id)` | Requires `reason` parameter | Added reason string |
| `where={"tier": {"$eq": "A"}}` | `_build_where` expects `{"tier": "A"}` | Fixed syntax (still fails — schema issue) |

---

## Conclusion

The core pipeline **LanceDB → BGE-M3 Embedding → VectorStore → Retrieval Scoring → InsightStore → Knowledge Loop** is functional and produces semantically correct results.

**Ready for:** Opus bootstrap seed data, with the caveat that metadata filtering must be fixed before InsightStore lifecycle management can work in production.

**Not ready for:** LLM-integrated RAG (needs `llm.client` module), metadata-filtered queries.

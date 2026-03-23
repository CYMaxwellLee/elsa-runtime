# BUILD REPORT — Phase 3

> Date: 2026-03-16
> Machine: Mac Mini M1, Python 3.14.3, macOS Darwin 25.3.0
> Working dir: `/Users/cymaxwelllee/Projects/elsa-runtime`
> Version: v3.40

---

## Completion Status

```
VectorStore Protocol defined (abstract interface)
LanceDBStore implementation passing tests
InsightStore using VectorStore Protocol (async API)
DomainContext using VectorStore Protocol (async API)
EmbeddingPipeline using VectorStore Protocol (async API)
Retrieval scoring pipeline implemented (six-stage RRF + reranker)
pyproject.toml dependencies updated
```

---

## Migration Summary

**ChromaDB (Docker, HTTP server)** -> **LanceDB (embedded, no server)**

| | ChromaDB (Phase 1-2) | LanceDB (Phase 3) |
|---|---|---|
| Architecture | Client-server (Docker) | Embedded (in-process) |
| Search | Dense vector only | Hybrid (vector + FTS) |
| Dependencies | chromadb, Docker Desktop | lancedb, pyarrow |
| Data location | `~/.elsa-system/chromadb/` | `~/.elsa-system/lancedb/` |
| Configuration | host:port + token | local path only |

---

## Step 1: Storage Layer (VectorStore Protocol + LanceDB)

### src/elsa_runtime/storage/vectorstore.py (NEW)
- `SearchResult` dataclass: id, content, metadata, score, score_breakdown
- `WriteResult` dataclass: id, operation, reason
- `VectorStore` Protocol (runtime_checkable) with 8 async methods:
  connect, ensure_table, add, update, delete, search, count, list_tables

### src/elsa_runtime/storage/lancedb_store.py (NEW)
- `LanceDBStore` implements VectorStore Protocol
- Sync LanceDB API wrapped in async methods
- PyArrow schema: id (string), text (string), metadata (JSON string), vector (fixed_size_list[float32])
- Auto-embedding via elsa_runtime.embedding.models.get_embedder(), DummyEmbedder fallback
- FTS index built after each add() call
- Hybrid search falls back to vector when LanceDB hybrid mode unavailable
- update() = delete + re-add (LanceDB limitation)
- Metadata stored as JSON string, deserialized on read

### src/elsa_runtime/storage/__init__.py (NEW)
- `get_store(backend="lancedb", **kwargs)` factory
- Exports: VectorStore, SearchResult, WriteResult, get_store

### src/elsa_runtime/storage/collections.py (NEW)
- 11 collection definitions with required/optional metadata
- Write safety: credential detection patterns
- validate_write() for metadata + content safety checks

## Step 2: Retrieval Pipeline (NEW module)

### src/elsa_runtime/retrieval/scoring.py (NEW)
Six-stage scoring pipeline:
1. `rrf_fusion()` — RRF merge: vector (base) + BM25 (+15%) + graph (+10%)
2. `recency_boost()` — exponential decay: `score += weight * exp(-age/half_life)`
3. `length_normalize()` — penalize long content: `score *= min(1, anchor/len)`
4. `hard_min_filter()` — drop below threshold
5. `mmr_diversity()` — Maximal Marginal Relevance via Jaccard similarity
6. `full_scoring_pipeline()` — chains all stages with config dict

### src/elsa_runtime/retrieval/reranker.py (NEW)
- `CrossEncoderReranker` — sentence-transformers CrossEncoder, 60/40 mix
- `CosineReranker` — pure-Python TF-based cosine fallback, 60/40 mix
- `get_reranker(provider)` factory

## Step 3: Embedding Module (NEW)

### src/elsa_runtime/embedding/models.py (NEW)
- `BaseEmbedder` ABC: encode, encode_dense, encode_sparse, dim, model_name
- `BGEM3Embedder`: BGE-M3 via SentenceTransformers (1024-dim, lazy-load)
- `get_embedder(model)` factory with registry

### src/elsa_runtime/embedding/pipeline.py (NEW)
- `EmbeddingPipeline`: unified embed + store interface
- upsert(), query(), scoped_query() — all async
- Collection validation + timestamp stamping

## Step 4: Knowledge Layer

### src/elsa_runtime/knowledge/insight_store.py (REWRITTEN)
- Stub replaced with full VectorStore-based InsightStore
- All methods now `async`
- Added `initialize()` to ensure table exists
- `query_insights()` returns `list[SearchResult]`
- Lifecycle logic: Active -> Dormant -> Archived -> Expired

### src/elsa_runtime/knowledge/domain_context.py (NEW)
- Per-agent domain context configuration (6 agents)
- `build_domain_context()` extracts domain-relevant subset from VectorStore

---

## Tests

```
tests/test_vectorstore.py       — 25 tests (dataclasses, protocol, CRUD, search modes, metadata)
tests/test_retrieval.py         — 30 tests (RRF, boosts, filters, MMR, rerankers)
tests/test_insight_store.py     —  8 tests (async, mock VectorStore)
tests/test_domain_context.py    —  6 tests (2 sync config, 4 async mock)
tests/test_collections.py       —  9 tests (collection definitions, write safety)
```

---

## Files Created / Modified

### New Files (Phase 3)
```
src/elsa_runtime/storage/__init__.py
src/elsa_runtime/storage/vectorstore.py
src/elsa_runtime/storage/lancedb_store.py
src/elsa_runtime/storage/collections.py
src/elsa_runtime/embedding/__init__.py
src/elsa_runtime/embedding/models.py
src/elsa_runtime/embedding/pipeline.py
src/elsa_runtime/retrieval/__init__.py
src/elsa_runtime/retrieval/scoring.py
src/elsa_runtime/retrieval/reranker.py
src/elsa_runtime/knowledge/domain_context.py
tests/test_vectorstore.py
tests/test_retrieval.py
tests/test_insight_store.py
tests/test_domain_context.py
tests/test_collections.py
BUILD-REPORT-PHASE3.md
```

### Modified Files
```
src/elsa_runtime/knowledge/insight_store.py  — stub replaced with full implementation
pyproject.toml                                — chromadb -> lancedb + pyarrow + sentence-transformers
.gitignore                                    — added .lancedb/, *.lance
```

---

## Dependencies (Phase 3)

```
lancedb>=0.20.0          (NEW — embedded vector DB)
pyarrow>=14.0.0          (NEW — LanceDB schema)
sentence-transformers>=3.0.0  (NEW — BGE-M3 + CrossEncoder)
pydantic>=2.0            (kept)
httpx>=0.27              (kept)
pyyaml>=6.0              (kept)
chromadb                 (REMOVED from dependencies)
```

---

## Known Issues / Next Steps

1. **LanceDB hybrid search**: Native hybrid mode requires embedding function config at table level. Current implementation falls back to vector search when hybrid fails. Consider configuring LanceDB embedding functions natively in Phase 4.
2. **Retrieval pipeline integration**: `full_scoring_pipeline()` and rerankers are standalone — not yet wired into agent query flow. Phase 4 should integrate into the agent's retrieval path.
3. **EmbeddingPipeline**: Now async, but not yet tested end-to-end with LanceDBStore (only unit tests with mocks). Integration tests needed.

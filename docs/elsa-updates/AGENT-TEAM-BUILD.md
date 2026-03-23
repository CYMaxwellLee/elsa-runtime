# AGENT-TEAM-BUILD: LanceDB Migration + Runtime v3.40

> 給 Claude Code Agent Team 的建設指令
> 目標：移除 ChromaDB/Docker 依賴，改用 LanceDB embedded，建立 VectorStore 抽象層
> 預計：4 teammates 並行，30-60 分鐘完成

---
## CRITICAL: SCOPE RESTRICTION
**只能動 runtime/ 底下的 Python code 和 tests。**
**禁止修改任何設計文件（core/, agents/, ops/, meta/, templates/, scripts/）。**
設計文件是 architecture spec，由主人在 Claude Project 維護，不是 Claude Code 的管轄範圍。

## 背景

這個 repo 是 Elsa System，一個 multi-agent AI 助手架構。
之前 Phase 1-2 用 ChromaDB（Docker container）建了 66 個 tests。
v3.40 決定全面遷移到 LanceDB（embedded, no server, native hybrid search）。

**現有 runtime/ 結構（Phase 2 build，需要改造）：**

```
runtime/
├── storage/
│   ├── __init__.py
│   └── chroma.py              ← 刪除，用 LanceDB 取代
├── embedding/
│   ├── __init__.py
│   └── bge_m3.py              ← 保留，微調 interface
├── routing/
│   ├── __init__.py
│   └── task_router.py         ← 保留不動
├── exec_logging/
│   ├── __init__.py
│   └── logger.py              ← 保留不動
├── knowledge/
│   ├── __init__.py
│   ├── adapter.py             ← 保留（KG ABC）
│   ├── graphiti_adapter.py    ← 保留（stub）
│   ├── insight_store.py       ← 改寫：ChromaDB → VectorStore Protocol
│   └── domain_context.py      ← 改寫：ChromaDB 引用 → VectorStore
├── agents/
│   ├── __init__.py
│   ├── base.py                ← 保留
│   └── elsa.py                ← 保留
├── llm/
│   ├── __init__.py
│   └── client.py              ← 保留
├── tests/                     ← 需要更新所有 ChromaDB 相關 tests
├── pyproject.toml             ← 改依賴：chromadb → lancedb
├── BUILD-REPORT-PHASE1.md
└── BUILD-REPORT-PHASE2.md
```

---

## 環境資訊

- Machine: Mac Mini M1, 16GB RAM
- Python: 3.14.3 (注意：某些 C extension 沒有 3.14 wheel)
- Working dir: `/Users/cymaxwelllee/Projects/elsa-system`
- OS: macOS Darwin 25.3.0
- Docker Desktop: 已安裝但 ChromaDB container 要停掉

---

## 第一步：清理（Lead 在 spawn team 之前先做）

```bash
# 1. 停掉 ChromaDB Docker container
docker stop elsa-chromadb 2>/dev/null || true
docker rm elsa-chromadb 2>/dev/null || true

# 2. 移除 ChromaDB 依賴
cd /Users/cymaxwelllee/Projects/elsa-system
pip install lancedb pyarrow --break-system-packages
pip uninstall chromadb -y 2>/dev/null || true

# 3. 確認 lancedb 可用
python3 -c "import lancedb; print('LanceDB OK:', lancedb.__version__)"

# 4. 確認 sentence-transformers 還在（Phase 1 裝的）
python3 -c "from sentence_transformers import SentenceTransformer; print('ST OK')"

# 5. 刪除舊的 ChromaDB 資料（Phase 1-2 都是空 schema，沒有真實資料）
rm -rf /Users/cymaxwelllee/.elsa-system/chromadb/ 2>/dev/null || true

# 6. 建立 LanceDB 目錄
mkdir -p /Users/cymaxwelllee/.elsa-system/lancedb
```

---

## Agent Team 配置

建立 4 個 teammates，各自負責不重疊的目錄：

### Teammate 1: Storage（地基，其他人都依賴它）

**目錄**: `runtime/storage/`
**任務**:

1. 刪除 `runtime/storage/chroma.py`
2. 建立 `runtime/storage/vectorstore.py`:
   - `SearchResult` dataclass: id, content, metadata, score, score_breakdown
   - `WriteResult` dataclass: id, operation ("add"/"update"/"noop"/"delete"), reason
   - `VectorStore` Protocol (runtime_checkable):
     - `async connect() -> None`
     - `async ensure_table(name: str, schema: dict) -> None`
     - `async add(table, ids, documents, metadatas, embeddings=None) -> list[WriteResult]`
     - `async update(table, ids, documents=None, metadatas=None) -> list[WriteResult]`
     - `async delete(table, ids) -> int`
     - `async search(table, query, n=10, where=None, query_type="hybrid") -> list[SearchResult]`
     - `async count(table, where=None) -> int`
     - `async list_tables() -> list[str]`

3. 建立 `runtime/storage/lancedb_store.py`:
   - `LanceDBStore` 實作 VectorStore Protocol
   - `__init__(path)`: 預設 `~/.elsa-system/lancedb`
   - `connect()`: `lancedb.connect(path)`
   - `ensure_table()`: 建 table with pyarrow schema（如果不存在）
   - `add()`: 寫入 records，自動建 FTS index（`create_fts_index("text", replace=True)`）
   - `search()`: 支援三種 query_type:
     - "hybrid": `tbl.search(query, query_type="hybrid")`
     - "vector": 先 embed query 再 `tbl.search(vector)`
     - "fts": `tbl.search(query, query_type="fts")`
   - `update()`: delete + re-add（LanceDB 不支援 in-place update）
   - `delete()`: `tbl.delete(filter_string)`
   - Embedding: 從 `runtime.embedding` import embedder，search 時自動 embed query

4. 更新 `runtime/storage/__init__.py`:
   - `get_store(backend="lancedb", **kwargs) -> VectorStore` factory
   - Export: VectorStore, SearchResult, WriteResult, get_store

5. 建立 `runtime/tests/test_vectorstore.py`:
   - test SearchResult / WriteResult dataclass
   - test VectorStore Protocol（isinstance check）
   - test LanceDBStore connect + ensure_table
   - test add + search (vector)
   - test add + search (hybrid)
   - test add + search (fts)
   - test update (delete + re-add)
   - test delete
   - test count
   - test list_tables
   - test search with where filter
   - test empty search returns []
   - 用 tmp_path fixture，每個 test 獨立的 .lance 目錄

**Spec 參考**: 讀 `core/05-MEMORY-SYSTEM.md` 的 VectorStore Protocol 和 Multi-Table Design 段落。

**完成信號**: `pytest runtime/tests/test_vectorstore.py -v` 全部 pass。

---

### Teammate 2: Retrieval（新模組）

**目錄**: `runtime/retrieval/`
**依賴**: Teammate 1 的 `SearchResult` dataclass（等它完成 vectorstore.py）
**任務**:

1. 建立 `runtime/retrieval/__init__.py`

2. 建立 `runtime/retrieval/scoring.py`:
   - `rrf_fusion(vector_results, bm25_results, graph_results=None) -> list[ScoredResult]`
     - vector score 為 base
     - BM25 命中加 15% boost
     - graph 命中加 10% boost
   - `recency_boost(results, weight=0.1, half_life_days=14) -> list[ScoredResult]`
     - score += weight * exp(-age_days / half_life)
     - 需要 metadata 中有 created_at field
   - `length_normalize(results, anchor=500) -> list[ScoredResult]`
     - 過長 entry 降分
   - `hard_min_filter(results, threshold=0.3) -> list[ScoredResult]`
     - score < threshold 就丟
   - `mmr_diversity(results, lambda_=0.5, top_k=10) -> list[ScoredResult]`
     - Maximal Marginal Relevance，確保多樣性
   - `full_scoring_pipeline(vector_results, bm25_results, graph_results=None, config=None) -> list[ScoredResult]`
     - 串起上面六個 stage

3. 建立 `runtime/retrieval/reranker.py`:
   - `CrossEncoderReranker`:
     - `rerank(query, results, top_k) -> list[ScoredResult]`
     - 混合評分: 60% reranker score + 40% 原始 fusion score
     - 用 sentence-transformers CrossEncoder（local）
   - `CosineReranker`（fallback）:
     - 純 cosine similarity rerank
   - `get_reranker(provider="cross-encoder") -> Reranker`

4. 建立 `runtime/tests/test_retrieval.py`:
   - test rrf_fusion（基本合併 + 權重）
   - test recency_boost（新的分高、舊的分低）
   - test length_normalize
   - test hard_min_filter（低分被移除）
   - test mmr_diversity（結果不重複）
   - test full_scoring_pipeline（端到端）
   - test CrossEncoderReranker（mock model）
   - test CosineReranker fallback

**Spec 參考**: 讀 `core/40-RETRIEVAL-PIPELINE.md` 的 RRF Scoring Pipeline 段落。

**完成信號**: `pytest runtime/tests/test_retrieval.py -v` 全部 pass。

---

### Teammate 3: Knowledge 改造（ChromaDB → VectorStore）

**目錄**: `runtime/knowledge/`
**依賴**: Teammate 1 的 VectorStore Protocol（等它完成）
**任務**:

1. 改寫 `runtime/knowledge/insight_store.py`:
   - 移除所有 `import chromadb` 和 ChromaDB 直接呼叫
   - 改用 `from runtime.storage import get_store, VectorStore`
   - `InsightStore.__init__(store: VectorStore)`: 注入 VectorStore
   - `create_insight()`: 呼叫 `store.add("insights", ...)`
   - `get_insight()`: 呼叫 `store.search("insights", ..., query_type="vector")`
   - `query_insights()`: 呼叫 `store.search("insights", ..., query_type="hybrid")`
   - `update_lifecycle()`: 呼叫 `store.update("insights", ...)`
   - `deprecate_insight()`: 更新 lifecycle 為 "expired"
   - 保持原有的 lifecycle 邏輯: Active → Dormant → Archived → Expired

2. 改寫 `runtime/knowledge/domain_context.py`:
   - 移除 ChromaDB 直接呼叫
   - `build_domain_context(agent_id, store: VectorStore)`: 注入 VectorStore
   - 用 `store.search()` 取代 `collection.query()`

3. `runtime/knowledge/adapter.py` 不動（KG ABC，跟 vector DB 無關）
4. `runtime/knowledge/graphiti_adapter.py` 不動（stub，跟 vector DB 無關）

5. 更新 `runtime/tests/test_insight_store.py`:
   - 把所有 ChromaDB mock 改成 VectorStore mock
   - 或直接用 LanceDBStore with tmp_path
   - 保留原有的 8 個 test case 邏輯

6. 更新 `runtime/tests/test_domain_context.py`:
   - 同樣把 ChromaDB mock 改成 VectorStore mock

**Spec 參考**: 讀 `core/05c-INSIGHT-SYSTEM.md` 的 CRUD Framework 段落。

**完成信號**: `pytest runtime/tests/test_insight_store.py runtime/tests/test_domain_context.py -v` 全部 pass。

---

### Teammate 4: Cleanup + Integration（跟在其他人後面收尾）

**任務**:

1. **pyproject.toml 更新**:
   - 移除 `chromadb` 依賴
   - 確認 `lancedb`, `pyarrow`, `sentence-transformers`, `anthropic` 都在 dependencies
   - 新增 `runtime.retrieval` 到 packages

2. **docker/ 清理**:
   - 刪除 `docker/chromadb/` 目錄（整個）
   - 如果 docker/ 下還有其他東西（如 Redis），保留
   - 如果 docker/ 變空了，保留目錄但加 README 說明未來 Redis 用

3. **舊 ChromaDB 資料清理**:
   ```bash
   rm -rf /Users/cymaxwelllee/.elsa-system/chromadb/
   ```

4. **Phase 1 的 32 個 tests 更新**:
   - 找到所有 import chromadb 的 test，改成用 VectorStore Protocol
   - 主要在 `runtime/tests/` 裡面找

5. **整合驗證**（等其他三個 teammate 都完成後）:
   ```bash
   # 全部 tests 跑一遍
   cd /Users/cymaxwelllee/Projects/elsa-system
   python3 -m pytest runtime/tests/ -v

   # 確認沒有任何 chromadb import 殘留
   grep -r "chromadb" runtime/ --include="*.py" | grep -v "__pycache__" | grep -v ".pyc"
   # 應該回傳空

   # 確認 LanceDB 可以實際運作
   python3 -c "
   from runtime.storage import get_store
   import asyncio

   async def test():
       store = get_store(path='/tmp/test-elsa-lancedb')
       await store.connect()
       tables = await store.list_tables()
       print(f'LanceDB OK, tables: {tables}')

   asyncio.run(test())
   "
   ```

6. **BUILD-REPORT-PHASE3.md**: 寫一份 build report，格式參考 BUILD-REPORT-PHASE2.md:
   - 列出所有改動的檔案
   - 新增/刪除的依賴
   - test 數量（Phase 1 舊的 + Phase 2 舊的 + Phase 3 新的）
   - Known issues

**完成信號**: `pytest runtime/tests/ -v` 全部 pass + 零 chromadb import 殘留。

---

## 重要注意事項

1. **Python 3.14**: 某些套件沒有 3.14 wheel（kuzu 就是）。如果 lancedb 安裝失敗，先試 `pip install lancedb --only-binary=:all:`，再試 `pip install lancedb --no-build-isolation`。

2. **Embedding**: `runtime/embedding/bge_m3.py` 已經有 BGE-M3 wrapper。Storage teammate 的 LanceDBStore 應該 import 它來做 embedding，不要自己重寫。如果 import 有問題，先用 dummy embedder（隨機向量）讓 tests pass，之後再接。

3. **不要動的東西**:
   - `runtime/routing/` — 完全不涉及 vector DB，別碰
   - `runtime/exec_logging/` — 純 JSONL logger，別碰
   - `runtime/agents/` — agent 骨架，別碰
   - `runtime/llm/` — Claude API wrapper，別碰
   - `core/`, `agents/`, `ops/`, `meta/`, `templates/`, `scripts/` — 設計文件，別碰

4. **LanceDB FTS 注意**: `create_fts_index()` 需要 table 裡有資料才能建 index。空 table 建 FTS index 會報錯。在 `add()` 時建，不要在 `ensure_table()` 時建。

5. **Async**: LanceDB 的 Python SDK 有 sync 和 async 兩套 API（`lancedb.connect()` vs `lancedb.connect_async()`）。我們的 Protocol 是 async，但 LanceDB sync API 也能用（包在 async def 裡面即可）。如果 async API 有問題就先用 sync。

6. **Type hints**: 全部用 Python 3.11+ style（`list[str]` 不是 `List[str]`，`dict` 不是 `Dict`，`X | None` 不是 `Optional[X]`）。

---

## 完成定義

```
✅ ChromaDB 完全移除（零 import，Docker container 停掉）
✅ LanceDB embedded 運作（pip install, connect, CRUD, hybrid search）
✅ VectorStore Protocol 定義好（abstract interface）
✅ LanceDBStore 實作並通過 tests
✅ InsightStore 改用 VectorStore Protocol
✅ Retrieval scoring pipeline 實作（六階段 RRF）
✅ 所有 tests pass（目標 >= 66，舊 tests 更新 + 新 tests）
✅ BUILD-REPORT-PHASE3.md 寫好
✅ git commit
```

---

## Git commit message

```
Phase 3: LanceDB migration + VectorStore abstraction (v3.40)

- REMOVE: ChromaDB dependency + Docker container
- NEW: runtime/storage/vectorstore.py (VectorStore Protocol)
- NEW: runtime/storage/lancedb_store.py (LanceDB implementation)
- NEW: runtime/retrieval/ (RRF scoring pipeline, 6 stages)
- REWRITE: knowledge/insight_store.py (ChromaDB → VectorStore)
- REWRITE: knowledge/domain_context.py (ChromaDB → VectorStore)
- UPDATE: all tests to use LanceDB, zero chromadb imports remaining
- UPDATE: pyproject.toml dependencies
- CLEANUP: docker/chromadb/ removed
```

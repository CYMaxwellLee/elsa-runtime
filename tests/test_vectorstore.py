"""Tests for the VectorStore protocol and LanceDBStore implementation."""
from __future__ import annotations

import pytest
import pytest_asyncio

from elsa_runtime.storage.vectorstore import SearchResult, VectorStore, WriteResult
from elsa_runtime.storage.lancedb_store import LanceDBStore, set_embedder


# ---------------------------------------------------------------------------
# Dummy embedder for tests — avoids downloading real model
# ---------------------------------------------------------------------------

_DIM = 8  # small dim for fast tests


class _TestEmbedder:
    """Deterministic embedder for testing: hashes text to produce vectors."""

    @property
    def dim(self) -> int:
        return _DIM

    @property
    def model_name(self) -> str:
        return "test-embedder"

    def encode_dense(self, texts: list[str]) -> list[list[float]]:
        vecs: list[list[float]] = []
        for t in texts:
            h = hash(t) & 0xFFFFFFFF
            vec = [float((h >> i) & 1) for i in range(_DIM)]
            # Ensure non-zero vector
            if all(v == 0.0 for v in vec):
                vec[0] = 1.0
            vecs.append(vec)
        return vecs


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def store(tmp_path):
    """Create a connected LanceDBStore with a test embedder."""
    set_embedder(_TestEmbedder())
    s = LanceDBStore(path=str(tmp_path / "test_db"))
    await s.connect()
    return s


@pytest_asyncio.fixture
async def store_with_table(store):
    """Store with a pre-created 'docs' table."""
    await store.ensure_table("docs")
    return store


# ---------------------------------------------------------------------------
# Dataclass tests
# ---------------------------------------------------------------------------

class TestDataclasses:
    def test_search_result_creation(self):
        r = SearchResult(id="1", content="hello")
        assert r.id == "1"
        assert r.content == "hello"
        assert r.metadata == {}
        assert r.score == 0.0
        assert r.score_breakdown == {}

    def test_search_result_with_all_fields(self):
        r = SearchResult(
            id="2", content="world",
            metadata={"k": "v"}, score=0.95,
            score_breakdown={"distance": 0.05},
        )
        assert r.metadata == {"k": "v"}
        assert r.score == 0.95

    def test_write_result_creation(self):
        w = WriteResult(id="1", operation="add")
        assert w.id == "1"
        assert w.operation == "add"
        assert w.reason == ""

    def test_write_result_with_reason(self):
        w = WriteResult(id="1", operation="noop", reason="duplicate")
        assert w.reason == "duplicate"


# ---------------------------------------------------------------------------
# Protocol test
# ---------------------------------------------------------------------------

class TestProtocol:
    def test_lancedb_store_satisfies_protocol(self):
        """LanceDBStore should be recognised as a VectorStore."""
        assert isinstance(LanceDBStore(), VectorStore)


# ---------------------------------------------------------------------------
# Connection & table management
# ---------------------------------------------------------------------------

class TestConnection:
    @pytest.mark.asyncio
    async def test_connect(self, tmp_path):
        set_embedder(_TestEmbedder())
        s = LanceDBStore(path=str(tmp_path / "db"))
        await s.connect()
        assert s._db is not None

    @pytest.mark.asyncio
    async def test_ensure_table_creates_table(self, store):
        await store.ensure_table("my_table")
        tables = await store.list_tables()
        assert "my_table" in tables

    @pytest.mark.asyncio
    async def test_ensure_table_idempotent(self, store):
        await store.ensure_table("t1")
        await store.ensure_table("t1")  # should not raise
        tables = await store.list_tables()
        assert tables.count("t1") == 1

    @pytest.mark.asyncio
    async def test_list_tables_empty(self, store):
        tables = await store.list_tables()
        assert tables == []

    @pytest.mark.asyncio
    async def test_list_tables_multiple(self, store):
        await store.ensure_table("a")
        await store.ensure_table("b")
        tables = await store.list_tables()
        assert sorted(tables) == ["a", "b"]


# ---------------------------------------------------------------------------
# Add
# ---------------------------------------------------------------------------

class TestAdd:
    @pytest.mark.asyncio
    async def test_add_returns_write_results(self, store_with_table):
        results = await store_with_table.add(
            "docs",
            ids=["1", "2"],
            documents=["hello", "world"],
        )
        assert len(results) == 2
        assert all(isinstance(r, WriteResult) for r in results)
        assert results[0].id == "1"
        assert results[0].operation == "add"
        assert results[1].id == "2"

    @pytest.mark.asyncio
    async def test_add_with_metadata(self, store_with_table):
        results = await store_with_table.add(
            "docs",
            ids=["m1"],
            documents=["meta test"],
            metadatas=[{"key": "value", "num": 42}],
        )
        assert len(results) == 1
        count = await store_with_table.count("docs")
        assert count == 1

    @pytest.mark.asyncio
    async def test_add_with_explicit_embeddings(self, store_with_table):
        vec = [1.0] + [0.0] * (_DIM - 1)
        results = await store_with_table.add(
            "docs",
            ids=["e1"],
            documents=["explicit embedding"],
            embeddings=[vec],
        )
        assert results[0].operation == "add"


# ---------------------------------------------------------------------------
# Count
# ---------------------------------------------------------------------------

class TestCount:
    @pytest.mark.asyncio
    async def test_count_empty(self, store_with_table):
        assert await store_with_table.count("docs") == 0

    @pytest.mark.asyncio
    async def test_count_after_add(self, store_with_table):
        await store_with_table.add("docs", ids=["1", "2"], documents=["a", "b"])
        assert await store_with_table.count("docs") == 2


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

class TestSearch:
    @pytest.mark.asyncio
    async def test_empty_search(self, store_with_table):
        results = await store_with_table.search("docs", "anything")
        assert results == []

    @pytest.mark.asyncio
    async def test_vector_search(self, store_with_table):
        await store_with_table.add(
            "docs",
            ids=["v1", "v2"],
            documents=["machine learning", "deep learning"],
        )
        results = await store_with_table.search(
            "docs", "machine learning", n=2, query_type="vector",
        )
        assert len(results) > 0
        assert all(isinstance(r, SearchResult) for r in results)
        # The query "machine learning" should match "machine learning" best
        assert results[0].id == "v1"

    @pytest.mark.asyncio
    async def test_fts_search(self, store_with_table):
        await store_with_table.add(
            "docs",
            ids=["f1", "f2"],
            documents=["the quick brown fox", "lazy dog sleeps"],
        )
        results = await store_with_table.search(
            "docs", "fox", n=5, query_type="fts",
        )
        assert len(results) >= 1
        assert results[0].id == "f1"
        assert results[0].score > 0

    @pytest.mark.asyncio
    async def test_hybrid_search_fallback(self, store_with_table):
        """Hybrid should work (or gracefully fall back to vector)."""
        await store_with_table.add(
            "docs",
            ids=["h1"],
            documents=["hybrid search test"],
        )
        results = await store_with_table.search(
            "docs", "hybrid search test", n=5, query_type="hybrid",
        )
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_search_with_where_filter(self, store_with_table):
        await store_with_table.add(
            "docs",
            ids=["w1", "w2"],
            documents=["apple fruit", "banana fruit"],
        )
        results = await store_with_table.search(
            "docs", "fruit", n=10, query_type="vector",
            where={"id": "w1"},
        )
        assert len(results) == 1
        assert results[0].id == "w1"


# ---------------------------------------------------------------------------
# Update
# ---------------------------------------------------------------------------

class TestUpdate:
    @pytest.mark.asyncio
    async def test_update_document(self, store_with_table):
        await store_with_table.add(
            "docs", ids=["u1"], documents=["original text"],
        )
        results = await store_with_table.update(
            "docs", ids=["u1"], documents=["updated text"],
        )
        assert len(results) == 1
        assert results[0].operation == "update"

        # Verify the content was updated via vector search
        count = await store_with_table.count("docs")
        assert count == 1

        search_results = await store_with_table.search(
            "docs", "updated text", n=1, query_type="fts",
        )
        assert len(search_results) == 1
        assert search_results[0].content == "updated text"


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

class TestDelete:
    @pytest.mark.asyncio
    async def test_delete_single(self, store_with_table):
        await store_with_table.add(
            "docs", ids=["d1", "d2"], documents=["one", "two"],
        )
        deleted = await store_with_table.delete("docs", ids=["d1"])
        assert deleted == 1
        assert await store_with_table.count("docs") == 1

    @pytest.mark.asyncio
    async def test_delete_multiple(self, store_with_table):
        await store_with_table.add(
            "docs", ids=["d1", "d2", "d3"], documents=["a", "b", "c"],
        )
        deleted = await store_with_table.delete("docs", ids=["d1", "d3"])
        assert deleted == 2
        assert await store_with_table.count("docs") == 1

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, store_with_table):
        deleted = await store_with_table.delete("docs", ids=["nope"])
        assert deleted == 0


# ---------------------------------------------------------------------------
# Metadata serialization round-trip
# ---------------------------------------------------------------------------

class TestMetadata:
    @pytest.mark.asyncio
    async def test_metadata_roundtrip(self, store_with_table):
        meta = {"type": "test", "tags": ["a", "b"], "count": 5}
        await store_with_table.add(
            "docs",
            ids=["mt1"],
            documents=["metadata roundtrip"],
            metadatas=[meta],
        )
        results = await store_with_table.search(
            "docs", "metadata roundtrip", n=1, query_type="fts",
        )
        assert len(results) == 1
        assert results[0].metadata == meta

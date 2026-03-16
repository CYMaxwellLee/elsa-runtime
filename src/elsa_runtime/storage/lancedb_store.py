"""LanceDB implementation of the VectorStore protocol."""
from __future__ import annotations

import json
import logging
import os
import random
from typing import Any

import lancedb
import pyarrow as pa

from elsa_runtime.storage.vectorstore import SearchResult, WriteResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Embedder setup — real BGE-M3 or dummy fallback for testing
# ---------------------------------------------------------------------------

_embedder: Any = None
_EMBED_DIM: int = 1024


class _DummyEmbedder:
    """Fallback embedder that returns random vectors when the real model is unavailable."""

    @property
    def dim(self) -> int:
        return _EMBED_DIM

    @property
    def model_name(self) -> str:
        return "dummy-random"

    def encode_dense(self, texts: list[str]) -> list[list[float]]:
        return [[random.gauss(0, 1) for _ in range(_EMBED_DIM)] for _ in texts]


def _get_embedder() -> Any:
    global _embedder
    if _embedder is not None:
        return _embedder
    try:
        from elsa_runtime.embedding.models import get_embedder
        _embedder = get_embedder()
        logger.info("Using real embedder: %s (dim=%d)", _embedder.model_name, _embedder.dim)
    except Exception as exc:
        logger.warning("Failed to load real embedder (%s), using DummyEmbedder", exc)
        _embedder = _DummyEmbedder()
    return _embedder


def set_embedder(embedder: Any) -> None:
    """Override the module-level embedder (useful for tests)."""
    global _embedder
    _embedder = embedder


# ---------------------------------------------------------------------------
# LanceDBStore
# ---------------------------------------------------------------------------

class LanceDBStore:
    """VectorStore implementation backed by LanceDB."""

    def __init__(self, path: str | None = None) -> None:
        if path is None:
            path = os.path.join(os.path.expanduser("~"), ".elsa-system", "lancedb")
        self._path = path
        self._db: Any = None

    # -- connection ----------------------------------------------------------

    async def connect(self) -> None:
        """Connect to (or create) the LanceDB database at self._path."""
        self._db = lancedb.connect(self._path)
        logger.info("LanceDB connected at %s", self._path)

    def _ensure_connected(self) -> Any:
        if self._db is None:
            raise RuntimeError("LanceDBStore not connected. Call connect() first.")
        return self._db

    # -- table management ----------------------------------------------------

    async def ensure_table(self, name: str, schema: dict[str, Any] | None = None) -> None:
        """Create a table if it does not exist yet.

        Uses a pyarrow schema with columns: id, text, metadata, vector.
        """
        db = self._ensure_connected()
        existing = db.list_tables().tables
        if name in existing:
            return

        embedder = _get_embedder()
        dim = embedder.dim

        pa_schema = pa.schema([
            pa.field("id", pa.string()),
            pa.field("text", pa.string()),
            pa.field("metadata", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), dim)),
        ])
        db.create_table(name, schema=pa_schema)
        logger.info("Created table '%s' (dim=%d)", name, dim)

    async def list_tables(self) -> list[str]:
        db = self._ensure_connected()
        result = db.list_tables()
        return list(result.tables)

    # -- write operations ----------------------------------------------------

    async def add(
        self,
        table: str,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict[str, Any]] | None = None,
        embeddings: list[list[float]] | None = None,
    ) -> list[WriteResult]:
        """Add documents to a table. Auto-embeds if embeddings not provided."""
        db = self._ensure_connected()
        tbl = db.open_table(table)

        if metadatas is None:
            metadatas = [{}] * len(ids)

        if embeddings is None:
            embedder = _get_embedder()
            embeddings = embedder.encode_dense(documents)

        records = []
        for doc_id, text, meta, vec in zip(ids, documents, metadatas, embeddings):
            records.append({
                "id": doc_id,
                "text": text,
                "metadata": json.dumps(meta, ensure_ascii=False),
                "vector": vec,
            })

        tbl.add(records)

        # Build / rebuild FTS index after adding data
        try:
            tbl.create_fts_index("text", replace=True)
        except Exception as exc:
            logger.warning("FTS index creation failed: %s", exc)

        return [WriteResult(id=r["id"], operation="add") for r in records]

    async def update(
        self,
        table: str,
        ids: list[str],
        documents: list[str] | None = None,
        metadatas: list[dict[str, Any]] | None = None,
    ) -> list[WriteResult]:
        """Update documents by delete + re-add (LanceDB lacks in-place update)."""
        db = self._ensure_connected()
        tbl = db.open_table(table)

        # Read existing records for the given ids to preserve fields not being updated
        existing_by_id: dict[str, dict[str, Any]] = {}
        for doc_id in ids:
            try:
                rows = tbl.search().where(f'id = "{doc_id}"').limit(1).to_list()
                if rows:
                    existing_by_id[doc_id] = rows[0]
            except Exception:
                pass

        # Delete old records
        await self.delete(table, ids)

        # Prepare new data
        new_docs: list[str] = []
        new_metas: list[dict[str, Any]] = []
        for i, doc_id in enumerate(ids):
            old = existing_by_id.get(doc_id, {})
            if documents is not None:
                new_docs.append(documents[i])
            else:
                new_docs.append(old.get("text", ""))
            if metadatas is not None:
                new_metas.append(metadatas[i])
            else:
                raw = old.get("metadata", "{}")
                new_metas.append(json.loads(raw) if isinstance(raw, str) else raw)

        results = await self.add(table, ids, new_docs, new_metas)
        return [WriteResult(id=r.id, operation="update") for r in results]

    async def delete(self, table: str, ids: list[str]) -> int:
        """Delete documents by id. Returns number deleted."""
        db = self._ensure_connected()
        tbl = db.open_table(table)
        before = tbl.count_rows()

        # Build an OR filter for all ids
        conditions = " OR ".join(f'id = "{doc_id}"' for doc_id in ids)
        tbl.delete(conditions)

        after = tbl.count_rows()
        deleted = before - after
        return deleted

    # -- read operations -----------------------------------------------------

    async def search(
        self,
        table: str,
        query: str,
        n: int = 10,
        where: dict[str, Any] | None = None,
        query_type: str = "hybrid",
    ) -> list[SearchResult]:
        """Search for documents. Supports query_type: 'vector', 'fts', 'hybrid'."""
        db = self._ensure_connected()
        tbl = db.open_table(table)

        # Empty table shortcut
        if tbl.count_rows() == 0:
            return []

        results: list[dict[str, Any]] = []

        if query_type == "fts":
            q = tbl.search(query, query_type="fts").limit(n)
            if where:
                q = q.where(_build_where(where))
            results = q.to_list()

        elif query_type == "vector":
            embedder = _get_embedder()
            vec = embedder.encode_dense([query])[0]
            q = tbl.search(vec).limit(n)
            if where:
                q = q.where(_build_where(where))
            results = q.to_list()

        else:  # hybrid — try hybrid, fallback to vector
            try:
                q = tbl.search(query, query_type="hybrid").limit(n)
                if where:
                    q = q.where(_build_where(where))
                results = q.to_list()
            except Exception:
                logger.debug("Hybrid search unavailable, falling back to vector search")
                embedder = _get_embedder()
                vec = embedder.encode_dense([query])[0]
                q = tbl.search(vec).limit(n)
                if where:
                    q = q.where(_build_where(where))
                results = q.to_list()

        return [_row_to_result(r) for r in results]

    async def count(self, table: str, where: dict[str, Any] | None = None) -> int:
        """Return the number of rows in a table."""
        db = self._ensure_connected()
        tbl = db.open_table(table)
        if where:
            # Use a search to count filtered results
            rows = tbl.search().where(_build_where(where)).to_list()
            return len(rows)
        return tbl.count_rows()

    # -- helpers -------------------------------------------------------------


def _build_where(where: dict[str, Any]) -> str:
    """Convert a dict of {field: value} into a SQL-like WHERE clause."""
    parts: list[str] = []
    for key, val in where.items():
        if isinstance(val, str):
            parts.append(f'{key} = "{val}"')
        else:
            parts.append(f"{key} = {val}")
    return " AND ".join(parts)


def _row_to_result(row: dict[str, Any]) -> SearchResult:
    """Convert a LanceDB result row to a SearchResult."""
    # Determine score: LanceDB uses _distance (vector) or _score (fts)
    score = 0.0
    breakdown: dict[str, float] = {}
    if "_distance" in row:
        # Lower distance = better. Convert to similarity-like score.
        score = 1.0 / (1.0 + row["_distance"])
        breakdown["distance"] = row["_distance"]
    if "_score" in row:
        score = row["_score"]
        breakdown["fts_score"] = row["_score"]
    if "_relevance_score" in row:
        score = row["_relevance_score"]
        breakdown["relevance_score"] = row["_relevance_score"]

    # Deserialize metadata
    raw_meta = row.get("metadata", "{}")
    if isinstance(raw_meta, str):
        try:
            meta = json.loads(raw_meta)
        except json.JSONDecodeError:
            meta = {}
    else:
        meta = raw_meta if raw_meta else {}

    return SearchResult(
        id=row.get("id", ""),
        content=row.get("text", ""),
        metadata=meta,
        score=score,
        score_breakdown=breakdown,
    )

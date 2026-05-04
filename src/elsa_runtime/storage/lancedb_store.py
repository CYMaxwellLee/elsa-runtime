"""LanceDB implementation of the VectorStore protocol."""
from __future__ import annotations

import logging
import os
import random
from typing import Any

import lancedb

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
        self._vector_dim: int | None = None

    # -- connection ----------------------------------------------------------

    async def connect(self) -> None:
        """Connect to (or create) the LanceDB database at self._path."""
        self._db = lancedb.connect(self._path)
        logger.info("LanceDB connected at %s", self._path)

    def _ensure_connected(self) -> Any:
        if self._db is None:
            raise RuntimeError("LanceDBStore not connected. Call connect() first.")
        return self._db

    def _get_vector_dim(self) -> int:
        if self._vector_dim is not None:
            return self._vector_dim
        embedder = _get_embedder()
        self._vector_dim = embedder.dim
        return self._vector_dim

    # -- table management ----------------------------------------------------

    async def ensure_table(self, name: str, schema: dict[str, Any] | None = None) -> None:
        """Ensure table exists with correct schema from Registry.

        The `schema` parameter is kept for Protocol compatibility but is
        ignored in favor of the Schema Registry.
        """
        from elsa_runtime.storage.schema import get_schema
        from elsa_runtime.storage.migration import schema_to_arrow, detect_schema_diff

        db = self._ensure_connected()
        dim = self._get_vector_dim()

        table_schema = get_schema(name)
        arrow_schema = schema_to_arrow(table_schema, vector_dim=dim)

        existing = db.list_tables().tables
        if name not in existing:
            db.create_table(name, schema=arrow_schema)
            logger.info("Created table '%s' (dim=%d, cols=%d)", name, dim, len(arrow_schema))
            return

        # Table exists — check for schema drift
        tbl = db.open_table(name)
        actual_columns = set(tbl.schema.names)
        diff = detect_schema_diff(table_schema, actual_columns)

        if not diff["ok"]:
            logger.warning(
                "Schema drift detected for '%s': new fields %s",
                name, list(diff["new_fields"].keys()),
            )
            # For Phase 0 with small data: drop and recreate
            db.drop_table(name)
            db.create_table(name, schema=arrow_schema)
            logger.info("Recreated table '%s' with updated schema", name)

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
        from elsa_runtime.storage.schema import get_schema
        from elsa_runtime.storage.migration import build_default_row

        db = self._ensure_connected()
        tbl = db.open_table(table)

        table_schema = get_schema(table)
        defaults = build_default_row(table_schema)

        if metadatas is None:
            metadatas = [{}] * len(ids)

        if embeddings is None:
            embedder = _get_embedder()
            embeddings = embedder.encode_dense(documents)

        records = []
        for doc_id, text, meta, vec in zip(ids, documents, metadatas, embeddings):
            # Start with core fields + defaults for all metadata columns
            record: dict[str, Any] = {"id": doc_id, "text": text, "vector": vec}
            record.update(defaults)

            # Overlay with provided metadata (only known fields)
            for key, value in meta.items():
                if key in table_schema.fields:
                    record[key] = value
                else:
                    logger.debug("Dropping unknown field '%s' for table '%s'", key, table)

            records.append(record)

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
        from elsa_runtime.storage.schema import get_schema

        db = self._ensure_connected()
        tbl = db.open_table(table)
        table_schema = get_schema(table)

        # Read existing records to preserve fields not being updated
        existing_by_id: dict[str, dict[str, Any]] = {}
        for doc_id in ids:
            try:
                rows = tbl.search().where(f'id = "{doc_id}"').limit(1).to_list()
                if rows:
                    existing_by_id[doc_id] = rows[0]
            except Exception:
                pass

        # Filter out IDs that do not actually exist. Without this guard
        # the subsequent add() would silently CREATE new records for
        # non-existent IDs and return them as "update" operations,
        # making update_insight on a missing ID look successful.
        # See test_write_tools.TestUpdateInsight.test_update_not_found
        # for the contract this enforces.
        present_ids = [i for i in ids if i in existing_by_id]
        if not present_ids:
            return []

        # Delete only the records that actually exist.
        await self.delete(table, present_ids)

        # Prepare new data — merge old metadata with new — for present IDs.
        new_docs: list[str] = []
        new_metas: list[dict[str, Any]] = []
        for doc_id in present_ids:
            # Position of doc_id in original ids list, to pick the
            # caller's documents[i] / metadatas[i].
            i = ids.index(doc_id)
            old = existing_by_id[doc_id]

            if documents is not None:
                new_docs.append(documents[i])
            else:
                new_docs.append(old.get("text", ""))

            # Reconstruct old metadata from top-level columns
            old_meta = _extract_metadata(old, table_schema)

            if metadatas is not None:
                # Merge: old values as base, new values overlay
                merged = {**old_meta, **metadatas[i]}
                new_metas.append(merged)
            else:
                new_metas.append(old_meta)

        results = await self.add(table, present_ids, new_docs, new_metas)
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

        # Build SQL filter if where clause provided
        where_sql = _build_filter(where, table) if where else None

        results: list[dict[str, Any]] = []

        if query_type == "fts":
            q = tbl.search(query, query_type="fts").limit(n)
            if where_sql:
                q = q.where(where_sql)
            results = q.to_list()

        elif query_type == "vector":
            embedder = _get_embedder()
            vec = embedder.encode_dense([query])[0]
            q = tbl.search(vec).limit(n)
            if where_sql:
                q = q.where(where_sql)
            results = q.to_list()

        else:  # hybrid — try hybrid, fallback to vector
            try:
                q = tbl.search(query, query_type="hybrid").limit(n)
                if where_sql:
                    q = q.where(where_sql)
                results = q.to_list()
            except Exception:
                logger.debug("Hybrid search unavailable, falling back to vector search")
                embedder = _get_embedder()
                vec = embedder.encode_dense([query])[0]
                q = tbl.search(vec).limit(n)
                if where_sql:
                    q = q.where(where_sql)
                results = q.to_list()

        return [_row_to_result(r, table) for r in results]

    async def count(self, table: str, where: dict[str, Any] | None = None) -> int:
        """Return the number of rows in a table."""
        db = self._ensure_connected()
        tbl = db.open_table(table)
        if where:
            where_sql = _build_filter(where, table)
            rows = tbl.search().where(where_sql).to_list()
            return len(rows)
        return tbl.count_rows()

    # -- helpers -------------------------------------------------------------


def _extract_metadata(row: dict[str, Any], table_schema: Any) -> dict[str, Any]:
    """Extract metadata fields from a row based on the schema."""
    skip = {"id", "text", "vector", "_distance", "_score", "_relevance_score"}
    meta = {}
    for key in table_schema.fields:
        if key in row and key not in skip:
            meta[key] = row[key]
    return meta


def _build_filter(where: dict[str, Any] | None, table_name: str) -> str:
    """Convert a where dict to a LanceDB SQL filter string.

    Validates that all fields are present in the Schema Registry and filterable.
    Supports direct equality and operator syntax ($eq, $in, $gt, $lt, $ne, etc.).
    """
    if not where:
        return ""

    from elsa_runtime.storage.schema import get_schema

    table_schema = get_schema(table_name)
    filterable = table_schema.filterable_fields()

    clauses = []
    for field_name, condition in where.items():
        if field_name not in filterable:
            valid = sorted(filterable)
            raise ValueError(
                f"Cannot filter on '{field_name}' in table '{table_name}'. "
                f"Filterable fields: {valid}"
            )

        if isinstance(condition, dict):
            for op, value in condition.items():
                clauses.append(_op_to_sql(field_name, op, value))
        elif isinstance(condition, list):
            # List = implicit $in
            vals = ", ".join(f"'{v}'" if isinstance(v, str) else str(v) for v in condition)
            clauses.append(f"{field_name} IN ({vals})")
        else:
            clauses.append(_eq_to_sql(field_name, condition))

    return " AND ".join(clauses)


def _op_to_sql(field: str, op: str, value: Any) -> str:
    """Convert operator to SQL."""
    if op == "$eq":
        return _eq_to_sql(field, value)
    elif op == "$in":
        vals = ", ".join(f"'{v}'" if isinstance(v, str) else str(v) for v in value)
        return f"{field} IN ({vals})"
    elif op == "$gt":
        return f"{field} > {value}"
    elif op == "$gte":
        return f"{field} >= {value}"
    elif op == "$lt":
        return f"{field} < {value}"
    elif op == "$lte":
        return f"{field} <= {value}"
    elif op == "$ne":
        return _ne_to_sql(field, value)
    else:
        raise ValueError(f"Unknown operator: {op}")


def _eq_to_sql(field: str, value: Any) -> str:
    if isinstance(value, str):
        return f"{field} = '{value}'"
    elif isinstance(value, bool):
        return f"{field} = {str(value).lower()}"
    else:
        return f"{field} = {value}"


def _ne_to_sql(field: str, value: Any) -> str:
    if isinstance(value, str):
        return f"{field} != '{value}'"
    else:
        return f"{field} != {value}"


def _row_to_result(row: dict[str, Any], table_name: str) -> SearchResult:
    """Convert a LanceDB result row to a SearchResult."""
    # Determine score: LanceDB uses _distance (vector) or _score (fts)
    score = 0.0
    breakdown: dict[str, float] = {}
    if "_distance" in row:
        score = 1.0 / (1.0 + row["_distance"])
        breakdown["distance"] = row["_distance"]
    if "_score" in row:
        score = row["_score"]
        breakdown["fts_score"] = row["_score"]
    if "_relevance_score" in row:
        score = row["_relevance_score"]
        breakdown["relevance_score"] = row["_relevance_score"]

    # Reconstruct metadata from top-level columns
    skip = {"id", "text", "vector", "_distance", "_score", "_relevance_score"}
    meta = {k: v for k, v in row.items() if k not in skip}

    return SearchResult(
        id=row.get("id", ""),
        content=row.get("text", ""),
        metadata=meta,
        score=score,
        score_breakdown=breakdown,
    )

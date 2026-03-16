"""Embedding pipeline — unified embed + store interface via VectorStore Protocol."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from elsa_runtime.embedding.models import BaseEmbedder, get_embedder
from elsa_runtime.storage.collections import COLLECTION_MAP, CollectionSpec
from elsa_runtime.storage.vectorstore import SearchResult, VectorStore


class EmbeddingPipeline:
    """Unified interface for embedding + VectorStore operations."""

    def __init__(
        self,
        store: VectorStore,
        embedder: BaseEmbedder | None = None,
    ) -> None:
        self._store = store
        if embedder is None:
            embedder = get_embedder()
        self._embedder = embedder

    @property
    def embedder(self) -> BaseEmbedder:
        return self._embedder

    def _get_spec(self, collection_name: str) -> CollectionSpec:
        spec = COLLECTION_MAP.get(collection_name)
        if spec is None:
            raise ValueError(f"Unknown collection: {collection_name}")
        return spec

    def _validate_metadata(self, spec: CollectionSpec, metadata: dict[str, Any]) -> None:
        missing = [f for f in spec.required_metadata if f not in metadata]
        if missing:
            raise ValueError(f"Collection '{spec.name}' missing required metadata: {missing}")

    def _stamp_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        if "created_at" not in metadata:
            metadata["created_at"] = datetime.now(tz=timezone.utc).isoformat()
        return metadata

    async def upsert(
        self,
        collection_name: str,
        ids: list[str],
        documents: list[str],
        metadatas: list[dict[str, Any]],
    ) -> None:
        """Embed documents and upsert into the specified table."""
        spec = self._get_spec(collection_name)
        stamped: list[dict[str, Any]] = []
        for meta in metadatas:
            self._validate_metadata(spec, meta)
            stamped.append(self._stamp_metadata(dict(meta)))

        embeddings = self._embedder.encode_dense(documents)
        await self._store.add(
            collection_name,
            ids=ids,
            documents=documents,
            metadatas=stamped,
            embeddings=embeddings,
        )

    async def query(
        self,
        collection_name: str,
        query_text: str,
        n_results: int = 5,
        where: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        """Semantic query against a table."""
        return await self._store.search(
            collection_name,
            query_text,
            n=n_results,
            where=where,
            query_type="vector",
        )

    async def scoped_query(
        self,
        collection_name: str,
        query_text: str,
        *,
        agent: str | None = None,
        domain: str | None = None,
        created_after: str | None = None,
        n_results: int = 5,
        extra_where: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        """Scoped query with anti-contamination filters."""
        conditions: dict[str, Any] = {}
        if agent:
            conditions["agent"] = agent
        if domain:
            conditions["domain"] = domain
        if created_after:
            conditions["created_after"] = created_after
        if extra_where:
            conditions.update(extra_where)

        where = conditions if conditions else None
        return await self.query(collection_name, query_text, n_results=n_results, where=where)

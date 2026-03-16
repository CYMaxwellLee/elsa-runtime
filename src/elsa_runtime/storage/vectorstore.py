"""VectorStore Protocol — abstract interface for vector database backends."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """Single search result."""
    id: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0
    score_breakdown: dict[str, float] = field(default_factory=dict)


@dataclass
class WriteResult:
    """Result of a write operation."""
    id: str
    operation: str  # "add", "update", "noop", "delete"
    reason: str = ""


@runtime_checkable
class VectorStore(Protocol):
    """Abstract vector store interface."""

    async def connect(self) -> None: ...
    async def ensure_table(self, name: str, schema: dict[str, Any] | None = None) -> None: ...
    async def add(self, table: str, ids: list[str], documents: list[str],
                  metadatas: list[dict[str, Any]] | None = None,
                  embeddings: list[list[float]] | None = None) -> list[WriteResult]: ...
    async def update(self, table: str, ids: list[str],
                     documents: list[str] | None = None,
                     metadatas: list[dict[str, Any]] | None = None) -> list[WriteResult]: ...
    async def delete(self, table: str, ids: list[str]) -> int: ...
    async def search(self, table: str, query: str, n: int = 10,
                     where: dict[str, Any] | None = None,
                     query_type: str = "hybrid") -> list[SearchResult]: ...
    async def count(self, table: str, where: dict[str, Any] | None = None) -> int: ...
    async def list_tables(self) -> list[str]: ...

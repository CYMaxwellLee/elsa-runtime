"""InsightStore v2 — CRUD + lifecycle for insights, backed by VectorStore Protocol.

Design source: core/05c-INSIGHT-SYSTEM.md
Migrated from ChromaDB to VectorStore abstraction in Phase 3 (v3.40).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from elsa_runtime.storage.vectorstore import VectorStore, SearchResult


class InsightStore:
    """VectorStore-based insight store with lifecycle management.

    Lifecycle: Active -> Dormant -> Archived -> Expired
    """

    TABLE_NAME = "insights"

    def __init__(self, store: VectorStore) -> None:
        self.store = store

    async def initialize(self) -> None:
        """Ensure the insights table exists."""
        await self.store.ensure_table(self.TABLE_NAME)

    async def create_insight(
        self,
        *,
        agent: str,
        domain: str,
        task_type: str,
        content: str,
        confidence: float,
        context: str = "",
        scope: str = "self",
        derived_from_task: str = "",
    ) -> str:
        """Create a new insight. Returns the insight ID."""
        insight_id = f"insight-{agent}-{datetime.now(tz=timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"
        now = datetime.now(tz=timezone.utc).isoformat()

        metadata = {
            "type": "insight",
            "agent": agent,
            "domain": domain,
            "task_type": task_type,
            "confidence": confidence,
            "scope": scope,
            "lifecycle": "active",
            "times_referenced": 0,
            "times_adopted": 0,
            "created_at": now,
            "derived_from_task": derived_from_task,
        }

        await self.store.add(
            self.TABLE_NAME,
            ids=[insight_id],
            documents=[content],
            metadatas=[metadata],
        )

        return insight_id

    async def query_insights(
        self,
        query_text: str,
        *,
        domain: str | None = None,
        lifecycle: list[str] | None = None,
        limit: int = 5,
    ) -> list[SearchResult]:
        """Semantic query for insights with optional filtering."""
        where: dict | None = None
        conditions: dict = {}
        if domain:
            conditions["domain"] = domain
        if lifecycle:
            conditions["lifecycle"] = lifecycle  # store handles list filtering
        if conditions:
            where = conditions

        return await self.store.search(
            self.TABLE_NAME,
            query_text,
            n=limit,
            where=where,
            query_type="hybrid",
        )

    async def update_lifecycle(self, insight_id: str, new_stage: str) -> None:
        """Update insight lifecycle stage."""
        valid_stages = {"active", "dormant", "archived", "expired"}
        if new_stage not in valid_stages:
            raise ValueError(f"Invalid lifecycle stage: {new_stage}. Must be one of {valid_stages}")

        await self.store.update(
            self.TABLE_NAME,
            ids=[insight_id],
            metadatas=[{"lifecycle": new_stage}],
        )

    async def deprecate_insight(
        self,
        insight_id: str,
        reason: str,
        superseded_by: str = "",
    ) -> None:
        """Mark insight as deprecated (soft delete)."""
        update_meta: dict = {
            "lifecycle": "expired",
            "deprecated": reason,
        }
        if superseded_by:
            update_meta["superseded_by"] = superseded_by

        await self.store.update(
            self.TABLE_NAME,
            ids=[insight_id],
            metadatas=[update_meta],
        )

    async def get_insight(self, insight_id: str) -> dict | None:
        """Get a single insight by ID. Returns None if not found."""
        results = await self.store.search(
            self.TABLE_NAME,
            query="",
            n=1,
            where={"id": insight_id},
            query_type="vector",
        )
        if not results:
            return None
        r = results[0]
        return {
            "id": r.id,
            "document": r.content,
            "metadata": r.metadata,
        }

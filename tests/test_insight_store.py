"""Tests for InsightStore CRUD + lifecycle (VectorStore mock)."""

from unittest.mock import AsyncMock

import pytest

from elsa_runtime.knowledge.insight_store import InsightStore
from elsa_runtime.storage.vectorstore import SearchResult, WriteResult, VectorStore


@pytest.fixture()
def mock_store():
    """Create a mock VectorStore for InsightStore tests."""
    store = AsyncMock(spec=VectorStore)
    store.ensure_table = AsyncMock()
    store.add = AsyncMock(return_value=[WriteResult(id="test", operation="add")])
    store.search = AsyncMock(return_value=[])
    store.update = AsyncMock(return_value=[WriteResult(id="test", operation="update")])
    store.delete = AsyncMock(return_value=0)
    store.count = AsyncMock(return_value=0)
    store.list_tables = AsyncMock(return_value=[])
    return store


@pytest.fixture()
def insight_store(mock_store):
    """Create an InsightStore backed by the mock VectorStore."""
    return InsightStore(mock_store)


@pytest.mark.asyncio
async def test_create_insight(insight_store: InsightStore):
    insight_id = await insight_store.create_insight(
        agent="rei",
        domain="research",
        task_type="paper_analysis",
        content="Authors often hide key ablations in appendix.",
        confidence=0.85,
        context="Analyzing memory-augmented transformer paper",
    )
    assert insight_id.startswith("insight-rei-")
    # Verify store.add was called
    insight_store.store.add.assert_called_once()
    call_args = insight_store.store.add.call_args
    assert call_args[0][0] == "insights"  # table name
    assert call_args[1]["documents"] == ["Authors often hide key ablations in appendix."]


@pytest.mark.asyncio
async def test_get_insight(insight_store: InsightStore, mock_store):
    # Set up mock to return a SearchResult for get_insight
    mock_store.search.return_value = [
        SearchResult(
            id="insight-rei-20260316-abc123",
            content="Check related work section for missing baselines.",
            metadata={
                "lifecycle": "active",
                "confidence": 0.75,
                "agent": "rei",
                "domain": "research",
            },
            score=1.0,
        )
    ]

    result = await insight_store.get_insight("insight-rei-20260316-abc123")
    assert result is not None
    assert result["id"] == "insight-rei-20260316-abc123"
    assert "missing baselines" in result["document"]
    assert result["metadata"]["lifecycle"] == "active"
    assert result["metadata"]["confidence"] == 0.75


@pytest.mark.asyncio
async def test_get_nonexistent_insight(insight_store: InsightStore, mock_store):
    mock_store.search.return_value = []
    result = await insight_store.get_insight("nonexistent-id")
    assert result is None


@pytest.mark.asyncio
async def test_query_insights(insight_store: InsightStore, mock_store):
    # Set up mock to return results for the query
    mock_store.search.return_value = [
        SearchResult(
            id="insight-rei-20260316-aaa111",
            content="Transformer attention patterns reveal model focus areas.",
            metadata={"domain": "research", "confidence": 0.9},
            score=0.95,
        ),
    ]

    results = await insight_store.query_insights("transformer attention", domain="research")
    assert len(results) >= 1
    assert results[0].content == "Transformer attention patterns reveal model focus areas."

    # Verify the search was called with correct parameters
    mock_store.search.assert_called_once_with(
        "insights",
        "transformer attention",
        n=5,
        where={"domain": "research"},
        query_type="hybrid",
    )


@pytest.mark.asyncio
async def test_update_lifecycle(insight_store: InsightStore, mock_store):
    insight_id = "insight-rei-20260316-abc123"

    await insight_store.update_lifecycle(insight_id, "dormant")

    mock_store.update.assert_called_once_with(
        "insights",
        ids=[insight_id],
        metadatas=[{"lifecycle": "dormant"}],
    )


@pytest.mark.asyncio
async def test_update_lifecycle_invalid_stage(insight_store: InsightStore):
    with pytest.raises(ValueError, match="Invalid lifecycle stage"):
        await insight_store.update_lifecycle("insight-rei-20260316-abc123", "invalid_stage")


@pytest.mark.asyncio
async def test_deprecate_insight(insight_store: InsightStore, mock_store):
    old_id = "insight-rei-20260316-old111"
    new_id = "insight-rei-20260316-new222"

    await insight_store.deprecate_insight(old_id, reason="outdated", superseded_by=new_id)

    mock_store.update.assert_called_once_with(
        "insights",
        ids=[old_id],
        metadatas=[{
            "lifecycle": "expired",
            "deprecated": "outdated",
            "superseded_by": new_id,
        }],
    )


@pytest.mark.asyncio
async def test_lifecycle_transitions(insight_store: InsightStore, mock_store):
    """Full lifecycle: active -> dormant -> archived -> expired."""
    insight_id = "insight-mayu-20260316-aaa111"

    # Simulate get_insight returning the current lifecycle state after each update.
    # We use side_effect on search to return appropriate lifecycle stages in sequence.
    mock_store.search.side_effect = [
        # get_insight after create (active)
        [SearchResult(id=insight_id, content="Check disk usage weekly.",
                      metadata={"lifecycle": "active"}, score=1.0)],
        # get_insight after update to dormant
        [SearchResult(id=insight_id, content="Check disk usage weekly.",
                      metadata={"lifecycle": "dormant"}, score=1.0)],
        # get_insight after update to archived
        [SearchResult(id=insight_id, content="Check disk usage weekly.",
                      metadata={"lifecycle": "archived"}, score=1.0)],
        # get_insight after update to expired
        [SearchResult(id=insight_id, content="Check disk usage weekly.",
                      metadata={"lifecycle": "expired"}, score=1.0)],
    ]

    # Check initial active state
    result = await insight_store.get_insight(insight_id)
    assert result["metadata"]["lifecycle"] == "active"

    await insight_store.update_lifecycle(insight_id, "dormant")
    result = await insight_store.get_insight(insight_id)
    assert result["metadata"]["lifecycle"] == "dormant"

    await insight_store.update_lifecycle(insight_id, "archived")
    result = await insight_store.get_insight(insight_id)
    assert result["metadata"]["lifecycle"] == "archived"

    await insight_store.update_lifecycle(insight_id, "expired")
    result = await insight_store.get_insight(insight_id)
    assert result["metadata"]["lifecycle"] == "expired"

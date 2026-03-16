"""Tests for Domain Context builder (VectorStore mock)."""

from unittest.mock import AsyncMock

import pytest

from elsa_runtime.knowledge.domain_context import (
    AGENT_DOMAIN_CONFIG,
    DomainConfig,
    build_domain_context,
)
from elsa_runtime.storage.vectorstore import SearchResult, VectorStore


def test_all_agents_have_config():
    expected_agents = {"elsa", "rei", "luna", "hikari", "mayu", "ririka"}
    assert set(AGENT_DOMAIN_CONFIG.keys()) == expected_agents


def test_domain_config_fields():
    config = AGENT_DOMAIN_CONFIG["rei"]
    assert config.domain == "research"
    assert config.primary_table == "papers"
    assert config.insight_limit > 0


@pytest.mark.asyncio
async def test_unknown_agent_returns_empty():
    result = await build_domain_context("nonexistent_agent")
    assert result == ""


@pytest.mark.asyncio
async def test_no_store_returns_placeholder():
    result = await build_domain_context("elsa", store=None)
    assert "no collections available" in result


@pytest.mark.asyncio
async def test_empty_store_returns_no_data():
    mock_store = AsyncMock(spec=VectorStore)
    mock_store.search = AsyncMock(return_value=[])

    result = await build_domain_context("elsa", store=mock_store)
    assert "no data yet" in result


@pytest.mark.asyncio
async def test_with_data_returns_context():
    """When insights exist, domain context should include them."""
    mock_store = AsyncMock(spec=VectorStore)

    # First call: insights table search returns data
    # Second call: primary table (tasks) search returns empty
    mock_store.search.side_effect = [
        [
            SearchResult(
                id="test-insight-1",
                content="Task routing improves with keyword matching.",
                metadata={
                    "type": "insight",
                    "agent": "elsa",
                    "domain": "orchestration",
                    "lifecycle": "active",
                },
                score=0.9,
            ),
        ],
        [],  # primary table returns no results
    ]

    result = await build_domain_context("elsa", store=mock_store)
    assert "keyword matching" in result

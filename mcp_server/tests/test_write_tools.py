"""Tests for MCP server write tools (save_insight, update_insight, list_recent_insights).

These tests import the tool functions directly and call them,
bypassing MCP transport. They use a real LanceDB in a temp directory.
"""

import json
import sys
from pathlib import Path

import pytest

# Ensure imports work
_root = str(Path(__file__).resolve().parent.parent.parent)
_src = str(Path(_root) / "src")
if _src not in sys.path:
    sys.path.insert(0, _src)

_mcp = str(Path(_root) / "mcp_server")
if _mcp not in sys.path:
    sys.path.insert(0, _mcp)


@pytest.fixture(autouse=True)
async def reset_server_state(tmp_path):
    """Reset global state before each test, using a temp LanceDB."""
    import mcp_server.server as srv

    srv._store = None
    srv._insight_store = None
    srv._lancedb_path_override = str(tmp_path / "lancedb")
    yield
    srv._store = None
    srv._insight_store = None
    srv._lancedb_path_override = None


def parse(result: str) -> dict:
    return json.loads(result)


# ── save_insight tests ──


class TestSaveInsight:
    async def test_normal_add(self):
        from mcp_server.server import save_insight

        result = parse(await save_insight(
            content="Diffusion models with classifier-free guidance achieve better FID when using large batch sizes during sampling",
            domain="research",
            source_type="paper_analysis",
            agent_id="elsa",
            source_ref="arXiv:2401.12345",
            confidence=0.7,
        ))
        assert result["operation"] == "ADD"
        assert "insight_id" in result
        assert result["insight_id"].startswith("insight-elsa-")

    async def test_reject_temporal_content(self):
        from mcp_server.server import save_insight

        result = parse(await save_insight(
            content="今天的會議中討論了關於新的研究方向的重要發現和計畫",
            domain="research",
            source_type="daily_observation",
            agent_id="elsa",
        ))
        assert result["operation"] == "REJECTED"
        assert "temporal" in result["reason"]

    async def test_reject_too_short(self):
        from mcp_server.server import save_insight

        result = parse(await save_insight(
            content="Too short",
            domain="research",
            source_type="paper_analysis",
            agent_id="elsa",
        ))
        assert result["operation"] == "REJECTED"
        assert "short" in result["reason"]

    async def test_reject_too_long(self):
        from mcp_server.server import save_insight

        result = parse(await save_insight(
            content="x" * 501,
            domain="research",
            source_type="paper_analysis",
            agent_id="elsa",
        ))
        assert result["operation"] == "REJECTED"
        assert "long" in result["reason"]

    async def test_reject_credential(self):
        from mcp_server.server import save_insight

        result = parse(await save_insight(
            content="The api_key for the service is stored in the config file at this path",
            domain="implementation",
            source_type="daily_observation",
            agent_id="elsa",
        ))
        assert result["operation"] == "REJECTED"
        assert "credential" in result["reason"]

    async def test_unknown_domain_accepted_with_warning(self):
        """Per the 4/9 normalize-with-warning policy (eb58b2af), unknown
        domains are no longer rejected — they're accepted as free-form
        with an advisory warning. The OLD test expected REJECTED; this
        version pins the new permissive contract."""
        from mcp_server.server import save_insight

        result = parse(await save_insight(
            content="This is a valid length insight about something interesting in the lab",
            domain="invalid_domain",
            source_type="paper_analysis",
            agent_id="elsa",
        ))
        assert result["operation"] == "ADD"
        # Implementation surfaces a warning when domain is not in
        # CANONICAL_DOMAINS (see server.py around line 425-426).
        assert "warning" in result
        assert "invalid_domain" in result["warning"] or "domain" in result["warning"].lower()

    async def test_reject_invalid_agent(self):
        from mcp_server.server import save_insight

        result = parse(await save_insight(
            content="This is a valid length insight about something interesting in the lab",
            domain="research",
            source_type="paper_analysis",
            agent_id="unknown_agent",
        ))
        assert result["operation"] == "REJECTED"
        assert "agent_id" in result["reason"]

    async def test_different_agent_id(self):
        from mcp_server.server import save_insight

        result = parse(await save_insight(
            content="Rei discovered that transformer attention patterns stabilize after layer 6 in most vision models",
            domain="research",
            source_type="paper_analysis",
            agent_id="rei",
            confidence=0.8,
        ))
        assert result["operation"] == "ADD"
        assert "insight-rei-" in result["insight_id"]

    async def test_semantic_dedup_noop(self):
        """Writing the same content twice should return NOOP on second write."""
        from mcp_server.server import save_insight

        content = "Large language models benefit significantly from chain-of-thought prompting in mathematical reasoning tasks"

        r1 = parse(await save_insight(
            content=content,
            domain="research",
            source_type="paper_analysis",
            agent_id="elsa",
        ))
        assert r1["operation"] == "ADD"

        # Same content again
        r2 = parse(await save_insight(
            content=content,
            domain="research",
            source_type="paper_analysis",
            agent_id="elsa",
        ))
        assert r2["operation"] == "NOOP"
        assert "similar" in r2["reason"]


# ── update_insight tests ──


class TestUpdateInsight:
    async def test_update_existing(self):
        from mcp_server.server import save_insight, update_insight

        r1 = parse(await save_insight(
            content="Initial observation about vision transformer attention patterns in deep networks",
            domain="research",
            source_type="paper_analysis",
            agent_id="elsa",
        ))
        insight_id = r1["insight_id"]

        r2 = parse(await update_insight(
            insight_id=insight_id,
            new_content="Updated: Vision transformer attention becomes sparse after layer 8, not layer 6 as previously noted",
            agent_id="elsa",
            reason="corrected based on new experiments",
        ))
        assert r2["operation"] == "UPDATED"
        assert r2["insight_id"] == insight_id

    async def test_update_not_found(self):
        from mcp_server.server import update_insight

        result = parse(await update_insight(
            insight_id="insight-elsa-20260101-nonexistent",
            new_content="Updated content that should not be written anywhere",
            agent_id="elsa",
        ))
        assert result["operation"] == "NOT_FOUND"


# ── list_recent_insights tests ──


class TestListRecentInsights:
    async def test_list_recent(self):
        from mcp_server.server import save_insight, list_recent_insights

        await save_insight(
            content="Insight one about diffusion model architectures and their training stability",
            domain="research",
            source_type="paper_analysis",
            agent_id="elsa",
        )
        await save_insight(
            content="Insight two about deployment optimization techniques for production systems",
            domain="implementation",
            source_type="daily_observation",
            agent_id="luna",
        )

        results = json.loads(await list_recent_insights(n=10))
        assert len(results) >= 2

    async def test_filter_by_agent(self):
        from mcp_server.server import save_insight, list_recent_insights

        await save_insight(
            content="Elsa insight about communication patterns in academic collaboration emails",
            domain="communication",
            source_type="email_triage",
            agent_id="elsa",
        )
        await save_insight(
            content="Rei insight about novel attention mechanisms in recent transformer papers",
            domain="research",
            source_type="paper_analysis",
            agent_id="rei",
        )

        results = json.loads(await list_recent_insights(n=10, agent_id="elsa"))
        for r in results:
            assert r["metadata"]["agent"] == "elsa"

    async def test_filter_by_domain(self):
        from mcp_server.server import save_insight, list_recent_insights

        await save_insight(
            content="Research insight about efficient fine-tuning methods for large language models",
            domain="research",
            source_type="paper_analysis",
            agent_id="elsa",
        )
        await save_insight(
            content="Ops insight about monitoring pipeline health and alerting thresholds",
            domain="ops",
            source_type="daily_observation",
            agent_id="elsa",
        )

        results = json.loads(await list_recent_insights(n=10, domain="research"))
        for r in results:
            assert r["metadata"]["domain"] == "research"


# ── insight_query with agent_id filter ──


class TestInsightQueryAgentFilter:
    async def test_query_with_agent_filter(self):
        from mcp_server.server import save_insight, insight_query

        await save_insight(
            content="Elsa noticed that reviewers prefer concise related work sections in NeurIPS papers",
            domain="research",
            source_type="paper_analysis",
            agent_id="elsa",
        )
        await save_insight(
            content="Rei found that contrastive learning benefits from larger negative sample pools",
            domain="research",
            source_type="paper_analysis",
            agent_id="rei",
        )

        results = json.loads(await insight_query(
            topic="research papers", agent_id="rei",
        ))
        for r in results:
            assert r["metadata"]["agent"] == "rei"

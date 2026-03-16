"""Domain Context builder — per-agent Tier 2 Warm Context extraction.

Design source: core/42-CONTEXT-ENGINEERING.md
Phase 3: VectorStore abstraction (ChromaDB removed).
"""

from __future__ import annotations

from dataclasses import dataclass

from elsa_runtime.storage.vectorstore import VectorStore


@dataclass
class DomainConfig:
    """Per-agent domain context configuration."""

    domain: str
    domain_query: str
    primary_table: str
    insight_limit: int = 10
    recent_limit: int = 5


AGENT_DOMAIN_CONFIG: dict[str, DomainConfig] = {
    "elsa": DomainConfig(
        domain="orchestration",
        domain_query="task management email calendar priority",
        primary_table="tasks",
        insight_limit=10,
        recent_limit=5,
    ),
    "rei": DomainConfig(
        domain="research",
        domain_query="paper method theory experiment contribution",
        primary_table="papers",
        insight_limit=15,
        recent_limit=10,
    ),
    "luna": DomainConfig(
        domain="implementation",
        domain_query="code tool debug implementation architecture",
        primary_table="solutions",
        insight_limit=10,
        recent_limit=5,
    ),
    "hikari": DomainConfig(
        domain="training",
        domain_query="experiment training cluster GPU hyperparameter",
        primary_table="procedures",
        insight_limit=10,
        recent_limit=5,
    ),
    "mayu": DomainConfig(
        domain="ops",
        domain_query="docker server monitoring incident deployment",
        primary_table="procedures",
        insight_limit=15,
        recent_limit=5,
    ),
    "ririka": DomainConfig(
        domain="presentation",
        domain_query="slide report document style format",
        primary_table="knowledge",
        insight_limit=5,
        recent_limit=5,
    ),
}


async def build_domain_context(
    agent_id: str,
    store: VectorStore | None = None,
) -> str:
    """Extract domain-relevant subset from VectorStore for Tier 2 injection."""
    config = AGENT_DOMAIN_CONFIG.get(agent_id)
    if not config:
        return ""

    if store is None:
        return f"[Domain context for {agent_id}: no collections available]"

    sections: list[str] = []

    # Query insights table
    try:
        results = await store.search(
            "insights",
            config.domain_query,
            n=config.insight_limit,
            where={"domain": config.domain},
            query_type="hybrid",
        )
        if results:
            sections.append(f"## {agent_id} Domain Insights")
            for r in results:
                sections.append(f"- {r.content}")
    except Exception:
        pass

    # Query primary table
    try:
        results = await store.search(
            config.primary_table,
            config.domain_query,
            n=config.recent_limit,
            query_type="hybrid",
        )
        if results:
            sections.append(f"## Recent {config.primary_table}")
            for r in results:
                sections.append(f"- {r.content}")
    except Exception:
        pass

    if not sections:
        return f"[Domain context for {agent_id}: no data yet]"

    return "\n".join(sections)

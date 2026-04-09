"""Elsa Knowledge MCP Server.

Exposes elsa-runtime knowledge infrastructure via MCP protocol.
Supports both stdio (Claude Code subprocess) and HTTP SSE (standalone daemon).

Usage:
    # stdio (default, Claude Code integration)
    python -m mcp_server.server

    # HTTP SSE (standalone daemon, multi-agent)
    python -m mcp_server.server --transport sse --port 9100
"""

import argparse
import asyncio
import json
import os
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path

import yaml

from mcp.server.fastmcp import FastMCP

# Ensure elsa-runtime src is importable
_runtime_src = str(Path(__file__).resolve().parent.parent / "src")
if _runtime_src not in sys.path:
    sys.path.insert(0, _runtime_src)

from elsa_runtime.storage import get_store
from elsa_runtime.knowledge.insight_store import InsightStore

mcp = FastMCP("elsa-knowledge", host="0.0.0.0", port=9100, stateless_http=True)

# Workspace registry for ACTIVE-INSIGHTS.md auto-promote
WORKSPACE_REGISTRY = {}


def load_workspace_registry(path: str | None):
    global WORKSPACE_REGISTRY
    if path and Path(path).exists():
        with open(path) as f:
            data = yaml.safe_load(f)
            WORKSPACE_REGISTRY = data.get("workspaces", {})
    elif Path("config/workspace_registry.yaml").exists():
        with open("config/workspace_registry.yaml") as f:
            data = yaml.safe_load(f)
            WORKSPACE_REGISTRY = data.get("workspaces", {})

# Shared state -- initialized on first tool call
_store = None
_insight_store = None
_lancedb_path_override = None


async def _get_store():
    global _store
    if _store is None:
        lancedb_path = _lancedb_path_override or os.environ.get(
            "ELSA_LANCEDB_PATH",
            str(Path.home() / ".elsa-system" / "lancedb"),
        )
        _store = get_store(backend="lancedb", path=lancedb_path)
        await _store.connect()
    return _store


async def _get_insight_store():
    global _insight_store
    if _insight_store is None:
        store = await _get_store()
        _insight_store = InsightStore(store)
        await _insight_store.initialize()
    return _insight_store


# ── Tool 1: knowledge_search ──

@mcp.tool()
async def knowledge_search(
    query: str,
    table: str = "all",
    n: int = 5,
    filters: str = "",
) -> str:
    """Search LanceDB for papers, insights, knowledge, and more.

    Args:
        query: Search keywords or semantic description.
        table: Table to search. Options: papers, insights, knowledge, tasks,
               conversations, solutions, procedures, theory_notes, tool_docs,
               or 'all' to search across main tables.
        n: Number of results to return (default 5).
        filters: JSON string of metadata filters, e.g. '{"tier": "A"}'.
    """
    store = await _get_store()
    where = json.loads(filters) if filters else None

    if table == "all":
        tables_to_search = ["papers", "insights", "knowledge"]
        all_results = []
        for t in tables_to_search:
            try:
                results = await store.search(t, query, n=n, where=where)
                for r in results:
                    all_results.append({
                        "table": t,
                        "id": r.id,
                        "content": r.content[:500],
                        "score": round(r.score, 4),
                        "metadata": r.metadata,
                    })
            except Exception:
                continue
        all_results.sort(key=lambda x: x["score"], reverse=True)
        return json.dumps(all_results[:n], ensure_ascii=False, indent=2)

    results = await store.search(table, query, n=n, where=where)
    return json.dumps(
        [
            {
                "id": r.id,
                "content": r.content[:500],
                "score": round(r.score, 4),
                "metadata": r.metadata,
            }
            for r in results
        ],
        ensure_ascii=False,
        indent=2,
    )


# ── Tool 2: insight_query ──

@mcp.tool()
async def insight_query(
    topic: str,
    lifecycle: str = "active",
    n: int = 5,
    agent_id: str = "",
) -> str:
    """Query accumulated insights from the InsightStore.

    Args:
        topic: Topic keywords to search.
        lifecycle: Lifecycle filter. Options: active, dormant, archived, expired.
        n: Number of results (default 5).
        agent_id: Optional. Filter by agent (elsa, rei, luna, etc.). Empty = all agents.
    """
    istore = await _get_insight_store()
    lifecycle_list = [lifecycle] if lifecycle else None
    results = await istore.query_insights(topic, lifecycle=lifecycle_list, limit=n)

    # Post-filter by agent_id if specified (InsightStore doesn't natively filter by agent)
    if agent_id:
        results = [r for r in results if r.metadata.get("agent") == agent_id]

    return json.dumps(
        [
            {
                "id": r.id,
                "content": r.content[:500],
                "score": round(r.score, 4),
                "metadata": r.metadata,
            }
            for r in results
        ],
        ensure_ascii=False,
        indent=2,
    )


# ── Tool 3: paper_analyze ──

@mcp.tool()
def paper_analyze(
    pdf_path: str,
    mode: str = "split",
) -> str:
    """Analyze a paper PDF: split into sections or extract metadata.

    Args:
        pdf_path: Absolute path to a PDF file, or arXiv ID (e.g. '2401.12345').
        mode: 'split' for section-level splitting, 'metadata' for title/authors/abstract only.
    """
    from elsa_runtime.paper.splitter import PaperSplitter

    splitter = PaperSplitter()
    result = splitter.split(pdf_path)

    if mode == "metadata":
        return json.dumps(
            {
                "paper_id": result.paper_id,
                "title": result.index.title,
                "abstract": result.index.abstract,
                "method": result.method.value,
                "total_sections": result.index.total_sections,
                "total_estimated_tokens": result.index.total_estimated_tokens,
            },
            ensure_ascii=False,
            indent=2,
        )

    return json.dumps(
        {
            "paper_id": result.paper_id,
            "method": result.method.value,
            "total_sections": len(result.sections),
            "warnings": result.warnings,
            "sections": [
                {
                    "id": s.id,
                    "title": s.title,
                    "level": s.level,
                    "order": s.order,
                    "estimated_tokens": s.estimated_tokens,
                    "content_preview": s.content[:300],
                }
                for s in result.sections
            ],
        },
        ensure_ascii=False,
        indent=2,
    )


# ── Tool 4: nstc_extract ──

@mcp.tool()
def nstc_extract(
    pdf_path: str,
    outdir: str = "/tmp/proposal/",
) -> str:
    """Extract structured content from NSTC research proposal PDFs.

    Args:
        pdf_path: Absolute path to the NSTC proposal PDF.
        outdir: Output directory for extracted files.
    """
    return json.dumps(
        {"error": "nstc_extract is not yet implemented. Coming in Phase 1."},
        ensure_ascii=False,
    )


# ── Tool 5: save_insight ──

# Guards for save_insight
TEMPORAL_PATTERNS = ["今天", "本次", "剛才", "最近", "暫時", "這次", "昨天", "明天"]
CRED_PATTERNS = ["api_key", "token", "password", "secret", "sk-ant-", "sk-"]
CANONICAL_DOMAINS = {
    "research", "implementation", "ops", "communication", "orchestration",
    "collaboration", "competition", "logistics", "teaching",
}
# Alias map: variant → canonical. Keeps categories from fragmenting.
DOMAIN_ALIASES = {
    # research
    "paper": "research", "papers": "research", "論文": "research", "研究": "research",
    "ml": "research", "ai": "research", "method": "research",
    # implementation
    "impl": "implementation", "code": "implementation", "coding": "implementation",
    "engineering": "implementation", "dev": "implementation", "實作": "implementation",
    "system": "implementation", "infra": "implementation", "infrastructure": "implementation",
    # ops
    "operations": "ops", "admin": "ops", "行政": "ops", "營運": "ops",
    # communication
    "email": "communication", "meeting": "communication", "信件": "communication",
    "溝通": "communication", "人脈": "communication",
    # orchestration
    "workflow": "orchestration", "pipeline": "orchestration", "協調": "orchestration",
    # collaboration
    "collab": "collaboration", "合作": "collaboration", "合作案": "collaboration",
    "partnership": "collaboration",
    # competition
    "contest": "competition", "hackathon": "competition", "比賽": "competition",
    "競賽": "competition", "challenge": "competition",
    # logistics
    "housing": "logistics", "travel": "logistics", "住宿": "logistics",
    "生活": "logistics", "搬家": "logistics",
    # teaching
    "course": "teaching", "教學": "teaching", "lecture": "teaching",
    "課程": "teaching", "student": "teaching",
}


def normalize_domain(raw: str) -> str:
    """Normalize domain to canonical form. Returns canonical domain or original lowercase."""
    d = raw.strip().lower().replace(" ", "_").replace("-", "_")
    if d in CANONICAL_DOMAINS:
        return d
    if d in DOMAIN_ALIASES:
        return DOMAIN_ALIASES[d]
    return d  # allow new domains, but pass through normalized
VALID_SOURCE_TYPES = {
    "paper_analysis", "email_triage", "meeting_prep",
    "daily_observation", "user_correction",
}
VALID_AGENTS = {"elsa", "rei", "luna", "hikari", "mayu", "ririka"}


@mcp.tool()
async def save_insight(
    content: str,
    domain: str,
    source_type: str,
    agent_id: str,
    source_ref: str = "",
    confidence: float = 0.7,
) -> str:
    """Save a distilled insight to long-term knowledge.

    Guards: rejects temporal content, too short/long, credentials.
    Deduplicates against existing insights (cosine > 0.92 = NOOP).

    Args:
        content: Distilled knowledge, 2-3 sentences, 20-500 chars.
        domain: Category (auto-normalized). Standard: research, implementation, ops, communication, orchestration, collaboration, competition, logistics, teaching. Aliases accepted (e.g. "paper"→"research", "合作"→"collaboration").
        source_type: One of: paper_analysis, email_triage, meeting_prep, daily_observation, user_correction.
        agent_id: Which agent is saving (elsa, rei, luna, hikari, mayu, ririka).
        source_ref: Optional reference (arXiv ID, email subject, meeting title).
        confidence: 0.0-1.0. User-confirmed=0.9, observed=0.7, guessed=0.5.
    """
    # === Guard 1: Temporal patterns (05-MEMORY L3) ===
    if any(p in content for p in TEMPORAL_PATTERNS):
        return json.dumps(
            {"operation": "REJECTED", "reason": "contains temporal words, not suitable for long-term memory"},
            ensure_ascii=False,
        )

    # === Guard 2: Length ===
    if len(content) < 20:
        return json.dumps(
            {"operation": "REJECTED", "reason": f"too short ({len(content)} chars, min 20)"},
            ensure_ascii=False,
        )
    if len(content) > 500:
        return json.dumps(
            {"operation": "REJECTED", "reason": f"too long ({len(content)} chars, max 500), please distill"},
            ensure_ascii=False,
        )

    # === Guard 3: Credential filter ===
    if any(p in content.lower() for p in CRED_PATTERNS):
        return json.dumps(
            {"operation": "REJECTED", "reason": "suspected credential in content"},
            ensure_ascii=False,
        )

    # === Guard 4: Domain/source_type/agent validation ===
    if not domain or not domain.strip():
        return json.dumps(
            {"operation": "REJECTED", "reason": f"domain cannot be empty, pick from: {sorted(CANONICAL_DOMAINS)}"},
            ensure_ascii=False,
        )
    domain = normalize_domain(domain)
    if source_type not in VALID_SOURCE_TYPES:
        return json.dumps(
            {"operation": "REJECTED", "reason": f"invalid source_type '{source_type}', must be one of {sorted(VALID_SOURCE_TYPES)}"},
            ensure_ascii=False,
        )
    if agent_id not in VALID_AGENTS:
        return json.dumps(
            {"operation": "REJECTED", "reason": f"invalid agent_id '{agent_id}', must be one of {sorted(VALID_AGENTS)}"},
            ensure_ascii=False,
        )

    # === Semantic Dedup (05c consolidate_before_write) ===
    store = await _get_store()
    try:
        similar = await store.search("insights", content, n=3)
        if similar:
            top = similar[0]
            if top.score > 0.92:
                return json.dumps(
                    {
                        "operation": "NOOP",
                        "reason": f"highly similar to existing insight {top.id} (sim={top.score:.2f})",
                    },
                    ensure_ascii=False,
                )
            # TODO [Phase 1, needs LLM client]: LLM merge judge for similarity 0.75-0.92
            #   Use LLM to decide UPDATE vs ADD
            #   Ref: 05c-INSIGHT-SYSTEM.md consolidate_before_write
    except Exception:
        pass  # Table may not exist yet, skip dedup

    # TODO [Phase 1, needs LLM client]: Guard 4 context-aware check
    #   Simulate injecting insight into typical query, verify result is sensible
    #   Phase 1: sample 1 in 10, Phase 2: all
    #   Ref: 05-MEMORY-SYSTEM.md L3 Guard 4

    # === Write via InsightStore ===
    istore = await _get_insight_store()
    insight_id = await istore.create_insight(
        agent=agent_id,
        domain=domain,
        task_type=source_type,
        content=content,
        confidence=confidence,
        context=source_ref,
        scope="self",  # Phase 0: always self; TODO [Phase 2]: scope="team" auto-write to Knowledge Graph
    )

    # TODO [Phase 2]: times_referenced auto-increment
    #   On every knowledge_search/insight_query hit, increment counter
    #   Ref: 05c-INSIGHT-SYSTEM.md lifecycle

    from mcp_server.active_insights import promote_insight

    promoted_to = promote_insight(
        content=content,
        domain=domain,
        source_type=source_type,
        confidence=confidence,
        agent_id=agent_id,
        insight_id=insight_id,
        created_at=datetime.now().isoformat(),
        registry=WORKSPACE_REGISTRY,
    )

    result = {"operation": "ADD", "reason": "passed all guards, written", "insight_id": insight_id, "domain": domain}
    if domain not in CANONICAL_DOMAINS:
        result["warning"] = f"new domain '{domain}' created — consider adding to CANONICAL_DOMAINS if recurring"
    if promoted_to:
        result["promoted_to"] = [Path(p).name for p in promoted_to]
        result["tier"] = "Tier 1 (push)"
    else:
        result["tier"] = "Tier 2 (pull-only)"
    return json.dumps(result, ensure_ascii=False)


# ── Tool 6: update_insight ──

@mcp.tool()
async def update_insight(
    insight_id: str,
    new_content: str,
    agent_id: str,
    reason: str = "",
) -> str:
    """Update an existing insight's content. Preserves created_at.

    Args:
        insight_id: The insight ID to update.
        new_content: New content to replace the old.
        agent_id: Who is updating.
        reason: Optional reason for the update.
    """
    store = await _get_store()
    try:
        results = await store.update(
            "insights",
            ids=[insight_id],
            documents=[new_content],
            metadatas=[{
                "updated_at": datetime.now().isoformat(),
                "updated_by": agent_id,
                "update_reason": reason,
            }],
        )
        if not results or results[0].operation == "noop":
            return json.dumps(
                {"operation": "NOT_FOUND", "insight_id": insight_id},
                ensure_ascii=False,
            )
    except Exception:
        return json.dumps(
            {"operation": "NOT_FOUND", "insight_id": insight_id},
            ensure_ascii=False,
        )

    from mcp_server.active_insights import update_insight_in_active

    update_insight_in_active(insight_id, new_content, WORKSPACE_REGISTRY)

    return json.dumps(
        {"operation": "UPDATED", "insight_id": insight_id},
        ensure_ascii=False,
    )


# ── Tool 7: list_recent_insights ──

@mcp.tool()
async def list_recent_insights(
    n: int = 10,
    domain: str = "",
    agent_id: str = "",
) -> str:
    """List recently saved insights, optionally filtered by domain or agent.

    Args:
        n: Number of insights to return (default 10).
        domain: Optional filter by domain (research, implementation, ops, communication, orchestration).
        agent_id: Optional filter by agent (elsa, rei, luna, etc.).
    """
    store = await _get_store()

    # Use a broad search to get recent insights, then filter
    # LanceDB doesn't have native "order by created_at", so we search with a generic query
    where = {"lifecycle": "active"}
    if domain:
        where["domain"] = domain
    if agent_id:
        where["agent"] = agent_id

    try:
        results = await store.search("insights", "insight knowledge", n=n * 3, where=where)
    except Exception:
        return json.dumps([], ensure_ascii=False)

    # Sort by created_at descending, take n
    def get_created(r):
        return r.metadata.get("created_at", "")

    results.sort(key=get_created, reverse=True)
    results = results[:n]

    return json.dumps(
        [
            {
                "id": r.id,
                "content": r.content[:500],
                "metadata": r.metadata,
            }
            for r in results
        ],
        ensure_ascii=False,
        indent=2,
    )


# ── Entry point ──

def main():
    parser = argparse.ArgumentParser(description="Elsa Knowledge MCP Server")
    parser.add_argument(
        "--transport", default="stdio", choices=["stdio", "sse", "streamable-http"],
        help="Transport mode: stdio (Claude Code subprocess), sse (legacy HTTP), or streamable-http (recommended HTTP daemon)",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Bind address (sse only)")
    parser.add_argument("--port", type=int, default=9100, help="Listen port (sse only)")
    parser.add_argument(
        "--lancedb-path", default=None,
        help="LanceDB data path (default: ~/.elsa-system/lancedb or ELSA_LANCEDB_PATH env)",
    )
    parser.add_argument(
        "--workspace-registry", default=None,
        help="Path to workspace_registry.yaml for ACTIVE-INSIGHTS.md auto-promote",
    )
    args = parser.parse_args()

    global _lancedb_path_override
    if args.lancedb_path:
        _lancedb_path_override = args.lancedb_path

    load_workspace_registry(args.workspace_registry)

    # Override host/port from CLI args
    mcp.settings.host = args.host
    mcp.settings.port = args.port

    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()

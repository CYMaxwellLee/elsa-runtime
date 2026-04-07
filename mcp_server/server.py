"""Elsa Knowledge MCP Server.

Exposes elsa-runtime knowledge infrastructure via MCP protocol.
Runs as stdio server for Claude Code integration.

Usage:
    python -m mcp_server.server
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# Ensure elsa-runtime src is importable
_runtime_src = str(Path(__file__).resolve().parent.parent / "src")
if _runtime_src not in sys.path:
    sys.path.insert(0, _runtime_src)

from elsa_runtime.storage import get_store
from elsa_runtime.knowledge.insight_store import InsightStore

mcp = FastMCP("elsa-knowledge")

# Shared state — initialized on first tool call
_store = None
_insight_store = None


async def _get_store():
    global _store
    if _store is None:
        lancedb_path = os.environ.get(
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
) -> str:
    """Query accumulated insights from the InsightStore.

    Args:
        topic: Topic keywords to search.
        lifecycle: Lifecycle filter. Options: active, dormant, archived, expired.
        n: Number of results (default 5).
    """
    istore = await _get_insight_store()
    lifecycle_list = [lifecycle] if lifecycle else None
    results = await istore.query_insights(topic, lifecycle=lifecycle_list, limit=n)
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
        {"error": "nstc_extract is not yet implemented. Coming in Phase 0-C."},
        ensure_ascii=False,
    )


if __name__ == "__main__":
    mcp.run()

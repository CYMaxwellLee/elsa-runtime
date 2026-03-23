"""
Step 4: LLM + Retrieval integration tests.
Test 4.1: Context assembly (no LLM needed).
Test 4.2/4.3: LLM calls — skipped because elsa_runtime.llm.client doesn't exist yet.
"""

import shutil
from pathlib import Path

import pytest

from elsa_runtime.storage.lancedb_store import LanceDBStore
from elsa_runtime.knowledge.insight_store import InsightStore

TEST_DB_PATH = "/tmp/elsa-integration-test/step4"

PAPER_SEED = [
    {
        "id": "paper-prime",
        "text": "Prime is a universal robot policy interface that enables cross-embodiment transfer. It achieves 95.2% success rate on the CALVIN benchmark, surpassing ARM.",
        "metadata": {"arxiv_id": "2402.14545", "title": "Prime", "tier": "A", "domain": "robotics", "created_at": "2024-02-22"},
    },
    {
        "id": "paper-diffusion-policy",
        "text": "Diffusion Policy represents visuomotor policy as conditional denoising diffusion, achieving SOTA across 11 tasks spanning 4 benchmarks.",
        "metadata": {"arxiv_id": "2303.04137", "title": "Diffusion Policy", "tier": "A", "domain": "robotics", "created_at": "2023-03-07"},
    },
    {
        "id": "paper-dpo",
        "text": "Direct Preference Optimization simplifies RLHF by directly optimizing without reward models. Stable, performant, lightweight.",
        "metadata": {"arxiv_id": "2305.18290", "title": "DPO", "tier": "A", "domain": "alignment", "created_at": "2023-05-29"},
    },
]

INSIGHT_SEED = [
    {
        "agent": "rei",
        "domain": "research",
        "task_type": "paper_analysis",
        "context": "Cross-embodiment transfer analysis",
        "content": "Cross-embodiment papers often overstate generalization. Check whether test embodiments share similar morphology or are truly different.",
        "scope": "team",
        "confidence": 0.85,
    },
]


@pytest.fixture(autouse=True)
def clean_db():
    if Path(TEST_DB_PATH).exists():
        shutil.rmtree(TEST_DB_PATH)
    yield


@pytest.fixture
async def seeded_env():
    store = LanceDBStore(path=TEST_DB_PATH)
    await store.connect()
    await store.ensure_table("papers")
    await store.add(
        "papers",
        ids=[p["id"] for p in PAPER_SEED],
        documents=[p["text"] for p in PAPER_SEED],
        metadatas=[p["metadata"] for p in PAPER_SEED],
    )

    istore = InsightStore(store)
    await istore.initialize()
    for seed in INSIGHT_SEED:
        await istore.create_insight(**seed)

    return store, istore


# ── Test 4.1: Retrieval context assembly (no LLM) ────────────

@pytest.mark.asyncio
async def test_retrieval_context_assembly(seeded_env):
    """No LLM call — just validate context assembly logic."""
    store, istore = seeded_env

    query = "How does Prime compare to Diffusion Policy for robotic manipulation?"

    paper_results = await store.search("papers", query, n=3, query_type="vector")
    insight_results = await istore.query_insights(query)

    # Assemble context
    context_parts = []
    if paper_results:
        context_parts.append("## Related Papers in Knowledge Base")
        for r in paper_results:
            title = r.metadata.get("title", "Unknown")
            context_parts.append(f"- [{title}] {r.content}")

    if insight_results:
        context_parts.append("\n## Relevant Insights from Past Analysis")
        for r in insight_results:
            context_parts.append(f"- {r.content}")

    context = "\n".join(context_parts)
    print("=== Assembled Context ===")
    print(context)
    print(f"=== Context length: {len(context)} chars ===")

    assert "Prime" in context, "Prime should appear in context"
    assert len(context) > 100, "Context should have meaningful content"


# ── Test 4.2: LLM uses injected knowledge ────────────────────

@pytest.mark.asyncio
@pytest.mark.skip(reason="elsa_runtime.llm.client module not yet implemented")
async def test_llm_uses_injected_knowledge(seeded_env):
    """Requires elsa_runtime.llm.client + ANTHROPIC_API_KEY."""
    pass


# ── Test 4.3: LLM without context baseline ───────────────────

@pytest.mark.asyncio
@pytest.mark.skip(reason="elsa_runtime.llm.client module not yet implemented")
async def test_llm_without_context_baseline(seeded_env):
    """Requires elsa_runtime.llm.client + ANTHROPIC_API_KEY."""
    pass

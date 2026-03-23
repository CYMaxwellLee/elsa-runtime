"""
Step 5: Knowledge accumulation loop tests.
Simulates: analyze paper -> produce insight -> write back -> next query finds fresh knowledge.
No LLM calls — uses hardcoded simulated LLM output. Pure knowledge flow test.
"""

import shutil
from pathlib import Path

import pytest

from elsa_runtime.storage.lancedb_store import LanceDBStore
from elsa_runtime.knowledge.insight_store import InsightStore

TEST_DB_PATH = "/tmp/elsa-integration-test/step5"

INITIAL_PAPERS = [
    {
        "id": "paper-prime",
        "text": "Prime is a universal robot policy interface achieving 95.2% on CALVIN.",
        "metadata": {"arxiv_id": "2402.14545", "title": "Prime", "tier": "A", "domain": "robotics", "created_at": "2024-02-22"},
    },
]

INITIAL_INSIGHTS = [
    {
        "agent": "rei",
        "domain": "research",
        "task_type": "paper_analysis",
        "context": "Analyzing Prime paper",
        "content": "Prime's action tokenization is the key innovation. But the 95.2% CALVIN score uses privileged oracle information for language grounding.",
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
async def env():
    store = LanceDBStore(path=TEST_DB_PATH)
    await store.connect()
    await store.ensure_table("papers")
    await store.add(
        "papers",
        ids=[p["id"] for p in INITIAL_PAPERS],
        documents=[p["text"] for p in INITIAL_PAPERS],
        metadatas=[p["metadata"] for p in INITIAL_PAPERS],
    )
    istore = InsightStore(store)
    await istore.initialize()
    for seed in INITIAL_INSIGHTS:
        await istore.create_insight(**seed)
    return store, istore


# ── Test 5.1: Write new analysis results back ────────────────

@pytest.mark.asyncio
async def test_write_new_analysis_results(env):
    """Simulate Rei finishing Diffusion Policy analysis, writing back paper + insight."""
    store, istore = env

    await store.add(
        "papers",
        ids=["paper-diffusion-policy"],
        documents=["Diffusion Policy uses conditional denoising for visuomotor control. Unlike Prime's discrete tokenization, it operates in continuous action space. Achieves SOTA on 11 tasks but requires 10x more demonstration data than Prime."],
        metadatas=[{
            "arxiv_id": "2303.04137",
            "title": "Diffusion Policy",
            "tier": "A",
            "domain": "robotics",
            "created_at": "2023-03-07",
        }],
    )

    await istore.create_insight(
        agent="rei",
        domain="research",
        task_type="paper_analysis",
        context="Comparing Prime vs Diffusion Policy",
        content="Prime and Diffusion Policy represent two opposing design choices: discrete tokenization vs continuous diffusion. Prime needs less data but loses fine-grained control. The tradeoff depends on whether the target task requires precision or generalization.",
        scope="team",
        confidence=0.90,
    )

    count = await store.count("papers")
    assert count == 2, f"Should have 2 papers, got {count}"


# ── Test 5.2: New query finds fresh knowledge ────────────────

@pytest.mark.asyncio
async def test_new_query_finds_fresh_knowledge(env):
    """
    After writing new analysis, query 'discrete vs continuous action space'
    should find the fresh Diffusion Policy analysis and comparison insight.
    """
    store, istore = env

    # Write fresh knowledge
    await store.add(
        "papers",
        ids=["paper-diffusion-policy"],
        documents=["Diffusion Policy uses conditional denoising for visuomotor control. Unlike Prime's discrete tokenization, it operates in continuous action space. Achieves SOTA on 11 tasks but requires 10x more demonstration data."],
        metadatas=[{"arxiv_id": "2303.04137", "title": "Diffusion Policy", "tier": "A", "domain": "robotics", "created_at": "2023-03-07"}],
    )
    await istore.create_insight(
        agent="rei",
        domain="research",
        task_type="paper_analysis",
        context="Comparing Prime vs Diffusion Policy",
        content="Prime and Diffusion Policy represent two opposing design choices: discrete tokenization vs continuous diffusion. The tradeoff depends on precision vs generalization.",
        scope="team",
        confidence=0.90,
    )

    # New query
    paper_results = await store.search(
        "papers", "discrete vs continuous action space for robot policy",
        n=3, query_type="vector",
    )
    insight_results = await istore.query_insights(
        "tradeoff between discrete tokenization and continuous diffusion"
    )

    print("=== Paper Results ===")
    for r in paper_results:
        print(f"  {r.id}: {r.content[:80]}...")

    print("=== Insight Results ===")
    for r in insight_results:
        print(f"  {r.content[:80]}...")

    paper_ids = [r.id for r in paper_results]
    assert "paper-diffusion-policy" in paper_ids, \
        f"Fresh paper should be found, got {paper_ids}"

    assert len(insight_results) >= 1, "Fresh insight should be found"
    found_comparison = any(
        "discrete" in r.content.lower() or "continuous" in r.content.lower()
        for r in insight_results
    )
    assert found_comparison, "Should find the discrete vs continuous insight"


# ── Test 5.3: Knowledge accumulation trace ────────────────────

@pytest.mark.asyncio
async def test_knowledge_accumulation_trace(env):
    """
    Simulate three rounds of analysis. Each round should find more results.
    Validates: knowledge truly accumulates, not reset each time.
    """
    store, istore = env

    # Round 1: Only Prime
    r1 = await store.search("papers", "robot policy", n=5, query_type="vector")
    r1_count = len(r1)
    print(f"Round 1: {r1_count} results")

    # Round 2: Add Diffusion Policy
    await store.add("papers", ids=["paper-dp"],
                     documents=["Diffusion Policy for visuomotor control"],
                     metadatas=[{"title": "DP", "tier": "A", "domain": "robotics", "created_at": "2023-03-07"}])

    r2 = await store.search("papers", "robot policy", n=5, query_type="vector")
    r2_count = len(r2)
    print(f"Round 2: {r2_count} results")

    # Round 3: Add SE(3) equivariant
    await store.add("papers", ids=["paper-se3"],
                     documents=["SE(3) equivariant neural networks for robotic manipulation"],
                     metadatas=[{"title": "SE3", "tier": "A", "domain": "robotics", "created_at": "2023-01-15"}])

    r3 = await store.search("papers", "robot policy", n=5, query_type="vector")
    r3_count = len(r3)
    print(f"Round 3: {r3_count} results")

    assert r3_count >= r2_count >= r1_count, \
        f"Knowledge should accumulate: {r1_count} -> {r2_count} -> {r3_count}"
    assert r3_count >= 3, "Should have at least 3 papers by round 3"

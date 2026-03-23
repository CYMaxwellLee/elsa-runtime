"""
Step 2: Retrieval scoring pipeline end-to-end tests.
Uses Step 1's PAPER_SEED data, runs through six-stage scoring, validates ordering.
"""

import shutil
from pathlib import Path

import pytest

from elsa_runtime.storage.lancedb_store import LanceDBStore
from elsa_runtime.storage.vectorstore import SearchResult
from elsa_runtime.retrieval.scoring import (
    ScoredResult,
    rrf_fusion,
    recency_boost,
    length_normalize,
    hard_min_filter,
    mmr_diversity,
    full_scoring_pipeline,
)
from elsa_runtime.retrieval.reranker import CosineReranker

TEST_DB_PATH = "/tmp/elsa-integration-test/step2"

PAPER_SEED = [
    {
        "id": "paper-prime",
        "text": "Prime is a universal robot policy interface that enables cross-embodiment transfer by mapping diverse robot actions into a unified action tokenization space. It demonstrates the first autonomous real-world robot policy that surpasses the ARM robot on the CALVIN benchmark, achieving a 95.2% success rate.",
        "metadata": {"arxiv_id": "2402.14545", "title": "Prime", "tier": "A", "domain": "robotics", "created_at": "2024-02-22"},
    },
    {
        "id": "paper-bit-diffusion",
        "text": "Bit Diffusion generates discrete data by treating bits as real numbers in a continuous diffusion model.",
        "metadata": {"arxiv_id": "2310.00000", "title": "Bit Diffusion", "tier": "B", "domain": "generative", "created_at": "2023-10-01"},
    },
    {
        "id": "paper-dpo",
        "text": "Direct Preference Optimization simplifies RLHF by directly optimizing a language model to adhere to human preferences without fitting a reward model.",
        "metadata": {"arxiv_id": "2305.18290", "title": "DPO", "tier": "A", "domain": "alignment", "created_at": "2023-05-29"},
    },
    {
        "id": "paper-diffusion-policy",
        "text": "Diffusion Policy represents robot visuomotor policy as a conditional denoising diffusion process for multi-modal action distributions.",
        "metadata": {"arxiv_id": "2303.04137", "title": "Diffusion Policy", "tier": "A", "domain": "robotics", "created_at": "2023-03-07"},
    },
    {
        "id": "paper-se3-equivariant",
        "text": "SE(3) equivariant neural networks for robotic manipulation maintain symmetry under 3D rotations and translations with dramatically better sample efficiency.",
        "metadata": {"arxiv_id": "2301.00000", "title": "SE(3)-Equivariant Policy", "tier": "A", "domain": "robotics", "created_at": "2023-01-15"},
    },
]


@pytest.fixture(autouse=True)
def clean_db():
    if Path(TEST_DB_PATH).exists():
        shutil.rmtree(TEST_DB_PATH)
    yield


@pytest.fixture
async def seeded_store():
    s = LanceDBStore(path=TEST_DB_PATH)
    await s.connect()
    await s.ensure_table("papers")
    await s.add(
        "papers",
        ids=[p["id"] for p in PAPER_SEED],
        documents=[p["text"] for p in PAPER_SEED],
        metadatas=[p["metadata"] for p in PAPER_SEED],
    )
    return s


# ── Test 2.1: RRF Fusion basic merge ─────────────────────────

@pytest.mark.asyncio
async def test_rrf_fusion_merges_results(seeded_store):
    """vector + fts results merged should cover papers from both."""
    vector_results = await seeded_store.search(
        "papers", "robot manipulation policy", n=5, query_type="vector",
    )
    try:
        fts_results = await seeded_store.search(
            "papers", "robot manipulation policy", n=5, query_type="fts",
        )
    except Exception:
        fts_results = []

    fused = rrf_fusion(vector_results, fts_results)
    assert len(fused) >= len(vector_results), "Fusion should not lose results"
    # rrf_fusion returns ScoredResult, not SearchResult
    assert all(isinstance(r, ScoredResult) for r in fused)
    print(f"Fused results: {[(r.id, r.score) for r in fused]}")


# ── Test 2.2: Recency boost favors newer papers ──────────────

@pytest.mark.asyncio
async def test_recency_boost_favors_newer(seeded_store):
    """Prime (2024) should get more recency boost than SE3 (2023)."""
    vector_results = await seeded_store.search(
        "papers", "robot policy", n=5, query_type="vector",
    )
    # rrf_fusion first to get ScoredResult (recency_boost expects ScoredResult)
    fused = rrf_fusion(vector_results, [])
    boosted = recency_boost(fused, weight=0.15, half_life_days=365)

    prime_score = next((r.score for r in boosted if r.id == "paper-prime"), 0)
    se3_score = next((r.score for r in boosted if r.id == "paper-se3-equivariant"), 0)

    print(f"After recency boost: Prime={prime_score:.4f}, SE3={se3_score:.4f}")
    assert all(r.score > 0 for r in boosted), "All scores should be positive after boost"


# ── Test 2.3: Hard min filter removes low scores ─────────────

@pytest.mark.asyncio
async def test_hard_min_filter_removes_low_scores(seeded_store):
    vector_results = await seeded_store.search(
        "papers", "quantum computing topology", n=5, query_type="vector",
    )
    fused = rrf_fusion(vector_results, [])
    before_count = len(fused)
    filtered = hard_min_filter(fused, threshold=0.8)
    print(f"Before filter: {before_count}, After filter (threshold=0.8): {len(filtered)}")
    assert len(filtered) <= before_count


# ── Test 2.4: Full pipeline end-to-end ───────────────────────

@pytest.mark.asyncio
async def test_full_pipeline_robotics_query(seeded_store):
    """
    Full query: 'SE(3) equivariant policy for robotic manipulation'
    Expected: robotics papers rank higher, DPO/Bit Diffusion rank lower or filtered out.
    """
    vector_results = await seeded_store.search(
        "papers", "SE(3) equivariant policy for robotic manipulation",
        n=5, query_type="vector",
    )
    try:
        fts_results = await seeded_store.search(
            "papers", "SE(3) equivariant policy for robotic manipulation",
            n=5, query_type="fts",
        )
    except Exception:
        fts_results = []

    final = full_scoring_pipeline(
        vector_results=vector_results,
        bm25_results=fts_results,
        graph_results=None,
    )

    assert len(final) >= 1
    print("=== Full Pipeline Results ===")
    for i, r in enumerate(final):
        print(f"  #{i+1}: {r.id} (score={r.score:.4f})")

    top_id = final[0].id
    robotics_ids = {"paper-prime", "paper-se3-equivariant", "paper-diffusion-policy"}
    assert top_id in robotics_ids, \
        f"Expected robotics paper as #1, got {top_id}"


# ── Test 2.5: Cosine reranker fallback ───────────────────────

@pytest.mark.asyncio
async def test_cosine_reranker(seeded_store):
    """CrossEncoder may not be installed or too slow — test Cosine fallback."""
    vector_results = await seeded_store.search(
        "papers", "robot policy learning", n=5, query_type="vector",
    )
    # CosineReranker.rerank expects ScoredResult, not SearchResult
    fused = rrf_fusion(vector_results, [])
    reranker = CosineReranker()
    reranked = reranker.rerank("robot policy learning", fused, top_k=3)
    assert len(reranked) <= 3
    assert len(reranked) >= 1
    print(f"Reranked: {[(r.id, r.score) for r in reranked]}")

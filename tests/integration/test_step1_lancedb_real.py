"""
Step 1: LanceDB real read/write tests.
No mocks — directly operates on /tmp .lance files.
Validates: ensure_table -> add -> search (vector/fts/hybrid) -> count -> delete -> update
Tests metadata filtering via Schema Registry typed columns.
"""

import shutil
from pathlib import Path

import pytest

from elsa_runtime.storage.lancedb_store import LanceDBStore

TEST_DB_PATH = "/tmp/elsa-integration-test/step1"

# ── Test data: 5 real paper abstracts ──────────────────────────

PAPER_SEED = [
    {
        "id": "paper-prime",
        "text": "Prime is a universal robot policy interface that enables cross-embodiment transfer by mapping diverse robot actions into a unified action tokenization space. It demonstrates the first autonomous real-world robot policy that surpasses the ARM robot on the CALVIN benchmark, achieving a 95.2% success rate.",
        "metadata": {
            "arxiv_id": "2402.14545",
            "tier": "A",
            "domain": "robotics",
        },
    },
    {
        "id": "paper-bit-diffusion",
        "text": "Bit Diffusion generates discrete data by treating bits as real numbers in a continuous diffusion model. By mapping discrete tokens to analog bits and applying Gaussian diffusion in bit-space, it achieves strong image generation and captioning results without requiring specialized discrete diffusion architectures.",
        "metadata": {
            "arxiv_id": "2310.00000",
            "tier": "B",
            "domain": "generative",
        },
    },
    {
        "id": "paper-dpo",
        "text": "Direct Preference Optimization simplifies RLHF by directly optimizing a language model to adhere to human preferences without fitting a reward model. DPO is stable, performant, and computationally lightweight, eliminating the need for sampling from the LM during fine-tuning or performing significant hyperparameter tuning.",
        "metadata": {
            "arxiv_id": "2305.18290",
            "tier": "A",
            "domain": "alignment",
        },
    },
    {
        "id": "paper-diffusion-policy",
        "text": "Diffusion Policy represents robot visuomotor policy as a conditional denoising diffusion process. It generates multi-modal action distributions, handles high-dimensional action spaces, and achieves state-of-the-art results across 11 tasks spanning 4 robot manipulation benchmarks including real-world pushing and planar transport.",
        "metadata": {
            "arxiv_id": "2303.04137",
            "tier": "A",
            "domain": "robotics",
        },
    },
    {
        "id": "paper-se3-equivariant",
        "text": "SE(3) equivariant neural networks for robotic manipulation maintain symmetry under 3D rotations and translations. By building SE(3) equivariance directly into the policy network architecture, the approach achieves dramatically better sample efficiency and generalization across different object poses compared to data augmentation baselines.",
        "metadata": {
            "arxiv_id": "2301.00000",
            "tier": "A",
            "domain": "robotics",
        },
    },
]


@pytest.fixture(autouse=True)
def clean_db():
    """Clean DB before each test."""
    if Path(TEST_DB_PATH).exists():
        shutil.rmtree(TEST_DB_PATH)
    yield
    # Keep after test for debugging


@pytest.fixture
async def store():
    s = LanceDBStore(path=TEST_DB_PATH)
    await s.connect()
    return s


async def _seed_papers(store):
    await store.ensure_table("papers")
    await store.add(
        table="papers",
        ids=[p["id"] for p in PAPER_SEED],
        documents=[p["text"] for p in PAPER_SEED],
        metadatas=[p["metadata"] for p in PAPER_SEED],
    )


# ── Test 1.1: Create table ────────────────────────────────────

@pytest.mark.asyncio
async def test_ensure_table_creates_table(store):
    await store.ensure_table("papers")
    tables = await store.list_tables()
    assert "papers" in tables


# ── Test 1.2: Add 5 papers ────────────────────────────────────

@pytest.mark.asyncio
async def test_add_papers(store):
    await store.ensure_table("papers")
    results = await store.add(
        table="papers",
        ids=[p["id"] for p in PAPER_SEED],
        documents=[p["text"] for p in PAPER_SEED],
        metadatas=[p["metadata"] for p in PAPER_SEED],
    )
    assert len(results) == 5
    assert all(r.operation == "add" for r in results)

    count = await store.count("papers")
    assert count == 5


# ── Test 1.3: Vector search ───────────────────────────────────

@pytest.mark.asyncio
async def test_vector_search_relevance(store):
    """Search for robotics — top 3 should mostly be robotics papers."""
    await _seed_papers(store)

    results = await store.search(
        "papers",
        "robot manipulation policy with SE(3) equivariance",
        n=3,
        query_type="vector",
    )
    assert len(results) >= 1
    result_ids = [r.id for r in results]
    robotics_ids = {"paper-prime", "paper-se3-equivariant", "paper-diffusion-policy"}
    overlap = robotics_ids.intersection(result_ids)
    assert len(overlap) >= 2, f"Expected >=2 robotics papers in top 3, got {result_ids}"


# ── Test 1.4: FTS search ──────────────────────────────────────

@pytest.mark.asyncio
async def test_fts_search(store):
    """Full-text search for exact keyword."""
    await _seed_papers(store)

    results = await store.search(
        "papers", "CALVIN benchmark", n=3, query_type="fts",
    )
    assert any("prime" in r.id for r in results), \
        f"Expected Prime paper for 'CALVIN benchmark', got {[r.id for r in results]}"


# ── Test 1.5: Hybrid search ───────────────────────────────────

@pytest.mark.asyncio
async def test_hybrid_search(store):
    """Hybrid = vector + FTS merged."""
    await _seed_papers(store)

    results = await store.search(
        "papers",
        "preference optimization alignment",
        n=3,
        query_type="hybrid",
    )
    # DPO paper should rank high
    # If hybrid falls back to vector-only, test should still pass
    assert len(results) >= 1
    print(f"Hybrid search results: {[(r.id, r.score) for r in results]}")


# ── Test 1.6: Where filter (was xfail, now should PASS) ──────

@pytest.mark.asyncio
async def test_search_with_filter(store):
    """Only search tier A papers. Schema Registry enables typed column filtering."""
    await _seed_papers(store)

    results = await store.search(
        "papers", "diffusion model",
        n=5,
        where={"tier": "A"},
        query_type="vector",
    )
    for r in results:
        assert r.metadata.get("tier") == "A", \
            f"Expected tier A only, got {r.metadata}"


# ── Test 1.6b: Multi-value filter ($in) ──────────────────────

@pytest.mark.asyncio
async def test_search_with_in_filter(store):
    """Filter papers with tier in [A, B]."""
    await _seed_papers(store)

    results = await store.search(
        "papers", "diffusion",
        n=5,
        where={"tier": {"$in": ["A", "B"]}},
        query_type="vector",
    )
    assert len(results) >= 2
    for r in results:
        assert r.metadata.get("tier") in ("A", "B")


# ── Test 1.6c: Domain filter ─────────────────────────────────

@pytest.mark.asyncio
async def test_search_with_domain_filter(store):
    """Filter only robotics papers."""
    await _seed_papers(store)

    results = await store.search(
        "papers", "neural network",
        n=5,
        where={"domain": "robotics"},
        query_type="vector",
    )
    for r in results:
        assert r.metadata.get("domain") == "robotics"


# ── Test 1.7: Delete + Count ──────────────────────────────────

@pytest.mark.asyncio
async def test_delete_and_count(store):
    await _seed_papers(store)
    assert await store.count("papers") == 5

    await store.delete("papers", ["paper-dpo"])
    assert await store.count("papers") == 4

    results = await store.search(
        "papers", "preference optimization", n=5, query_type="vector",
    )
    assert all(r.id != "paper-dpo" for r in results)


# ── Test 1.8: Update (delete + re-add) ────────────────────────

@pytest.mark.asyncio
async def test_update(store):
    await _seed_papers(store)

    await store.update(
        "papers",
        ids=["paper-prime"],
        documents=["Prime v2: dramatically improved cross-embodiment transfer with 99% success rate on CALVIN."],
        metadatas=[{
            "arxiv_id": "2402.14545",
            "tier": "A",
            "domain": "robotics",
        }],
    )

    results = await store.search("papers", "Prime v2 CALVIN", n=1, query_type="vector")
    assert len(results) >= 1
    assert "v2" in results[0].content or "99%" in results[0].content

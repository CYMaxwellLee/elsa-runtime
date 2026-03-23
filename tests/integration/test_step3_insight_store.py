"""
Step 3: InsightStore real CRUD tests.
No mocks — directly writes to LanceDB.
"""

import shutil
from pathlib import Path

import pytest

from elsa_runtime.storage.lancedb_store import LanceDBStore
from elsa_runtime.knowledge.insight_store import InsightStore

TEST_DB_PATH = "/tmp/elsa-integration-test/step3"

INSIGHT_SEED = [
    {
        "agent": "rei",
        "domain": "research",
        "task_type": "paper_analysis",
        "context": "Analyzing memory-augmented transformer papers",
        "content": "Authors frequently hide critical ablation studies in appendices rather than the main body. Always check appendix tables for the real performance breakdown.",
        "scope": "team",
        "confidence": 0.85,
    },
    {
        "agent": "rei",
        "domain": "research",
        "task_type": "paper_analysis",
        "context": "Reviewing diffusion policy papers",
        "content": "Diffusion policy papers often claim SOTA but use non-standard evaluation protocols. Cross-check whether baselines use the same number of demonstrations and the same success metric.",
        "scope": "team",
        "confidence": 0.90,
    },
    {
        "agent": "elsa",
        "domain": "orchestration",
        "task_type": "email_triage",
        "context": "Processing NeurIPS reviewer responses",
        "content": "When reviewer tone is aggressive but score is borderline, prioritize addressing their specific technical concern rather than defending. Rebuttal acceptance rate is higher with this approach.",
        "scope": "team",
        "confidence": 0.75,
    },
]


@pytest.fixture(autouse=True)
def clean_db():
    if Path(TEST_DB_PATH).exists():
        shutil.rmtree(TEST_DB_PATH)
    yield


@pytest.fixture
async def insight_store():
    """Build InsightStore with real LanceDBStore."""
    store = LanceDBStore(path=TEST_DB_PATH)
    await store.connect()
    istore = InsightStore(store)
    await istore.initialize()  # MUST call — ensures "insights" table exists
    return istore


async def _seed_insights(istore):
    ids = []
    for seed in INSIGHT_SEED:
        insight_id = await istore.create_insight(**seed)
        ids.append(insight_id)
    return ids


# ── Test 3.1: Create + Query ──────────────────────────────────

@pytest.mark.asyncio
async def test_create_and_query_insights(insight_store):
    """Write 3 insights, query should find relevant ones."""
    await _seed_insights(insight_store)

    results = await insight_store.query_insights("ablation study appendix")
    assert len(results) >= 1
    print(f"Query 'ablation study appendix': {[(r.id, r.content[:50]) for r in results]}")
    assert "appendix" in results[0].content.lower() or "ablation" in results[0].content.lower()


# ── Test 3.2: Cross-domain query ──────────────────────────────

@pytest.mark.asyncio
async def test_cross_domain_query(insight_store):
    """Query email/reviewer related — should find Elsa's insight."""
    await _seed_insights(insight_store)

    results = await insight_store.query_insights("reviewer rebuttal strategy")
    assert len(results) >= 1
    found_elsa = any("rebuttal" in r.content.lower() or "reviewer" in r.content.lower() for r in results)
    assert found_elsa, "Should find Elsa's reviewer insight"


# ── Test 3.3: Lifecycle transition (was xfail, now should PASS) ──

@pytest.mark.asyncio
async def test_lifecycle_transition(insight_store):
    """Active -> Dormant, then active-only query should not find it."""
    ids = await _seed_insights(insight_store)
    target_id = ids[0]  # First insight

    await insight_store.update_lifecycle(target_id, "dormant")

    # Query with lifecycle filter = ["active"] only
    active_results = await insight_store.query_insights(
        "ablation appendix",
        lifecycle=["active"],
    )
    active_ids = [r.id for r in active_results]
    print(f"After dormant transition: active_ids={active_ids}")
    assert target_id not in active_ids, \
        f"Dormant insight {target_id} should not appear in active-only query"


# ── Test 3.3b: Lifecycle filter active returns only active ────

@pytest.mark.asyncio
async def test_lifecycle_filter_active_only(insight_store):
    """Create active + dormant insights, filter active only."""
    ids = await _seed_insights(insight_store)

    # Make second insight dormant
    await insight_store.update_lifecycle(ids[1], "dormant")

    active_results = await insight_store.query_insights(
        "paper analysis",
        lifecycle=["active"],
    )
    active_ids = [r.id for r in active_results]
    assert ids[1] not in active_ids, "Dormant insight should be excluded"


# ── Test 3.4: Deprecate ──────────────────────────────────────

@pytest.mark.asyncio
async def test_deprecate_insight(insight_store):
    """Deprecate marks lifecycle as expired."""
    ids = await _seed_insights(insight_store)
    target_id = ids[1]  # Second insight

    # deprecate_insight requires `reason` parameter
    await insight_store.deprecate_insight(target_id, reason="Superseded by newer evaluation protocol")

    print(f"Deprecated insight {target_id}")


# ── Test 3.5: Similar insight detection (skip if not implemented) ──

@pytest.mark.asyncio
async def test_similar_insight_detection(insight_store):
    """
    Write an insight very similar to an existing one.
    If semantic dedup is implemented, should return NOOP or UPDATE.
    If not implemented yet, skip.
    """
    await _seed_insights(insight_store)

    # Very similar to first seed insight
    similar_content = "Always look at appendix tables for hidden ablation results — authors tend to bury critical numbers there."

    # No dedup API exists yet — just verify we can create it without error
    new_id = await insight_store.create_insight(
        agent="rei",
        domain="research",
        task_type="paper_analysis",
        content=similar_content,
        confidence=0.80,
    )
    assert new_id is not None
    print(f"NOTE: Semantic dedup not yet implemented. Created duplicate as {new_id}")

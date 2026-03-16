"""Tests for retrieval scoring pipeline and rerankers."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from elsa_runtime.retrieval.reranker import CosineReranker, CrossEncoderReranker, get_reranker
from elsa_runtime.retrieval.scoring import (
    ScoredResult,
    full_scoring_pipeline,
    hard_min_filter,
    length_normalize,
    mmr_diversity,
    recency_boost,
    rrf_fusion,
)
from elsa_runtime.storage.vectorstore import SearchResult


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_search_result(
    id: str, content: str = "test content", score: float = 0.8, **meta
) -> SearchResult:
    return SearchResult(id=id, content=content, metadata=dict(meta), score=score)


def _make_scored_result(
    id: str, content: str = "test content", score: float = 0.8, **meta
) -> ScoredResult:
    return ScoredResult(
        id=id, content=content, metadata=dict(meta), score=score, original_score=score
    )


# ---------------------------------------------------------------------------
# ScoredResult dataclass
# ---------------------------------------------------------------------------


class TestScoredResultDataclass:
    def test_scored_result_dataclass(self):
        r = ScoredResult(id="a", content="hello", score=0.9, source="vector")
        assert r.id == "a"
        assert r.content == "hello"
        assert r.score == 0.9
        assert r.source == "vector"
        assert r.metadata == {}
        assert r.original_score == 0.0

    def test_scored_result_defaults(self):
        r = ScoredResult(id="b", content="world")
        assert r.score == 0.0
        assert r.original_score == 0.0
        assert r.source == ""
        assert r.metadata == {}


# ---------------------------------------------------------------------------
# RRF fusion
# ---------------------------------------------------------------------------


class TestRRFFusion:
    def test_rrf_fusion_basic(self):
        """Merge vector + bm25 results."""
        vector = [
            _make_search_result("1", "doc one", score=0.9),
            _make_search_result("2", "doc two", score=0.7),
        ]
        bm25 = [
            _make_search_result("1", "doc one", score=0.85),
            _make_search_result("3", "doc three", score=0.6),
        ]
        results = rrf_fusion(vector, bm25)

        ids = [r.id for r in results]
        assert "1" in ids
        assert "2" in ids
        assert "3" in ids

        # Result "1" appears in both, should have boosted score
        r1 = next(r for r in results if r.id == "1")
        assert r1.score > 0.9  # boosted by bm25

        # Results should be sorted descending
        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_rrf_fusion_with_graph(self):
        """Graph results add a 10% boost."""
        vector = [_make_search_result("1", "doc one", score=0.8)]
        bm25 = [_make_search_result("1", "doc one", score=0.7)]
        graph = [_make_search_result("1", "doc one", score=0.6)]

        results = rrf_fusion(vector, bm25, graph)
        r1 = results[0]

        # base 0.8 + 15% bm25 (0.12) + 10% graph (0.08) = 1.0
        expected = 0.8 + 0.8 * 0.15 + 0.8 * 0.10
        assert abs(r1.score - expected) < 1e-9

    def test_rrf_boost_values(self):
        """Verify exact 15% bm25 and 10% graph boosts."""
        base_score = 1.0
        vector = [_make_search_result("a", score=base_score)]

        # BM25 only
        bm25 = [_make_search_result("a", score=0.5)]
        results_bm25 = rrf_fusion(vector, bm25)
        r = results_bm25[0]
        assert abs(r.score - (base_score + base_score * 0.15)) < 1e-9

        # Graph only
        vector2 = [_make_search_result("a", score=base_score)]
        graph = [_make_search_result("a", score=0.5)]
        results_graph = rrf_fusion(vector2, [], graph)
        r2 = results_graph[0]
        assert abs(r2.score - (base_score + base_score * 0.10)) < 1e-9

    def test_rrf_fusion_empty_inputs(self):
        results = rrf_fusion([], [])
        assert results == []

    def test_rrf_fusion_no_overlap(self):
        """Disjoint vector and bm25 sets produce all results."""
        vector = [_make_search_result("1", score=0.9)]
        bm25 = [_make_search_result("2", score=0.7)]
        results = rrf_fusion(vector, bm25)
        assert len(results) == 2


# ---------------------------------------------------------------------------
# Recency boost
# ---------------------------------------------------------------------------


class TestRecencyBoost:
    def test_recency_boost_newer_scores_higher(self):
        now = datetime.now(timezone.utc)
        recent = _make_scored_result(
            "r", score=0.5, created_at=(now - timedelta(days=1)).isoformat()
        )
        old = _make_scored_result(
            "o", score=0.5, created_at=(now - timedelta(days=30)).isoformat()
        )

        results = recency_boost([recent, old])
        assert results[0].score > results[1].score  # recent gets bigger boost

    def test_recency_boost_no_created_at(self):
        """Gracefully skip when created_at is missing."""
        r = _make_scored_result("x", score=0.5)
        original_score = r.score
        results = recency_boost([r])
        assert results[0].score == original_score  # unchanged

    def test_recency_boost_invalid_date(self):
        """Gracefully skip invalid date strings."""
        r = _make_scored_result("x", score=0.5, created_at="not-a-date")
        original_score = r.score
        results = recency_boost([r])
        assert results[0].score == original_score


# ---------------------------------------------------------------------------
# Length normalize
# ---------------------------------------------------------------------------


class TestLengthNormalize:
    def test_length_normalize_long_content_penalized(self):
        long_text = "word " * 200  # 1000 chars
        r = _make_scored_result("long", content=long_text, score=1.0)
        results = length_normalize([r], anchor=500)
        assert results[0].score < 1.0
        assert abs(results[0].score - 500 / len(long_text)) < 1e-9

    def test_length_normalize_short_content_unchanged(self):
        short_text = "hello world"
        r = _make_scored_result("short", content=short_text, score=1.0)
        results = length_normalize([r], anchor=500)
        assert results[0].score == 1.0

    def test_length_normalize_exact_anchor(self):
        text = "x" * 500
        r = _make_scored_result("exact", content=text, score=1.0)
        results = length_normalize([r], anchor=500)
        assert results[0].score == 1.0


# ---------------------------------------------------------------------------
# Hard min filter
# ---------------------------------------------------------------------------


class TestHardMinFilter:
    def test_hard_min_filter_removes_low_scores(self):
        results = [
            _make_scored_result("a", score=0.5),
            _make_scored_result("b", score=0.2),
            _make_scored_result("c", score=0.8),
        ]
        filtered = hard_min_filter(results, threshold=0.3)
        ids = [r.id for r in filtered]
        assert "a" in ids
        assert "c" in ids
        assert "b" not in ids

    def test_hard_min_filter_empty_input(self):
        assert hard_min_filter([]) == []

    def test_hard_min_filter_all_pass(self):
        results = [_make_scored_result("a", score=0.9)]
        filtered = hard_min_filter(results, threshold=0.1)
        assert len(filtered) == 1

    def test_hard_min_filter_exact_threshold(self):
        results = [_make_scored_result("a", score=0.3)]
        filtered = hard_min_filter(results, threshold=0.3)
        assert len(filtered) == 1  # >= threshold passes


# ---------------------------------------------------------------------------
# MMR diversity
# ---------------------------------------------------------------------------


class TestMMRDiversity:
    def test_mmr_diversity_limits_results(self):
        results = [_make_scored_result(str(i), score=1.0 - i * 0.05) for i in range(20)]
        selected = mmr_diversity(results, top_k=5)
        assert len(selected) == 5

    def test_mmr_diversity_different_content(self):
        """Different content should all be selected before duplicates."""
        results = [
            _make_scored_result("a", content="cats are great pets", score=0.9),
            _make_scored_result("b", content="dogs love to play fetch", score=0.85),
            _make_scored_result("c", content="cats are great pets indeed", score=0.8),
        ]
        selected = mmr_diversity(results, lambda_=0.5, top_k=3)
        # All three should be selected
        assert len(selected) == 3

    def test_mmr_diversity_empty(self):
        assert mmr_diversity([]) == []

    def test_mmr_diversity_fewer_than_top_k(self):
        results = [_make_scored_result("a", score=0.9)]
        selected = mmr_diversity(results, top_k=10)
        assert len(selected) == 1


# ---------------------------------------------------------------------------
# Full scoring pipeline
# ---------------------------------------------------------------------------


class TestFullScoringPipeline:
    def test_full_scoring_pipeline(self):
        now = datetime.now(timezone.utc)
        vector = [
            _make_search_result(
                "1", "short relevant doc", score=0.9,
                created_at=(now - timedelta(days=1)).isoformat(),
            ),
            _make_search_result(
                "2", "another document here", score=0.7,
                created_at=(now - timedelta(days=5)).isoformat(),
            ),
            _make_search_result(
                "3", "low scoring doc", score=0.1,
            ),
        ]
        bm25 = [
            _make_search_result("1", "short relevant doc", score=0.85),
        ]

        results = full_scoring_pipeline(
            vector, bm25,
            config={"min_threshold": 0.2, "mmr_top_k": 10},
        )

        # "3" should be filtered out (score 0.1 < 0.2 threshold even after boosts)
        ids = [r.id for r in results]
        assert "3" not in ids
        assert "1" in ids

    def test_full_scoring_pipeline_custom_config(self):
        vector = [
            _make_search_result("a", "a document", score=0.8),
        ]
        bm25: list[SearchResult] = []

        results = full_scoring_pipeline(
            vector, bm25,
            config={
                "recency_weight": 0.0,
                "min_threshold": 0.0,
                "mmr_lambda": 1.0,
                "mmr_top_k": 5,
            },
        )
        assert len(results) >= 1


# ---------------------------------------------------------------------------
# CosineReranker
# ---------------------------------------------------------------------------


class TestCosineReranker:
    def test_cosine_reranker(self):
        reranker = CosineReranker()
        results = [
            _make_scored_result("a", content="machine learning algorithms", score=0.8),
            _make_scored_result("b", content="cooking recipes and food", score=0.9),
            _make_scored_result("c", content="deep learning neural networks", score=0.7),
        ]

        reranked = reranker.rerank("machine learning", results, top_k=3)
        assert len(reranked) == 3

        # "a" has the best word overlap with "machine learning"
        assert reranked[0].id == "a"

    def test_cosine_reranker_empty(self):
        reranker = CosineReranker()
        assert reranker.rerank("query", []) == []

    def test_cosine_reranker_top_k(self):
        reranker = CosineReranker()
        results = [_make_scored_result(str(i), content=f"doc {i}", score=0.5) for i in range(10)]
        reranked = reranker.rerank("doc", results, top_k=3)
        assert len(reranked) == 3


# ---------------------------------------------------------------------------
# CrossEncoderReranker (mocked)
# ---------------------------------------------------------------------------


class TestCrossEncoderReranker:
    def test_cross_encoder_reranker_with_mock(self):
        reranker = CrossEncoderReranker()

        # Mock the cross-encoder model
        mock_model = MagicMock()
        # Return fake scores: higher for first result
        mock_model.predict.return_value = [0.95, 0.3, 0.6]
        reranker._model = mock_model

        results = [
            _make_scored_result("a", content="relevant doc", score=0.8),
            _make_scored_result("b", content="irrelevant doc", score=0.7),
            _make_scored_result("c", content="somewhat relevant", score=0.6),
        ]

        reranked = reranker.rerank("test query", results, top_k=3)

        # Model's predict should have been called with query-doc pairs
        mock_model.predict.assert_called_once()
        call_args = mock_model.predict.call_args[0][0]
        assert len(call_args) == 3
        assert call_args[0] == ["test query", "relevant doc"]

        # "a" should be ranked first (highest CE score)
        assert reranked[0].id == "a"
        assert len(reranked) == 3

    def test_cross_encoder_reranker_empty(self):
        reranker = CrossEncoderReranker()
        assert reranker.rerank("query", []) == []


# ---------------------------------------------------------------------------
# get_reranker factory
# ---------------------------------------------------------------------------


class TestGetRerankerFactory:
    def test_get_reranker_factory(self):
        ce = get_reranker("cross-encoder")
        assert isinstance(ce, CrossEncoderReranker)

        cos = get_reranker("cosine")
        assert isinstance(cos, CosineReranker)

    def test_get_reranker_unknown(self):
        with pytest.raises(ValueError, match="Unknown reranker provider"):
            get_reranker("unknown")

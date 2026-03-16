"""Retrieval scoring pipeline -- RRF fusion + post-processing stages."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from elsa_runtime.storage.vectorstore import SearchResult


@dataclass
class ScoredResult:
    """Result with fusion score."""
    id: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0
    original_score: float = 0.0
    source: str = ""  # "vector", "bm25", "graph"


def rrf_fusion(
    vector_results: list[SearchResult],
    bm25_results: list[SearchResult],
    graph_results: list[SearchResult] | None = None,
) -> list[ScoredResult]:
    """Reciprocal Rank Fusion across multiple retrieval sources.

    - Vector score as base
    - BM25 hit adds 15% boost
    - Graph hit adds 10% boost
    - Merge by id, sort by final score descending
    """
    merged: dict[str, ScoredResult] = {}

    # Vector results provide the base score
    for r in vector_results:
        merged[r.id] = ScoredResult(
            id=r.id,
            content=r.content,
            metadata=dict(r.metadata),
            score=r.score,
            original_score=r.score,
            source="vector",
        )

    # BM25 hits: add 15% boost
    bm25_ids = {r.id for r in bm25_results}
    for r in bm25_results:
        if r.id in merged:
            merged[r.id].score += merged[r.id].original_score * 0.15
            merged[r.id].source = "vector+bm25"
        else:
            # BM25-only result: use its own score as base + 15% boost
            base = r.score
            merged[r.id] = ScoredResult(
                id=r.id,
                content=r.content,
                metadata=dict(r.metadata),
                score=base + base * 0.15,
                original_score=base,
                source="bm25",
            )

    # Graph hits: add 10% boost
    if graph_results:
        for r in graph_results:
            if r.id in merged:
                merged[r.id].score += merged[r.id].original_score * 0.10
                if "graph" not in merged[r.id].source:
                    merged[r.id].source += "+graph"
            else:
                base = r.score
                merged[r.id] = ScoredResult(
                    id=r.id,
                    content=r.content,
                    metadata=dict(r.metadata),
                    score=base + base * 0.10,
                    original_score=base,
                    source="graph",
                )

    results = list(merged.values())
    results.sort(key=lambda x: x.score, reverse=True)
    return results


def recency_boost(
    results: list[ScoredResult],
    weight: float = 0.1,
    half_life_days: float = 14,
) -> list[ScoredResult]:
    """Boost recent results.

    score += weight * exp(-age_days / half_life)
    Needs `created_at` in metadata (ISO format string).
    If no `created_at`, skip the boost for that result.
    """
    now = datetime.now(timezone.utc)
    for r in results:
        created_at_str = r.metadata.get("created_at")
        if not created_at_str:
            continue
        try:
            created_at = datetime.fromisoformat(created_at_str)
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            age_days = (now - created_at).total_seconds() / 86400.0
            if age_days < 0:
                age_days = 0
            r.score += weight * math.exp(-age_days / half_life_days)
        except (ValueError, TypeError):
            continue
    return results


def length_normalize(
    results: list[ScoredResult],
    anchor: int = 500,
) -> list[ScoredResult]:
    """Penalize very long content.

    score *= min(1.0, anchor / len(content)) if content > anchor chars
    """
    for r in results:
        content_len = len(r.content)
        if content_len > anchor:
            r.score *= anchor / content_len
    return results


def hard_min_filter(
    results: list[ScoredResult],
    threshold: float = 0.3,
) -> list[ScoredResult]:
    """Remove results with score < threshold."""
    return [r for r in results if r.score >= threshold]


def _jaccard_similarity(text_a: str, text_b: str) -> float:
    """Jaccard similarity on word sets."""
    words_a = set(text_a.lower().split())
    words_b = set(text_b.lower().split())
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union)


def mmr_diversity(
    results: list[ScoredResult],
    lambda_: float = 0.5,
    top_k: int = 10,
) -> list[ScoredResult]:
    """Maximal Marginal Relevance for diversity.

    Iteratively select the result that maximizes:
        lambda_ * relevance - (1 - lambda_) * max_similarity_to_selected

    Uses Jaccard similarity on word sets.
    Returns top_k results.
    """
    if not results:
        return []

    selected: list[ScoredResult] = []
    candidates = list(results)

    # Normalize relevance scores to [0, 1] for MMR calculation
    max_score = max(r.score for r in candidates)
    min_score = min(r.score for r in candidates)
    score_range = max_score - min_score if max_score != min_score else 1.0

    while candidates and len(selected) < top_k:
        best_idx = -1
        best_mmr = float("-inf")

        for i, cand in enumerate(candidates):
            # Normalize relevance
            relevance = (cand.score - min_score) / score_range

            # Max similarity to any already-selected result
            if selected:
                max_sim = max(
                    _jaccard_similarity(cand.content, s.content) for s in selected
                )
            else:
                max_sim = 0.0

            mmr_score = lambda_ * relevance - (1 - lambda_) * max_sim

            if mmr_score > best_mmr:
                best_mmr = mmr_score
                best_idx = i

        selected.append(candidates.pop(best_idx))

    return selected


def full_scoring_pipeline(
    vector_results: list[SearchResult],
    bm25_results: list[SearchResult],
    graph_results: list[SearchResult] | None = None,
    config: dict[str, Any] | None = None,
) -> list[ScoredResult]:
    """Full scoring pipeline: rrf_fusion -> recency_boost -> length_normalize -> hard_min_filter -> mmr_diversity.

    Config keys:
        recency_weight (float): weight for recency boost (default 0.1)
        recency_half_life (float): half-life in days (default 14)
        length_anchor (int): anchor length for normalization (default 500)
        min_threshold (float): minimum score threshold (default 0.3)
        mmr_lambda (float): MMR lambda parameter (default 0.5)
        mmr_top_k (int): number of results to return (default 10)
    """
    cfg = config or {}

    results = rrf_fusion(vector_results, bm25_results, graph_results)
    results = recency_boost(
        results,
        weight=cfg.get("recency_weight", 0.1),
        half_life_days=cfg.get("recency_half_life", 14),
    )
    results = length_normalize(
        results,
        anchor=cfg.get("length_anchor", 500),
    )
    results = hard_min_filter(
        results,
        threshold=cfg.get("min_threshold", 0.3),
    )
    results = mmr_diversity(
        results,
        lambda_=cfg.get("mmr_lambda", 0.5),
        top_k=cfg.get("mmr_top_k", 10),
    )
    return results

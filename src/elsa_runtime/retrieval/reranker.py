"""Rerankers -- cross-encoder and cosine fallback."""

from __future__ import annotations

import logging
import math
from collections import Counter
from typing import Any

from elsa_runtime.retrieval.scoring import ScoredResult

logger = logging.getLogger(__name__)


class CosineReranker:
    """Cosine similarity reranker using simple TF-IDF-like word overlap.

    No external model needed -- pure Python implementation.
    """

    def _tokenize(self, text: str) -> list[str]:
        """Simple whitespace + lowercase tokenization."""
        return text.lower().split()

    def _tf(self, tokens: list[str]) -> dict[str, float]:
        """Term frequency (raw count normalized by total tokens)."""
        counts = Counter(tokens)
        total = len(tokens) if tokens else 1
        return {t: c / total for t, c in counts.items()}

    def _cosine_sim(self, tf_a: dict[str, float], tf_b: dict[str, float]) -> float:
        """Cosine similarity between two TF vectors."""
        all_terms = set(tf_a) | set(tf_b)
        if not all_terms:
            return 0.0

        dot = sum(tf_a.get(t, 0.0) * tf_b.get(t, 0.0) for t in all_terms)
        mag_a = math.sqrt(sum(v ** 2 for v in tf_a.values()))
        mag_b = math.sqrt(sum(v ** 2 for v in tf_b.values()))

        if mag_a == 0 or mag_b == 0:
            return 0.0
        return dot / (mag_a * mag_b)

    def rerank(
        self, query: str, results: list[ScoredResult], top_k: int = 10
    ) -> list[ScoredResult]:
        """Rerank results using cosine similarity on TF vectors.

        Final score: 60% cosine similarity + 40% original fusion score.
        """
        if not results:
            return []

        query_tf = self._tf(self._tokenize(query))

        for r in results:
            doc_tf = self._tf(self._tokenize(r.content))
            cos_sim = self._cosine_sim(query_tf, doc_tf)
            r.score = 0.6 * cos_sim + 0.4 * r.score

        results.sort(key=lambda x: x.score, reverse=True)
        return results[:top_k]


class CrossEncoderReranker:
    """Cross-encoder reranker using sentence-transformers.

    Lazy-loads the model on first use.
    """

    def __init__(self, model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"):
        self.model_name = model_name
        self._model: Any = None

    def _load_model(self) -> Any:
        """Lazy load the cross-encoder model."""
        if self._model is None:
            try:
                from sentence_transformers import CrossEncoder
                logger.info("Loading cross-encoder model: %s", self.model_name)
                self._model = CrossEncoder(self.model_name)
            except ImportError:
                raise ImportError(
                    "sentence-transformers is required for CrossEncoderReranker. "
                    "Install with: pip install sentence-transformers"
                )
        return self._model

    def rerank(
        self, query: str, results: list[ScoredResult], top_k: int = 10
    ) -> list[ScoredResult]:
        """Rerank results using cross-encoder.

        Final score: 60% reranker score + 40% original fusion score.
        """
        if not results:
            return []

        model = self._load_model()

        pairs = [[query, r.content] for r in results]
        ce_scores = model.predict(pairs)

        # Normalize cross-encoder scores to [0, 1]
        ce_min = float(min(ce_scores))
        ce_max = float(max(ce_scores))
        ce_range = ce_max - ce_min if ce_max != ce_min else 1.0

        for r, ce_score in zip(results, ce_scores):
            normalized_ce = (float(ce_score) - ce_min) / ce_range
            r.score = 0.6 * normalized_ce + 0.4 * r.score

        results.sort(key=lambda x: x.score, reverse=True)
        return results[:top_k]


def get_reranker(provider: str = "cross-encoder") -> CrossEncoderReranker | CosineReranker:
    """Factory to get a reranker instance.

    Args:
        provider: "cross-encoder" or "cosine"

    Returns:
        A reranker instance.
    """
    if provider == "cross-encoder":
        return CrossEncoderReranker()
    elif provider == "cosine":
        return CosineReranker()
    else:
        raise ValueError(f"Unknown reranker provider: {provider}")

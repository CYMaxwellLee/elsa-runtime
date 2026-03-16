"""Embedding models — abstract base + concrete implementations.

Design: base class defines the interface, swapping models only requires
a new subclass + registering in the factory.
Phase A: BGE-M3 via SentenceTransformers (dense only, stable)
Phase B: Qwen3-Embedding-8B or whatever's SOTA
Sparse support: interface reserved, to be wired when hybrid search needs it
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class EmbeddingResult:
    """Result from encoding."""

    dense: list[list[float]]
    sparse: list[dict[str, float]] = field(default_factory=list)


class BaseEmbedder(ABC):
    """Abstract embedding interface. Subclass this to add new models."""

    @abstractmethod
    def encode(self, texts: list[str]) -> EmbeddingResult:
        """Encode texts into dense (+ optionally sparse) representations."""

    def encode_dense(self, texts: list[str]) -> list[list[float]]:
        return self.encode(texts).dense

    def encode_sparse(self, texts: list[str]) -> list[dict[str, float]]:
        return self.encode(texts).sparse

    @property
    @abstractmethod
    def dim(self) -> int:
        """Embedding dimension."""

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Human-readable model identifier."""


class BGEM3Embedder(BaseEmbedder):
    """BGE-M3 via SentenceTransformers — dense only, stable deps.

    1024-dim, 8K context, multilingual. Lazy-loads on first use.
    """

    def __init__(self, **kwargs: Any) -> None:
        self._model: Any = None

    def _load(self) -> None:
        if self._model is not None:
            return
        from sentence_transformers import SentenceTransformer
        self._model = SentenceTransformer("BAAI/bge-m3")

    @property
    def dim(self) -> int:
        return 1024

    @property
    def model_name(self) -> str:
        return "BAAI/bge-m3"

    def encode(self, texts: list[str]) -> EmbeddingResult:
        self._load()
        vectors = self._model.encode(texts, normalize_embeddings=True)
        if hasattr(vectors, "tolist"):
            vectors = vectors.tolist()
        return EmbeddingResult(dense=vectors)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, type[BaseEmbedder]] = {
    "bge-m3": BGEM3Embedder,
}


def register_embedder(name: str, cls: type[BaseEmbedder]) -> None:
    """Register a new embedder class at runtime."""
    _REGISTRY[name] = cls


def get_embedder(model: str = "bge-m3", **kwargs: Any) -> BaseEmbedder:
    """Factory to get an embedder by name.

    Usage:
        embedder = get_embedder()              # default BGE-M3
        embedder = get_embedder("bge-m3")      # explicit
        # Future: register_embedder("qwen3-8b", Qwen3Embedder)
        # embedder = get_embedder("qwen3-8b")
    """
    cls = _REGISTRY.get(model)
    if cls is None:
        available = ", ".join(_REGISTRY.keys())
        raise ValueError(f"Unknown embedder '{model}'. Available: {available}")
    return cls(**kwargs)

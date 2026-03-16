"""VectorStore abstraction layer."""

from elsa_runtime.storage.vectorstore import SearchResult, VectorStore, WriteResult


def get_store(backend: str = "lancedb", **kwargs) -> VectorStore:
    """Factory to get a VectorStore backend."""
    if backend == "lancedb":
        from elsa_runtime.storage.lancedb_store import LanceDBStore
        return LanceDBStore(**kwargs)
    raise ValueError(f"Unknown backend: {backend}")


__all__ = ["VectorStore", "SearchResult", "WriteResult", "get_store"]

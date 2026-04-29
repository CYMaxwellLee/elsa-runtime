"""
Paper processing package — section extraction engine for QDIP.

Public API:
    from elsa_runtime.paper import PaperSplitter, Section, SectionIndex, SplitResult

Usage:
    splitter = PaperSplitter()
    result = splitter.split("2401.12345")       # arXiv ID
    result = splitter.split("/path/to/paper.pdf")  # local PDF

    for section in result.sections:
        print(section.title, section.estimated_tokens)

    # Lightweight index for QDIP Phase 0
    print(result.index.to_prompt_string())
"""

from .splitter import (
    PaperSplitter,
    Section,
    SectionIndex,
    SplitResult,
    SplitMethod,
    BaseSplitter,
    SourceUnavailable,
)
from .chunker import (
    Chunk,
    chunk_section,
    chunk_sections,
    chunk_text,
    TARGET_CHARS,
)

__all__ = [
    "PaperSplitter",
    "Section",
    "SectionIndex",
    "SplitResult",
    "SplitMethod",
    "BaseSplitter",
    "SourceUnavailable",
    "Chunk",
    "chunk_section",
    "chunk_sections",
    "chunk_text",
    "TARGET_CHARS",
]

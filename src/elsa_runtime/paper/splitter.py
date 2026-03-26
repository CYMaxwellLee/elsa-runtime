"""
splitter.py — PaperSplitter orchestrator + shared data structures.

This is the public API. Consumers (QDIP, Paper Harvest, etc.) only import from here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from abc import ABC, abstractmethod
from enum import Enum
from pathlib import Path


class SplitMethod(str, Enum):
    LATEX = "latex"       # Method 1: ArXiv LaTeX source
    PDF = "pdf"           # Method 2: PDF structural parsing
    LLM = "llm"           # Method 3: LLM-assisted (fallback)


@dataclass
class Section:
    """One section extracted from a paper."""
    id: str                         # "section:Introduction", "subsection:Loss Function"
    title: str                      # "Introduction", "Loss Function"
    content: str                    # Full text content (LaTeX cleaned or PDF extracted)
    level: int                      # 1=section, 2=subsection, 3=subsubsection
    order: int                      # Position in paper (0-indexed)
    estimated_tokens: int           # len(content) // 4 as rough estimate
    page_start: int | None = None   # PDF page number (0-indexed), None for LaTeX
    metadata: dict = field(default_factory=dict)
    # metadata examples:
    #   {"has_equations": True, "has_tables": True, "has_figures": ["fig1", "fig3"]}


@dataclass
class SectionIndex:
    """Lightweight index for QDIP Phase 0 — fits in ~500 tokens.

    Phase 0 reads ONLY this index (not the full sections) to decide
    which sections to send to Phase 1 for each question.
    """
    paper_id: str                   # arXiv ID or filename
    title: str                      # Paper title
    abstract: str                   # Full abstract (always available)
    method: SplitMethod             # Which method was used
    total_sections: int
    total_estimated_tokens: int
    sections: dict[str, str]        # section_id -> first ~100 words (truncated preview)

    def to_prompt_string(self) -> str:
        """Format for injection into QDIP Phase 0 triage prompt."""
        lines = [f"Paper: {self.title}", f"Sections ({self.total_sections} total):"]
        for sid, preview in self.sections.items():
            lines.append(f"  [{sid}] {preview[:200]}...")
        return "\n".join(lines)


@dataclass
class SplitResult:
    """Complete output of splitting a paper."""
    paper_id: str
    method: SplitMethod
    sections: list[Section]
    index: SectionIndex
    warnings: list[str] = field(default_factory=list)


class SourceUnavailable(Exception):
    """Raised when a splitting method cannot access the paper source."""
    pass


class BaseSplitter(ABC):
    """Interface for all splitting methods."""

    @abstractmethod
    def split(self, source: str) -> list[Section]:
        """Split a paper source into sections.

        Args:
            source: arXiv ID (e.g. "2401.12345") or file path

        Returns:
            List of Section objects, ordered by appearance in paper.

        Raises:
            SourceUnavailable: if the source cannot be accessed/parsed
        """
        ...

    def build_index(
        self,
        paper_id: str,
        title: str,
        abstract: str,
        sections: list[Section],
        method: SplitMethod,
    ) -> SectionIndex:
        """Build lightweight index from sections."""
        return SectionIndex(
            paper_id=paper_id,
            title=title,
            abstract=abstract,
            method=method,
            total_sections=len(sections),
            total_estimated_tokens=sum(s.estimated_tokens for s in sections),
            sections={s.id: s.content[:400] for s in sections},
        )


# ============================================================
# PaperSplitter Orchestrator
# ============================================================

class PaperSplitter:
    """Orchestrator: automatically selects the best splitting method.

    Priority: LaTeX (best) -> PDF (good) -> LLM (fallback)

    Usage:
        splitter = PaperSplitter()
        result = splitter.split("2401.12345")           # arXiv ID
        result = splitter.split("/path/to/paper.pdf")   # local PDF

        # Access results
        for section in result.sections:
            print(section.title, section.estimated_tokens)

        # Get lightweight index for QDIP Phase 0
        print(result.index.to_prompt_string())
    """

    def split(self, source: str, title: str = "", abstract: str = "") -> SplitResult:
        """Split a paper into sections using the best available method.

        Args:
            source: arXiv ID (e.g. "2401.12345") or path to PDF file
            title: Paper title (if known). Extracted from source if empty.
            abstract: Paper abstract (if known). Extracted from source if empty.
        """
        from .latex_splitter import ArxivLatexSplitter
        from .pdf_splitter import PdfStructuralSplitter

        sections = None
        method = None
        warnings: list[str] = []

        # Method 1: LaTeX (best quality)
        if self._looks_like_arxiv_id(source):
            try:
                splitter = ArxivLatexSplitter()
                sections = splitter.split(source)
                method = SplitMethod.LATEX

                if not title:
                    title = self._extract_title_from_sections(sections)
                if not abstract:
                    abstract = self._extract_abstract_from_sections(sections)

            except SourceUnavailable as e:
                warnings.append(f"LaTeX source unavailable: {e}. Falling back to PDF.")

        # Method 2: PDF structural (fallback)
        if sections is None:
            pdf_path = self._resolve_pdf_path(source)
            if pdf_path:
                try:
                    splitter = PdfStructuralSplitter()
                    sections = splitter.split(pdf_path)
                    method = SplitMethod.PDF

                    if not title:
                        title = self._extract_title_from_sections(sections)
                    if not abstract:
                        abstract = self._extract_abstract_from_sections(sections)

                except SourceUnavailable as e:
                    warnings.append(f"PDF parsing failed: {e}.")

        # Method 3: LLM (last resort) — stub for now
        if sections is None:
            warnings.append("LLM splitter not yet implemented. Cannot process this source.")
            raise SourceUnavailable(
                f"All splitting methods failed for '{source}'. "
                f"Warnings: {'; '.join(warnings)}"
            )

        # Sanity checks
        if len(sections) < 2:
            warnings.append(
                f"Only {len(sections)} section(s) found — paper may not have been split correctly."
            )

        very_short = [s for s in sections if s.estimated_tokens < 50]
        if very_short:
            warnings.append(
                f"{len(very_short)} section(s) under 50 tokens — may be incorrectly split."
            )

        # Build result
        paper_id = source if self._looks_like_arxiv_id(source) else Path(source).stem

        # Use BaseSplitter.build_index (any concrete splitter works)
        _builder = ArxivLatexSplitter()
        index = _builder.build_index(
            paper_id=paper_id,
            title=title,
            abstract=abstract,
            sections=sections,
            method=method,
        )

        return SplitResult(
            paper_id=paper_id,
            method=method,
            sections=sections,
            index=index,
            warnings=warnings,
        )

    def _looks_like_arxiv_id(self, source: str) -> bool:
        return bool(re.match(r"^\d{4}\.\d{4,5}(v\d+)?$", source))

    def _resolve_pdf_path(self, source: str) -> str | None:
        p = Path(source)
        if p.exists() and p.suffix.lower() == ".pdf":
            return str(p)
        return None

    def _extract_title_from_sections(self, sections: list[Section]) -> str:
        for s in sections:
            if s.title not in ("Abstract", "Preamble", "Full Document"):
                return s.title
        return ""

    def _extract_abstract_from_sections(self, sections: list[Section]) -> str:
        for s in sections:
            if "abstract" in s.title.lower():
                return s.content
        return ""

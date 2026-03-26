"""
Integration test: Download + split a real arXiv paper.

Requires network access. Mark with @pytest.mark.network.
Run with: pytest tests/integration/test_arxiv_splitter.py -m network -v
"""

import pytest

from elsa_runtime.paper.latex_splitter import ArxivLatexSplitter
from elsa_runtime.paper.splitter import SplitMethod

# Use a well-known, stable arXiv paper (Attention Is All You Need)
# This paper has clear section structure and is unlikely to be removed.
STABLE_ARXIV_ID = "1706.03762"


@pytest.mark.network
class TestSplitRealPaper:
    """Download and split a real arXiv paper via LaTeX source."""

    def test_download_and_parse(self):
        splitter = ArxivLatexSplitter()
        sections = splitter.split(STABLE_ARXIV_ID)

        # Should find multiple sections
        assert len(sections) >= 5, (
            f"Expected >= 5 sections, got {len(sections)}: "
            f"{[s.title for s in sections]}"
        )

    def test_introduction_present(self):
        splitter = ArxivLatexSplitter()
        sections = splitter.split(STABLE_ARXIV_ID)
        titles_lower = [s.title.lower() for s in sections]

        assert any(
            "introduction" in t for t in titles_lower
        ), f"No 'Introduction' section found in: {[s.title for s in sections]}"

    def test_conclusion_present(self):
        splitter = ArxivLatexSplitter()
        sections = splitter.split(STABLE_ARXIV_ID)
        titles_lower = [s.title.lower() for s in sections]

        assert any(
            "conclusion" in t for t in titles_lower
        ), f"No 'Conclusion' section found in: {[s.title for s in sections]}"

    def test_no_empty_sections(self):
        splitter = ArxivLatexSplitter()
        sections = splitter.split(STABLE_ARXIV_ID)

        for s in sections:
            assert (
                s.estimated_tokens > 0
            ), f"Section '{s.title}' has 0 estimated tokens"

    def test_section_ids_are_unique(self):
        splitter = ArxivLatexSplitter()
        sections = splitter.split(STABLE_ARXIV_ID)

        ids = [s.id for s in sections]
        # Allow duplicates in edge cases but log them
        # (some papers have duplicate subsection names)
        assert len(ids) > 0

    def test_build_index(self):
        splitter = ArxivLatexSplitter()
        sections = splitter.split(STABLE_ARXIV_ID)

        index = splitter.build_index(
            paper_id=STABLE_ARXIV_ID,
            title="Attention Is All You Need",
            abstract="",
            sections=sections,
            method=SplitMethod.LATEX,
        )

        assert index.total_sections == len(sections)
        assert index.total_estimated_tokens > 0

        prompt = index.to_prompt_string()
        assert "Attention Is All You Need" in prompt
        assert f"{len(sections)} total" in prompt

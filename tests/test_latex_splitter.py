"""Unit tests for ArXiv LaTeX splitter (no network required)."""

import os
from pathlib import Path

import pytest

from elsa_runtime.paper.latex_splitter import ArxivLatexSplitter

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SAMPLE_TEX = FIXTURES_DIR / "sample_paper.tex"


@pytest.fixture
def splitter():
    return ArxivLatexSplitter()


class TestParseSimplePaper:
    def test_finds_all_sections(self, splitter):
        sections = splitter.split_from_file(str(SAMPLE_TEX))
        titles = [s.title for s in sections]
        # Should find: Abstract, Introduction, Method, Problem Setup, Our Approach, Experiments, Conclusion
        assert "Abstract" in titles
        assert "Introduction" in titles
        assert "Method" in titles
        assert "Conclusion" in titles

    def test_section_count(self, splitter):
        sections = splitter.split_from_file(str(SAMPLE_TEX))
        # Abstract + Introduction + Method + Problem Setup + Our Approach + Experiments + Conclusion = 7
        assert len(sections) >= 6  # At least the main sections

    def test_sections_have_content(self, splitter):
        sections = splitter.split_from_file(str(SAMPLE_TEX))
        # Parent sections that only contain subsections may have empty content
        # (e.g., \section{Method} followed immediately by \subsection{...})
        leaf_sections = [s for s in sections if s.title != "Method"]
        for s in leaf_sections:
            assert len(s.content) > 0, f"Section '{s.title}' has empty content"

    def test_sections_have_estimated_tokens(self, splitter):
        sections = splitter.split_from_file(str(SAMPLE_TEX))
        for s in sections:
            assert s.estimated_tokens >= 0

    def test_sections_ordered(self, splitter):
        sections = splitter.split_from_file(str(SAMPLE_TEX))
        orders = [s.order for s in sections]
        assert orders == sorted(orders), "Sections should be in order"


class TestNestedSubsections:
    def test_subsections_detected(self, splitter):
        sections = splitter.split_from_file(str(SAMPLE_TEX))
        subsections = [s for s in sections if s.level == 2]
        assert len(subsections) >= 2  # "Problem Setup" and "Our Approach"

    def test_subsection_titles(self, splitter):
        sections = splitter.split_from_file(str(SAMPLE_TEX))
        sub_titles = [s.title for s in sections if s.level == 2]
        assert "Problem Setup" in sub_titles
        assert "Our Approach" in sub_titles


class TestUnnumberedSections:
    def test_parse_section_star(self, splitter):
        tex = r"""
\documentclass{article}
\begin{document}
\section*{Acknowledgments}
We thank the reviewers.
\section*{Supplementary Material}
Additional details here.
\end{document}
"""
        # Write temp file
        import tempfile

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".tex", delete=False
        ) as f:
            f.write(tex)
            f.flush()
            sections = splitter.split_from_file(f.name)

        os.unlink(f.name)

        titles = [s.title for s in sections]
        assert "Acknowledgments" in titles
        assert "Supplementary Material" in titles


class TestNoSections:
    def test_returns_full_document(self, splitter):
        tex = r"""
\documentclass{article}
\begin{document}
This is a short paper with no section commands at all.
Just plain text about something interesting.
\end{document}
"""
        import tempfile

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".tex", delete=False
        ) as f:
            f.write(tex)
            f.flush()
            sections = splitter.split_from_file(f.name)

        os.unlink(f.name)

        assert len(sections) == 1
        assert sections[0].title == "Full Document"
        assert "plain text" in sections[0].content


class TestAbstractExtraction:
    def test_abstract_is_first(self, splitter):
        sections = splitter.split_from_file(str(SAMPLE_TEX))
        assert sections[0].title == "Abstract"
        assert "abstract" in sections[0].content.lower() or "testing" in sections[0].content.lower()

    def test_abstract_level_is_1(self, splitter):
        sections = splitter.split_from_file(str(SAMPLE_TEX))
        abstract = [s for s in sections if s.title == "Abstract"]
        assert len(abstract) == 1
        assert abstract[0].level == 1


class TestMetadataDetection:
    def test_equations_detected(self, splitter):
        sections = splitter.split_from_file(str(SAMPLE_TEX))
        # "Our Approach" section has an equation
        approach = [s for s in sections if s.title == "Our Approach"]
        assert len(approach) == 1
        assert approach[0].metadata.get("has_equations") is True

    def test_tables_detected(self, splitter):
        sections = splitter.split_from_file(str(SAMPLE_TEX))
        # "Experiments" section has a table
        experiments = [s for s in sections if s.title == "Experiments"]
        assert len(experiments) == 1
        assert experiments[0].metadata.get("has_tables") is True


class TestSectionIds:
    def test_section_id_format(self, splitter):
        sections = splitter.split_from_file(str(SAMPLE_TEX))
        for s in sections:
            assert ":" in s.id, f"Section ID should have format 'cmd:title', got '{s.id}'"

    def test_section_id_contains_title(self, splitter):
        sections = splitter.split_from_file(str(SAMPLE_TEX))
        for s in sections:
            # Section ID should end with the title
            assert s.title in s.id

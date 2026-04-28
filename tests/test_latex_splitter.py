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


# ── \input / \include expansion (modern arXiv convention) ──────────────


class TestIncludeExpansion:
    """Modern arXiv papers split content across multiple .tex files via
    \\input{sections/intro} etc. The splitter must inline these before
    parsing or it'll find zero sections."""

    def test_simple_input_resolved(self, splitter):
        """Plain \\input{name} resolves to name.tex in same dir."""
        files = {
            "main.tex": (
                "\\documentclass{article}\\begin{document}"
                "\\input{intro}"
                "\\end{document}"
            ),
            "intro.tex": "\\section{Introduction}This is the intro body.",
        }
        out = splitter._expand_includes(files["main.tex"], files)
        assert "\\section{Introduction}" in out
        assert "This is the intro body" in out

    def test_input_with_explicit_extension(self, splitter):
        files = {
            "main.tex": "\\input{intro.tex}",
            "intro.tex": "\\section{Hi}",
        }
        out = splitter._expand_includes(files["main.tex"], files)
        assert "\\section{Hi}" in out

    def test_input_in_subdir(self, splitter):
        """Common pattern: \\input{sections/intro} → sections/intro.tex"""
        files = {
            "main.tex": (
                "\\begin{document}"
                "\\input{sections/intro}"
                "\\input{sections/method}"
                "\\end{document}"
            ),
            "sections/intro.tex": "\\section{Introduction}Intro text",
            "sections/method.tex": "\\section{Method}Method text",
        }
        out = splitter._expand_includes(files["main.tex"], files)
        assert "Intro text" in out
        assert "Method text" in out

    def test_include_command_also_expanded(self, splitter):
        """\\include{...} should expand same as \\input."""
        files = {
            "main.tex": "\\include{chapter1}",
            "chapter1.tex": "\\section{Chapter One}",
        }
        out = splitter._expand_includes(files["main.tex"], files)
        assert "Chapter One" in out

    def test_recursive_input(self, splitter):
        """\\input{a} → a.tex contains \\input{b} → b.tex."""
        files = {
            "main.tex": "\\input{a}",
            "a.tex": "Top-level \\input{b}",
            "b.tex": "\\section{Deep}deep body",
        }
        out = splitter._expand_includes(files["main.tex"], files)
        assert "Top-level" in out
        assert "deep body" in out

    def test_commented_input_not_expanded(self, splitter):
        """`% \\input{x}` is a comment and must not trigger expansion."""
        files = {
            "main.tex": "% \\input{ghost}\n\\input{real}",
            "real.tex": "\\section{Real}",
            # No ghost.tex on purpose.
        }
        out = splitter._expand_includes(files["main.tex"], files)
        assert "\\section{Real}" in out
        # The comment line is stripped, so 'ghost' string shouldn't appear.
        assert "ghost" not in out

    def test_missing_include_left_as_is(self, splitter):
        """Unresolvable include is left as-is (parser will get less content,
        but we don't crash)."""
        files = {
            "main.tex": "\\input{missing}\n\\section{Real}",
        }
        out = splitter._expand_includes(files["main.tex"], files)
        # The literal \input{missing} should still be in output (not raised)
        assert "\\input{missing}" in out
        assert "\\section{Real}" in out

    def test_circular_input_does_not_loop(self, splitter):
        """a → b → a should not infinite-loop."""
        files = {
            "main.tex": "\\input{a}",
            "a.tex": "A start \\input{b} A end",
            "b.tex": "B start \\input{a} B end",
        }
        out = splitter._expand_includes(files["main.tex"], files)
        # We just need it not to hang. Some content from a/b should appear.
        assert "A start" in out
        assert "B start" in out

    def test_full_pipeline_with_input(self, splitter, tmp_path):
        """End-to-end: split_from_file + \\input expansion + section parsing."""
        (tmp_path / "main.tex").write_text(
            "\\documentclass{article}\n"
            "\\begin{document}\n"
            "\\input{sections/intro}\n"
            "\\input{sections/method}\n"
            "\\input{sections/conclusion}\n"
            "\\end{document}\n",
            encoding="utf-8",
        )
        (tmp_path / "sections").mkdir()
        (tmp_path / "sections" / "intro.tex").write_text(
            "\\section{Introduction}This is the intro of a multi-file paper.",
            encoding="utf-8",
        )
        (tmp_path / "sections" / "method.tex").write_text(
            "\\section{Method}We propose a clever approach to the problem.",
            encoding="utf-8",
        )
        (tmp_path / "sections" / "conclusion.tex").write_text(
            "\\section{Conclusion}We presented a method that works.",
            encoding="utf-8",
        )
        sections = splitter.split_from_file(str(tmp_path / "main.tex"))
        titles = [s.title for s in sections]
        assert "Introduction" in titles
        assert "Method" in titles
        assert "Conclusion" in titles
        # And bodies should be there
        intro_section = next(s for s in sections if s.title == "Introduction")
        assert "intro of a multi-file paper" in intro_section.content


class TestResolveInclude:
    """Unit tests for the include-path resolver."""

    def test_direct_match(self, splitter):
        files = {"intro.tex": ""}
        assert splitter._resolve_include("intro", files) == "intro.tex"

    def test_with_explicit_tex_suffix(self, splitter):
        files = {"intro.tex": ""}
        assert splitter._resolve_include("intro.tex", files) == "intro.tex"

    def test_subdir_path(self, splitter):
        files = {"sections/intro.tex": ""}
        assert (
            splitter._resolve_include("sections/intro", files)
            == "sections/intro.tex"
        )

    def test_relative_dot_prefix(self, splitter):
        files = {"intro.tex": ""}
        assert splitter._resolve_include("./intro", files) == "intro.tex"

    def test_unknown_returns_none(self, splitter):
        assert splitter._resolve_include("nope", {"other.tex": ""}) is None

    def test_basename_search_when_main_in_subdir(self, splitter):
        """Common: main.tex is in src/, \\input{intro} should find src/intro.tex."""
        files = {"src/intro.tex": "x", "src/main.tex": ""}
        assert splitter._resolve_include("intro", files) == "src/intro.tex"


# ── Multi-document archive disambiguation (rebuttal vs paper) ──────────


class TestMultiDocArchive:
    """Some arXiv archives include both the paper AND a CVPR/etc rebuttal,
    response letter, or supplement. Splitter must pick the real paper."""

    def _make_tar(self, tmp_path, files: dict):
        """Build a tar.gz with given files dict (path → content)."""
        import tarfile
        import io
        tar_path = tmp_path / "src.tar.gz"
        with tarfile.open(tar_path, "w:gz") as tar:
            for name, content in files.items():
                data = content.encode("utf-8")
                info = tarfile.TarInfo(name=name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        return tar_path

    def test_picks_paper_over_rebuttal(self, splitter, tmp_path):
        """Real bug: archive has arxiv.tex (paper) AND rebuttal.tex (response).
        rebuttal.tex is larger. Splitter should NOT pick rebuttal."""
        files = {
            "arxiv.tex": (
                "\\documentclass{article}\\title{Real Paper}\\begin{document}"
                "\\input{intro}\\input{method}\\end{document}"
            ),
            "rebuttal.tex": (
                "\\documentclass{article}\\begin{document}"
                # No \title, no \input — typical rebuttal format.
                # Pad so it's larger than arxiv.tex.
                + ("Reviewer response text. " * 100)
                + "\\end{document}"
            ),
            "intro.tex": "\\section{Introduction}Intro body of the real paper.",
            "method.tex": "\\section{Method}Method body of the real paper.",
        }
        tar_path = self._make_tar(tmp_path, files)

        import tarfile
        with tarfile.open(tar_path, "r:gz") as tar:
            main_text, all_files = splitter._read_all_tex_from_tar(tar)

        assert "Real Paper" in main_text, (
            "Picked the wrong main file (probably rebuttal)"
        )
        assert "Reviewer response text" not in main_text

    def test_filename_blacklist_filters_response(self, splitter, tmp_path):
        """Filename hint alone (response.tex) should deprioritize a candidate."""
        files = {
            "main.tex": (
                "\\begin{document}\\input{body}\\end{document}"
            ),
            "response.tex": (
                "\\begin{document}" + "x" * 5000 + "\\end{document}"
            ),
            "body.tex": "\\section{Body}",
        }
        tar_path = self._make_tar(tmp_path, files)

        import tarfile
        with tarfile.open(tar_path, "r:gz") as tar:
            main_text, _ = splitter._read_all_tex_from_tar(tar)

        # main.tex should win even though response.tex is much larger
        assert "\\input{body}" in main_text

    def test_supp_filename_filtered(self, splitter, tmp_path):
        """`supplement.tex` should also be deprioritized."""
        files = {
            "main.tex": (
                "\\title{Paper}\\begin{document}\\input{intro}\\end{document}"
            ),
            "supplement.tex": (
                "\\begin{document}" + "y" * 9000 + "\\end{document}"
            ),
            "intro.tex": "\\section{Hi}",
        }
        tar_path = self._make_tar(tmp_path, files)

        import tarfile
        with tarfile.open(tar_path, "r:gz") as tar:
            main_text, _ = splitter._read_all_tex_from_tar(tar)

        assert "\\title{Paper}" in main_text

    def test_falls_back_when_all_look_supplementary(self, splitter, tmp_path):
        """If every candidate name has a blacklist hint, use the unfiltered pool."""
        files = {
            "rebuttal_v1.tex": "\\title{Some}\\begin{document}\\section{X}\\end{document}",
            "rebuttal_v2.tex": "\\begin{document}plain\\end{document}",
        }
        tar_path = self._make_tar(tmp_path, files)

        import tarfile
        with tarfile.open(tar_path, "r:gz") as tar:
            main_text, _ = splitter._read_all_tex_from_tar(tar)

        # Should still pick something rather than return None
        assert main_text is not None
        # And prefer the one with \title
        assert "\\title{Some}" in main_text

    def test_single_main_unaffected(self, splitter, tmp_path):
        """Sanity: single-main archives (the common case) still work."""
        files = {
            "paper.tex": (
                "\\title{Solo}\\begin{document}\\input{a}\\end{document}"
            ),
            "a.tex": "\\section{A}body",
        }
        tar_path = self._make_tar(tmp_path, files)

        import tarfile
        with tarfile.open(tar_path, "r:gz") as tar:
            main_text, _ = splitter._read_all_tex_from_tar(tar)

        assert "Solo" in main_text

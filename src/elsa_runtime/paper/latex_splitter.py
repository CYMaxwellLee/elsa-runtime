"""
latex_splitter.py — Extract sections from arXiv LaTeX source.
"""

import re
import io
import gzip
import tarfile

import requests
from pathlib import Path

from .splitter import BaseSplitter, Section, SplitMethod, SourceUnavailable
from .latex_cleaner import clean_latex


class ArxivLatexSplitter(BaseSplitter):
    """Parse arXiv LaTeX source into sections."""

    ARXIV_EPRINT_URL = "https://arxiv.org/e-print/{arxiv_id}"

    # Match \section{...}, \subsection{...}, \subsubsection{...}, \paragraph{...}
    # Also handles \section*{...} (unnumbered)
    SECTION_PATTERN = re.compile(
        r"\\(section|subsection|subsubsection|paragraph)\*?\{([^}]+)\}"
    )

    LEVEL_MAP = {
        "section": 1,
        "subsection": 2,
        "subsubsection": 3,
        "paragraph": 4,
    }

    def split(self, arxiv_id: str) -> list[Section]:
        """Download arXiv source and extract sections.

        Args:
            arxiv_id: e.g. "2401.12345" or "2401.12345v2"
        """
        tex_content = self._download_and_find_main_tex(arxiv_id)
        return self._parse_sections(tex_content)

    def split_from_file(self, tex_path: str) -> list[Section]:
        """Parse a local .tex file (for testing without network)."""
        content = Path(tex_path).read_text(encoding="utf-8", errors="ignore")
        return self._parse_sections(content)

    def _download_and_find_main_tex(self, arxiv_id: str) -> str:
        """Download arXiv source tarball and find the main .tex file."""
        url = self.ARXIV_EPRINT_URL.format(arxiv_id=arxiv_id)

        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise SourceUnavailable(
                f"Cannot download arXiv source for {arxiv_id}: {e}"
            )

        content_bytes = resp.content
        tex_content = None

        # Try tar.gz first
        try:
            tar = tarfile.open(fileobj=io.BytesIO(content_bytes), mode="r:gz")
            tex_content = self._find_main_tex_in_tar(tar)
        except (tarfile.TarError, EOFError):
            pass

        # Try plain tar
        if tex_content is None:
            try:
                tar = tarfile.open(fileobj=io.BytesIO(content_bytes), mode="r:")
                tex_content = self._find_main_tex_in_tar(tar)
            except (tarfile.TarError, EOFError):
                pass

        # Try single gzipped file
        if tex_content is None:
            try:
                tex_content = gzip.decompress(content_bytes).decode(
                    "utf-8", errors="ignore"
                )
            except (gzip.BadGzipFile, OSError):
                pass

        # Try plain text
        if tex_content is None:
            try:
                tex_content = content_bytes.decode("utf-8", errors="ignore")
                if "\\begin{document}" not in tex_content:
                    tex_content = None
            except UnicodeDecodeError:
                pass

        if tex_content is None:
            raise SourceUnavailable(
                f"Cannot extract .tex from arXiv source for {arxiv_id}"
            )

        return tex_content

    def _find_main_tex_in_tar(self, tar: tarfile.TarFile) -> str | None:
        """Find the main .tex file (the one containing \\begin{document})."""
        tex_files = [f for f in tar.getnames() if f.endswith(".tex")]

        if not tex_files:
            return None

        # Strategy: find the .tex file that contains \begin{document}
        for tf in tex_files:
            try:
                content = (
                    tar.extractfile(tf).read().decode("utf-8", errors="ignore")
                )
                if "\\begin{document}" in content:
                    return content
            except (KeyError, AttributeError):
                continue

        # Fallback: return the largest .tex file
        largest = max(tex_files, key=lambda f: tar.getmember(f).size)
        return tar.extractfile(largest).read().decode("utf-8", errors="ignore")

    def _parse_sections(self, tex_content: str) -> list[Section]:
        """Parse section structure from LaTeX content."""

        # Extract content between \begin{document} and \end{document}
        doc_match = re.search(
            r"\\begin\{document\}(.*?)\\end\{document\}",
            tex_content,
            re.DOTALL,
        )
        if doc_match:
            tex_content = doc_match.group(1)

        # Find all section commands
        matches = list(self.SECTION_PATTERN.finditer(tex_content))

        if not matches:
            # No sections found — return entire document as one section
            cleaned = clean_latex(tex_content)
            return [
                Section(
                    id="section:Full Document",
                    title="Full Document",
                    content=cleaned,
                    level=1,
                    order=0,
                    estimated_tokens=len(cleaned) // 4,
                )
            ]

        sections = []

        # Extract abstract separately (before first \section)
        pre_first = tex_content[: matches[0].start()]
        abstract_match = re.search(
            r"\\begin\{abstract\}(.*?)\\end\{abstract\}",
            pre_first,
            re.DOTALL,
        )
        if abstract_match:
            abstract_text = clean_latex(abstract_match.group(1))
            sections.append(
                Section(
                    id="section:Abstract",
                    title="Abstract",
                    content=abstract_text,
                    level=1,
                    order=0,
                    estimated_tokens=len(abstract_text) // 4,
                )
            )

        # Extract each section
        for i, match in enumerate(matches):
            cmd = match.group(1)  # "section", "subsection", etc.
            title = match.group(2).strip()
            level = self.LEVEL_MAP.get(cmd, 1)

            start = match.end()
            end = (
                matches[i + 1].start()
                if i + 1 < len(matches)
                else len(tex_content)
            )

            raw_body = tex_content[start:end]
            cleaned_body = clean_latex(raw_body)

            # Detect content characteristics
            metadata = {}
            if re.search(r"\\begin\{(equation|align|gather)", raw_body):
                metadata["has_equations"] = True
            if re.search(r"\\begin\{(table|tabular)", raw_body):
                metadata["has_tables"] = True
            fig_refs = re.findall(
                r"\\includegraphics.*?\{([^}]+)\}", raw_body
            )
            if fig_refs:
                metadata["has_figures"] = fig_refs

            section_id = f"{cmd}:{title}"

            sections.append(
                Section(
                    id=section_id,
                    title=title,
                    content=cleaned_body,
                    level=level,
                    order=len(sections),
                    estimated_tokens=len(cleaned_body) // 4,
                    metadata=metadata,
                )
            )

        return sections

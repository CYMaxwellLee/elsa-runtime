"""
latex_splitter.py — Extract sections from arXiv LaTeX source.

Handles modern arXiv papers that split content across multiple .tex files
via `\\input{...}` / `\\include{...}` by recursively expanding includes
before section parsing. Without this, ~75% of post-2023 arXiv papers
collapse to a single "Full Document" section because their main file just
holds preamble + `\\input{sections/...}` calls (verified 2026-04-28 dry-run).
"""

import re
import io
import gzip
import logging
import tarfile

import requests
from pathlib import Path

from .splitter import BaseSplitter, Section, SourceUnavailable
from .latex_cleaner import clean_latex

logger = logging.getLogger(__name__)

# `\input{path}` and `\include{path}` — also `\subfile{path}` and `\import{dir}{file}` (less common).
# We strip line comments BEFORE applying this regex, so a `% \input{x}` comment doesn't trigger.
_INCLUDE_RE = re.compile(
    r"\\(?:input|include|subfile)\s*\{([^}]+)\}"
)
# Inline `% ...` comment stripping. Preserves escaped \% (literal percent).
# Apply line-by-line; doesn't handle multi-line `\iffalse...\fi` blocks (rare).
_COMMENT_RE = re.compile(r"(?<!\\)%.*$", re.MULTILINE)

_MAX_INPUT_DEPTH = 10  # Defensive: hard stop on circular includes.


class ArxivLatexSplitter(BaseSplitter):
    """Parse arXiv LaTeX source into sections."""

    ARXIV_EPRINT_URL = "https://arxiv.org/e-print/{arxiv_id}"

    # Match \section{...} and \subsection{...} only (also `*` variants).
    # 2026-04-28: previously also matched \subsubsection and \paragraph but
    # modern arXiv papers use \paragraph as inline labels (Datasets:,
    # Metrics:, Baselines:, ...) which inflates section count from ~10 to
    # ~30+. Filtering to section/subsection gives chunks that match the
    # reader's mental model of paper structure.
    SECTION_PATTERN = re.compile(
        r"\\(section|subsection)\*?\{([^}]+)\}"
    )

    LEVEL_MAP = {
        "section": 1,
        "subsection": 2,
    }

    def split(self, arxiv_id: str) -> list[Section]:
        """Download arXiv source and extract sections.

        Args:
            arxiv_id: e.g. "2401.12345" or "2401.12345v2"
        """
        tex_content = self._download_and_assemble_tex(arxiv_id)
        return self._parse_sections(tex_content)

    def split_from_file(self, tex_path: str) -> list[Section]:
        """Parse a local .tex file (for testing without network).

        Resolves `\\input` / `\\include` against the directory containing
        `tex_path`. Falls back to original (unexpanded) content for missing
        includes.
        """
        path = Path(tex_path)
        content = path.read_text(encoding="utf-8", errors="ignore")
        # Build a virtual file map: {relative_path_to_root: content} from the
        # directory tree under the .tex file's parent.
        root = path.parent
        files: dict[str, str] = {}
        for p in root.rglob("*.tex"):
            rel = str(p.relative_to(root))
            try:
                files[rel] = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
        expanded = self._expand_includes(content, files)
        return self._parse_sections(expanded)

    # ── Tex assembly (download + include expansion) ─────────────────────

    def _download_and_assemble_tex(self, arxiv_id: str) -> str:
        """Download arXiv source, find main .tex, recursively expand includes."""
        url = self.ARXIV_EPRINT_URL.format(arxiv_id=arxiv_id)

        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise SourceUnavailable(
                f"Cannot download arXiv source for {arxiv_id}: {e}"
            )

        content_bytes = resp.content

        # Try tar.gz, plain tar, gzip, plain — collect ALL .tex files in tar cases.
        tar = self._open_archive(content_bytes)
        if tar is not None:
            try:
                main, files = self._read_all_tex_from_tar(tar)
                if main is None:
                    raise SourceUnavailable(
                        f"No .tex with \\begin{{document}} in archive for {arxiv_id}"
                    )
                return self._expand_includes(main, files)
            finally:
                tar.close()

        # Single-file fallbacks: gzip or plain text. No includes possible.
        try:
            text = gzip.decompress(content_bytes).decode("utf-8", errors="ignore")
            if "\\begin{document}" in text:
                return text
        except (gzip.BadGzipFile, OSError):
            pass

        try:
            text = content_bytes.decode("utf-8", errors="ignore")
            if "\\begin{document}" in text:
                return text
        except UnicodeDecodeError:
            pass

        raise SourceUnavailable(
            f"Cannot extract .tex from arXiv source for {arxiv_id}"
        )

    def _open_archive(self, content_bytes: bytes) -> tarfile.TarFile | None:
        """Try opening as tar.gz, then plain tar. Returns None if neither."""
        for mode in ("r:gz", "r:"):
            try:
                return tarfile.open(fileobj=io.BytesIO(content_bytes), mode=mode)
            except (tarfile.TarError, EOFError):
                continue
        return None

    # Filename keywords that suggest a non-paper supplementary document
    # (rebuttals, response letters, cover letters). These are deprioritized
    # when picking the main .tex among multiple candidates with \begin{document}.
    _NON_PAPER_FILENAME_HINTS = (
        "rebuttal", "response", "cover", "cover_letter", "coverletter",
        "supplement", "supplementary", "supp_", "reply",
    )

    def _read_all_tex_from_tar(
        self, tar: tarfile.TarFile
    ) -> tuple[str | None, dict[str, str]]:
        """Extract every .tex file. Return (main_content, all_files_by_relpath).

        Main-file selection (when multiple .tex have `\\begin{document}`):
          1. Filter out files whose names look like supplementary docs
             (rebuttal, response, cover_letter, supplement, ...).
          2. Score remaining by signals of "real paper":
             - has `\\title{}`               +50
             - count of `\\input{...}`/`\\include{...}` inside    +5 each
             - file size as final tie-breaker (smaller for typical main)
          3. Highest score wins.

        Without scoring, the previous "pick largest" heuristic mis-picked
        rebuttal letters when they happened to be longer than the paper
        (observed: OpenScene 2211.15654 archive has both arxiv.tex and
        rebuttal.tex, rebuttal is larger but contains no \\section commands).
        """
        files: dict[str, str] = {}
        candidates: list[tuple[str, str, int]] = []  # (name, content, size)

        for member in tar.getmembers():
            if not member.isfile() or not member.name.endswith(".tex"):
                continue
            try:
                f = tar.extractfile(member)
                if f is None:
                    continue
                text = f.read().decode("utf-8", errors="ignore")
            except (KeyError, AttributeError, tarfile.TarError):
                continue
            files[member.name] = text
            if "\\begin{document}" in text:
                candidates.append((member.name, text, member.size))

        if not candidates:
            return None, files

        # Filter out supplementary-looking filenames first; if that empties
        # the list, fall back to the unfiltered set.
        def looks_supplementary(name: str) -> bool:
            base = name.lower()
            return any(hint in base for hint in self._NON_PAPER_FILENAME_HINTS)

        non_supp = [c for c in candidates if not looks_supplementary(c[0])]
        pool = non_supp if non_supp else candidates

        def score(item: tuple[str, str, int]) -> tuple[int, int]:
            name, text, size = item
            s = 0
            if "\\title{" in text:
                s += 50
            s += 5 * len(re.findall(r"\\(?:input|include|subfile)\b", text))
            # Tie-breaker: prefer smaller (typical main-only file is small;
            # large file is more likely a self-contained doc that may not be
            # the real paper). Use negative size so larger size = lower secondary.
            return (s, -size)

        pool.sort(key=score, reverse=True)
        main_name = pool[0][0]
        return files[main_name], files

    def _expand_includes(
        self, text: str, files: dict[str, str], depth: int = 0,
        visited: set[str] | None = None,
    ) -> str:
        """Recursively inline `\\input{...}` and `\\include{...}` references.

        Args:
            text: LaTeX source to expand.
            files: map of {relative_path: content}, scoped to project root.
            depth: recursion depth (defensive against pathological cycles).
            visited: set of already-expanded file paths in the current chain.

        Missing includes are left as-is (not raised) — the parser will simply
        not find sections in them, which is recoverable.
        """
        if depth > _MAX_INPUT_DEPTH:
            logger.warning("Max include depth %d exceeded", _MAX_INPUT_DEPTH)
            return text

        if visited is None:
            visited = set()

        # Strip comments first so commented-out \input lines don't get expanded.
        # Done on a working copy; we don't return the comment-stripped form
        # (other parsing wants comments stripped too, but that's done later
        # inside clean_latex/section parsing).
        stripped = _COMMENT_RE.sub("", text)

        def repl(match: re.Match) -> str:
            ref = match.group(1).strip()
            target = self._resolve_include(ref, files)
            if target is None:
                logger.debug("Cannot resolve \\input{%s} (not in archive)", ref)
                return match.group(0)  # leave as-is
            if target in visited:
                logger.warning("Circular include detected: %s", target)
                return ""  # break the cycle
            sub = files[target]
            new_visited = visited | {target}
            return self._expand_includes(sub, files, depth + 1, new_visited)

        return _INCLUDE_RE.sub(repl, stripped)

    @staticmethod
    def _resolve_include(ref: str, files: dict[str, str]) -> str | None:
        """Resolve a `\\input{ref}` argument to a key in `files`.

        LaTeX's `\\input{x}` resolution tries `x` and `x.tex`. We additionally
        normalize away `./` prefixes and trailing slashes.
        """
        cleaned = ref.lstrip("./").rstrip("/")
        candidates = [cleaned, cleaned + ".tex"]

        # Direct hit
        for c in candidates:
            if c in files:
                return c

        # Match by basename or suffix (handles cases where main file is in
        # subdir and \input is relative to that subdir, e.g. main in src/main.tex
        # and \input{intro} → src/intro.tex).
        for c in candidates:
            for key in files:
                if key.endswith("/" + c) or key == c:
                    return key

        return None

    # ── Section parsing ─────────────────────────────────────────────────

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
                    estimated_tokens=max(1, len(cleaned) // 4),
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
            if abstract_text.strip():  # don't append empty abstract block
                sections.append(
                    Section(
                        id="section:Abstract",
                        title="Abstract",
                        content=abstract_text,
                        level=1,
                        order=0,
                        estimated_tokens=max(1, len(abstract_text) // 4),
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

            # Skip sections that have neither prose nor other rich
            # content (figures / tables / equations) AND are not parent
            # headers (i.e. NOT immediately followed by a deeper-level
            # subsection). The latter case ("\section{Method}" followed
            # only by subsections with the actual body) is a legitimate
            # parent section header and must be kept; tests/test_latex_
            # splitter.py::test_finds_all_sections pins that contract.
            #
            # The orphan-empty-section drop fixes
            # tests/integration/test_arxiv_splitter.py::TestSplitRealPaper
            # ::test_no_empty_sections (real arxiv paper produced an
            # empty "Results" section, no children, no body, no figures).
            next_match = matches[i + 1] if i + 1 < len(matches) else None
            next_level = (
                self.LEVEL_MAP.get(next_match.group(1), 1)
                if next_match else 0
            )
            is_parent_header = next_level > level
            has_other_content = bool(metadata)
            if (
                not cleaned_body.strip()
                and not has_other_content
                and not is_parent_header
            ):
                continue

            section_id = f"{cmd}:{title}"

            sections.append(
                Section(
                    id=section_id,
                    title=title,
                    content=cleaned_body,
                    level=level,
                    order=len(sections),
                    # Floor at 1 token so the no-empty-sections invariant
                    # holds even for figure-only / table-only sections.
                    estimated_tokens=max(1, len(cleaned_body) // 4),
                    metadata=metadata,
                )
            )

        return sections

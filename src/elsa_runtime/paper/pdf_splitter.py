"""
pdf_splitter.py — Extract sections from PDF using font analysis.
"""

import re
from collections import Counter
from pathlib import Path

import pymupdf  # PyMuPDF

from .splitter import BaseSplitter, Section, SourceUnavailable


class PdfStructuralSplitter(BaseSplitter):
    """Parse PDF structure by detecting headings via font size + bold."""

    # Common heading patterns in academic papers
    HEADING_PATTERNS = [
        re.compile(r"^(\d+\.?\s+)(.+)$"),          # "1 Introduction", "2.1 Method"
        re.compile(r"^(\d+\.\d+\.?\s+)(.+)$"),      # "2.1 Overview"
        re.compile(r"^([A-Z]\.?\s+)(.+)$"),          # "A Appendix"
        re.compile(r"^(Abstract)$", re.IGNORECASE),
        re.compile(r"^(Introduction)$", re.IGNORECASE),
        re.compile(r"^(Related Work)$", re.IGNORECASE),
        re.compile(r"^(Conclusion)s?$", re.IGNORECASE),
        re.compile(r"^(References)$", re.IGNORECASE),
        re.compile(r"^(Acknowledgments?)$", re.IGNORECASE),
        re.compile(r"^(Appendix.*)$", re.IGNORECASE),
    ]

    def split(self, pdf_path: str) -> list[Section]:
        path = Path(pdf_path)
        if not path.exists():
            raise SourceUnavailable(f"PDF not found: {pdf_path}")

        doc = pymupdf.open(str(path))
        spans = self._extract_spans(doc)
        body_size = self._detect_body_font_size(spans)
        sections = self._segment_by_headings(spans, body_size)
        doc.close()

        return sections

    def _extract_spans(self, doc) -> list[dict]:
        """Extract all text spans with font metadata."""
        spans = []
        for page_num, page in enumerate(doc):
            blocks = page.get_text(
                "dict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE
            )["blocks"]
            for block in blocks:
                if "lines" not in block:
                    continue
                for line in block["lines"]:
                    line_text_parts = []
                    line_size = 0
                    line_flags = 0
                    for span in line["spans"]:
                        line_text_parts.append(span["text"])
                        line_size = max(line_size, span["size"])
                        line_flags |= span["flags"]

                    full_text = "".join(line_text_parts).strip()
                    if not full_text:
                        continue

                    spans.append(
                        {
                            "text": full_text,
                            "size": line_size,
                            "flags": line_flags,
                            "bold": bool(line_flags & 16),
                            "page": page_num,
                            "y": (
                                line["spans"][0]["origin"][1]
                                if line["spans"]
                                else 0
                            ),
                        }
                    )
        return spans

    def _detect_body_font_size(self, spans: list[dict]) -> float:
        """Find the most common font size (= body text size)."""
        sizes = [round(s["size"], 1) for s in spans if len(s["text"]) > 20]
        if not sizes:
            return 10.0
        return Counter(sizes).most_common(1)[0][0]

    def _is_heading(self, span: dict, body_size: float) -> tuple[bool, int]:
        """Determine if a span is a heading and what level.

        Returns: (is_heading, level) where level 1=section, 2=subsection.
        """
        text = span["text"].strip()
        size = span["size"]
        bold = span["bold"]

        # Too long to be a heading
        if len(text) > 100:
            return False, 0

        # References / bibliography — stop here
        if re.match(r"^References$", text, re.IGNORECASE):
            return True, 1

        # Size-based detection
        size_ratio = size / body_size if body_size > 0 else 1.0

        if size_ratio > 1.15:
            # Significantly larger font — likely section heading
            level = 1 if size_ratio > 1.25 else 2
            return True, level

        if bold and size_ratio >= 0.98:
            # Bold at body size — check if it matches heading patterns
            for pattern in self.HEADING_PATTERNS:
                if pattern.match(text):
                    # Determine level from numbering
                    if re.match(r"^\d+\.\d+", text):
                        return True, 2
                    return True, 1
            # Bold short text that looks like a heading
            if len(text) < 60 and not text.endswith("."):
                return True, 2

        return False, 0

    def _segment_by_headings(
        self, spans: list[dict], body_size: float
    ) -> list[Section]:
        """Segment spans into sections based on detected headings."""
        sections = []
        current_title = "Preamble"
        current_level = 1
        current_content_parts: list[str] = []
        current_page = 0
        order = 0

        stop_at_references = True

        for span in spans:
            is_heading, level = self._is_heading(span, body_size)

            if is_heading:
                # Save previous section
                if current_content_parts:
                    content = "\n".join(current_content_parts).strip()
                    if content:  # Skip empty sections
                        sections.append(
                            Section(
                                id=f"{'section' if current_level == 1 else 'subsection'}:{current_title}",
                                title=current_title,
                                content=content,
                                level=current_level,
                                order=order,
                                estimated_tokens=len(content) // 4,
                                page_start=current_page,
                            )
                        )
                        order += 1

                # Check if we should stop
                if stop_at_references and re.match(
                    r"^References$", span["text"], re.IGNORECASE
                ):
                    break

                # Start new section
                current_title = span["text"].strip()
                # Clean numbering prefix: "2.1 Method" -> "Method"
                cleaned = re.sub(r"^[\d.]+\s+", "", current_title)
                if cleaned:
                    current_title = cleaned
                current_level = level
                current_content_parts = []
                current_page = span["page"]
            else:
                current_content_parts.append(span["text"])

        # Don't forget the last section
        if current_content_parts:
            content = "\n".join(current_content_parts).strip()
            if content:
                sections.append(
                    Section(
                        id=f"{'section' if current_level == 1 else 'subsection'}:{current_title}",
                        title=current_title,
                        content=content,
                        level=current_level,
                        order=order,
                        estimated_tokens=len(content) // 4,
                        page_start=current_page,
                    )
                )

        return sections

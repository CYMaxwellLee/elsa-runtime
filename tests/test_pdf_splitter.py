"""Unit tests for PDF structural splitter."""

import pytest

from elsa_runtime.paper.pdf_splitter import PdfStructuralSplitter
from elsa_runtime.paper.splitter import SourceUnavailable


@pytest.fixture
def splitter():
    return PdfStructuralSplitter()


class TestDetectBodyFontSize:
    def test_most_common_size(self, splitter):
        spans = [
            {"text": "A" * 30, "size": 10.0},  # body text
            {"text": "B" * 30, "size": 10.0},  # body text
            {"text": "C" * 30, "size": 10.0},  # body text
            {"text": "D" * 30, "size": 14.0},  # heading
        ]
        assert splitter._detect_body_font_size(spans) == 10.0

    def test_empty_spans_default(self, splitter):
        assert splitter._detect_body_font_size([]) == 10.0

    def test_short_spans_ignored(self, splitter):
        # Spans with < 20 chars should be ignored for body size detection
        spans = [
            {"text": "Short", "size": 14.0},  # too short, ignored
            {"text": "A" * 30, "size": 10.0},
            {"text": "B" * 30, "size": 10.0},
        ]
        assert splitter._detect_body_font_size(spans) == 10.0


class TestHeadingDetectionBySize:
    def test_large_font_is_heading(self, splitter):
        span = {"text": "Introduction", "size": 14.0, "bold": False}
        is_h, level = splitter._is_heading(span, body_size=10.0)
        assert is_h is True
        assert level == 1  # size_ratio = 1.4 > 1.25

    def test_medium_font_is_subsection(self, splitter):
        span = {"text": "Problem Setup", "size": 12.0, "bold": False}
        is_h, level = splitter._is_heading(span, body_size=10.0)
        assert is_h is True
        assert level == 2  # size_ratio = 1.2, between 1.15 and 1.25

    def test_body_size_not_heading(self, splitter):
        span = {"text": "This is a regular sentence in the body text.", "size": 10.0, "bold": False}
        is_h, _ = splitter._is_heading(span, body_size=10.0)
        assert is_h is False

    def test_long_text_not_heading(self, splitter):
        span = {"text": "A" * 101, "size": 14.0, "bold": True}
        is_h, _ = splitter._is_heading(span, body_size=10.0)
        assert is_h is False


class TestHeadingDetectionByBoldPattern:
    def test_bold_numbered_section(self, splitter):
        span = {"text": "1 Introduction", "size": 10.0, "bold": True}
        is_h, level = splitter._is_heading(span, body_size=10.0)
        assert is_h is True
        assert level == 1

    def test_bold_numbered_subsection(self, splitter):
        span = {"text": "2.1 Architecture", "size": 10.0, "bold": True}
        is_h, level = splitter._is_heading(span, body_size=10.0)
        assert is_h is True
        assert level == 2

    def test_bold_keyword_heading(self, splitter):
        span = {"text": "Introduction", "size": 10.0, "bold": True}
        is_h, level = splitter._is_heading(span, body_size=10.0)
        assert is_h is True
        assert level == 1

    def test_bold_conclusion(self, splitter):
        span = {"text": "Conclusions", "size": 10.0, "bold": True}
        is_h, _ = splitter._is_heading(span, body_size=10.0)
        assert is_h is True


class TestStopAtReferences:
    def test_references_detected_as_heading(self, splitter):
        span = {"text": "References", "size": 10.0, "bold": True}
        is_h, level = splitter._is_heading(span, body_size=10.0)
        assert is_h is True
        assert level == 1


class TestSubsectionNumberingDetection:
    def test_two_level_numbering(self, splitter):
        span = {"text": "3.2 Training Details", "size": 10.0, "bold": True}
        is_h, level = splitter._is_heading(span, body_size=10.0)
        assert is_h is True
        assert level == 2

    def test_single_level_numbering(self, splitter):
        span = {"text": "3 Experiments", "size": 10.0, "bold": True}
        is_h, level = splitter._is_heading(span, body_size=10.0)
        assert is_h is True
        assert level == 1


class TestSegmentByHeadings:
    def test_basic_segmentation(self, splitter):
        spans = [
            {"text": "Introduction", "size": 14.0, "bold": True, "page": 0, "y": 100, "flags": 16},
            {"text": "This is the intro.", "size": 10.0, "bold": False, "page": 0, "y": 120, "flags": 0},
            {"text": "More intro text.", "size": 10.0, "bold": False, "page": 0, "y": 140, "flags": 0},
            {"text": "Method", "size": 14.0, "bold": True, "page": 1, "y": 100, "flags": 16},
            {"text": "Method description.", "size": 10.0, "bold": False, "page": 1, "y": 120, "flags": 0},
            {"text": "References", "size": 14.0, "bold": True, "page": 2, "y": 100, "flags": 16},
        ]
        sections = splitter._segment_by_headings(spans, body_size=10.0)

        # Should find Introduction and Method (stops at References)
        titles = [s.title for s in sections]
        assert "Introduction" in titles
        assert "Method" in titles
        assert "References" not in titles

        intro = [s for s in sections if s.title == "Introduction"][0]
        assert "This is the intro." in intro.content

    def test_stops_at_references(self, splitter):
        spans = [
            {"text": "Conclusion", "size": 14.0, "bold": True, "page": 5, "y": 100, "flags": 16},
            {"text": "We conclude.", "size": 10.0, "bold": False, "page": 5, "y": 120, "flags": 0},
            {"text": "References", "size": 14.0, "bold": True, "page": 5, "y": 200, "flags": 16},
            {"text": "[1] Smith et al.", "size": 10.0, "bold": False, "page": 5, "y": 220, "flags": 0},
        ]
        sections = splitter._segment_by_headings(spans, body_size=10.0)

        titles = [s.title for s in sections]
        assert "Conclusion" in titles
        assert "References" not in titles


class TestPdfNotFound:
    def test_missing_pdf_raises(self, splitter):
        with pytest.raises(SourceUnavailable, match="PDF not found"):
            splitter.split("/nonexistent/path/paper.pdf")

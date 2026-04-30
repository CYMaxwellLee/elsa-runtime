"""Tests for arXiv metadata extraction.

Network-dependent tests are mocked via urllib.request.urlopen patching so
the suite stays offline-runnable.
"""

from __future__ import annotations

import io
import urllib.request
from unittest.mock import patch

import pytest

from elsa_runtime.paper.arxiv_meta import (
    ArxivMetadata,
    clear_cache,
    fetch_arxiv_metadata,
    parse_year_from_arxiv_id,
)


# ── parse_year_from_arxiv_id ────────────────────────────────────────────


class TestParseYear:
    def test_new_format_2107(self):
        assert parse_year_from_arxiv_id("2107.03006") == 2021

    def test_new_format_2503(self):
        assert parse_year_from_arxiv_id("2503.04482") == 2025

    def test_new_format_with_version(self):
        assert parse_year_from_arxiv_id("2503.04482v3") == 2025

    def test_old_format(self):
        assert parse_year_from_arxiv_id("cs.LG/0701001") == 2007

    def test_old_format_pre_2000(self):
        # Old archive convention: 1991-1999 papers used 91-99 prefix
        assert parse_year_from_arxiv_id("hep-th/9601001") == 1996

    def test_invalid_returns_zero(self):
        assert parse_year_from_arxiv_id("garbage") == 0
        assert parse_year_from_arxiv_id("") == 0
        assert parse_year_from_arxiv_id("not.a.real.id") == 0


# ── fetch_arxiv_metadata (mocked HTTP) ──────────────────────────────────


def _atom_response(authors=None, journal_ref=None, title="Sample Paper", published="2021-07-07T00:00:00Z"):
    """Build a minimal arXiv Atom XML response."""
    author_blocks = ""
    for name in (authors or []):
        author_blocks += f"<author><name>{name}</name></author>"
    journal_block = (
        f"<arxiv:journal_ref>{journal_ref}</arxiv:journal_ref>"
        if journal_ref
        else ""
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <title>{title}</title>
    <published>{published}</published>
    {author_blocks}
    {journal_block}
  </entry>
</feed>""".encode("utf-8")


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body
    def read(self):
        return self._body
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


@pytest.fixture(autouse=True)
def reset_cache():
    clear_cache()
    yield
    clear_cache()


class TestFetchArxivMetadata:
    def test_happy_path(self):
        body = _atom_response(
            authors=["Jacob Austin", "Daniel D. Johnson", "Jonathan Ho"],
            journal_ref="NeurIPS 2021",
            title="Structured Denoising Diffusion Models",
            published="2021-07-07T17:00:00Z",
        )
        with patch.object(urllib.request, "urlopen", return_value=_FakeResponse(body)):
            meta = fetch_arxiv_metadata("2107.03006")
        assert meta.arxiv_id == "2107.03006"
        assert meta.year == 2021
        assert meta.authors == "Jacob Austin, Daniel D. Johnson, Jonathan Ho"
        assert meta.venue == "NeurIPS 2021"
        assert "Structured Denoising" in meta.title

    def test_no_journal_ref_leaves_venue_empty(self):
        body = _atom_response(
            authors=["A. Author"],
            journal_ref=None,  # arXiv-only papers omit this
            title="Some Preprint",
        )
        with patch.object(urllib.request, "urlopen", return_value=_FakeResponse(body)):
            meta = fetch_arxiv_metadata("2503.04482")
        assert meta.venue == ""
        assert meta.authors == "A. Author"

    def test_year_from_published_overrides_id_year(self):
        # Suppose ID says 2021 but the API says 2022 (e.g. corrected republish)
        body = _atom_response(
            authors=["X"], published="2022-03-15T00:00:00Z",
        )
        with patch.object(urllib.request, "urlopen", return_value=_FakeResponse(body)):
            meta = fetch_arxiv_metadata("2107.03006")
        assert meta.year == 2022

    def test_network_failure_returns_shell(self):
        with patch.object(urllib.request, "urlopen", side_effect=OSError("boom")):
            meta = fetch_arxiv_metadata("2107.03006", retries=0)
        # Still gets year from ID; authors/venue/title empty
        assert meta.arxiv_id == "2107.03006"
        assert meta.year == 2021
        assert meta.authors == ""
        assert meta.venue == ""
        assert meta.title == ""

    def test_malformed_xml_returns_shell(self):
        with patch.object(urllib.request, "urlopen", return_value=_FakeResponse(b"<not-xml")):
            meta = fetch_arxiv_metadata("2503.04482", retries=0)
        assert meta.year == 2025
        assert meta.authors == ""

    def test_cache_hit_avoids_second_call(self):
        body = _atom_response(authors=["Solo Author"])
        mock = patch.object(urllib.request, "urlopen", return_value=_FakeResponse(body))
        with mock as m:
            fetch_arxiv_metadata("2107.03006")
            fetch_arxiv_metadata("2107.03006")
            fetch_arxiv_metadata("2107.03006")
        # urlopen should be called once even with 3 fetches
        assert m.call_count == 1

    def test_empty_id_returns_zeroed(self):
        meta = fetch_arxiv_metadata("")
        assert meta == ArxivMetadata("", 0, "", "", "")

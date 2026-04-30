"""arXiv metadata extraction.

Fills the gap left by paper_harvest.py — `authors`, `year`, `venue` were
written as empty strings into LanceDB. This module fetches them from:

  1. arXiv ID itself (year, derivable without network)
  2. arXiv export API (authors, journal-ref / venue)

Designed to be cheap and offline-tolerant:
  - Year parsing is pure string work.
  - The API call is one HTTP GET per paper, ~200ms typical.
  - Failures degrade to empty strings — the harvest still proceeds.
  - Per-process cache so the same paper is fetched only once.
"""

from __future__ import annotations

import logging
import re
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# arXiv IDs come in two shapes:
#   - new (>=2007-04): YYMM.NNNNN[vN]  e.g. 2107.03006 / 2503.04482v1
#   - old (<2007-04):  archive/YYMMNNN e.g. cs.LG/0701001
_NEW_ID_RE = re.compile(r"^(\d{2})(\d{2})\.\d{4,5}(?:v\d+)?$")
_OLD_ID_RE = re.compile(r"^[a-z\-]+(?:\.[A-Z]{2})?/(\d{2})(\d{2})\d{3}$")


def parse_year_from_arxiv_id(arxiv_id: str) -> int:
    """Decode the year (4-digit) from an arXiv ID.

    arXiv started its current YYMM.NNNNN scheme in April 2007. Before that
    the archive/YYMMNNN form was used. We handle both, plus optional vN
    version suffix.

    Returns 0 if the ID doesn't match either format — caller should treat
    that as 'unknown year' rather than '1900'.
    """
    if not arxiv_id:
        return 0
    m = _NEW_ID_RE.match(arxiv_id) or _OLD_ID_RE.match(arxiv_id)
    if not m:
        return 0
    yy = int(m.group(1))
    # arXiv ID format started 2007 → 91-99 = 1991-1999, 00-90 = 2000-2090.
    # In practice the new format only emits 07+ so this branch is mostly
    # cosmetic; included for the rare archive/9xMMNNN cross-listing.
    return 1900 + yy if yy >= 91 else 2000 + yy


# Atom namespace used by arXiv's API
_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}


@dataclass(frozen=True)
class ArxivMetadata:
    arxiv_id: str
    year: int
    authors: str  # comma-joined; empty string on failure
    venue: str    # journal-ref if available; empty string otherwise
    title: str    # canonical title from arXiv (currently not stored in
                  # LanceDB schema, but useful for callers / logging)


# Per-process cache. paper_harvest hits each paper once per chunk-group
# and we don't want to repeat the network call.
_cache: dict[str, ArxivMetadata] = {}


def fetch_arxiv_metadata(
    arxiv_id: str,
    *,
    timeout: float = 10.0,
    retries: int = 2,
    retry_backoff: float = 1.5,
) -> ArxivMetadata:
    """Fetch authors / venue / title from arXiv export API.

    Idempotent: caches by arxiv_id within the process. Network failures
    degrade to a metadata object with empty author/venue/title and the
    derived year, rather than raising.
    """
    if not arxiv_id:
        return ArxivMetadata("", 0, "", "", "")

    cached = _cache.get(arxiv_id)
    if cached is not None:
        return cached

    year = parse_year_from_arxiv_id(arxiv_id)
    url = (
        "https://export.arxiv.org/api/query?"
        + urllib.parse.urlencode({"id_list": arxiv_id, "max_results": 1})
    )

    last_err: Exception | None = None
    xml_text = ""
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "elsa-runtime/paper_harvest"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                xml_text = resp.read().decode("utf-8", errors="replace")
            break
        except Exception as exc:
            last_err = exc
            if attempt < retries:
                time.sleep(retry_backoff ** attempt)
                continue
            logger.warning(
                "arXiv metadata fetch failed for %s after %d attempts: %s",
                arxiv_id, retries + 1, exc,
            )

    if not xml_text:
        # All retries exhausted — return shell metadata so harvest can continue.
        meta = ArxivMetadata(arxiv_id, year, "", "", "")
        _cache[arxiv_id] = meta
        return meta

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        logger.warning(
            "arXiv metadata XML parse failed for %s: %s", arxiv_id, exc
        )
        meta = ArxivMetadata(arxiv_id, year, "", "", "")
        _cache[arxiv_id] = meta
        return meta

    entry = root.find("atom:entry", _NS)
    if entry is None:
        logger.warning("arXiv API returned no <entry> for %s", arxiv_id)
        meta = ArxivMetadata(arxiv_id, year, "", "", "")
        _cache[arxiv_id] = meta
        return meta

    # Authors: <author><name>X</name></author>+
    author_names = []
    for a in entry.findall("atom:author", _NS):
        name_el = a.find("atom:name", _NS)
        if name_el is not None and name_el.text:
            author_names.append(name_el.text.strip())
    authors_str = ", ".join(author_names)

    # Venue: <arxiv:journal_ref>...</arxiv:journal_ref> if it exists.
    venue = ""
    journal = entry.find("arxiv:journal_ref", _NS)
    if journal is not None and journal.text:
        venue = journal.text.strip()

    # Title: <title>X</title>. Strip whitespace + collapse internal newlines.
    title = ""
    title_el = entry.find("atom:title", _NS)
    if title_el is not None and title_el.text:
        title = re.sub(r"\s+", " ", title_el.text).strip()

    # Confirm year against arXiv's reported published date if we got one.
    # If the ID-derived year is wrong (replaced paper / cross-listing),
    # arXiv's published date is more authoritative.
    pub_el = entry.find("atom:published", _NS)
    if pub_el is not None and pub_el.text:
        try:
            year_from_pub = int(pub_el.text[:4])
            if year_from_pub > 1990:
                year = year_from_pub
        except ValueError:
            pass

    meta = ArxivMetadata(
        arxiv_id=arxiv_id,
        year=year,
        authors=authors_str,
        venue=venue,
        title=title,
    )
    _cache[arxiv_id] = meta
    return meta


def clear_cache() -> None:
    """Reset the per-process cache. Mostly for tests."""
    _cache.clear()

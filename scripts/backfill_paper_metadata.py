#!/usr/bin/env python3
"""Backfill missing arXiv metadata (authors / venue) for papers in LanceDB.

Context: the 2026-04-30 → 2026-05-01 launchd harvest run successfully
ingested 81 papers, but 9 papers' arXiv API calls failed (HTTP 429 / read
timeout) inside a 6-minute burst window. Those papers ended up in LanceDB
with vectors + text + year + domain populated, but `authors` and `venue`
empty. The vectors and text are correct; only the metadata layer needs
backfilling — we do NOT need to re-embed.

This script:
  1. Connects to ~/.elsa-system/lancedb (papers table)
  2. Finds rows where authors is empty
  3. Re-fetches arxiv_meta (now with rate-limit hardening) for each
     unique arxiv_id
  4. Updates the `authors` and `venue` columns in place via
     lance native `update(where=..., values=...)` — no re-embedding

Idempotent: re-running after success is a no-op (no rows match the
where clause).
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from pathlib import Path

import lancedb

from elsa_runtime.paper.arxiv_meta import (
    clear_cache,
    fetch_arxiv_metadata,
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backfill_paper_metadata")


DEFAULT_LANCE_PATH = Path.home() / ".elsa-system" / "lancedb"
TABLE_NAME = "papers"


def _escape_sql_string(value: str) -> str:
    """Escape a string for safe interpolation into a SQL literal."""
    return value.replace("'", "''")


def find_papers_missing_authors(tbl) -> dict[str, int]:
    """Return {arxiv_id: chunk_count} for papers with at least one chunk
    where authors is empty. Empty = NULL or empty string."""
    arrow = tbl.to_arrow()
    ids = arrow["arxiv_id"].to_pylist()
    authors = arrow["authors"].to_pylist()

    counts: dict[str, int] = defaultdict(int)
    for aid, a in zip(ids, authors):
        if a is None or a == "":
            counts[aid or ""] += 1
    counts.pop("", None)
    return dict(counts)


def backfill_paper(tbl, arxiv_id: str) -> tuple[bool, str, str]:
    """Fetch fresh metadata and update all rows for this arxiv_id in place.

    Returns (ok, authors, venue). On API failure returns (False, "", "")
    and the caller can decide whether to log + continue.
    """
    meta = fetch_arxiv_metadata(arxiv_id)
    if not meta.authors:
        # Still no authors after the refresh — could be a permanently
        # malformed ID, a withdrawn paper, or arXiv API still rate-limiting.
        return False, "", meta.venue

    # Update via lance native — only writes the metadata columns, no
    # vector / text disturbance. lance handles `values` parameter
    # escaping; only `where` (a raw SQL fragment) needs manual escape.
    safe_id = _escape_sql_string(arxiv_id)

    where = f"arxiv_id = '{safe_id}'"
    values = {
        "authors": meta.authors,
        "venue": meta.venue or "",
    }
    # Update venue too in case the API returned one we didn't have before.
    result = tbl.update(where=where, values=values)
    rows = getattr(result, "rows_updated", None)
    if rows is None:
        # Older lancedb versions may return dict
        rows = result.get("rows_updated") if isinstance(result, dict) else 0
    logger.info(
        "  %s: updated %s rows | authors='%s'%s",
        arxiv_id,
        rows,
        meta.authors[:60] + ("..." if len(meta.authors) > 60 else ""),
        f" venue='{meta.venue}'" if meta.venue else "",
    )
    return True, meta.authors, meta.venue


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill missing arXiv metadata (authors/venue) "
                    "for papers already ingested into LanceDB.",
    )
    parser.add_argument(
        "--lance-path",
        default=str(DEFAULT_LANCE_PATH),
        help="LanceDB connection path (default: ~/.elsa-system/lancedb)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be backfilled without writing.",
    )
    parser.add_argument(
        "--ids",
        default="",
        help="Comma-separated arxiv_ids to backfill (defaults to "
             "auto-detect papers with empty authors).",
    )
    args = parser.parse_args()

    lance_path = Path(args.lance_path).expanduser().resolve()
    db = lancedb.connect(str(lance_path))
    if TABLE_NAME not in [t for t in db.list_tables().tables]:
        logger.error("Table '%s' not found at %s", TABLE_NAME, lance_path)
        return 1

    tbl = db.open_table(TABLE_NAME)
    logger.info("Connected to %s, %s table = %d rows",
                lance_path, TABLE_NAME, tbl.count_rows())

    if args.ids:
        # Explicit list — useful for re-running specific papers
        targets = {x.strip(): -1 for x in args.ids.split(",") if x.strip()}
    else:
        targets = find_papers_missing_authors(tbl)

    if not targets:
        logger.info("No papers need backfilling. Already complete.")
        return 0

    logger.info("Papers needing backfill: %d", len(targets))
    for aid in sorted(targets):
        n = targets[aid]
        suffix = f" ({n} chunks affected)" if n >= 0 else ""
        logger.info("  - %s%s", aid, suffix)

    if args.dry_run:
        logger.info("Dry-run: no writes performed.")
        return 0

    # Reset the per-process cache so we don't pull stale empty entries
    # from earlier partially-successful runs.
    clear_cache()

    succeeded = 0
    failed: list[str] = []
    for aid in sorted(targets):
        ok, _, _ = backfill_paper(tbl, aid)
        if ok:
            succeeded += 1
        else:
            failed.append(aid)
            logger.warning("  %s: still no authors after refetch; skipped", aid)

    logger.info(
        "============================================================\n"
        "Done. %d backfilled / %d failed-still / %d total targets.",
        succeeded, len(failed), len(targets),
    )
    if failed:
        logger.warning("Failed (consider re-running later):")
        for aid in failed:
            logger.warning("  - %s", aid)

    return 0 if not failed else 2


if __name__ == "__main__":
    sys.exit(main())

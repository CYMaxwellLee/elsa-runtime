#!/usr/bin/env python3
"""
paper_harvest.py — Batch ingestion pipeline to load papers into LanceDB.

Reads a curated YAML manifest of papers (arxiv_id or local pdf_path), splits
each into sections via PaperSplitter, and writes to the LanceDB `papers` table.

In dry-run mode, runs splitting only (no DB writes) and produces a report
flagging papers where the splitter likely failed (too few sections, suspiciously
long sections, etc.) so the user can curate the seed list before full ingest.

Usage:
    # Dry run with the standard seed list (writes dry_run_report.md next to it):
    python scripts/paper_harvest.py \\
        --seed-yaml ~/Projects/elsa-data/paper_harvest/seed_papers.yaml \\
        --dry-run

    # Full ingest:
    python scripts/paper_harvest.py \\
        --seed-yaml ~/Projects/elsa-data/paper_harvest/seed_papers.yaml \\
        --batch-size 5
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

import yaml

from elsa_runtime.paper import PaperSplitter, SourceUnavailable
from elsa_runtime.paper.arxiv_meta import fetch_arxiv_metadata
from elsa_runtime.paper.chunker import (
    TARGET_CHARS,
    chunk_sections,
    filter_garbage_chunks,
)
from elsa_runtime.storage.lancedb_store import LanceDBStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("paper_harvest")

ARXIV_RATE_LIMIT_SECONDS = 3
TABLE_NAME = "papers"

# Heuristic thresholds for dry-run sanity checks.
# Tuned 2026-04-28 v2 to reduce false positives: a single short section
# (acks, brief conclusion) is normal; we only flag when MANY sections are
# pathologically short, which suggests over-aggressive splitting.
SUSPICIOUS_FEW_SECTIONS = 5
SUSPICIOUS_AVG_SECTION_CHARS = 10_000
SUSPICIOUS_SHORT_SECTION_CHARS = 30  # was 100; acks/refs legitimately ~50-100 chars
SUSPICIOUS_SHORT_RATIO = 0.30        # flag only if >30% of sections are tiny
SUSPICIOUS_SHORT_MIN_COUNT = 3       # AND at least this many tiny sections


def load_manifest(path: Path) -> list[dict]:
    """Load and validate a YAML manifest file."""
    with open(path) as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict) or "papers" not in data:
        raise ValueError(
            f"Manifest must contain a top-level 'papers' key: {path}"
        )

    papers = data["papers"]
    if not isinstance(papers, list):
        raise ValueError(f"'papers' must be a list, got {type(papers).__name__}")

    for i, entry in enumerate(papers):
        if not isinstance(entry, dict):
            raise ValueError(f"Paper #{i} is not a dict: {entry}")
        if not (
            entry.get("arxiv_id") or entry.get("pdf_path") or entry.get("path")
        ):
            raise ValueError(
                f"Paper #{i} must have one of "
                f"'arxiv_id' / 'pdf_path' / 'path': {entry}"
            )

    return papers


def resolve_source(entry: dict) -> str:
    """Pick the right source string for PaperSplitter."""
    return (
        entry.get("arxiv_id")
        or entry.get("pdf_path")
        or entry.get("path", "???")
    )


def analyze_split_result(sections: list, warnings: list[str]) -> dict:
    """Compute split quality metrics for the dry-run report."""
    if not sections:
        return {
            "section_count": 0,
            "avg_chars": 0,
            "min_chars": 0,
            "max_chars": 0,
            "warnings": warnings,
            "flags": ["NO_SECTIONS"],
        }

    char_counts = [len(s.content) for s in sections]
    avg = sum(char_counts) / len(char_counts)
    min_chars = min(char_counts)
    max_chars = max(char_counts)

    flags: list[str] = []
    if len(sections) < SUSPICIOUS_FEW_SECTIONS:
        flags.append(f"FEW_SECTIONS<{SUSPICIOUS_FEW_SECTIONS}")
    if avg > SUSPICIOUS_AVG_SECTION_CHARS:
        flags.append(f"LONG_AVG>{SUSPICIOUS_AVG_SECTION_CHARS}")
    short_count = sum(1 for c in char_counts if c < SUSPICIOUS_SHORT_SECTION_CHARS)
    short_ratio = short_count / len(char_counts) if char_counts else 0.0
    if (
        short_count >= SUSPICIOUS_SHORT_MIN_COUNT
        and short_ratio > SUSPICIOUS_SHORT_RATIO
    ):
        flags.append(
            f"MANY_SHORT_SECTIONS({short_count}/{len(char_counts)}<{SUSPICIOUS_SHORT_SECTION_CHARS}c)"
        )

    return {
        "section_count": len(sections),
        "avg_chars": round(avg),
        "min_chars": min_chars,
        "max_chars": max_chars,
        "warnings": warnings,
        "flags": flags,
    }


async def is_already_ingested(store: LanceDBStore, paper_id: str) -> int:
    """Check if any sections for this paper_id are already in LanceDB.
    Returns existing section count (0 = not ingested, >0 = at least partial)."""
    try:
        return await store.count(TABLE_NAME, where={"arxiv_id": paper_id})
    except Exception:
        return 0


async def ingest_paper(
    splitter: PaperSplitter,
    store: LanceDBStore | None,
    entry: dict,
    dry_run: bool,
    batch_size: int,
    skip_if_exists: bool = True,
) -> dict:
    """Ingest a single paper. Returns a status dict."""
    source = resolve_source(entry)
    title = entry.get("title", "")
    tier = entry.get("tier", "C")
    domain = entry.get("domain", "")

    logger.info("Processing: %s (%s)", source, title or "no title")

    # Resume support: skip if already ingested. We use the arxiv_id as the
    # idempotency key. paper_id from splitter.split() == arxiv_id for arXiv
    # entries; for local PDFs it's the filename stem (also stable).
    if not dry_run and skip_if_exists and store is not None:
        # Best guess for paper_id without doing a full split: use entry's
        # arxiv_id if present (matches what splitter would produce for arXiv).
        prejudge_id = entry.get("arxiv_id") or ""
        if prejudge_id:
            existing = await is_already_ingested(store, prejudge_id)
            if existing > 0:
                logger.info(
                    "  SKIP (already in DB): %d sections for %s",
                    existing, prejudge_id,
                )
                return {
                    "source": source,
                    "title": title,
                    "tier": tier,
                    "domain": domain,
                    "method": "cached",
                    "status": "already_ingested",
                    "sections": existing,
                }

    result = splitter.split(source, title=title)
    sections = result.sections
    paper_id = result.paper_id
    method = result.method.value
    warnings = list(result.warnings)

    logger.info(
        "  Split OK: %d sections, method=%s, warnings=%d",
        len(sections), method, len(warnings),
    )
    for w in warnings:
        logger.warning("  Warning: %s", w)

    analysis = analyze_split_result(sections, warnings)

    if dry_run:
        return {
            "source": source,
            "title": title,
            "tier": tier,
            "domain": domain,
            "method": method,
            "status": "dry_run",
            "analysis": analysis,
        }

    # Real ingest path
    if store is None:
        raise RuntimeError("Store not connected; cannot ingest in non-dry-run mode")

    # Chunk sections so no single embedding input exceeds BGE-M3's safe limit.
    # Without this, sections > ~6000 chars caused "Invalid buffer size: NN GiB"
    # MPS errors on M1 (verified 2026-04-29 ingest).
    chunks = chunk_sections(sections, target_chars=TARGET_CHARS)

    # Filter out figure-only / empty chunks. Without this, the harvest
    # writes ~6% phantom rows (verified 4/30 audit: 74 / 1252 chunks were
    # garbage like "[FIGURE]\n[FIGURE]\n..."). Phantom rows still get
    # embedded, then pollute retrieval downstream.
    chunks, n_dropped = filter_garbage_chunks(chunks)
    logger.info(
        "  Chunked: %d sections -> %d chunks (max %d chars each)%s",
        len(sections),
        len(chunks),
        TARGET_CHARS,
        f" [dropped {n_dropped} garbage]" if n_dropped else "",
    )

    # Fetch authors / venue / year from arXiv API. One network call per
    # paper, cached in-process. Failures degrade to empty strings (the
    # harvest still completes; metadata can be backfilled later).
    arxiv_meta = fetch_arxiv_metadata(paper_id) if paper_id else None
    if arxiv_meta:
        logger.info(
            "  arXiv meta: year=%d, authors=%d names, venue=%r",
            arxiv_meta.year,
            len([a for a in arxiv_meta.authors.split(",") if a.strip()]),
            arxiv_meta.venue or "(none)",
        )

    ids = []
    documents = []
    metadatas = []

    for ch in chunks:
        # Deterministic ID: <arxiv_id>::<section_id>::chunk:<idx>
        # Single-chunk sections still get the chunk:0 suffix for consistency.
        doc_id = f"{paper_id}::{ch.section_id}::chunk:{ch.chunk_idx}"
        ids.append(doc_id)
        documents.append(ch.content)
        meta_row = {
            "arxiv_id": paper_id,
            "tier": tier,
            "domain": domain,
        }
        if arxiv_meta:
            # Only set when fetched successfully — empty strings are still
            # better than the previous pure-default behaviour because
            # downstream filters can distinguish "API confirmed empty" from
            # "never fetched", but for now we just write what we have.
            meta_row["year"] = arxiv_meta.year
            meta_row["authors"] = arxiv_meta.authors
            meta_row["venue"] = arxiv_meta.venue
        metadatas.append(meta_row)

    total_added = 0
    for start in range(0, len(ids), batch_size):
        end = start + batch_size
        write_results = await store.add(
            TABLE_NAME, ids[start:end], documents[start:end], metadatas[start:end],
        )
        total_added += len(write_results)
        logger.info(
            "  Added batch [%d:%d] (%d docs)", start, end, len(write_results)
        )

    return {
        "source": source,
        "title": title,
        "tier": tier,
        "domain": domain,
        "method": method,
        "status": "ok",
        "sections": len(sections),
        "added": total_added,
        "analysis": analysis,
    }


def write_dry_run_report(report_path: Path, results: list[dict]) -> None:
    """Write a markdown report for the dry-run results."""
    report_path.parent.mkdir(parents=True, exist_ok=True)

    total = len(results)
    successful = [r for r in results if r["status"] == "dry_run"]
    failed = [r for r in results if r["status"] == "error"]
    flagged = [r for r in successful if r["analysis"]["flags"]]
    clean = [r for r in successful if not r["analysis"]["flags"]]

    lines = [
        "# Paper Harvest Dry-Run Report",
        "",
        f"_Generated: {datetime.now().isoformat(timespec='seconds')}_",
        "",
        "## Summary",
        "",
        f"- Total papers in seed: **{total}**",
        f"- Splitter succeeded: **{len(successful)}**",
        f"- Splitter failed: **{len(failed)}**",
        f"- Clean (no flags): **{len(clean)}**",
        f"- Flagged (review before ingest): **{len(flagged)}**",
        "",
    ]

    # Method distribution
    methods = Counter(r["method"] for r in successful)
    if methods:
        lines += ["## Splitter method distribution", ""]
        for m, n in methods.most_common():
            lines.append(f"- `{m}`: {n}")
        lines.append("")

    # Domain distribution
    domains = Counter(r.get("domain", "?") for r in successful)
    if domains:
        lines += ["## Domain distribution", ""]
        for d, n in domains.most_common():
            lines.append(f"- `{d}`: {n}")
        lines.append("")

    # Failed (couldn't split at all)
    if failed:
        lines += ["## ❌ Failed (cannot ingest)", ""]
        for r in failed:
            lines.append(f"- `{r['source']}` — {r.get('title', '?')}")
            lines.append(f"  - error: `{r.get('error', '?')}`")
        lines.append("")

    # Flagged (split but suspicious)
    if flagged:
        lines += ["## ⚠️ Flagged (review before ingest)", ""]
        lines += [
            "| Source | Title | Method | Sections | Avg chars | Flags |",
            "|--------|-------|--------|---------:|----------:|-------|",
        ]
        for r in flagged:
            a = r["analysis"]
            flags_str = ", ".join(a["flags"])
            title = (r.get("title") or "")[:40]
            lines.append(
                f"| `{r['source']}` | {title} | {r['method']} "
                f"| {a['section_count']} | {a['avg_chars']} | {flags_str} |"
            )
        lines.append("")

    # Clean papers
    if clean:
        lines += [
            "## ✅ Clean (ready to ingest)",
            "",
            "| Source | Title | Method | Sections | Avg chars |",
            "|--------|-------|--------|---------:|----------:|",
        ]
        for r in clean:
            a = r["analysis"]
            title = (r.get("title") or "")[:40]
            lines.append(
                f"| `{r['source']}` | {title} | {r['method']} "
                f"| {a['section_count']} | {a['avg_chars']} |"
            )
        lines.append("")

    # Per-paper warnings (compact)
    warned = [r for r in successful if r["analysis"].get("warnings")]
    if warned:
        lines += ["## Per-paper splitter warnings", ""]
        for r in warned:
            lines.append(f"- `{r['source']}` — {r.get('title', '?')[:40]}")
            for w in r["analysis"]["warnings"]:
                lines.append(f"  - {w}")
        lines.append("")

    lines += [
        "## Flag legend",
        "",
        f"- `FEW_SECTIONS<{SUSPICIOUS_FEW_SECTIONS}`: splitter only found a"
        " handful of sections; LaTeX `\\input` expansion or PDF heuristic likely failed.",
        f"- `LONG_AVG>{SUSPICIOUS_AVG_SECTION_CHARS}`: average section is"
        " huge; sections probably were not actually split.",
        f"- `MANY_SHORT_SECTIONS(...)`: more than"
        f" {int(SUSPICIOUS_SHORT_RATIO*100)}% of sections are under"
        f" {SUSPICIOUS_SHORT_SECTION_CHARS} chars (and at least"
        f" {SUSPICIOUS_SHORT_MIN_COUNT} of them); suggests over-aggressive splitting.",
        "",
        "## Next steps",
        "",
        "1. Review flagged + failed papers above.",
        "2. For papers you decide to skip, add `skip: true` to their YAML entry"
        " (or remove them).",
        "3. Re-run dry-run if you edited the seed list.",
        "4. Once the report is clean enough, run without `--dry-run` to ingest.",
        "",
    ]

    report_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Dry-run report written to %s", report_path)


async def main():
    parser = argparse.ArgumentParser(
        description="Batch paper ingestion into LanceDB",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--seed-yaml", required=True,
        help="Path to YAML manifest file (e.g. elsa-data/paper_harvest/seed_papers.yaml)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Parse papers and write a quality report; do not touch LanceDB.",
    )
    parser.add_argument(
        "--batch-size", type=int, default=50,
        help="Max sections per store.add() call (reduce if OOM on low-VRAM).",
    )
    parser.add_argument(
        "--cache-dir",
        default=str(Path.home() / "Projects/elsa-data/paper_harvest/splitter_cache"),
        help="Directory for splitter intermediate files (default: elsa-data path).",
    )
    parser.add_argument(
        "--pdf-dir",
        default=str(Path.home() / "Projects/elsa-data/paper_harvest/pdfs"),
        help="Directory where downloaded PDFs are cached (default: elsa-data path).",
    )
    parser.add_argument(
        "--report-path",
        default=None,
        help="Where to write the dry-run report. Default: dry_run_report.md "
             "next to --seed-yaml.",
    )
    args = parser.parse_args()

    seed_path = Path(args.seed_yaml).expanduser().resolve()
    cache_dir = Path(args.cache_dir).expanduser().resolve()
    pdf_dir = Path(args.pdf_dir).expanduser().resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    pdf_dir.mkdir(parents=True, exist_ok=True)

    if args.report_path:
        report_path = Path(args.report_path).expanduser().resolve()
    else:
        report_path = seed_path.parent / "dry_run_report.md"

    papers = load_manifest(seed_path)
    logger.info("Loaded manifest: %d paper(s) from %s", len(papers), seed_path)
    logger.info("Cache dir: %s", cache_dir)
    logger.info("PDF dir:   %s", pdf_dir)

    splitter = PaperSplitter()

    store: LanceDBStore | None = None
    if not args.dry_run:
        store = LanceDBStore()
        await store.connect()
        await store.ensure_table(TABLE_NAME)
        logger.info("LanceDB ready, table '%s' ensured", TABLE_NAME)

    # Process papers (skip those marked `skip: true` in YAML)
    results = []
    skipped_in_yaml = 0
    for i, entry in enumerate(papers):
        source = resolve_source(entry)

        if entry.get("skip"):
            logger.info("SKIP (yaml flag): %s", source)
            skipped_in_yaml += 1
            continue

        try:
            status = await ingest_paper(
                splitter, store, entry,
                dry_run=args.dry_run, batch_size=args.batch_size,
            )
            results.append(status)
        except SourceUnavailable as exc:
            logger.error("SKIP %s | source unavailable: %s", source, exc)
            results.append({
                "source": source,
                "title": entry.get("title", ""),
                "tier": entry.get("tier", ""),
                "domain": entry.get("domain", ""),
                "method": "?",
                "status": "error",
                "error": str(exc),
                "analysis": {"flags": ["UNAVAILABLE"]},
            })
        except Exception as exc:
            logger.error("SKIP %s | unexpected error: %s", source, exc, exc_info=True)
            results.append({
                "source": source,
                "title": entry.get("title", ""),
                "tier": entry.get("tier", ""),
                "domain": entry.get("domain", ""),
                "method": "?",
                "status": "error",
                "error": str(exc),
                "analysis": {"flags": ["EXCEPTION"]},
            })

        # Rate limit between papers (not after last)
        if i < len(papers) - 1:
            time.sleep(ARXIV_RATE_LIMIT_SECONDS)

    # Summary
    new = sum(1 for r in results if r["status"] == "ok")
    dry = sum(1 for r in results if r["status"] == "dry_run")
    already = sum(1 for r in results if r["status"] == "already_ingested")
    failed = sum(1 for r in results if r["status"] == "error")
    logger.info("=" * 60)
    if dry > 0:
        logger.info("DONE (dry-run): %d processed | %d failed", len(results), failed)
    else:
        logger.info(
            "DONE: %d processed | %d new | %d already-in-db | %d failed | %d yaml-skipped",
            len(results), new, already, failed, skipped_in_yaml,
        )
    if failed:
        logger.warning("Failed papers:")
        for r in results:
            if r["status"] == "error":
                logger.warning("  %s: %s", r["source"], r.get("error", ""))

    if args.dry_run:
        write_dry_run_report(report_path, results)
        flagged = sum(
            1 for r in results
            if r["status"] == "dry_run" and r["analysis"].get("flags")
        )
        logger.info(
            # `dry` (line above) counts status=="dry_run"; clean = dry - flagged.
            # Was previously `ok - flagged` (introduced 4/28, NameError typo
            # caught by ruff F821 on 5/4).
            "Dry-run summary: %d clean, %d flagged, %d failed. See %s",
            dry - flagged, flagged, failed, report_path,
        )


if __name__ == "__main__":
    asyncio.run(main())

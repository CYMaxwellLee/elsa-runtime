#!/usr/bin/env python3
"""
paper_harvest.py — Batch ingestion pipeline to load papers into LanceDB.

Usage:
    python scripts/paper_harvest.py data/paper_harvest/test_batch.yaml
    python scripts/paper_harvest.py data/paper_harvest/test_batch.yaml --dry-run
    python scripts/paper_harvest.py data/paper_harvest/test_batch.yaml --batch-size 5
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

import yaml

from elsa_runtime.paper import PaperSplitter, SourceUnavailable
from elsa_runtime.storage.lancedb_store import LanceDBStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("paper_harvest")

ARXIV_RATE_LIMIT_SECONDS = 3
TABLE_NAME = "papers"


def load_manifest(path: str) -> list[dict]:
    """Load and validate a YAML manifest file."""
    with open(path) as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict) or "papers" not in data:
        raise ValueError(f"Manifest must contain a top-level 'papers' key: {path}")

    papers = data["papers"]
    for i, entry in enumerate(papers):
        if "arxiv_id" not in entry and "path" not in entry:
            raise ValueError(f"Paper #{i} must have 'arxiv_id' or 'path': {entry}")

    return papers


async def ingest_paper(
    splitter: PaperSplitter,
    store: LanceDBStore,
    entry: dict,
    dry_run: bool = False,
    batch_size: int = 50,
) -> dict:
    """Ingest a single paper. Returns a status dict."""
    source = entry.get("arxiv_id") or entry.get("path")
    title = entry.get("title", "")
    tier = entry.get("tier", "C")
    domain = entry.get("domain", "")

    logger.info("Processing: %s (%s)", source, title or "no title")

    # Split the paper
    result = splitter.split(source, title=title)

    sections = result.sections
    paper_id = result.paper_id
    logger.info(
        "  Split OK: %d sections, method=%s, warnings=%d",
        len(sections), result.method.value, len(result.warnings),
    )
    for w in result.warnings:
        logger.warning("  Warning: %s", w)

    if dry_run:
        logger.info("  [DRY RUN] Would add %d sections to '%s'", len(sections), TABLE_NAME)
        return {
            "source": source,
            "status": "dry_run",
            "sections": len(sections),
            "method": result.method.value,
        }

    # Build ids, documents, metadatas for store.add()
    ids = []
    documents = []
    metadatas = []

    for section in sections:
        doc_id = f"{paper_id}::{section.id}"
        ids.append(doc_id)
        documents.append(section.content)
        metadatas.append({
            "arxiv_id": paper_id,
            "tier": tier,
            "domain": domain,
        })

    # Add in batches
    total_added = 0
    for start in range(0, len(ids), batch_size):
        end = start + batch_size
        batch_ids = ids[start:end]
        batch_docs = documents[start:end]
        batch_metas = metadatas[start:end]

        write_results = await store.add(TABLE_NAME, batch_ids, batch_docs, batch_metas)
        total_added += len(write_results)
        logger.info("  Added batch [%d:%d] (%d docs)", start, end, len(write_results))

    return {
        "source": source,
        "status": "ok",
        "sections": len(sections),
        "added": total_added,
        "method": result.method.value,
    }


async def main():
    parser = argparse.ArgumentParser(description="Batch paper ingestion into LanceDB")
    parser.add_argument("manifest", help="Path to YAML manifest file")
    parser.add_argument("--dry-run", action="store_true", help="Parse papers but don't write to DB")
    parser.add_argument("--batch-size", type=int, default=50, help="Max sections per store.add() call (reduce if OOM on low-VRAM machines)")
    args = parser.parse_args()

    # Load manifest
    papers = load_manifest(args.manifest)
    logger.info("Loaded manifest: %d paper(s) from %s", len(papers), args.manifest)

    # Init splitter and store
    splitter = PaperSplitter()

    store = LanceDBStore()
    if not args.dry_run:
        await store.connect()
        await store.ensure_table(TABLE_NAME)
        logger.info("LanceDB ready, table '%s' ensured", TABLE_NAME)

    # Process each paper
    results = []
    for i, entry in enumerate(papers):
        source = entry.get("arxiv_id") or entry.get("path", "???")
        try:
            status = await ingest_paper(
                splitter, store, entry,
                dry_run=args.dry_run,
                batch_size=args.batch_size,
            )
            results.append(status)
        except SourceUnavailable as exc:
            logger.error("SKIP %s — source unavailable: %s", source, exc)
            results.append({"source": source, "status": "error", "error": str(exc)})
        except Exception as exc:
            logger.error("SKIP %s — unexpected error: %s", source, exc, exc_info=True)
            results.append({"source": source, "status": "error", "error": str(exc)})

        # Rate limit between papers (not after the last one)
        if i < len(papers) - 1:
            logger.info("  Sleeping %ds (arXiv rate limit)...", ARXIV_RATE_LIMIT_SECONDS)
            time.sleep(ARXIV_RATE_LIMIT_SECONDS)

    # Summary
    ok = sum(1 for r in results if r["status"] in ("ok", "dry_run"))
    failed = sum(1 for r in results if r["status"] == "error")
    logger.info("=" * 60)
    logger.info("DONE: %d/%d succeeded, %d failed", ok, len(papers), failed)
    for r in results:
        logger.info("  %s — %s", r["source"], r["status"])
    if failed:
        logger.warning("Failed papers:")
        for r in results:
            if r["status"] == "error":
                logger.warning("  %s: %s", r["source"], r.get("error", ""))


if __name__ == "__main__":
    asyncio.run(main())

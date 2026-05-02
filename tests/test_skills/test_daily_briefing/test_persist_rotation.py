"""PersistForElsaNode archive prune: keep briefings/ bounded.

Critical because Elsa's 5/2 session crash was directly caused by `ls`
of a large directory blowing up context. Same risk applies here if
briefings/ accumulates unbounded — auto-prune of files older than
PERSIST_RETAIN_DAYS caps any reflexive ls listing.

Per main user 2026-05-03: 「最多存一週或一個月，不用存太多啦」 — old
snapshots are deleted, not archived. Long-term audit lives in LanceDB
trajectory table.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from elsa_runtime.skills.daily_briefing import nodes


@pytest.fixture
def persist_dir(tmp_path, monkeypatch):
    """Redirect persist path into a fresh tmp tree."""
    flat = tmp_path / "briefings"
    flat.mkdir()
    monkeypatch.setattr(nodes, "PERSIST_ARCHIVE_DIR", flat)
    return flat


def _make_snapshot(flat_dir, name: str, age_days: int):
    """Create a fake snapshot file with mtime offset by age_days."""
    p = flat_dir / name
    p.write_text("{}", encoding="utf-8")
    aged = (datetime.now(timezone.utc) - timedelta(days=age_days)).timestamp()
    os.utime(p, (aged, aged))
    return p


def test_files_younger_than_retain_stay(persist_dir):
    _make_snapshot(persist_dir, "2026-05-01-0530.json", age_days=2)
    _make_snapshot(persist_dir, "2026-05-02-0530.json", age_days=1)

    deleted = nodes.PersistForElsaNode._prune_old_archives(
        datetime.now(timezone.utc)
    )

    assert deleted == 0
    flat_files = sorted(p.name for p in persist_dir.iterdir() if p.is_file())
    assert flat_files == ["2026-05-01-0530.json", "2026-05-02-0530.json"]


def test_files_older_than_retain_are_deleted(persist_dir):
    _make_snapshot(persist_dir, "2025-12-15-0530.json", age_days=140)
    _make_snapshot(persist_dir, "2026-01-15-0530.json", age_days=110)
    _make_snapshot(persist_dir, "2026-04-30-0530.json", age_days=2)  # stays

    deleted = nodes.PersistForElsaNode._prune_old_archives(
        datetime.now(timezone.utc)
    )

    assert deleted == 2
    remaining = sorted(p.name for p in persist_dir.iterdir() if p.is_file())
    assert remaining == ["2026-04-30-0530.json"]


def test_prune_handles_empty_dir_gracefully(persist_dir):
    deleted = nodes.PersistForElsaNode._prune_old_archives(
        datetime.now(timezone.utc)
    )
    assert deleted == 0


def test_underscore_prefixed_files_skipped(persist_dir):
    """Reserved namespace (e.g. _index.jsonl future rolling summary)."""
    p = _make_snapshot(persist_dir, "_index.json", age_days=400)
    nodes.PersistForElsaNode._prune_old_archives(datetime.now(timezone.utc))
    assert p.exists()


def test_non_json_files_skipped(persist_dir):
    """Don't accidentally delete a README or .gitkeep."""
    keep = persist_dir / "README.md"
    keep.write_text("informational")
    aged = (datetime.now(timezone.utc) - timedelta(days=400)).timestamp()
    os.utime(keep, (aged, aged))

    nodes.PersistForElsaNode._prune_old_archives(datetime.now(timezone.utc))
    assert keep.exists()


def test_subdirectories_skipped(persist_dir):
    """Don't traverse into subdirectories — they are user-managed."""
    sub = persist_dir / "manual_archive"
    sub.mkdir()
    nodes.PersistForElsaNode._prune_old_archives(datetime.now(timezone.utc))
    assert sub.exists()


def test_custom_retain_days(persist_dir):
    """Caller can override retain window for testing or stricter ops."""
    _make_snapshot(persist_dir, "2026-04-25-0530.json", age_days=8)
    _make_snapshot(persist_dir, "2026-04-30-0530.json", age_days=3)

    deleted = nodes.PersistForElsaNode._prune_old_archives(
        datetime.now(timezone.utc), retain_days=7
    )
    assert deleted == 1
    remaining = sorted(p.name for p in persist_dir.iterdir() if p.is_file())
    assert remaining == ["2026-04-30-0530.json"]

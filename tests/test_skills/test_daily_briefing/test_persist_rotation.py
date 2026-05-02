"""PersistForElsaNode archive rotation: keep briefings/ bounded.

Critical because Elsa's 5/2 session crash was directly caused by `ls`
of a large directory blowing up context. Same risk applies here if
briefings/ accumulates unbounded — auto-rotate to _archive/<year>/
caps any reflexive ls listing.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

from elsa_runtime.skills.daily_briefing import nodes


@pytest.fixture
def persist_dirs(tmp_path, monkeypatch):
    """Redirect persist paths into a fresh tmp tree."""
    flat = tmp_path / "briefings"
    flat.mkdir()
    archive = flat / "_archive"
    monkeypatch.setattr(nodes, "PERSIST_ARCHIVE_DIR", flat)
    monkeypatch.setattr(nodes, "PERSIST_ARCHIVE_OLD_DIR", archive)
    return flat, archive


def _make_snapshot(flat_dir, name: str, age_days: int):
    """Create a fake snapshot file with mtime offset by age_days."""
    p = flat_dir / name
    p.write_text("{}", encoding="utf-8")
    mtime = (datetime.now(timezone.utc) - timedelta(days=age_days)).timestamp()
    os.utime(p, (mtime, mtime))
    return p


def test_files_younger_than_retain_stay_in_flat(persist_dirs):
    flat, archive = persist_dirs
    _make_snapshot(flat, "2026-05-01-0530.json", age_days=2)
    _make_snapshot(flat, "2026-05-02-0530.json", age_days=1)

    nodes.PersistForElsaNode._rotate_old_archives(datetime.now(timezone.utc))

    flat_files = sorted(p.name for p in flat.iterdir() if p.is_file())
    assert flat_files == ["2026-05-01-0530.json", "2026-05-02-0530.json"]
    assert not archive.exists() or not any(archive.rglob("*.json"))


def test_files_older_than_retain_move_to_archive(persist_dirs):
    flat, archive = persist_dirs
    _make_snapshot(flat, "2025-12-15-0530.json", age_days=140)
    _make_snapshot(flat, "2026-01-15-0530.json", age_days=110)
    _make_snapshot(flat, "2026-04-30-0530.json", age_days=2)  # stays

    nodes.PersistForElsaNode._rotate_old_archives(datetime.now(timezone.utc))

    flat_remaining = sorted(p.name for p in flat.iterdir() if p.is_file())
    assert flat_remaining == ["2026-04-30-0530.json"]

    moved_2025 = sorted(p.name for p in (archive / "2025").iterdir())
    moved_2026 = sorted(p.name for p in (archive / "2026").iterdir())
    assert moved_2025 == ["2025-12-15-0530.json"]
    assert moved_2026 == ["2026-01-15-0530.json"]


def test_archive_subdirectory_itself_is_skipped(persist_dirs):
    """_archive must never recurse into itself for moves."""
    flat, archive = persist_dirs
    archive.mkdir(parents=True)
    (archive / "2025").mkdir()
    pre_existing = archive / "2025" / "2025-01-01-0530.json"
    pre_existing.write_text("{}")

    _make_snapshot(flat, "2026-04-30-0530.json", age_days=2)

    nodes.PersistForElsaNode._rotate_old_archives(datetime.now(timezone.utc))

    # Pre-existing archive untouched, recent flat file stays.
    assert pre_existing.exists()
    assert (flat / "2026-04-30-0530.json").exists()


def test_rotation_handles_empty_dir_gracefully(persist_dirs):
    nodes.PersistForElsaNode._rotate_old_archives(datetime.now(timezone.utc))
    # No exception = pass.


def test_underscore_prefixed_files_skipped(persist_dirs):
    flat, _archive = persist_dirs
    p = _make_snapshot(flat, "_index.json", age_days=400)  # very old
    nodes.PersistForElsaNode._rotate_old_archives(datetime.now(timezone.utc))
    # Underscore-prefixed names are reserved; never moved.
    assert p.exists()


def test_collision_is_renamed_not_clobbered(persist_dirs):
    flat, archive = persist_dirs
    archive.mkdir(parents=True, exist_ok=True)
    (archive / "2026").mkdir()
    existing = archive / "2026" / "2026-01-15-0530.json"
    existing.write_text('{"original": true}')

    new_old = flat / "2026-01-15-0530.json"
    new_old.write_text('{"newer_dup": true}')
    aged = (datetime.now(timezone.utc) - timedelta(days=110)).timestamp()
    os.utime(new_old, (aged, aged))

    nodes.PersistForElsaNode._rotate_old_archives(datetime.now(timezone.utc))

    # Original archive entry preserved
    assert existing.read_text() == '{"original": true}'
    # New file rotated under a -dup suffix
    dups = list((archive / "2026").glob("2026-01-15-0530-dup*.json"))
    assert len(dups) == 1

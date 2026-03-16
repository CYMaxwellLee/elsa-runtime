"""Tests for collection definitions + write safety."""

from elsa_runtime.storage.collections import (
    ALL_COLLECTIONS, COLLECTION_MAP,
    check_content_safety, validate_write, TASKS,
)


def test_all_collections_count():
    """Should have exactly 11 collections."""
    assert len(ALL_COLLECTIONS) == 11


def test_collection_map_keys():
    """Collection map should contain all expected names."""
    expected = {
        "tasks", "errors", "knowledge", "conversations", "solutions",
        "papers", "insights", "procedures",
        "project_digests", "theory_notes", "tool_docs",
    }
    assert set(COLLECTION_MAP.keys()) == expected


def test_all_collections_have_required_metadata():
    """Every collection should have at least 'type' in required_metadata."""
    for spec in ALL_COLLECTIONS:
        assert "type" in spec.required_metadata, f"{spec.name} missing 'type'"


def test_collection_names_unique():
    """Collection names must be unique."""
    names = [c.name for c in ALL_COLLECTIONS]
    assert len(names) == len(set(names))


# --- Write safety ---

def test_content_safety_blocks_api_keys():
    """Should detect API keys in content."""
    assert check_content_safety("my key is sk-abc1234567890abcdef")
    assert check_content_safety("token: ghp_abcdefghij1234567890")


def test_content_safety_blocks_private_keys():
    """Should detect private keys."""
    assert check_content_safety("-----BEGIN RSA PRIVATE KEY-----")


def test_content_safety_allows_normal_text():
    """Normal text should pass safety check."""
    assert not check_content_safety("Analyze this paper's methodology")
    assert not check_content_safety("The model achieved 95% accuracy")


def test_validate_write_missing_metadata():
    """Should catch missing required metadata."""
    errors = validate_write(TASKS, "some task", {"type": "task"})
    assert any("Missing required metadata" in e for e in errors)


def test_validate_write_passes():
    """Should pass with complete metadata and safe content."""
    meta = {"type": "task", "agent": "elsa", "title": "test", "verified": True}
    errors = validate_write(TASKS, "completed the task successfully", meta)
    assert errors == []

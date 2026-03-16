"""Collection definitions — Multi-Collection Design.

11 collections + write safety:
- required_metadata enforces each record carries agent/type for scoped queries
- forbidden_content blocks credentials from being written
- deprecation strategy: no deletion, mark deprecated + superseded_by
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CollectionSpec:
    """Specification for a single ChromaDB collection."""

    name: str
    description: str
    required_metadata: list[str] = field(default_factory=list)
    optional_metadata: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Write safety
# ---------------------------------------------------------------------------

_FORBIDDEN_PATTERNS = [
    re.compile(r"(?:sk-|AKIA|ghp_|gho_|xoxb-|xoxp-)\S{10,}", re.IGNORECASE),
    re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"),
    re.compile(r"password\s*[:=]\s*\S+", re.IGNORECASE),
]


def check_content_safety(text: str) -> list[str]:
    """Check if text contains forbidden content (credentials, keys, etc.).

    Returns list of violation descriptions. Empty = safe.
    """
    violations: list[str] = []
    for pattern in _FORBIDDEN_PATTERNS:
        if pattern.search(text):
            violations.append(f"Matched forbidden pattern: {pattern.pattern[:40]}...")
    return violations


def validate_write(
    spec: CollectionSpec,
    document: str,
    metadata: dict[str, Any],
) -> list[str]:
    """Full write validation: metadata completeness + content safety.

    Returns list of error messages. Empty = valid.
    """
    errors: list[str] = []

    # Check required metadata
    missing = [f for f in spec.required_metadata if f not in metadata]
    if missing:
        errors.append(f"Missing required metadata: {missing}")

    # Check content safety
    errors.extend(check_content_safety(document))
    for key, val in metadata.items():
        if isinstance(val, str):
            errors.extend(check_content_safety(val))

    return errors


# ---------------------------------------------------------------------------
# Working Memory collections
# ---------------------------------------------------------------------------

TASKS = CollectionSpec(
    name="tasks",
    description="Task memory - execution records for each task",
    required_metadata=["type", "agent", "title", "verified"],
    optional_metadata=[
        "body", "transferable", "body_specific",
        "request", "approach", "rounds",
        "issues_encountered", "lessons",
        "created_at", "deprecated", "superseded_by",
    ],
)

ERRORS = CollectionSpec(
    name="errors",
    description="Error memory - resolved errors and their fixes",
    required_metadata=["type", "agent", "error_type", "resolved", "verified"],
    optional_metadata=[
        "symptom", "root_cause", "fix",
        "created_at", "deprecated", "superseded_by",
    ],
)

KNOWLEDGE = CollectionSpec(
    name="knowledge",
    description="Knowledge memory - user preferences, domain facts",
    required_metadata=["type", "category", "source", "verified"],
    optional_metadata=[
        "content", "created_at", "deprecated", "superseded_by",
    ],
)

CONVERSATIONS = CollectionSpec(
    name="conversations",
    description="Conversation summaries - auto-generated at conversation end",
    required_metadata=["type", "agent"],
    optional_metadata=["summary", "key_topics", "date", "created_at"],
)

SOLUTIONS = CollectionSpec(
    name="solutions",
    description="Solution templates - successful completion AND zero user corrections",
    required_metadata=["type", "agent", "verified"],
    optional_metadata=[
        "zero_corrections", "template",
        "created_at", "deprecated", "superseded_by",
    ],
)

PAPERS = CollectionSpec(
    name="papers",
    description="Paper notes - Rei agent dedicated",
    required_metadata=["type", "agent", "title", "tier"],
    optional_metadata=[
        "arxiv_id", "summary", "key_contributions",
        "relevance_to_lab", "relevance_score",
        "related_project", "tags", "similar_papers",
        "source_url", "source_type", "retrieved_date",
        "created_at", "deprecated", "superseded_by",
    ],
)

INSIGHTS = CollectionSpec(
    name="insights",
    description="Experience distillation - transferable judgment",
    required_metadata=["type", "agent", "domain", "task_type", "confidence"],
    optional_metadata=[
        "context", "content", "scope", "lifecycle",
        "times_referenced", "times_adopted",
        "derived_from_task", "supersedes", "superseded_by",
        "created_at", "deprecated",
    ],
)

PROCEDURES = CollectionSpec(
    name="procedures",
    description="Operational procedures - reusable workflows",
    required_metadata=["type", "agent", "domain", "task_type", "title", "verified"],
    optional_metadata=[
        "steps", "prerequisites", "gotchas",
        "times_used", "created_at", "derived_from_task",
        "deprecated", "superseded_by",
    ],
)

# ---------------------------------------------------------------------------
# Knowledge Layer collections
# ---------------------------------------------------------------------------

PROJECT_DIGESTS = CollectionSpec(
    name="project_digests",
    description="Project digest chunks - Knowledge Layer vector layer",
    required_metadata=["type", "project_name"],
    optional_metadata=["summary", "status", "last_updated", "created_at"],
)

THEORY_NOTES = CollectionSpec(
    name="theory_notes",
    description="Theory digest - Rei agent dedicated write",
    required_metadata=["type", "agent", "topic"],
    optional_metadata=["content", "related_papers", "last_updated", "created_at"],
)

TOOL_DOCS = CollectionSpec(
    name="tool_docs",
    description="Tool documentation chunks",
    required_metadata=["type", "tool_name"],
    optional_metadata=["version", "usage", "last_updated", "created_at"],
)

# ---------------------------------------------------------------------------
# All collections registry
# ---------------------------------------------------------------------------

ALL_COLLECTIONS: list[CollectionSpec] = [
    TASKS, ERRORS, KNOWLEDGE, CONVERSATIONS, SOLUTIONS,
    PAPERS, INSIGHTS, PROCEDURES,
    PROJECT_DIGESTS, THEORY_NOTES, TOOL_DOCS,
]

COLLECTION_MAP: dict[str, CollectionSpec] = {c.name: c for c in ALL_COLLECTIONS}

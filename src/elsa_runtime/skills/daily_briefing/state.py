"""DailyBriefing state schema (Pydantic).

Per elsa-system C29 §4.2 + PATCH-v3.51-A §4.2.

The state container flows through every node in the StateGraph. Each
node reads named fields (Node.inputs) and writes named fields
(Node.outputs); ApplyFilterNode is the architectural-critical gate
between candidates and the briefing.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, field_validator


# ─── reference primitives ───


class EmailRef(BaseModel):
    thread_id: str
    sender: str = ""
    subject: str = ""
    last_msg_at: str = ""
    snippet: str = ""
    starred: bool = False


class CalendarRef(BaseModel):
    event_id: str
    title: str = ""
    start: str = ""
    end: str = ""
    location: str = ""


class DriveRef(BaseModel):
    file_id: str
    name: str = ""
    mime_type: str = ""
    modified_at: str = ""


# ─── candidate items + filter rules ───


EvidenceType = Literal[
    "gmail_thread", "calendar_event", "drive_doc", "web_url"
]
DiscoveredVia = Literal[
    "gmail_index",
    "calendar_index",
    "drive_index",
    "gmail_deep_scan",
    "calendar_verifier",
    "web_verifier",
    "risk_hunter",
]


class CandidateItem(BaseModel):
    """A potential briefing entry. evidence_id is mandatory by schema."""

    topic: str
    summary: str
    evidence_type: EvidenceType
    evidence_id: str  # Pydantic enforces non-empty via _evidence_id_set below
    discovered_via: DiscoveredVia
    suggested_action: str = ""

    @field_validator("evidence_id")
    @classmethod
    def _evidence_id_set(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("evidence_id is required and non-empty")
        return v


class SurfaceRule(BaseModel):
    """One rule from insight_query, decides surface vs drop."""

    insight_id: str
    pattern: str
    action: Literal["drop", "keep", "flag"]
    reason: str = ""


class FilteredItem(CandidateItem):
    keep_reason: str = "no matching drop rule"


class RejectedItem(CandidateItem):
    drop_reason: str
    matched_rule: str  # insight_id


# ─── briefing output ───


class BriefingSection(BaseModel):
    title: str
    items: list[FilteredItem] = []


class FinalBriefing(BaseModel):
    sections: list[BriefingSection] = []
    closing_note: str = ""
    total_count: int = 0
    rejected_count: int = 0

    @field_validator("sections")
    @classmethod
    def _all_items_have_evidence(cls, v: list[BriefingSection]):
        for section in v:
            for item in section.items:
                if not item.evidence_id:
                    raise ValueError(
                        f"section '{section.title}' item '{item.topic}' "
                        "missing evidence_id"
                    )
        return v


# ─── master state container ───


class BriefingState(BaseModel):
    """Shared state. Nodes read/write named fields."""

    model_config = {"arbitrary_types_allowed": True}

    trigger_time: str = ""
    dry_run: bool = False

    # Stage 1: indices
    raw_emails: list[EmailRef] = []
    raw_events: list[CalendarRef] = []
    raw_drive_docs: list[DriveRef] = []

    # Stage 2: workers append candidates
    candidate_items: list[CandidateItem] = []

    # Stage 3-4: insight rules + filtered output
    surface_rules: list[SurfaceRule] = []
    filtered_items: list[FilteredItem] = []
    rejected_items: list[RejectedItem] = []

    # Stage 5: composed briefing
    final_briefing: FinalBriefing | None = None
    briefing_text: str = ""  # rendered telegram-ready string

    # Stage 6: send + persist
    persisted_path: str = ""
    sent: bool = False

    # Errors per node (non-fatal; final state captures partial failures)
    errors: list[str] = []

    # Router scratch (unused in 10-node linear-with-fanout, kept for
    # forward compatibility per Module framework convention).
    _next_node: str | None = None

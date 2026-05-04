"""DailyBriefing nodes.

10 architectural nodes (per C29 §4.3) plus PersistForElsaNode (writes
final briefing snapshot for Elsa's 24/7 session to read on next turn).

Stages:
  1. Index gathering (parallel, narrow LLM):
     GmailIndexNode, CalendarIndexNode, DriveIndexNode
  2. Worker fan-out (parallel, fuller LLM):
     GmailDeepScanWorker, CalendarVerifierWorker,
     WebVerifierWorker, RiskHunterWorker
  3. Insights query (LLM via subprocess):
     QueryInsightsNode
  4. Filter (deterministic, hack #9 retire):
     ApplyFilterNode
  5. Compose (LLM, Elsa's voice + EvidenceAttachedVerifier):
     ComposeBriefingNode
  6. Persist for Elsa (deterministic):
     PersistForElsaNode
  7. Send (terminal, forced MCP, hack #4 retire):
     SendBriefingNode
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from elsa_runtime.module import (
    BusinessRuleVerifier,
    DeterministicNode,
    LLMNode,
    NodeExecutionError,
    Signature,
    TerminalNode,
    Verdict,
    Verifier,
)

from .claude_worker import ClaudeWorkerError, call_claude
from .state import (
    BriefingState,
    BriefingSection,
    CalendarRef,
    CandidateItem,
    DriveRef,
    EmailRef,
    FilteredItem,
    FinalBriefing,
    RejectedItem,
    SurfaceRule,
)


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _schema_of(model: type[BaseModel]) -> dict:
    """Return JSON Schema dict for a Pydantic model (used with claude --json-schema)."""
    return model.model_json_schema()


def _schema_block(model: type[BaseModel]) -> str:
    """Render the JSON Schema as a fenced block for inclusion in prompts.

    Compensates for claude CLI 2.1.126 --json-schema flag returning
    empty output when combined with --allowedTools; we include the
    schema in the prompt instead.
    """
    return "```json\n" + json.dumps(
        model.model_json_schema(), indent=2, ensure_ascii=False
    ) + "\n```"


# LLMs commonly emit truncated literals; map them back before Pydantic
# validation runs. Applied in worker _merge_state overrides.
_EVIDENCE_TYPE_NORMALIZE = {
    "thread": "gmail_thread",
    "gmail": "gmail_thread",
    "event": "calendar_event",
    "calendar": "calendar_event",
    "drive": "drive_doc",
    "doc": "drive_doc",
    "url": "web_url",
    "web": "web_url",
}


def _normalize_candidate_dict(c: dict) -> dict:
    """Tolerant pre-processor: fix common LLM-emitted shape mistakes.

    - Coerce evidence_type short forms ("thread" -> "gmail_thread")
    - Backfill topic/summary from common alt names if present
    """
    if not isinstance(c, dict):
        return c
    out = dict(c)
    et = out.get("evidence_type", "")
    if isinstance(et, str) and et in _EVIDENCE_TYPE_NORMALIZE:
        out["evidence_type"] = _EVIDENCE_TYPE_NORMALIZE[et]
    # alt field names sometimes used by LLMs
    if "topic" not in out:
        for alt in ("title", "subject", "name", "headline"):
            if alt in out and out[alt]:
                out["topic"] = out[alt]
                break
    if "summary" not in out:
        for alt in ("description", "details", "body", "context", "summary_text"):
            if alt in out and out[alt]:
                out["summary"] = out[alt]
                break
    if "evidence_id" not in out:
        for alt in ("id", "thread_id", "event_id", "file_id", "url"):
            if alt in out and out[alt]:
                out["evidence_id"] = str(out[alt])
                break
    if "discovered_via" not in out:
        # leave as-is; Pydantic will still reject (this is a worker bug)
        pass
    return out


def _normalize_worker_output(payload: Any) -> Any:
    """Apply candidate normalization to a _WorkerOutput-shaped dict."""
    if not isinstance(payload, dict):
        return payload
    out = dict(payload)
    cands = out.get("candidates")
    if isinstance(cands, list):
        out["candidates"] = [_normalize_candidate_dict(c) for c in cands]
    return out


# Read-only MCP tool whitelist for data-gathering workers. Tool names
# resolved against actual MCP servers configured in elsa-workspace
# settings (see ~/Projects/elsa-workspace/.claude/settings.json) and
# user-level (~/.claude/settings.json).
MCP_GMAIL = [
    "mcp__claude_ai_Gmail__search_threads",
    "mcp__claude_ai_Gmail__get_thread",
    "mcp__claude_ai_Gmail__gmail_search_messages",
    "mcp__claude_ai_Gmail__gmail_read_message",
    "mcp__claude_ai_Gmail__gmail_read_thread",
    "mcp__elsa-knowledge__gmail_list_attachments",
]
MCP_CALENDAR = [
    "mcp__claude_ai_Google_Calendar__list_events",
    "mcp__claude_ai_Google_Calendar__get_event",
    "mcp__claude_ai_Google_Calendar__gcal_list_events",
    "mcp__claude_ai_Google_Calendar__gcal_get_event",
]
MCP_DRIVE = [
    "mcp__elsa-knowledge__gdrive_search",
    "mcp__elsa-knowledge__gdoc_read",
    "mcp__elsa-knowledge__gdoc_read_universal",
]
MCP_WEB = ["WebSearch", "WebFetch"]
MCP_INSIGHT = [
    "mcp__elsa-knowledge__insight_query",
    "mcp__elsa-knowledge__knowledge_search",
    "mcp__elsa-knowledge__list_recent_insights",
]
MCP_TELEGRAM_SEND = ["mcp__plugin_telegram_telegram__reply"]


# ──────────────────────────────────────────────────────────────────
# Stage 1: Index gathering (narrow LLM workers)
# ──────────────────────────────────────────────────────────────────


class _IndexInput(BaseModel):
    trigger_time: str


class _GmailIndexOutput(BaseModel):
    threads: list[EmailRef]


class _CalendarIndexOutput(BaseModel):
    events: list[CalendarRef]


class _DriveIndexOutput(BaseModel):
    docs: list[DriveRef]


class _GmailIndexSig(Signature):
    description = "Index gmail threads for briefing (last 7 days + starred 30 days)"
    input_schema = _IndexInput
    output_schema = _GmailIndexOutput


class _CalendarIndexSig(Signature):
    description = "Index calendar events for next 14 days"
    input_schema = _IndexInput
    output_schema = _CalendarIndexOutput


class _DriveIndexSig(Signature):
    description = "Index recently shared Drive docs (last 7 days)"
    input_schema = _IndexInput
    output_schema = _DriveIndexOutput


class GmailIndexNode(LLMNode[BriefingState]):
    name = "gmail_index"
    inputs = ["trigger_time"]
    outputs = ["raw_emails"]
    signature = _GmailIndexSig
    max_retries = 2

    def _call_llm(self, inputs, error_context=None):
        prompt = f"""You are a Gmail index worker for Elsa's daily briefing pipeline.

TASK: list candidate threads for review. NO summarization, NO judgement.

Required tool calls (run all three, dedupe by thread_id):
1. search_threads or gmail_search_messages with query "in:inbox newer_than:7d"
2. search_threads or gmail_search_messages with query "is:starred newer_than:30d"
3. search_threads or gmail_search_messages with query "is:important newer_than:14d -in:sent"

OUTPUT FORMAT (strict): a single JSON object matching the schema below.
NO prose, NO explanation, NO markdown fence. Just the JSON object.

Required schema:
{_schema_block(_GmailIndexOutput)}

For each thread the schema demands these EXACT field names:
- thread_id (string, required, non-empty)
- sender, subject, last_msg_at (strings; ISO/RFC if available else "")
- snippet (string <= 240 chars)
- starred (bool)

DO NOT use alternate field names. DO NOT skip required fields.

Trigger time: {inputs.trigger_time}
"""
        if error_context:
            prompt += f"\n\nPrevious attempt failed schema validation:\n{error_context}\nFix the field names/types and retry."
        return call_claude(
            prompt,
            json_schema=_schema_of(_GmailIndexOutput),
            allowed_tools=MCP_GMAIL,
            timeout=300,
        )

    def _merge_state(self, state, output):
        state.raw_emails = list(output.threads)
        return state


class CalendarIndexNode(LLMNode[BriefingState]):
    name = "calendar_index"
    inputs = ["trigger_time"]
    outputs = ["raw_events"]
    signature = _CalendarIndexSig
    max_retries = 2

    def _call_llm(self, inputs, error_context=None):
        prompt = f"""You are a Calendar index worker for Elsa's daily briefing pipeline.

TASK: list events for the next 14 days. NO summarization, NO judgement.

Required tool call:
- list_events from {inputs.trigger_time} to +14 days

OUTPUT FORMAT (strict): a single JSON object matching the schema below.
NO prose, NO explanation, NO markdown fence.

Required schema:
{_schema_block(_CalendarIndexOutput)}

For each event the schema demands these EXACT field names:
- event_id (string, required, non-empty)
- title, start, end, location (strings; "" if missing)
"""
        if error_context:
            prompt += f"\n\nPrevious attempt failed schema validation:\n{error_context}\nFix the field names/types and retry."
        return call_claude(
            prompt,
            json_schema=_schema_of(_CalendarIndexOutput),
            allowed_tools=MCP_CALENDAR,
            timeout=180,
        )

    def _merge_state(self, state, output):
        state.raw_events = list(output.events)
        return state


class DriveIndexNode(LLMNode[BriefingState]):
    name = "drive_index"
    inputs = ["trigger_time"]
    outputs = ["raw_drive_docs"]
    signature = _DriveIndexSig
    max_retries = 2

    def _call_llm(self, inputs, error_context=None):
        prompt = f"""You are a Drive index worker for Elsa's daily briefing pipeline.

TASK: list recently shared Drive docs (last 7 days). If Drive search MCP
unavailable, return empty docs list with no error.

For each doc return:
- file_id (mandatory)
- name, mime_type, modified_at (if any)

Return JSON matching the output schema. No prose outside JSON.
"""
        if error_context:
            prompt += f"\n\nPrevious attempt failed: {error_context}\nTry again."
        try:
            return call_claude(
                prompt,
                json_schema=_schema_of(_DriveIndexOutput),
                allowed_tools=MCP_DRIVE,
                timeout=180,
            )
        except ClaudeWorkerError:
            # Drive integration optional in v3.51-A; degrade gracefully.
            return {"docs": []}

    def _merge_state(self, state, output):
        state.raw_drive_docs = list(output.docs)
        return state


# ──────────────────────────────────────────────────────────────────
# Stage 2: 4-worker fan-out
# ──────────────────────────────────────────────────────────────────


class _WorkerInput(BaseModel):
    raw_emails: list[EmailRef] = []
    raw_events: list[CalendarRef] = []
    raw_drive_docs: list[DriveRef] = []
    candidate_items: list[CandidateItem] = []


class _WorkerOutput(BaseModel):
    candidates: list[CandidateItem]


class _GmailDeepScanSig(Signature):
    description = "Identify pending action threads from gmail index"
    input_schema = _WorkerInput
    output_schema = _WorkerOutput


class _CalendarVerifierSig(Signature):
    description = "Cross-check calendar events vs gmail content"
    input_schema = _WorkerInput
    output_schema = _WorkerOutput


class _WebVerifierSig(Signature):
    description = "Web-verify deadlines and announcements"
    input_schema = _WorkerInput
    output_schema = _WorkerOutput


class _RiskHunterSig(Signature):
    description = "Surface things the user may have forgotten"
    input_schema = _WorkerInput
    output_schema = _WorkerOutput


def _appended_state(state: BriefingState, output: _WorkerOutput) -> BriefingState:
    """Append worker candidates to state.candidate_items (workers do NOT overwrite)."""
    existing = list(state.candidate_items)
    existing.extend(output.candidates)
    state.candidate_items = existing
    return state


def _format_email_list(emails: list[EmailRef]) -> str:
    if not emails:
        return "(none)"
    out = []
    for e in emails[:50]:  # cap context size
        out.append(
            f"- thread={e.thread_id} from={e.sender} | {e.subject} | "
            f"starred={e.starred} | last={e.last_msg_at}\n  snippet: {e.snippet[:160]}"
        )
    return "\n".join(out)


def _format_event_list(events: list[CalendarRef]) -> str:
    if not events:
        return "(none)"
    out = []
    for ev in events[:50]:
        out.append(
            f"- event={ev.event_id} | {ev.title} | {ev.start} - {ev.end} | "
            f"loc={ev.location}"
        )
    return "\n".join(out)


def _format_candidate_list(items: list[CandidateItem]) -> str:
    if not items:
        return "(none)"
    out = []
    for c in items[:80]:
        out.append(
            f"- topic={c.topic} | via={c.discovered_via} | "
            f"evidence={c.evidence_type}:{c.evidence_id}\n  summary: {c.summary[:160]}"
        )
    return "\n".join(out)


_WORKER_FIELD_RULES = """STRICT field rules per candidate (Pydantic will reject otherwise):
- topic: string, required, <= 60 chars (use this exact field name; NOT "title")
- summary: string, required, <= 240 chars (NOT "description")
- evidence_type: ONE of these literal strings exactly:
    "gmail_thread" | "calendar_event" | "drive_doc" | "web_url"
  (NOT short forms like "thread" / "event" / "drive" / "web")
- evidence_id: string, required, non-empty (the actual thread_id /
  event_id / file_id / URL — not a description)
- discovered_via: ONE of:
    "gmail_index" | "calendar_index" | "drive_index"
    | "gmail_deep_scan" | "calendar_verifier" | "web_verifier" | "risk_hunter"
- suggested_action: short string, optional (default "")
"""


class GmailDeepScanWorker(LLMNode[BriefingState]):
    name = "worker_gmail_deep_scan"
    inputs = ["raw_emails"]
    outputs = ["candidate_items"]
    signature = _GmailDeepScanSig
    max_retries = 3

    def _call_llm(self, inputs, error_context=None):
        prompt = f"""You are Elsa's Gmail Deep Scan worker for the daily briefing.

Given indexed gmail threads, identify pending action items the user must
respond to today.

Threads:
{_format_email_list(inputs.raw_emails)}

OUTPUT FORMAT (strict): single JSON object matching the schema below.
NO prose, NO markdown fence.

Required schema:
{_schema_block(_WorkerOutput)}

{_WORKER_FIELD_RULES}

Rules:
- Every candidate MUST have evidence_type="gmail_thread" and evidence_id=<thread_id from indexed list>
- discovered_via="gmail_deep_scan"
- DO NOT invent thread_ids. Only use thread_ids from the indexed list above.
- Skip already-replied / closed threads.
"""
        if error_context:
            prompt += f"\n\nPrevious attempt failed schema validation:\n{error_context}\nFix the EXACT field names + literal values and retry."
        return _normalize_worker_output(
            call_claude(
                prompt,
                json_schema=_schema_of(_WorkerOutput),
                allowed_tools=MCP_GMAIL,
                timeout=420,
            )
        )

    def _merge_state(self, state, output):
        return _appended_state(state, output)


class CalendarVerifierWorker(LLMNode[BriefingState]):
    name = "worker_calendar_verifier"
    inputs = ["raw_events", "raw_emails"]
    outputs = ["candidate_items"]
    signature = _CalendarVerifierSig
    max_retries = 3

    def _call_llm(self, inputs, error_context=None):
        prompt = f"""You are Elsa's Calendar Verifier worker for the daily briefing.

Calendar events (next 14 days) + gmail index for cross-checking
reschedules / cancels.

Events:
{_format_event_list(inputs.raw_events)}

Gmail (for cross-check):
{_format_email_list(inputs.raw_emails)}

OUTPUT FORMAT (strict): single JSON object matching the schema below.
NO prose, NO markdown fence.

Required schema:
{_schema_block(_WorkerOutput)}

{_WORKER_FIELD_RULES}

Rules:
- Each candidate MUST have evidence_type="calendar_event" + evidence_id=<event_id from list above>
- discovered_via="calendar_verifier"
- DO NOT invent event_ids.
"""
        if error_context:
            prompt += f"\n\nPrevious attempt failed schema validation:\n{error_context}\nFix the EXACT field names + literal values and retry."
        return _normalize_worker_output(
            call_claude(
                prompt,
                json_schema=_schema_of(_WorkerOutput),
                allowed_tools=MCP_CALENDAR + MCP_GMAIL,
                timeout=420,
            )
        )

    def _merge_state(self, state, output):
        return _appended_state(state, output)


class WebVerifierWorker(LLMNode[BriefingState]):
    name = "worker_web_verifier"
    inputs = ["candidate_items", "raw_emails", "raw_events"]
    outputs = ["candidate_items"]
    signature = _WebVerifierSig
    max_retries = 3

    def _call_llm(self, inputs, error_context=None):
        prompt = f"""You are Elsa's Web Verifier worker for the daily briefing.

Find conferences / competitions / grant deadlines among existing
candidates and verify dates against the organiser's official site.

Existing candidates:
{_format_candidate_list(inputs.candidate_items)}

OUTPUT FORMAT (strict): single JSON object matching the schema below.
NO prose, NO markdown fence.

Required schema:
{_schema_block(_WorkerOutput)}

{_WORKER_FIELD_RULES}

Rules:
- Only return NEW candidates (typically discrepancies you find via web).
- evidence_type="web_url", evidence_id=<the actual URL>
- discovered_via="web_verifier"
- If nothing needs verification, return {{"candidates": []}}.
- DO NOT fabricate URLs.
"""
        if error_context:
            prompt += f"\n\nPrevious attempt failed schema validation:\n{error_context}\nFix the EXACT field names + literal values and retry."
        return _normalize_worker_output(
            call_claude(
                prompt,
                json_schema=_schema_of(_WorkerOutput),
                allowed_tools=MCP_WEB,
                timeout=420,
            )
        )

    def _merge_state(self, state, output):
        return _appended_state(state, output)


class RiskHunterWorker(LLMNode[BriefingState]):
    name = "worker_risk_hunter"
    inputs = ["raw_emails", "raw_events"]
    outputs = ["candidate_items"]
    signature = _RiskHunterSig
    max_retries = 3

    def _call_llm(self, inputs, error_context=None):
        prompt = f"""You are Elsa's Risk Hunter worker for the daily briefing.

Surface risks the user may have forgotten or missed. Focus areas:
1. Events in the next 7 days not yet RSVP'd / mentioned
2. Starred / important threads >14 days unanswered
3. Approaching deadlines (next 14 days) without a recent action thread
4. Important senders silent for 60+ days who used to be active

Emails:
{_format_email_list(inputs.raw_emails)}

Events:
{_format_event_list(inputs.raw_events)}

OUTPUT FORMAT (strict): single JSON object matching the schema below.
NO prose, NO markdown fence.

Required schema:
{_schema_block(_WorkerOutput)}

{_WORKER_FIELD_RULES}

Rules:
- Each risk MUST have evidence_type + evidence_id (the actual thread_id
  or event_id from the lists above; NOT a description).
- discovered_via="risk_hunter"
- suggested_action: concrete next step.
- No vague warnings.
"""
        if error_context:
            prompt += f"\n\nPrevious attempt failed schema validation:\n{error_context}\nFix the EXACT field names + literal values and retry."
        return _normalize_worker_output(
            call_claude(
                prompt,
                json_schema=_schema_of(_WorkerOutput),
                allowed_tools=MCP_GMAIL + MCP_CALENDAR,
                timeout=420,
            )
        )

    def _merge_state(self, state, output):
        return _appended_state(state, output)


# ──────────────────────────────────────────────────────────────────
# Stage 3: Insight query
# ──────────────────────────────────────────────────────────────────


class _InsightInput(BaseModel):
    candidate_items: list[CandidateItem]


class _InsightOutput(BaseModel):
    rules: list[SurfaceRule]


class _InsightSig(Signature):
    description = "Query insights for surface rules per candidate"
    input_schema = _InsightInput
    output_schema = _InsightOutput


class QueryInsightsNode(LLMNode[BriefingState]):
    name = "query_insights"
    inputs = ["candidate_items"]
    outputs = ["surface_rules"]
    signature = _InsightSig
    max_retries = 2

    def _call_llm(self, inputs, error_context=None):
        prompt = f"""You query Elsa's insight knowledge base to find rules about which
candidates to surface vs drop in the briefing.

For each distinct candidate sender / topic, call insight_query (and
optionally knowledge_search). Build SurfaceRule entries.

Candidates:
{_format_candidate_list(inputs.candidate_items)}

OUTPUT FORMAT (strict): single JSON object matching the schema below.
NO prose, NO markdown fence.

Required schema:
{_schema_block(_InsightOutput)}

Per rule:
- insight_id (string, required): the actual ID returned by insight_query (NOT a description)
- pattern (string, required): a keyword from the insight that matches the candidate
- action: ONE of "drop" | "keep" | "flag"
  - "drop" if user_correction says do-not-surface / ignore / 已拒絕 / 不申請 /
    closed / archived; this is the critical filter signal.
  - "keep" if active interest / important sender
  - "flag" if ambiguous
- reason (string): short summary from the insight content

Rules:
- Hit insight_query at least once per distinct candidate sender / topic.
- DO NOT invent insight_ids; only use IDs returned by the MCP.
- If no rules apply, return {{"rules": []}}.
"""
        if error_context:
            prompt += f"\n\nPrevious attempt failed schema validation:\n{error_context}\nFix and retry."
        return call_claude(
            prompt,
            json_schema=_schema_of(_InsightOutput),
            allowed_tools=MCP_INSIGHT,
            timeout=420,
        )

    def _merge_state(self, state, output):
        state.surface_rules = list(output.rules)
        return state


# ──────────────────────────────────────────────────────────────────
# Stage 4: Apply Filter (DETERMINISTIC, hack #9 retire)
# ──────────────────────────────────────────────────────────────────


class ApplyFilterNode(DeterministicNode[BriefingState]):
    """Pure Python; LLM cannot bypass this filter. Replaces C27 §11
    Curation Discipline prose (hack #9)."""

    name = "apply_filter"
    inputs = ["candidate_items", "surface_rules"]
    outputs = ["filtered_items", "rejected_items"]

    def run(self, state: BriefingState) -> BriefingState:
        filtered: list[FilteredItem] = []
        rejected: list[RejectedItem] = []

        for item in state.candidate_items:
            matched = self._first_drop_match(item, state.surface_rules)
            if matched:
                rejected.append(
                    RejectedItem(
                        **item.model_dump(),
                        drop_reason=matched.reason or "matched drop rule",
                        matched_rule=matched.insight_id,
                    )
                )
            else:
                keep_reasons = [
                    r.reason
                    for r in state.surface_rules
                    if r.action == "keep" and self._matches(item, r)
                ]
                filtered.append(
                    FilteredItem(
                        **item.model_dump(),
                        keep_reason=(
                            keep_reasons[0]
                            if keep_reasons
                            else "no matching drop rule"
                        ),
                    )
                )

        state.filtered_items = filtered
        state.rejected_items = rejected
        return state

    @staticmethod
    def _first_drop_match(item: CandidateItem, rules: list[SurfaceRule]) -> SurfaceRule | None:
        for r in rules:
            if r.action != "drop":
                continue
            if ApplyFilterNode._matches(item, r):
                return r
        return None

    @staticmethod
    def _matches(item: CandidateItem, rule: SurfaceRule) -> bool:
        """Phase 1-B-A: case-insensitive keyword match.

        Phase 3+: swap to semantic similarity (sentence-transformers).
        """
        if not rule.pattern:
            return False
        pat = rule.pattern.strip().lower()
        if not pat:
            return False
        haystack = " ".join(
            [item.topic, item.summary, item.evidence_id, item.suggested_action]
        ).lower()
        return pat in haystack


# ──────────────────────────────────────────────────────────────────
# Stage 5: Compose (Elsa's voice)
# ──────────────────────────────────────────────────────────────────


class _ComposeInput(BaseModel):
    filtered_items: list[FilteredItem]
    rejected_count: int


class _ComposeOutput(BaseModel):
    final_briefing: FinalBriefing
    briefing_text: str


class _ComposeSig(Signature):
    description = "Compose final briefing in Elsa's voice"
    input_schema = _ComposeInput
    output_schema = _ComposeOutput


class EvidenceAttachedVerifier(BusinessRuleVerifier):
    """Every section item must carry a non-empty evidence_id; gmail
    threads must use the thread_ format."""

    def check(self, output: Any, context: Any | None = None) -> Verdict:
        if not isinstance(output, _ComposeOutput):
            return Verdict(passed=False, error_msg="output is not _ComposeOutput")
        briefing = output.final_briefing
        if briefing is None:
            return Verdict(passed=False, error_msg="final_briefing is None")
        for section in briefing.sections:
            for item in section.items:
                if not item.evidence_id:
                    return Verdict(
                        passed=False,
                        error_msg=f"section '{section.title}' item "
                                  f"'{item.topic}' missing evidence_id",
                    )
        if not output.briefing_text.strip():
            return Verdict(passed=False, error_msg="briefing_text is empty")
        return Verdict(passed=True)


_ELSA_PERSONA = """You are Elsa, a shark-maiden majordomo composing a daily briefing
for your master. Tone: 鯊魚女僕長, concise, slightly playful, but
deeply respectful and operationally precise. Use 「ご主人」 sparingly.
Mix Traditional Chinese (default) and English where natural for
technical terms / titles.
"""


class ComposeBriefingNode(LLMNode[BriefingState]):
    """Elsa summarises the filtered items into final briefing.

    Inputs are force-fed (filtered_items already passed through
    ApplyFilterNode); Elsa's job is grouping, ordering, and writing
    the human-facing text. Output passes EvidenceAttachedVerifier
    (every fact carries evidence)."""

    name = "compose_briefing"
    inputs = ["filtered_items", "rejected_items"]
    outputs = ["final_briefing", "briefing_text"]
    signature = _ComposeSig
    verifier = EvidenceAttachedVerifier()
    max_retries = 3

    def _extract_inputs(self, state):
        return {
            "filtered_items": [it.model_dump() for it in state.filtered_items],
            "rejected_count": len(state.rejected_items),
        }

    def _call_llm(self, inputs, error_context=None):
        items_dump = json.dumps(
            [
                it.model_dump() if hasattr(it, "model_dump") else it
                for it in inputs.filtered_items
            ],
            ensure_ascii=False,
            indent=2,
        )
        prompt = f"""{_ELSA_PERSONA}

Compose today's briefing from the filtered items below. The items have
already been filtered through user_correction insights; you do NOT need
to drop anything. Your job is grouping, ordering by importance, and
writing concise human-readable lines that always carry evidence.

Filtered items (JSON):
{items_dump}

Rejected count (do NOT mention specifics; only the count if useful): {inputs.rejected_count}

OUTPUT FORMAT (strict): single JSON object matching the schema below.
NO prose, NO markdown fence outside the JSON.

Required schema:
{_schema_block(_ComposeOutput)}

Output requirements:
1. final_briefing: structured FinalBriefing.
   - sections: list of BriefingSection. Suggested titles:
     "📅 今日 Events", "📨 待處理信件", "🚨 風險", "🔄 變更".
   - Each BriefingSection.items entry MUST reuse the input
     evidence_type and evidence_id verbatim. DO NOT invent IDs.
   - Use exact field names: topic, summary, evidence_type, evidence_id,
     discovered_via, suggested_action, keep_reason.
   - evidence_type literals: "gmail_thread"|"calendar_event"|"drive_doc"|"web_url"
2. briefing_text: full Telegram-ready string in Elsa's voice. Open with
   a brief greeting, list each section, end with a one-line closing.
   Each line carrying a fact must include "(evidence: <id>)".
3. total_count and rejected_count: integers (will be backfilled from state
   if you set them, but include them with reasonable values).
"""
        if error_context:
            prompt += f"\n\nPrevious attempt failed verifier: {error_context}\nFix and retry."
        return call_claude(
            prompt,
            json_schema=_schema_of(_ComposeOutput),
            allowed_tools=[],  # no MCPs needed; pure compose
            timeout=420,
        )

    def _merge_state(self, state, output):
        state.final_briefing = output.final_briefing
        state.briefing_text = output.briefing_text
        # backfill totals from state
        if state.final_briefing is not None:
            state.final_briefing.total_count = sum(
                len(s.items) for s in state.final_briefing.sections
            )
            state.final_briefing.rejected_count = len(state.rejected_items)
        return state


# ──────────────────────────────────────────────────────────────────
# Stage 6: Persist for Elsa (deterministic)
# ──────────────────────────────────────────────────────────────────


PERSIST_LATEST = Path(
    os.path.expanduser(
        "~/Projects/elsa-workspace/data/elsa-state/today-briefing.md"
    )
)
PERSIST_ARCHIVE_DIR = Path(
    os.path.expanduser("~/Projects/elsa-workspace/data/briefings")
)

# Days to keep snapshots in briefings/. Older snapshots are deleted
# entirely (per main user direction 2026-05-03: 「最多存一週或一個月，
# 不用存太多啦」). Trajectory log in LanceDB is the long-term audit
# store; raw JSON snapshots are short-term inspection only.
PERSIST_RETAIN_DAYS = 30


class PersistForElsaNode(DeterministicNode[BriefingState]):
    """Write the final briefing to two places so Elsa's 24/7 session
    can read it on the next turn:

    - latest: today-briefing.md (overwritten each run, single file)
    - archive: data/briefings/YYYY-MM-DD-HHmm.json (full state snapshot;
      auto-pruned after PERSIST_RETAIN_DAYS days)
    """

    name = "persist_for_elsa"
    inputs = ["briefing_text", "final_briefing", "filtered_items", "rejected_items"]
    outputs = ["persisted_path"]

    def run(self, state: BriefingState) -> BriefingState:
        now = datetime.now(timezone.utc).astimezone()
        stamp = now.strftime("%Y-%m-%d-%H%M")

        PERSIST_LATEST.parent.mkdir(parents=True, exist_ok=True)
        PERSIST_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

        # latest: human-readable markdown (overwritten)
        latest_md = self._render_markdown(state, stamp)
        PERSIST_LATEST.write_text(latest_md, encoding="utf-8")

        # archive: full JSON snapshot (accumulates, then rotates)
        archive_path = PERSIST_ARCHIVE_DIR / f"{stamp}.json"
        archive_path.write_text(
            json.dumps(state.model_dump(), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

        # Prune snapshots older than retain window. Failure must NOT
        # block the briefing send — housekeeping only.
        try:
            self._prune_old_archives(now)
        except Exception as e:
            state.errors.append(f"persist_for_elsa prune: {e}")

        state.persisted_path = str(archive_path)
        return state

    @staticmethod
    def _prune_old_archives(now: datetime, retain_days: int = PERSIST_RETAIN_DAYS) -> int:
        """Delete briefings/*.json files older than ``retain_days``.

        Returns the number of files deleted. Caps any reflexive
        ``ls briefings/`` listing to ~retain_days entries — the same
        anti-pattern that crashed Elsa's session on 2026-05-02 with an
        8.7 MB PDF directory listing.

        Long-term audit lives in LanceDB ``trajectory`` table; raw JSON
        snapshots are short-term inspection only.
        """
        if not PERSIST_ARCHIVE_DIR.exists():
            return 0
        cutoff = now.timestamp() - retain_days * 86400
        deleted = 0
        for entry in PERSIST_ARCHIVE_DIR.iterdir():
            if not entry.is_file():
                continue
            if entry.suffix != ".json":
                continue
            if entry.name.startswith("_"):
                # Reserved namespace (_index.jsonl etc.) — never touch.
                continue
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            if mtime >= cutoff:
                continue
            try:
                entry.unlink()
                deleted += 1
            except OSError:
                continue
        return deleted

    @staticmethod
    def _render_markdown(state: BriefingState, stamp: str) -> str:
        lines = [
            f"# Today's Briefing ({stamp})",
            "",
            "_Auto-generated by elsa_runtime.skills.daily_briefing (v3.51-A)._",
            "",
            "## Briefing text (sent to Telegram)",
            "",
            "```",
            state.briefing_text or "(empty)",
            "```",
            "",
            f"## Stats",
            "",
            f"- Filtered items: {len(state.filtered_items)}",
            f"- Rejected items: {len(state.rejected_items)}",
            "",
        ]
        if state.rejected_items:
            lines += ["## Dropped (for audit)", ""]
            for r in state.rejected_items:
                lines.append(
                    f"- {r.topic} — drop_reason: {r.drop_reason} "
                    f"(rule: {r.matched_rule})"
                )
            lines.append("")
        return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────
# Stage 7: Send (terminal, hack #4 retire)
#
# 5/3 + 5/4 production failures: claude --print subprocess could not
# load mcp__plugin_telegram_telegram__reply ("ToolSearch found no
# matching deferred tool"). Plugin tools register via the Claude Code
# plugin system but appear deferred under --print mode; even a
# ToolSearch hint in-prompt did not surface them.
#
# Fix (5/4): hybrid path. Try MCP first (Elsa's preferred path; if the
# CLI/plugin interaction is fixed upstream this becomes seamless), then
# always fall back to a direct HTTPS POST against the Telegram bot
# API. Briefing send is mission-critical: the terminal node MUST
# deliver, even if the upstream "force-Elsa-through-MCP" preference
# can't be honored on this run. Path taken is recorded in
# state.errors for post-mortem visibility.
# ──────────────────────────────────────────────────────────────────


# Telegram bot config: shared with workspace/scripts/trigger-briefing.sh
TELEGRAM_BOT_API = "https://api.telegram.org/bot{token}/sendMessage"
TELEGRAM_TOKEN_ENV_FILE = Path(
    os.path.expanduser(
        "~/Projects/elsa-workspace/data/telegram-state/.env"
    )
)
TELEGRAM_TOKEN_ENV_KEYS = ("TELEGRAM_BOT_TOKEN", "BOT_TOKEN", "TELEGRAM_TOKEN")
TELEGRAM_DEFAULT_CHAT_ID = "1044616033"  # main user
TELEGRAM_MAX_MESSAGE_CHARS = 4000  # Telegram cap is 4096; leave headroom


class _SendOutput(BaseModel):
    sent: bool
    note: str = ""


class _SendSig(Signature):
    description = "Forced telegram send via MCP"
    input_schema = BaseModel  # not used
    output_schema = _SendOutput


class SendBriefingNode(TerminalNode[BriefingState]):
    """Graph terminal. Hybrid send: MCP primary, HTTPS fallback.

    Topology guarantees this node is unreachable without going through
    ApplyFilterNode + EvidenceAttachedVerifier upstream. Replaces Layer 4
    PreToolUse hook (hack #4 retire)."""

    name = "send_briefing"
    inputs = ["briefing_text", "dry_run"]
    outputs = ["sent"]

    def run(self, state: BriefingState) -> BriefingState:
        text = state.briefing_text.strip()
        if not text:
            raise NodeExecutionError(
                "send_briefing: briefing_text is empty (compose stage failed?)"
            )

        if state.dry_run:
            print("=" * 60)
            print("[DRY-RUN] would send to telegram:")
            print("=" * 60)
            print(text)
            print("=" * 60)
            state.sent = True
            return state

        # ---- Path 1: MCP via claude subprocess (Elsa-flavored) ----
        mcp_error: str | None = None
        try:
            result = self._send_via_mcp(text)
            if result.get("sent"):
                state.errors.append("send_briefing: MCP path succeeded")
                state.sent = True
                return state
            mcp_error = str(result.get("note") or "MCP returned sent=false")
        except Exception as e:
            mcp_error = f"{type(e).__name__}: {e}"

        state.errors.append(
            f"send_briefing: MCP path failed ({mcp_error[:200]}); "
            "falling back to HTTPS direct POST"
        )

        # ---- Path 2: HTTPS fallback (bulletproof) ----
        try:
            self._send_via_https(text)
        except Exception as e:
            raise NodeExecutionError(
                f"send_briefing: BOTH paths failed. "
                f"MCP: {mcp_error}. HTTPS: {type(e).__name__}: {e}"
            ) from e

        state.errors.append("send_briefing: HTTPS fallback succeeded")
        state.sent = True
        return state

    # ---- Path 1: MCP ----

    def _send_via_mcp(self, text: str) -> dict:
        prompt = f"""You have ONE job: deliver the briefing text below to the master
via the telegram reply MCP, verbatim.

The telegram MCP tool is `mcp__plugin_telegram_telegram__reply`. It may
appear as a deferred tool in this session — if your first attempt to call
it fails with a "tool not loaded" or "ToolSearch found no matching" error:

  1. Call ToolSearch with query "select:mcp__plugin_telegram_telegram__reply"
     to load the tool's schema.
  2. Then call mcp__plugin_telegram_telegram__reply with the exact text below.

Do NOT modify, summarise, translate, paraphrase, truncate, or otherwise
alter the briefing text. Send it verbatim.

After the MCP call returns, output JSON exactly matching:
  {{"sent": true, "note": "<short response summary>"}}
If both ToolSearch+call attempts fail, output:
  {{"sent": false, "note": "<the exact error you saw>"}}

=== BEGIN BRIEFING TEXT (send verbatim) ===
{text}
=== END BRIEFING TEXT ==="""

        result = call_claude(
            prompt,
            json_schema=_SendOutput.model_json_schema(),
            allowed_tools=["ToolSearch"] + MCP_TELEGRAM_SEND,
            timeout=240,
        )
        if not isinstance(result, dict):
            return {"sent": False, "note": f"unexpected MCP output: {result!r}"}
        return result

    # ---- Path 2: HTTPS direct ----

    def _send_via_https(self, text: str) -> None:
        token = self._resolve_bot_token()
        if not token:
            raise RuntimeError(
                "telegram bot token not in env or "
                f"{TELEGRAM_TOKEN_ENV_FILE}"
            )
        chat_id = os.environ.get("ELSA_TELEGRAM_CHAT_ID") or TELEGRAM_DEFAULT_CHAT_ID

        # Local import keeps pyproject.toml's existing httpx dep contained
        # to where it's used, and lets unit tests monkeypatch.
        import httpx

        chunks = self._split_for_telegram(text)
        api = TELEGRAM_BOT_API.format(token=token)
        for idx, chunk in enumerate(chunks, start=1):
            try:
                resp = httpx.post(
                    api,
                    data={"chat_id": chat_id, "text": chunk},
                    timeout=30,
                )
            except httpx.HTTPError as e:
                raise RuntimeError(f"chunk {idx}/{len(chunks)} POST failed: {e}") from e
            if resp.status_code != 200:
                raise RuntimeError(
                    f"chunk {idx}/{len(chunks)} -> HTTP {resp.status_code}: "
                    f"{resp.text[:300]}"
                )
            payload = resp.json() if resp.headers.get("content-type", "").startswith(
                "application/json"
            ) else {}
            if not payload.get("ok", False):
                raise RuntimeError(
                    f"chunk {idx}/{len(chunks)} -> Telegram replied not-ok: "
                    f"{str(payload)[:300]}"
                )

    @staticmethod
    def _resolve_bot_token() -> str | None:
        for k in TELEGRAM_TOKEN_ENV_KEYS:
            v = os.environ.get(k)
            if v:
                return v.strip()
        if not TELEGRAM_TOKEN_ENV_FILE.exists():
            return None
        try:
            for line in TELEGRAM_TOKEN_ENV_FILE.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                if key.strip() in TELEGRAM_TOKEN_ENV_KEYS:
                    return val.strip().strip('"').strip("'")
        except OSError:
            return None
        return None

    @staticmethod
    def _split_for_telegram(text: str, limit: int = TELEGRAM_MAX_MESSAGE_CHARS) -> list[str]:
        """Split text into Telegram-safe chunks, preferring paragraph
        boundaries, then line boundaries, then hard cut."""
        text = text.rstrip()
        if len(text) <= limit:
            return [text]

        chunks: list[str] = []
        remaining = text
        while len(remaining) > limit:
            cut = remaining.rfind("\n\n", 0, limit)
            if cut < limit // 2:  # avoid degenerate tiny chunks
                cut = remaining.rfind("\n", 0, limit)
            if cut < limit // 2:
                cut = remaining.rfind(" ", 0, limit)
            if cut <= 0:
                cut = limit  # hard cut as last resort
            chunks.append(remaining[:cut].rstrip())
            remaining = remaining[cut:].lstrip()
        if remaining:
            chunks.append(remaining)
        return chunks

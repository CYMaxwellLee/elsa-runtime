"""ApplyFilterNode: the architectural-critical test for v3.51-A.

This is the test that proves hack #9 (C27 §11 Curation Discipline prose)
is genuinely retired. The 5/2 incident surfaced 5 items that violated
existing user_correction insights because a markdown rule said
"filter these" but the LLM ignored it. ApplyFilterNode is pure Python:
LLM has no path to bypass.

If THIS test breaks, daily-briefing tomorrow morning regresses to
"surfacing items the user already said do not surface".
"""

from __future__ import annotations

import pytest

from elsa_runtime.skills.daily_briefing.nodes import ApplyFilterNode
from elsa_runtime.skills.daily_briefing.state import (
    BriefingState,
    CandidateItem,
    SurfaceRule,
)


def _candidate(topic: str, summary: str = "", evidence_id: str = "thread_1") -> CandidateItem:
    return CandidateItem(
        topic=topic,
        summary=summary,
        evidence_type="gmail_thread",
        evidence_id=evidence_id,
        discovered_via="gmail_deep_scan",
    )


def _drop_rule(pattern: str, insight_id: str = "i1", reason: str = "user said no") -> SurfaceRule:
    return SurfaceRule(
        insight_id=insight_id, pattern=pattern, action="drop", reason=reason
    )


def _keep_rule(pattern: str, insight_id: str = "i2", reason: str = "active") -> SurfaceRule:
    return SurfaceRule(
        insight_id=insight_id, pattern=pattern, action="keep", reason=reason
    )


# ─── Critical 5/2 regression tests ───


def test_jump_trading_dropped_per_user_correction():
    state = BriefingState(
        candidate_items=[_candidate(topic="Jump Trading recruiting")],
        surface_rules=[_drop_rule("Jump Trading", insight_id="ins-jump", reason="一律忽略")],
    )
    out = ApplyFilterNode().run(state)
    assert len(out.rejected_items) == 1
    assert out.rejected_items[0].matched_rule == "ins-jump"
    assert len(out.filtered_items) == 0


def test_xueshu_jixiao_dropped_per_5_2_correction():
    state = BriefingState(
        candidate_items=[_candidate(topic="學術績效獎勵 申請提醒")],
        surface_rules=[_drop_rule("學術績效", insight_id="ins-jixiao", reason="不申請")],
    )
    out = ApplyFilterNode().run(state)
    assert len(out.rejected_items) == 1
    assert out.rejected_items[0].matched_rule == "ins-jixiao"


def test_irsip_noc_dropped():
    state = BriefingState(
        candidate_items=[_candidate(topic="IRSIP NOC review request")],
        surface_rules=[_drop_rule("IRSIP", insight_id="ins-irsip", reason="cold email")],
    )
    out = ApplyFilterNode().run(state)
    assert len(out.rejected_items) == 1


def test_vlsi_dropped():
    state = BriefingState(
        candidate_items=[_candidate(topic="VLSI Symposium 投稿邀請")],
        surface_rules=[_drop_rule("VLSI", insight_id="ins-vlsi", reason="不投稿")],
    )
    out = ApplyFilterNode().run(state)
    assert len(out.rejected_items) == 1


def test_iccad_review_dropped():
    state = BriefingState(
        candidate_items=[_candidate(topic="ICCAD 2026 review invitation")],
        surface_rules=[_drop_rule("ICCAD", insight_id="ins-iccad", reason="已拒絕")],
    )
    out = ApplyFilterNode().run(state)
    assert len(out.rejected_items) == 1


# ─── Positive cases ───


def test_active_item_passes_through_when_no_drop_rule():
    state = BriefingState(
        candidate_items=[_candidate(topic="Student qualifier exam tomorrow")],
        surface_rules=[],
    )
    out = ApplyFilterNode().run(state)
    assert len(out.filtered_items) == 1
    assert len(out.rejected_items) == 0


def test_active_item_passes_through_when_only_keep_rule():
    state = BriefingState(
        candidate_items=[_candidate(topic="Student qualifier exam")],
        surface_rules=[_keep_rule("qualifier", reason="active interest")],
    )
    out = ApplyFilterNode().run(state)
    assert len(out.filtered_items) == 1
    assert out.filtered_items[0].keep_reason == "active interest"


def test_match_is_case_insensitive():
    state = BriefingState(
        candidate_items=[_candidate(topic="JUMP TRADING outreach")],
        surface_rules=[_drop_rule("jump trading")],
    )
    out = ApplyFilterNode().run(state)
    assert len(out.rejected_items) == 1


def test_pattern_matches_summary_field_too():
    state = BriefingState(
        candidate_items=[
            _candidate(
                topic="recruiter outreach",
                summary="Quant firm Jump Trading reaches out for trading research",
            )
        ],
        surface_rules=[_drop_rule("Jump Trading")],
    )
    out = ApplyFilterNode().run(state)
    assert len(out.rejected_items) == 1


def test_first_drop_rule_wins():
    state = BriefingState(
        candidate_items=[_candidate(topic="VLSI Symposium 2026")],
        surface_rules=[
            _drop_rule("VLSI", insight_id="ins-A", reason="rule A"),
            _drop_rule("Symposium", insight_id="ins-B", reason="rule B"),
        ],
    )
    out = ApplyFilterNode().run(state)
    assert len(out.rejected_items) == 1
    assert out.rejected_items[0].matched_rule == "ins-A"


def test_empty_pattern_does_not_match_everything():
    state = BriefingState(
        candidate_items=[_candidate(topic="anything")],
        surface_rules=[
            SurfaceRule(insight_id="ins-blank", pattern="", action="drop", reason="bug")
        ],
    )
    out = ApplyFilterNode().run(state)
    assert len(out.filtered_items) == 1  # NOT dropped by empty pattern
    assert len(out.rejected_items) == 0


def test_mixed_batch():
    """5/2-style batch: 5 items, 4 should drop, 1 should pass."""
    state = BriefingState(
        candidate_items=[
            _candidate(topic="Jump Trading recruiting"),
            _candidate(topic="學術績效獎勵 申請"),
            _candidate(topic="IRSIP NOC review"),
            _candidate(topic="VLSI Symposium 投稿"),
            _candidate(topic="Student qualifier prep"),  # legitimate — must pass
        ],
        surface_rules=[
            _drop_rule("Jump Trading", insight_id="ins-jump"),
            _drop_rule("學術績效", insight_id="ins-jixiao"),
            _drop_rule("IRSIP", insight_id="ins-irsip"),
            _drop_rule("VLSI", insight_id="ins-vlsi"),
        ],
    )
    out = ApplyFilterNode().run(state)
    assert len(out.rejected_items) == 4
    assert len(out.filtered_items) == 1
    assert out.filtered_items[0].topic == "Student qualifier prep"
    rejected_topics = {r.topic for r in out.rejected_items}
    assert "Jump Trading recruiting" in rejected_topics
    assert "學術績效獎勵 申請" in rejected_topics


# ─── Module-level integration: graph topology ───


def test_module_graph_compiles_with_all_11_nodes():
    """Ensure the full graph validates: entry/terminal/reachable/no-cycles."""
    from elsa_runtime.skills.daily_briefing import DailyBriefingModule

    m = DailyBriefingModule()
    nodes = sorted(m.graph.nodes.keys())
    assert "gmail_index" in nodes
    assert "calendar_index" in nodes
    assert "drive_index" in nodes
    assert "worker_gmail_deep_scan" in nodes
    assert "worker_calendar_verifier" in nodes
    assert "worker_web_verifier" in nodes
    assert "worker_risk_hunter" in nodes
    assert "query_insights" in nodes
    assert "apply_filter" in nodes
    assert "compose_briefing" in nodes
    assert "persist_for_elsa" in nodes
    assert "send_briefing" in nodes
    assert "send_briefing" in m.graph.exits  # terminal


def test_module_describe_includes_source_insights():
    from elsa_runtime.skills.daily_briefing import DailyBriefingModule

    desc = DailyBriefingModule().describe()
    assert "daily_briefing" in desc
    assert "incident-2026-05-02" in desc
    assert "```mermaid" in desc

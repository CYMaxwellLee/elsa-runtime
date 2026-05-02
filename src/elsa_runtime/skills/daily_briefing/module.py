"""DailyBriefingModule: 11-node graph topology.

Per C29 §4.4. Stages:

  Stage 1 (parallel): gmail_index, calendar_index, drive_index
  Stage 2 (parallel fan-out): 4 workers reading the indices
  Stage 3 (linear): query_insights -> apply_filter -> compose_briefing
  Stage 4 (linear): persist_for_elsa -> send_briefing (terminal)

Topology guarantees:
- LLM cannot reach send_briefing without going through apply_filter
  (hack #9 retire) and compose_briefing's verifier (evidence-attached).
- send_briefing is a TerminalNode; the only way to send is via the
  telegram MCP forced path (hack #4 retire).
"""

from __future__ import annotations

import os

from elsa_runtime.module import Module, StateGraph, TrajectoryLogger

from .nodes import (
    ApplyFilterNode,
    CalendarIndexNode,
    CalendarVerifierWorker,
    ComposeBriefingNode,
    DriveIndexNode,
    GmailDeepScanWorker,
    GmailIndexNode,
    PersistForElsaNode,
    QueryInsightsNode,
    RiskHunterWorker,
    SendBriefingNode,
    WebVerifierWorker,
)
from .state import BriefingState

LANCE_DB_PATH = os.path.expanduser("~/.elsa-system/lancedb")


class DailyBriefingModule(Module):
    name = "daily_briefing"
    description = (
        "Active investigator: scan, verify, filter, compose, send daily "
        "briefing. Replaces v3.50 markdown skill + Layer 4 hook with "
        "compiled StateGraph (C29 architecture)."
    )
    state_schema = BriefingState
    source_insights = [
        # User corrections that shaped the filter rules
        "insight-elsa-...c1705b",  # cold meeting do-not-surface (4/29)
        "insight-elsa-...e2deae",  # 學術績效獎勵 不 surface (5/1)
        "insight-elsa-...6f529a",  # 外籍 cold email 一律忽略 (4/27)
        # Incidents that drove the architectural shift
        "incident-2026-04-30",     # 4 連錯 (skip verification)
        "incident-2026-05-02",     # daily-briefing skill bypass
        # 5/2 user_correction batch
        "insight-elsa-...20260502a",  # 彈性加給不申請
        "insight-elsa-...20260502b",  # VLSI 不投稿
        "insight-elsa-...20260502c",  # ICCAD review 已拒絕
        "insight-elsa-...20260502d",  # closed thread 不報
    ]

    def build_graph(self) -> StateGraph[BriefingState]:
        try:
            telemetry = TrajectoryLogger(
                lance_db_path=LANCE_DB_PATH, module_name=self.name
            )
        except Exception:
            telemetry = None  # never block briefing on telemetry init

        g: StateGraph[BriefingState] = StateGraph(BriefingState)

        # Stage 1: index nodes (parallel after entry)
        g.add_node(GmailIndexNode(telemetry=telemetry))
        g.add_node(CalendarIndexNode(telemetry=telemetry))
        g.add_node(DriveIndexNode(telemetry=telemetry))

        # Stage 2: 4-worker fan-out
        g.add_node(GmailDeepScanWorker(telemetry=telemetry))
        g.add_node(CalendarVerifierWorker(telemetry=telemetry))
        g.add_node(WebVerifierWorker(telemetry=telemetry))
        g.add_node(RiskHunterWorker(telemetry=telemetry))

        # Stage 3-5: insights + filter + compose
        g.add_node(QueryInsightsNode(telemetry=telemetry))
        g.add_node(ApplyFilterNode(telemetry=telemetry))
        g.add_node(ComposeBriefingNode(telemetry=telemetry))

        # Stage 6-7: persist + send
        g.add_node(PersistForElsaNode(telemetry=telemetry))
        g.add_node(SendBriefingNode(telemetry=telemetry))

        # Topology
        # Entry: gmail_index, then fan out to calendar + drive in parallel,
        # then converge into the 4-worker fan-out.
        g.set_entry("gmail_index")
        g.add_parallel_then(
            "gmail_index",
            ["calendar_index", "drive_index"],
            converge="worker_gmail_deep_scan",
        )

        # Stage 2: gmail_deep_scan -> fan-out to other 3 workers,
        # converge into web_verifier (which reads earlier candidates).
        g.add_parallel_then(
            "worker_gmail_deep_scan",
            ["worker_calendar_verifier", "worker_risk_hunter"],
            converge="worker_web_verifier",
        )

        # Stage 3+: linear
        g.add_edge("worker_web_verifier", "query_insights")
        g.add_edge("query_insights", "apply_filter")
        g.add_edge("apply_filter", "compose_briefing")
        g.add_edge("compose_briefing", "persist_for_elsa")
        g.add_edge("persist_for_elsa", "send_briefing")

        return g

"""Tests for ACTIVE-INSIGHTS.md auto-promote logic."""
from mcp_server.active_insights import (
    should_promote,
    get_target_workspaces,
    read_active_insights,
    promote_insight,
    update_insight_in_active,
    MAX_ENTRIES,
)


class TestShouldPromote:
    def test_user_correction_always_promotes(self):
        assert should_promote("user_correction", 0.5) is True
        assert should_promote("user_correction", 0.9) is True

    def test_high_confidence_promotes(self):
        assert should_promote("daily_observation", 0.9) is True
        assert should_promote("paper_analysis", 0.95) is True

    def test_low_confidence_no_promote(self):
        assert should_promote("daily_observation", 0.7) is False
        assert should_promote("paper_analysis", 0.5) is False


class TestGetTargetWorkspaces:
    def test_user_correction_all_workspaces(self, tmp_path):
        w1 = tmp_path / "elsa-ws"
        w2 = tmp_path / "rei-ws"
        w1.mkdir()
        w2.mkdir()
        registry = {"elsa": str(w1), "rei": str(w2)}
        targets = get_target_workspaces("user_correction", "elsa", registry)
        assert len(targets) == 2

    def test_other_only_saving_agent(self, tmp_path):
        w1 = tmp_path / "elsa-ws"
        w2 = tmp_path / "rei-ws"
        w1.mkdir()
        w2.mkdir()
        registry = {"elsa": str(w1), "rei": str(w2)}
        targets = get_target_workspaces("daily_observation", "elsa", registry)
        assert targets == [str(w1)]

    def test_nonexistent_workspace_skipped(self, tmp_path):
        registry = {"elsa": str(tmp_path / "nonexistent")}
        targets = get_target_workspaces("daily_observation", "elsa", registry)
        assert targets == []


class TestPromoteInsight:
    def test_promote_creates_file(self, tmp_path):
        ws = tmp_path / "elsa-ws"
        ws.mkdir()
        registry = {"elsa": str(ws)}
        affected = promote_insight(
            content="晚上不要說早安",
            domain="communication",
            source_type="user_correction",
            confidence=0.9,
            agent_id="elsa",
            insight_id="insight-elsa-20260409-abc123",
            created_at="2026-04-09T18:30:00",
            registry=registry,
        )
        assert len(affected) == 1
        content = (ws / "ACTIVE-INSIGHTS.md").read_text()
        assert "晚上不要說早安" in content
        assert "insight-elsa-20260409-abc123" in content

    def test_duplicate_not_added(self, tmp_path):
        ws = tmp_path / "elsa-ws"
        ws.mkdir()
        registry = {"elsa": str(ws)}
        kwargs = dict(
            content="test", domain="ops", source_type="user_correction",
            confidence=0.9, agent_id="elsa",
            insight_id="insight-elsa-dup", created_at="2026-04-09",
            registry=registry,
        )
        promote_insight(**kwargs)
        promote_insight(**kwargs)
        entries = read_active_insights(str(ws))
        assert len(entries) == 1

    def test_eviction_at_max(self, tmp_path):
        ws = tmp_path / "elsa-ws"
        ws.mkdir()
        registry = {"elsa": str(ws)}
        for i in range(MAX_ENTRIES + 5):
            promote_insight(
                content=f"rule number {i}",
                domain="ops",
                source_type="user_correction",
                confidence=0.9,
                agent_id="elsa",
                insight_id=f"insight-{i:04d}",
                created_at="2026-04-09",
                registry=registry,
            )
        entries = read_active_insights(str(ws))
        assert len(entries) == MAX_ENTRIES
        # oldest (0-4) should be evicted
        content = (ws / "ACTIVE-INSIGHTS.md").read_text()
        assert "rule number 0" not in content
        assert f"rule number {MAX_ENTRIES + 4}" in content

    def test_low_confidence_not_promoted(self, tmp_path):
        ws = tmp_path / "elsa-ws"
        ws.mkdir()
        registry = {"elsa": str(ws)}
        affected = promote_insight(
            content="just an observation",
            domain="research",
            source_type="daily_observation",
            confidence=0.7,
            agent_id="elsa",
            insight_id="insight-low",
            created_at="2026-04-09",
            registry=registry,
        )
        assert affected == []

    def test_user_correction_promotes_to_all(self, tmp_path):
        w1 = tmp_path / "elsa-ws"
        w2 = tmp_path / "rei-ws"
        w1.mkdir()
        w2.mkdir()
        registry = {"elsa": str(w1), "rei": str(w2)}
        affected = promote_insight(
            content="global rule",
            domain="communication",
            source_type="user_correction",
            confidence=0.9,
            agent_id="elsa",
            insight_id="insight-global",
            created_at="2026-04-09",
            registry=registry,
        )
        assert len(affected) == 2
        assert "global rule" in (w1 / "ACTIVE-INSIGHTS.md").read_text()
        assert "global rule" in (w2 / "ACTIVE-INSIGHTS.md").read_text()


class TestUpdateInsightInActive:
    def test_update_existing(self, tmp_path):
        ws = tmp_path / "elsa-ws"
        ws.mkdir()
        registry = {"elsa": str(ws)}
        promote_insight(
            content="old content",
            domain="ops",
            source_type="user_correction",
            confidence=0.9,
            agent_id="elsa",
            insight_id="insight-upd",
            created_at="2026-04-09",
            registry=registry,
        )
        update_insight_in_active("insight-upd", "new content", registry)
        text = (ws / "ACTIVE-INSIGHTS.md").read_text()
        assert "new content" in text
        assert "old content" not in text

    def test_update_nonexistent_noop(self, tmp_path):
        ws = tmp_path / "elsa-ws"
        ws.mkdir()
        registry = {"elsa": str(ws)}
        affected = update_insight_in_active("insight-nope", "whatever", registry)
        assert affected == []

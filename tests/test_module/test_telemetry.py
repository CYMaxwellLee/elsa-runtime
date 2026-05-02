"""TrajectoryLogger: enter/exit/route + flush to LanceDB.

Live LanceDB integration is gated on lancedb being importable; if not
available the flush test is skipped (still validates buffer mechanics).
"""
from __future__ import annotations

import pytest
from pydantic import BaseModel

from elsa_runtime.module import TrajectoryLogger


class S(BaseModel):
    n: int = 0


def _make_logger(tmp_path, **kw):
    return TrajectoryLogger(
        lance_db_path=str(tmp_path / "lance"),
        module_name="test_module",
        **kw,
    )


def test_log_enter_exit_buffers_events(tmp_path):
    log = _make_logger(tmp_path)
    log.log_enter("nodeA", S(n=1))
    log.log_exit("nodeA", S(n=2), success=True)
    assert len(log.events) == 2
    assert log.events[0]["phase"] == "enter"
    assert log.events[0]["node"] == "nodeA"
    assert log.events[0]["module"] == "test_module"
    assert log.events[0]["state_snapshot"]
    assert log.events[1]["phase"] == "exit"
    assert log.events[1]["success"] is True
    # All events share the same trajectory id.
    assert log.events[0]["trajectory_id"] == log.events[1]["trajectory_id"]


def test_log_exit_failure_records_error(tmp_path):
    log = _make_logger(tmp_path)
    log.log_exit("nodeB", S(), success=False, error="boom")
    assert log.events[0]["success"] is False
    assert log.events[0]["error"] == "boom"


def test_log_route_records_route_taken(tmp_path):
    log = _make_logger(tmp_path)
    log.log_route("router1", "yes_branch")
    assert log.events[0]["phase"] == "route"
    assert log.events[0]["route_taken"] == "yes_branch"
    assert log.events[0]["node"] == "router1"


def test_reset_changes_trajectory_id_and_clears_buffer(tmp_path):
    log = _make_logger(tmp_path)
    log.log_enter("nodeA", S())
    old_id = log.trajectory_id
    log.reset()
    assert log.trajectory_id != old_id
    assert log.events == []


def test_two_loggers_have_distinct_trajectory_ids(tmp_path):
    a = _make_logger(tmp_path)
    b = _make_logger(tmp_path)
    assert a.trajectory_id != b.trajectory_id


def test_flush_writes_to_lancedb_when_available(tmp_path):
    pytest.importorskip("lancedb")
    import lancedb

    log = _make_logger(tmp_path)
    log.log_enter("nodeA", S(n=1))
    log.log_exit("nodeA", S(n=2), success=True)
    log.flush()

    db = lancedb.connect(str(tmp_path / "lance"))
    # table_names() works across lancedb 0.20.x (list_tables paginates).
    assert "trajectory" in db.table_names()
    tbl = db.open_table("trajectory")
    rows = tbl.to_arrow().to_pylist()
    assert len(rows) == 2
    assert {r["node"] for r in rows} == {"nodeA"}
    assert {r["phase"] for r in rows} == {"enter", "exit"}
    # Buffer cleared after flush.
    assert log.events == []


def test_flush_empty_buffer_is_no_op(tmp_path):
    log = _make_logger(tmp_path)
    log.flush()  # should not raise even with no events
    assert log.events == []

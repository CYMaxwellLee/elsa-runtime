"""TrajectoryLogger: log every node execution to LanceDB.

Per C29 §3.6 + IMPLEMENTATION-v3.51-A Step 11.7.

Each Module run creates a fresh trajectory_id. Every Node enter/exit
plus RouterNode route decisions are appended as events. flush() writes
all buffered events to the LanceDB ``trajectory`` table.

The schema is intentionally flat (no nested) so that Phase 2+ RL
training can ingest with minimal preprocessing:

    trajectory_id : string
    timestamp     : string (ISO8601)
    module        : string
    node          : string
    phase         : string  ("enter" / "exit" / "route")
    state_snapshot: string  (JSON-serialised state)
    success       : bool    (only meaningful on phase=="exit")
    error         : string  (set when success is False)
    route_taken   : string  (only meaningful on phase=="route")

Storage failures must NOT silently swallow: re-raise so the runner sees
them. (Per IMPLEMENTATION §"Trajectory LanceDB 寫入失敗".)
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_state_snapshot(state: Any) -> str:
    if isinstance(state, BaseModel):
        try:
            return state.model_dump_json()
        except Exception:
            return json.dumps(str(state))
    try:
        return json.dumps(state, default=str)
    except Exception:
        return json.dumps(str(state))


class TrajectoryLogger:
    """Buffer + flush trajectory events to LanceDB.

    Args:
        lance_db_path: filesystem path to the LanceDB directory (e.g.
            ``~/.elsa-system/lancedb``). Expanded with ``os.path.expanduser``.
        table: table name. Defaults to ``trajectory``.
        module_name: name of the owning Module. Recorded on every event
            so trajectories from different modules can be filtered.
    """

    DEFAULT_TABLE = "trajectory"

    def __init__(
        self,
        lance_db_path: str,
        table: str = DEFAULT_TABLE,
        module_name: str = "",
    ):
        self.db_path = os.path.expanduser(lance_db_path)
        self.table_name = table
        self.module_name = module_name
        self.trajectory_id = str(uuid.uuid4())
        self.events: list[dict] = []

    def reset(self) -> None:
        """Start a new trajectory id (called by Module.run)."""
        self.trajectory_id = str(uuid.uuid4())
        self.events = []

    def _base_event(self, node_name: str, phase: str) -> dict:
        return {
            "trajectory_id": self.trajectory_id,
            "timestamp": _utcnow_iso(),
            "module": self.module_name,
            "node": node_name,
            "phase": phase,
            "state_snapshot": "",
            "success": False,
            "error": "",
            "route_taken": "",
        }

    def log_enter(self, node_name: str, state: Any) -> None:
        ev = self._base_event(node_name, "enter")
        ev["state_snapshot"] = _safe_state_snapshot(state)
        self.events.append(ev)

    def log_exit(
        self,
        node_name: str,
        state: Any,
        success: bool,
        error: str | None = None,
    ) -> None:
        ev = self._base_event(node_name, "exit")
        ev["state_snapshot"] = _safe_state_snapshot(state)
        ev["success"] = bool(success)
        ev["error"] = error or ""
        self.events.append(ev)

    def log_route(self, router_name: str, route: str) -> None:
        ev = self._base_event(router_name, "route")
        ev["route_taken"] = route
        self.events.append(ev)

    def flush(self) -> None:
        """Write buffered events to LanceDB. Clears buffer on success.

        If LanceDB is unavailable or the path is unwritable, raises.
        Callers (Module.run) decide whether failure is fatal.
        """
        if not self.events:
            return
        try:
            import lancedb  # local import: keep module importable without lancedb at unit test time
        except ImportError as e:
            raise RuntimeError(
                "TrajectoryLogger.flush requires lancedb installed"
            ) from e

        os.makedirs(self.db_path, exist_ok=True)
        db = lancedb.connect(self.db_path)
        # lancedb >=0.20 deprecates table_names() in favour of
        # list_tables(), but the latter returns a paginated dict-like
        # in 0.20.x. Try the cleanest path first; fall back to
        # open_table-then-create on errors.
        try:
            existing = set(db.table_names())
        except Exception:
            existing = set()
        if self.table_name not in existing:
            db.create_table(self.table_name, data=self.events)
        else:
            tbl = db.open_table(self.table_name)
            tbl.add(self.events)
        self.events = []

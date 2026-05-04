"""
T1.0.1 ExecutionLogger
======================
Append-only JSONL logger for all LLM calls across the Elsa System.
Zero external dependencies (stdlib only). Works on any machine/OS.

Usage:
    from data.execution_log import ExecutionLogger

    logger = ExecutionLogger()
    logger.log(
        agent_id="elsa",
        task_type="daily_briefing",
        model_used="anthropic/claude-sonnet-4-6",
        success=True,
        wall_clock_seconds=3.2,
        input_tokens=1200,
        output_tokens=400,
        effort_level="medium",
    )

File layout (in LOGS_DIR):
    execution-2026-03.jsonl        <- current month, active writes
    execution-2026-02.jsonl.gz     <- past months, compressed
    daily/2026-03-04-summary.json  <- daily aggregates (written by aggregate_today())
"""

import gzip
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Override via env var ELSA_LOGS_DIR for portability across machines.
_DEFAULT_LOGS_DIR = Path.home() / ".elsa-system" / "logs"
LOGS_DIR = Path(os.environ.get("ELSA_LOGS_DIR", _DEFAULT_LOGS_DIR))

# Months older than this threshold get compressed to .jsonl.gz
ARCHIVE_AFTER_MONTHS = 3


# ---------------------------------------------------------------------------
# Core logger
# ---------------------------------------------------------------------------

class ExecutionLogger:
    """
    Thread-safe* append-only JSONL logger with monthly rotation.
    (*safe enough for single-process use; add a lock if multi-threaded)

    Design constraints:
    - stdlib only: runs on any Python 3.9+ without pip install
    - one file per month: easy to inspect, easy to archive
    - no DB dependency: survives machine migrations
    - schema is additive: new fields are safe to add any time
    """

    def __init__(self, logs_dir: Optional[Path] = None, archive_after_months: int = ARCHIVE_AFTER_MONTHS):
        self.logs_dir = Path(logs_dir or LOGS_DIR)
        self.archive_after_months = archive_after_months
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        (self.logs_dir / "daily").mkdir(exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def log(
        self,
        agent_id: str,
        task_type: str,
        model_used: str,
        success: bool,
        wall_clock_seconds: float,
        input_tokens: int = 0,
        output_tokens: int = 0,
        effort_level: str = "medium",          # low / medium / high / max
        user_correction_count: int = 0,
        retry_count: int = 0,
        cost_usd: Optional[float] = None,      # filled in later if known
        task_id: Optional[str] = None,
        extra: Optional[dict] = None,
    ) -> None:
        """Append one execution record to the current month's JSONL file."""
        now = datetime.now(timezone.utc)
        record = {
            "ts": now.isoformat(),
            "agent_id": agent_id,
            "task_type": task_type,
            "model_used": model_used,
            "success": success,
            "wall_clock_s": round(wall_clock_seconds, 3),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "effort_level": effort_level,
            "user_correction_count": user_correction_count,
            "retry_count": retry_count,
        }
        if cost_usd is not None:
            record["cost_usd"] = round(cost_usd, 6)
        if task_id is not None:
            record["task_id"] = task_id
        if extra:
            record["extra"] = extra

        log_file = self._current_log_path(now)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def aggregate_today(self) -> dict:
        """
        Read today's records, compute a daily summary, save to daily/.
        Called by HEARTBEAT at 21:00 to produce the token brief.

        Returns the summary dict (also persisted as JSON).
        """
        today = datetime.now(timezone.utc).date()
        records = self._read_day(today)

        summary = self._compute_summary(records, label=today.isoformat())
        out = self.logs_dir / "daily" / f"{today.isoformat()}-summary.json"
        with open(out, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        return summary

    def telegram_brief(self, summary: Optional[dict] = None) -> str:
        """
        Return a 5-line Telegram message from today's summary.
        Compatible with Elsa HEARTBEAT daily token brief format.
        """
        if summary is None:
            summary = self.aggregate_today()

        lines = [
            f"📊 *Daily Execution Brief* ({summary['date']})",
            f"   Tasks: {summary['total_tasks']} ({summary['success_count']} ok / {summary['fail_count']} fail)",
            f"   Tokens: {summary['total_tokens']:,} in+out",
            f"   Cost: ${summary.get('total_cost_usd', 0):.4f}",
            f"   Top model: {summary.get('top_model', 'n/a')}",
        ]
        return "\n".join(lines)

    def archive_old_months(self) -> list[str]:
        """
        Compress monthly JSONL files older than archive_after_months.
        Safe to run repeatedly (idempotent).
        Returns list of newly archived filenames.
        """
        archived = []
        cutoff = self._months_ago(self.archive_after_months)

        for p in sorted(self.logs_dir.glob("execution-*.jsonl")):
            month_str = p.stem.replace("execution-", "")   # e.g. "2026-01"
            try:
                file_month = datetime.strptime(month_str, "%Y-%m").date().replace(day=1)
            except ValueError:
                continue
            if file_month < cutoff:
                gz_path = p.with_suffix(".jsonl.gz")
                if not gz_path.exists():
                    with open(p, "rb") as f_in, gzip.open(gz_path, "wb") as f_out:
                        shutil.copyfileobj(f_in, f_out)
                    p.unlink()
                    archived.append(gz_path.name)
        return archived

    def read_month(self, year: int, month: int) -> list[dict]:
        """Read all records for a given month (decompresses .gz if needed)."""
        plain = self.logs_dir / f"execution-{year:04d}-{month:02d}.jsonl"
        gz    = plain.with_suffix(".jsonl.gz")

        if plain.exists():
            return self._parse_jsonl(plain.read_text(encoding="utf-8"))
        if gz.exists():
            with gzip.open(gz, "rt", encoding="utf-8") as f:
                return self._parse_jsonl(f.read())
        return []

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _current_log_path(self, now: datetime) -> Path:
        return self.logs_dir / f"execution-{now.year:04d}-{now.month:02d}.jsonl"

    def _read_day(self, day) -> list[dict]:
        """Read all records that belong to a given date (datetime.date)."""
        records = self.read_month(day.year, day.month)
        return [r for r in records if r.get("ts", "").startswith(day.isoformat())]

    @staticmethod
    def _parse_jsonl(text: str) -> list[dict]:
        records = []
        for line in text.splitlines():
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass   # corrupted line: skip, don't crash
        return records

    @staticmethod
    def _compute_summary(records: list[dict], label: str) -> dict:
        total = len(records)
        ok    = sum(1 for r in records if r.get("success"))
        tokens = sum(r.get("total_tokens", 0) for r in records)
        cost   = sum(r.get("cost_usd", 0.0) for r in records)

        model_counts: dict[str, int] = {}
        for r in records:
            m = r.get("model_used", "unknown")
            model_counts[m] = model_counts.get(m, 0) + 1
        top_model = max(model_counts, key=model_counts.get) if model_counts else "n/a"

        agent_stats: dict[str, dict] = {}
        for r in records:
            aid = r.get("agent_id", "unknown")
            if aid not in agent_stats:
                agent_stats[aid] = {"tasks": 0, "tokens": 0, "cost": 0.0}
            agent_stats[aid]["tasks"]  += 1
            agent_stats[aid]["tokens"] += r.get("total_tokens", 0)
            agent_stats[aid]["cost"]   += r.get("cost_usd", 0.0)

        return {
            "date": label,
            "total_tasks": total,
            "success_count": ok,
            "fail_count": total - ok,
            "total_tokens": tokens,
            "total_cost_usd": round(cost, 6),
            "top_model": top_model,
            "model_breakdown": model_counts,
            "agent_breakdown": agent_stats,
        }

    @staticmethod
    def _months_ago(n: int):
        """Return a date object representing the first day of n months ago."""
        now = datetime.now(timezone.utc).date()
        month = now.month - n
        year  = now.year
        while month <= 0:
            month += 12
            year  -= 1
        return now.replace(year=year, month=month, day=1)


# ---------------------------------------------------------------------------
# CLI smoke test  (python3 data/execution_log.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        logger = ExecutionLogger(logs_dir=Path(tmp))

        # Write a few test records
        for i in range(5):
            logger.log(
                agent_id="elsa",
                task_type="daily_briefing",
                model_used="anthropic/claude-sonnet-4-6",
                success=(i % 4 != 0),
                wall_clock_seconds=1.5 + i * 0.3,
                input_tokens=1000 + i * 100,
                output_tokens=300 + i * 50,
                effort_level="medium",
                cost_usd=0.002 + i * 0.0005,
            )
        logger.log(
            agent_id="rei",
            task_type="paper_analysis",
            model_used="anthropic/claude-opus-4-6",
            success=True,
            wall_clock_seconds=12.1,
            input_tokens=8000,
            output_tokens=2000,
            effort_level="max",
            cost_usd=0.15,
        )

        # Verify output
        log_files = list(Path(tmp).glob("execution-*.jsonl"))
        assert len(log_files) == 1, f"Expected 1 log file, got {len(log_files)}"

        with open(log_files[0]) as f:
            lines = [line for line in f if line.strip()]
        assert len(lines) == 6, f"Expected 6 lines, got {len(lines)}"

        # Test aggregation
        summary = logger.aggregate_today()
        assert summary["total_tasks"] == 6
        # i=0 and i=4 → success=(0%4!=0)=False and (4%4!=0)=False → 2 failures
        assert summary["fail_count"] == 2

        # Test Telegram brief
        brief = logger.telegram_brief(summary)
        assert "Daily Execution Brief" in brief
        assert "6" in brief    # total tasks

        print("All smoke tests passed.")
        print()
        print(brief)

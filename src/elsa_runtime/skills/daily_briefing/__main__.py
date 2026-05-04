"""launchd entry point.

Invoked by ~/Library/LaunchAgents/com.elsa.daily-briefing.plist at
05:30 daily. Returns 0 on send success, 1 on any failure (briefing
not sent — main user will not receive it; if 7am no message arrives,
that itself is the bug signal per main user 5/2 directive).

Manual invocation:
    cd ~/Projects/elsa-runtime
    .venv/bin/python -m elsa_runtime.skills.daily_briefing            # real send
    .venv/bin/python -m elsa_runtime.skills.daily_briefing --dry-run  # local print only
"""

from __future__ import annotations

import sys
import traceback
from datetime import datetime, timezone

from .module import DailyBriefingModule


def main() -> int:
    dry_run = "--dry-run" in sys.argv

    started_at = datetime.now(timezone.utc).isoformat()
    print(f"[daily_briefing] {started_at} starting (dry_run={dry_run})")

    try:
        module = DailyBriefingModule()
    except Exception as e:
        print(f"[daily_briefing] FATAL: module construction failed: {e}", file=sys.stderr)
        traceback.print_exc()
        return 1

    print(f"[daily_briefing] source_insights: {module.source_insights}")
    print(f"[daily_briefing] graph nodes: {sorted(module.graph.nodes.keys())}")

    try:
        result = module.run(
            trigger_time=started_at,
            dry_run=dry_run,
        )
    except Exception as e:
        print(f"[daily_briefing] run failed: {e}", file=sys.stderr)
        traceback.print_exc()
        return 1

    print(
        f"[daily_briefing] done. sent={result.get('sent')} "
        f"filtered={len(result.get('filtered_items', []))} "
        f"rejected={len(result.get('rejected_items', []))} "
        f"persisted={result.get('persisted_path', '')}"
    )
    # Surface state.errors trail (informational; SendBriefingNode logs
    # which path it took here — "MCP path succeeded" or
    # "MCP path failed (...); falling back to HTTPS direct POST" then
    # "HTTPS fallback succeeded"). Captured in launchd briefing-stdout.log
    # so production runs are self-documenting; the persisted JSON
    # snapshot is taken before SendBriefingNode runs and so cannot
    # carry the send-path info.
    for line in result.get("errors", []) or []:
        print(f"[daily_briefing] {line}")
    return 0 if result.get("sent") else 1


if __name__ == "__main__":
    sys.exit(main())

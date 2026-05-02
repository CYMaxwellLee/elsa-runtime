"""Claude Code subprocess transport for LLM nodes.

Each LLMNode calls ``call_claude(...)`` to spawn a fresh ``claude`` CLI
in non-interactive mode (``--print``) with:

- ``--json-schema``: forces structured JSON output matching Pydantic
  output_schema; this implements Compiled AI's bounded-output sandwich.
- ``--allowedTools``: limits which MCPs the worker can touch (e.g.
  data-gathering workers cannot send messages; SendBriefingNode can
  ONLY send).
- ``--dangerously-skip-permissions``: required because launchd-spawned
  processes have no interactive Claude Code permission UI. Risk is
  bounded by the ``--allowedTools`` whitelist plus prompt design.
- ``--max-budget-usd``: per-call cap.

The transport returns parsed JSON (dict) on success; raises on subprocess
failure, timeout, or unparseable output.

Per main user direction (5/2): unlimited Claude Code usage available;
subprocess-per-node is acceptable. Phase 2+ may swap to Anthropic API
direct or shared session, but that requires v3.50 unified-session
revision and is out of scope for v3.51-A.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any


# Resolved at import time so launchd / cron environments without PATH
# still work. Falls back to "claude" (PATH lookup) if absolute path not
# found.
def _resolve_claude_bin() -> str:
    candidates = [
        os.path.expanduser("~/.local/bin/claude"),
        "/usr/local/bin/claude",
        "/opt/homebrew/bin/claude",
    ]
    for p in candidates:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    found = shutil.which("claude")
    return found if found else "claude"


CLAUDE_BIN = _resolve_claude_bin()


@dataclass
class ClaudeWorkerError(RuntimeError):
    msg: str
    stderr: str = ""
    stdout: str = ""

    def __str__(self) -> str:
        s = self.msg
        if self.stderr:
            s += f"\nstderr: {self.stderr[:1000]}"
        if self.stdout:
            s += f"\nstdout: {self.stdout[:1000]}"
        return s


# Default cwd for spawned claude. The elsa-workspace project is where
# the telegram plugin is enabled, MCP permissions are configured, and
# Elsa's persona docs live. All daily_briefing subprocess calls inherit
# this cwd unless overridden.
DEFAULT_CWD = os.path.expanduser("~/Projects/elsa-workspace")


def call_claude(
    prompt: str,
    *,
    json_schema: dict | None = None,
    allowed_tools: list[str] | None = None,
    timeout: int = 600,
    max_budget_usd: float | None = None,
    extra_args: list[str] | None = None,
    cwd: str | None = None,
) -> Any:
    """Invoke claude CLI non-interactively. Returns parsed JSON if
    json_schema given, else raw string output.

    Args:
        prompt: full user prompt (sent via stdin to avoid arg-length issues)
        json_schema: JSON Schema dict; if set, ``--json-schema`` is passed
            and output is parsed as JSON
        allowed_tools: whitelist of tool names (e.g.
            ``["mcp__plugin_telegram_telegram__reply"]``); if None, no
            ``--allowedTools`` flag is passed (claude allows defaults)
        timeout: subprocess timeout in seconds
        max_budget_usd: per-call dollar cap
        extra_args: additional CLI args
        cwd: working directory for the subprocess
    """
    args: list[str] = [CLAUDE_BIN, "--print"]

    # Permissions: launchd has no interactive UI, so we must skip prompts.
    # Risk bounded by allowed_tools whitelist + prompt design.
    args.append("--dangerously-skip-permissions")

    # Budget cap optional. Per main user 5/2 directive: subscription
    # has unlimited Claude Code usage; per-call cap is a self-imposed
    # false-economy that fails legitimate work mid-pipeline.
    if max_budget_usd is not None:
        args += ["--max-budget-usd", str(max_budget_usd)]

    if allowed_tools:
        # claude CLI accepts comma- or space-separated; we use comma.
        args += ["--allowedTools", ",".join(allowed_tools)]

    # NOTE: claude CLI 2.1.126 --json-schema interacts poorly with
    # --allowedTools (returns empty output). Workaround: skip the flag
    # and rely on prompt-level "Output ONLY JSON" + downstream
    # _parse_json_output extraction (handles markdown fences, mixed
    # narrative+JSON, etc.). Re-enable when CLI fix lands.
    if json_schema is not None and os.environ.get("ELSA_USE_JSON_SCHEMA"):
        args += ["--json-schema", json.dumps(json_schema)]

    if extra_args:
        args += list(extra_args)

    # Pass prompt via stdin to avoid arg-length blowup.
    effective_cwd = cwd if cwd is not None else DEFAULT_CWD
    try:
        proc = subprocess.run(
            args,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=effective_cwd,
            env=_subprocess_env(),
        )
    except subprocess.TimeoutExpired as e:
        raise ClaudeWorkerError(
            msg=f"claude subprocess timed out after {timeout}s",
            stderr=(e.stderr or b"").decode("utf-8", errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or ""),
            stdout=(e.stdout or b"").decode("utf-8", errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or ""),
        ) from e

    if proc.returncode != 0:
        raise ClaudeWorkerError(
            msg=f"claude exited {proc.returncode}",
            stderr=proc.stderr,
            stdout=proc.stdout,
        )

    out = proc.stdout.strip()
    if json_schema is None:
        return out

    return _parse_json_output(out, raw_stderr=proc.stderr)


def _subprocess_env() -> dict[str, str]:
    """Inherit env but ensure HOME and PATH are set for launchd.

    launchd plists usually pass HOME explicitly via EnvironmentVariables.
    We extend PATH to include claude bin's parent directory so any
    further subprocess (e.g. mcp servers) resolves.
    """
    env = dict(os.environ)
    env.setdefault("HOME", os.path.expanduser("~"))
    extra_paths = [
        os.path.expanduser("~/.local/bin"),
        "/usr/local/bin",
        "/opt/homebrew/bin",
    ]
    cur_path = env.get("PATH", "")
    parts = cur_path.split(":") if cur_path else []
    for p in extra_paths:
        if p not in parts:
            parts.insert(0, p)
    env["PATH"] = ":".join(parts)
    return env


_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*\n(.*?)\n```", re.DOTALL)


def _parse_json_output(out: str, raw_stderr: str = "") -> Any:
    """Parse claude --json-schema output.

    With --json-schema set, claude is supposed to emit valid JSON
    directly. In practice it sometimes wraps the JSON in a markdown
    fence; strip that before parse.
    """
    if not out:
        raise ClaudeWorkerError(
            msg="claude returned empty output", stderr=raw_stderr
        )

    # Try direct parse first.
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        pass

    # Strip a markdown fence if present.
    m = _JSON_BLOCK_RE.search(out)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError as e:
            raise ClaudeWorkerError(
                msg=f"JSON parse failed in fenced block: {e}",
                stdout=out,
                stderr=raw_stderr,
            ) from e

    # Try to find first {...} or [...] balanced block.
    for opener, closer in [("{", "}"), ("[", "]")]:
        start = out.find(opener)
        end = out.rfind(closer)
        if start >= 0 and end > start:
            chunk = out[start : end + 1]
            try:
                return json.loads(chunk)
            except json.JSONDecodeError:
                continue

    raise ClaudeWorkerError(
        msg="claude output is not valid JSON",
        stdout=out,
        stderr=raw_stderr,
    )

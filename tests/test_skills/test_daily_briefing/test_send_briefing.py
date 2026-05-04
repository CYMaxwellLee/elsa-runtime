"""SendBriefingNode hybrid path tests.

Two production failures (5/3, 5/4) hit the same root cause: claude
--print subprocess could not load the telegram MCP plugin tool. The
hybrid path now tries MCP first, then falls back to a direct HTTPS
POST against the Telegram bot API. These tests pin both branches and
the helper utilities.
"""
from __future__ import annotations

import os
from unittest import mock

import pytest

from elsa_runtime.module import NodeExecutionError
from elsa_runtime.skills.daily_briefing import nodes
from elsa_runtime.skills.daily_briefing.nodes import (
    SendBriefingNode,
    TELEGRAM_DEFAULT_CHAT_ID,
    TELEGRAM_TOKEN_ENV_KEYS,
)
from elsa_runtime.skills.daily_briefing.state import BriefingState


# ─── Token resolution ───


def test_token_resolved_from_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token-123")
    assert SendBriefingNode._resolve_bot_token() == "env-token-123"


def test_token_resolved_from_alternate_env_keys(monkeypatch):
    for key in TELEGRAM_TOKEN_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("BOT_TOKEN", "alt-key-token")
    assert SendBriefingNode._resolve_bot_token() == "alt-key-token"


def test_token_resolved_from_env_file(monkeypatch, tmp_path):
    for key in TELEGRAM_TOKEN_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# telegram\n"
        "TELEGRAM_BOT_TOKEN=\"file-token-xyz\"\n"
        "OTHER=foo\n"
    )
    monkeypatch.setattr(nodes, "TELEGRAM_TOKEN_ENV_FILE", env_file)
    assert SendBriefingNode._resolve_bot_token() == "file-token-xyz"


def test_token_returns_none_when_missing(monkeypatch, tmp_path):
    for key in TELEGRAM_TOKEN_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(nodes, "TELEGRAM_TOKEN_ENV_FILE", tmp_path / "missing.env")
    assert SendBriefingNode._resolve_bot_token() is None


# ─── Splitter ───


def test_split_short_text_unchanged():
    assert SendBriefingNode._split_for_telegram("hello world") == ["hello world"]


def test_split_long_text_at_paragraph_boundary():
    para = "x" * 1000
    text = para + "\n\n" + para + "\n\n" + para + "\n\n" + para
    chunks = SendBriefingNode._split_for_telegram(text, limit=2200)
    assert len(chunks) >= 2
    assert all(len(c) <= 2200 for c in chunks)
    assert "".join(chunks).replace("\n\n", "").count("x") == len(text.replace("\n\n", ""))


def test_split_falls_through_to_line_boundary_then_space_then_hard():
    no_paragraph = ("abcdefghij " * 600).strip()
    chunks = SendBriefingNode._split_for_telegram(no_paragraph, limit=1000)
    assert all(len(c) <= 1000 for c in chunks)
    assert sum(len(c) for c in chunks) >= len(no_paragraph) - len(chunks)


def test_split_no_breaks_anywhere_uses_hard_cut():
    blob = "a" * 5000
    chunks = SendBriefingNode._split_for_telegram(blob, limit=1000)
    assert len(chunks) == 5
    assert all(len(c) == 1000 for c in chunks)


# ─── HTTPS path ───


def _httpx_resp(status: int, json_body: dict | None = None, text: str = ""):
    m = mock.MagicMock()
    m.status_code = status
    m.headers = {"content-type": "application/json"} if json_body is not None else {}
    m.text = text or (str(json_body) if json_body is not None else "")
    m.json = mock.MagicMock(return_value=json_body or {})
    return m


def test_https_send_posts_single_chunk_with_correct_payload(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok-abc")
    monkeypatch.delenv("ELSA_TELEGRAM_CHAT_ID", raising=False)
    posts = []

    def fake_post(url, data, timeout):
        posts.append({"url": url, "data": data, "timeout": timeout})
        return _httpx_resp(200, json_body={"ok": True, "result": {"message_id": 1}})

    import httpx
    monkeypatch.setattr(httpx, "post", fake_post)

    SendBriefingNode()._send_via_https("hello briefing")

    assert len(posts) == 1
    assert posts[0]["data"]["chat_id"] == TELEGRAM_DEFAULT_CHAT_ID
    assert posts[0]["data"]["text"] == "hello briefing"
    assert "tok-abc" in posts[0]["url"]


def test_https_send_chunks_long_briefing(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok-xyz")
    posts = []

    def fake_post(url, data, timeout):
        posts.append(data)
        return _httpx_resp(200, json_body={"ok": True})

    import httpx
    monkeypatch.setattr(httpx, "post", fake_post)

    long_text = "block\n\n" * 1500  # >> 4000 chars
    SendBriefingNode()._send_via_https(long_text)

    assert len(posts) >= 2
    assert all(len(p["text"]) <= 4000 for p in posts)


def test_https_send_raises_on_http_error(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    import httpx
    monkeypatch.setattr(
        httpx, "post", lambda *a, **kw: _httpx_resp(403, text="Forbidden")
    )
    with pytest.raises(RuntimeError, match="HTTP 403"):
        SendBriefingNode()._send_via_https("text")


def test_https_send_raises_on_telegram_not_ok(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    import httpx
    monkeypatch.setattr(
        httpx, "post",
        lambda *a, **kw: _httpx_resp(200, json_body={"ok": False, "description": "bad chat"}),
    )
    with pytest.raises(RuntimeError, match="not-ok"):
        SendBriefingNode()._send_via_https("text")


def test_https_send_raises_when_token_missing(monkeypatch, tmp_path):
    for key in TELEGRAM_TOKEN_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(nodes, "TELEGRAM_TOKEN_ENV_FILE", tmp_path / "no.env")
    with pytest.raises(RuntimeError, match="bot token not in env"):
        SendBriefingNode()._send_via_https("text")


def test_https_send_uses_custom_chat_id_from_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("ELSA_TELEGRAM_CHAT_ID", "999999999")
    captured = {}

    def fake_post(url, data, timeout):
        captured.update(data)
        return _httpx_resp(200, json_body={"ok": True})

    import httpx
    monkeypatch.setattr(httpx, "post", fake_post)

    SendBriefingNode()._send_via_https("hi")
    assert captured["chat_id"] == "999999999"


# ─── Hybrid orchestration ───


def test_run_uses_mcp_when_it_succeeds(monkeypatch):
    monkeypatch.setattr(
        SendBriefingNode,
        "_send_via_mcp",
        lambda self, text: {"sent": True, "note": "ok via mcp"},
    )
    https_called = mock.MagicMock()
    monkeypatch.setattr(SendBriefingNode, "_send_via_https", https_called)

    state = BriefingState(briefing_text="hello", dry_run=False)
    out = SendBriefingNode().run(state)
    assert out.sent is True
    https_called.assert_not_called()
    assert any("MCP path succeeded" in e for e in out.errors)


def test_run_falls_back_to_https_when_mcp_returns_failure(monkeypatch):
    monkeypatch.setattr(
        SendBriefingNode,
        "_send_via_mcp",
        lambda self, text: {"sent": False, "note": "tool not loaded"},
    )
    https_called = mock.MagicMock()
    monkeypatch.setattr(SendBriefingNode, "_send_via_https", https_called)

    state = BriefingState(briefing_text="hello", dry_run=False)
    out = SendBriefingNode().run(state)
    assert out.sent is True
    https_called.assert_called_once_with("hello")
    assert any("MCP path failed" in e and "tool not loaded" in e for e in out.errors)
    assert any("HTTPS fallback succeeded" in e for e in out.errors)


def test_run_falls_back_to_https_when_mcp_raises(monkeypatch):
    def boom(self, text):
        raise RuntimeError("subprocess died")
    monkeypatch.setattr(SendBriefingNode, "_send_via_mcp", boom)
    https_called = mock.MagicMock()
    monkeypatch.setattr(SendBriefingNode, "_send_via_https", https_called)

    state = BriefingState(briefing_text="hello", dry_run=False)
    out = SendBriefingNode().run(state)
    assert out.sent is True
    https_called.assert_called_once_with("hello")
    assert any("subprocess died" in e for e in out.errors)


def test_run_raises_when_both_paths_fail(monkeypatch):
    monkeypatch.setattr(
        SendBriefingNode,
        "_send_via_mcp",
        lambda self, text: {"sent": False, "note": "tool not loaded"},
    )

    def https_boom(self, text):
        raise RuntimeError("network unreachable")
    monkeypatch.setattr(SendBriefingNode, "_send_via_https", https_boom)

    state = BriefingState(briefing_text="hello", dry_run=False)
    with pytest.raises(NodeExecutionError, match="BOTH paths failed"):
        SendBriefingNode().run(state)


def test_run_dry_run_short_circuits(monkeypatch, capsys):
    mcp = mock.MagicMock()
    https = mock.MagicMock()
    monkeypatch.setattr(SendBriefingNode, "_send_via_mcp", mcp)
    monkeypatch.setattr(SendBriefingNode, "_send_via_https", https)

    state = BriefingState(briefing_text="dry hello", dry_run=True)
    out = SendBriefingNode().run(state)
    assert out.sent is True
    mcp.assert_not_called()
    https.assert_not_called()
    captured = capsys.readouterr()
    assert "DRY-RUN" in captured.out
    assert "dry hello" in captured.out


def test_run_raises_on_empty_briefing_text():
    state = BriefingState(briefing_text="   ", dry_run=False)
    with pytest.raises(NodeExecutionError, match="briefing_text is empty"):
        SendBriefingNode().run(state)

"""Tests for GmailComposer (Gmail thread-aware draft creation).

The Google API service is mocked — we don't hit the network. These tests
verify our MIME assembly and threadId/header injection logic, which is
the value-add over the Anthropic-managed connector.
"""

from __future__ import annotations

import base64
from email import message_from_bytes
from email.message import Message
from unittest.mock import MagicMock


from elsa_runtime.tools.gmail.compose import GmailComposer


def _make_mock_service(message_headers=None):
    """Build a mock Gmail service whose drafts().create() captures the body."""
    service = MagicMock()

    # users().messages().get() returns a fake message with headers
    if message_headers is None:
        message_headers = {}
    headers_list = [{"name": k, "value": v} for k, v in message_headers.items()]
    message_get = MagicMock()
    message_get.execute.return_value = {"payload": {"headers": headers_list}}
    service.users.return_value.messages.return_value.get.return_value = message_get

    # users().drafts().create() — record body, return fake draft
    drafts_create = MagicMock()
    drafts_create.execute.return_value = {
        "id": "draft-fake-id",
        "message": {"id": "msg-fake-id", "threadId": "thread-from-arg"},
    }
    service.users.return_value.drafts.return_value.create.return_value = (
        drafts_create
    )
    service._captured_body = None

    def capture(userId=None, body=None, **kwargs):
        service._captured_body = body
        return drafts_create

    service.users.return_value.drafts.return_value.create.side_effect = capture
    return service


def _decode_raw(body: dict) -> Message:
    """Pull the MIME message out of a draft body's `raw` field."""
    raw = body["message"]["raw"]
    raw_bytes = base64.urlsafe_b64decode(raw)
    return message_from_bytes(raw_bytes)


# ─── Tests ───────────────────────────────────────────────────────────


class TestThreadIdInjection:
    def test_threadId_passed_to_api(self):
        service = _make_mock_service()
        composer = GmailComposer(service)
        composer.create_draft_reply(
            thread_id="abc123",
            to=["foo@example.com"],
            body="hi",
            subject="Re: test",
        )
        body = service._captured_body
        assert body["message"]["threadId"] == "abc123"

    def test_returns_draft_object(self):
        service = _make_mock_service()
        composer = GmailComposer(service)
        result = composer.create_draft_reply(
            thread_id="t1", to=["x@y.com"], body="hi", subject="hi"
        )
        assert result["id"] == "draft-fake-id"


class TestMimeAssembly:
    def test_to_header(self):
        service = _make_mock_service()
        composer = GmailComposer(service)
        composer.create_draft_reply(
            thread_id="t1",
            to=["alice@example.com", "bob@example.com"],
            body="hi",
            subject="re: x",
        )
        msg = _decode_raw(service._captured_body)
        assert "alice@example.com" in msg["To"]
        assert "bob@example.com" in msg["To"]

    def test_to_string_form(self):
        service = _make_mock_service()
        composer = GmailComposer(service)
        composer.create_draft_reply(
            thread_id="t1",
            to="single@example.com",
            body="hi",
            subject="x",
        )
        msg = _decode_raw(service._captured_body)
        assert msg["To"] == "single@example.com"

    def test_cc_and_bcc(self):
        service = _make_mock_service()
        composer = GmailComposer(service)
        composer.create_draft_reply(
            thread_id="t1",
            to=["x@y.com"],
            body="hi",
            subject="x",
            cc=["c@d.com"],
            bcc=["b@d.com"],
        )
        msg = _decode_raw(service._captured_body)
        assert msg["Cc"] == "c@d.com"
        assert msg["Bcc"] == "b@d.com"

    def test_subject(self):
        service = _make_mock_service()
        composer = GmailComposer(service)
        composer.create_draft_reply(
            thread_id="t1", to=["x@y.com"], body="hi", subject="Re: meeting"
        )
        msg = _decode_raw(service._captured_body)
        assert msg["Subject"] == "Re: meeting"

    def test_utf8_body(self):
        service = _make_mock_service()
        composer = GmailComposer(service)
        body = "主人您好，這是中文回信"
        composer.create_draft_reply(
            thread_id="t1", to=["x@y.com"], body=body, subject="x"
        )
        msg = _decode_raw(service._captured_body)
        decoded_body = msg.get_payload(decode=True).decode("utf-8")
        assert body in decoded_body


class TestThreadingHeaders:
    def test_in_reply_to_set(self):
        service = _make_mock_service()
        composer = GmailComposer(service)
        composer.create_draft_reply(
            thread_id="t1",
            to=["x@y.com"],
            body="hi",
            subject="Re: x",
            in_reply_to_message_id="<src-msg@gmail.com>",
        )
        msg = _decode_raw(service._captured_body)
        assert msg["In-Reply-To"] == "<src-msg@gmail.com>"

    def test_references_extends_chain(self):
        service = _make_mock_service(
            message_headers={
                "Message-ID": "<src-msg@gmail.com>",
                "References": "<earlier@gmail.com> <middle@gmail.com>",
            }
        )
        composer = GmailComposer(service)
        composer.create_draft_reply(
            thread_id="t1",
            to=["x@y.com"],
            body="hi",
            subject="Re: x",
            in_reply_to_message_id="<src-msg@gmail.com>",
        )
        msg = _decode_raw(service._captured_body)
        # References should include earlier chain + the message-id we're replying to
        refs = msg["References"]
        assert "<earlier@gmail.com>" in refs
        assert "<middle@gmail.com>" in refs
        assert "<src-msg@gmail.com>" in refs

    def test_references_falls_back_when_fetch_fails(self):
        # Service that explodes on .get() — composer should swallow and fall back
        service = _make_mock_service()
        service.users.return_value.messages.return_value.get.side_effect = (
            RuntimeError("boom")
        )
        composer = GmailComposer(service)
        composer.create_draft_reply(
            thread_id="t1",
            to=["x@y.com"],
            body="hi",
            subject="Re: x",
            in_reply_to_message_id="<src-msg@gmail.com>",
        )
        msg = _decode_raw(service._captured_body)
        # Fallback: References = the in_reply_to_message_id alone
        assert msg["References"] == "<src-msg@gmail.com>"


class TestSubjectAutoDerivation:
    def test_derives_subject_from_source(self):
        service = _make_mock_service(
            message_headers={"Subject": "Project Sync", "Message-ID": "<x@y>"}
        )
        composer = GmailComposer(service)
        composer.create_draft_reply(
            thread_id="t1",
            to=["x@y.com"],
            body="hi",
            in_reply_to_message_id="<x@y>",
        )
        msg = _decode_raw(service._captured_body)
        assert msg["Subject"] == "Re: Project Sync"

    def test_does_not_double_re_prefix(self):
        service = _make_mock_service(
            message_headers={
                "Subject": "Re: already a reply",
                "Message-ID": "<x@y>",
            }
        )
        composer = GmailComposer(service)
        composer.create_draft_reply(
            thread_id="t1",
            to=["x@y.com"],
            body="hi",
            in_reply_to_message_id="<x@y>",
        )
        msg = _decode_raw(service._captured_body)
        assert msg["Subject"] == "Re: already a reply"

    def test_no_subject_no_source_falls_back(self):
        service = _make_mock_service()
        composer = GmailComposer(service)
        composer.create_draft_reply(
            thread_id="t1", to=["x@y.com"], body="hi"
        )
        msg = _decode_raw(service._captured_body)
        assert msg["Subject"] == "(no subject)"


class TestSecurityBoundary:
    """The composer module must not expose any send capability."""

    def test_no_send_method(self):
        composer = GmailComposer(_make_mock_service())
        # Verify no method on GmailComposer can send mail
        send_like = [
            m
            for m in dir(composer)
            if not m.startswith("_") and "send" in m.lower()
        ]
        assert send_like == [], (
            f"GmailComposer must not expose send-like methods, found: {send_like}"
        )

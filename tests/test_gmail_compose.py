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

import pytest

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


# ─────────────────────────────────────────────────────────────────────
# v3.51-A.x (2026-05-05): html_body + attachments support
# ─────────────────────────────────────────────────────────────────────


class TestHtmlBody:
    """When html_body is set, message becomes multipart/alternative
    so mail clients render HTML and bypass plain-text line folding."""

    def test_no_html_body_stays_plain_text(self):
        service = _make_mock_service()
        GmailComposer(service).create_draft_reply(
            thread_id="t1", to=["a@b.com"], body="hello",
        )
        msg = _decode_raw(service._captured_body)
        assert msg.get_content_type() == "text/plain"

    def test_html_body_only_yields_alternative_multipart(self):
        service = _make_mock_service()
        GmailComposer(service).create_draft_reply(
            thread_id="t1", to=["a@b.com"],
            body="plain hello", html_body="<p>html hello</p>",
        )
        msg = _decode_raw(service._captured_body)
        assert msg.is_multipart()
        assert msg.get_content_type() == "multipart/alternative"
        parts = msg.get_payload()
        types = sorted(p.get_content_type() for p in parts)
        assert types == ["text/html", "text/plain"]

    def test_html_body_preserves_unicode(self):
        service = _make_mock_service()
        GmailComposer(service).create_draft_reply(
            thread_id="t1", to=["a@b.com"],
            body="主人您好", html_body="<p>主人您好</p>",
        )
        msg = _decode_raw(service._captured_body)
        for part in msg.get_payload():
            content = part.get_payload(decode=True).decode("utf-8")
            assert "主人您好" in content


class TestAttachments:
    """attachments=[<paths>] base64-encodes files into multipart/mixed."""

    def test_no_attachments_no_multipart(self, tmp_path):
        service = _make_mock_service()
        GmailComposer(service).create_draft_reply(
            thread_id="t1", to=["a@b.com"], body="hello",
            attachments=None,
        )
        msg = _decode_raw(service._captured_body)
        assert not msg.is_multipart()

    def test_empty_attachment_list_no_multipart(self, tmp_path):
        service = _make_mock_service()
        GmailComposer(service).create_draft_reply(
            thread_id="t1", to=["a@b.com"], body="hello",
            attachments=[],
        )
        msg = _decode_raw(service._captured_body)
        assert not msg.is_multipart()

    def test_single_pdf_attachment(self, tmp_path):
        pdf = tmp_path / "report.pdf"
        pdf.write_bytes(b"%PDF-1.4\nfake pdf bytes")
        service = _make_mock_service()
        GmailComposer(service).create_draft_reply(
            thread_id="t1", to=["a@b.com"], body="see attached",
            attachments=[str(pdf)],
        )
        msg = _decode_raw(service._captured_body)
        assert msg.is_multipart()
        assert msg.get_content_type() == "multipart/mixed"
        parts = msg.get_payload()
        # Should be: 1 text/plain body + 1 attachment
        assert len(parts) == 2
        att = parts[1]
        assert att.get_filename() == "report.pdf"
        assert att.get_content_type() == "application/pdf"
        assert att.get("Content-Disposition", "").startswith("attachment")
        # Payload base64-decodes back to original bytes
        assert att.get_payload(decode=True) == b"%PDF-1.4\nfake pdf bytes"

    def test_multiple_mixed_type_attachments(self, tmp_path):
        pdf = tmp_path / "a.pdf"
        png = tmp_path / "b.png"
        txt = tmp_path / "c.txt"
        pdf.write_bytes(b"PDF content")
        png.write_bytes(b"\x89PNG\r\n\x1a\n fake png")
        txt.write_text("plain text body of attachment", encoding="utf-8")

        service = _make_mock_service()
        GmailComposer(service).create_draft_reply(
            thread_id="t1", to=["a@b.com"], body="see attached",
            attachments=[str(pdf), str(png), str(txt)],
        )
        msg = _decode_raw(service._captured_body)
        parts = msg.get_payload()
        assert len(parts) == 4  # body + 3 attachments
        att_types = [p.get_content_type() for p in parts[1:]]
        att_names = [p.get_filename() for p in parts[1:]]
        assert att_types == ["application/pdf", "image/png", "text/plain"]
        assert att_names == ["a.pdf", "b.png", "c.txt"]

    def test_unknown_extension_falls_back_to_octet_stream(self, tmp_path):
        odd = tmp_path / "thing.fakeextxyz"
        odd.write_bytes(b"random bytes")
        service = _make_mock_service()
        GmailComposer(service).create_draft_reply(
            thread_id="t1", to=["a@b.com"], body="see attached",
            attachments=[str(odd)],
        )
        att = _decode_raw(service._captured_body).get_payload()[1]
        assert att.get_content_type() == "application/octet-stream"

    def test_missing_file_raises_filenotfound(self, tmp_path):
        service = _make_mock_service()
        with pytest.raises(FileNotFoundError, match="attachment not found"):
            GmailComposer(service).create_draft_reply(
                thread_id="t1", to=["a@b.com"], body="hi",
                attachments=[str(tmp_path / "ghost.pdf")],
            )

    def test_directory_path_rejected_as_not_a_file(self, tmp_path):
        service = _make_mock_service()
        with pytest.raises(FileNotFoundError, match="not a regular file"):
            GmailComposer(service).create_draft_reply(
                thread_id="t1", to=["a@b.com"], body="hi",
                attachments=[str(tmp_path)],
            )

    def test_oversize_attachment_rejected(self, tmp_path, monkeypatch):
        # Use monkeypatched smaller cap so we don't actually allocate 35 MiB.
        from elsa_runtime.tools.gmail import compose as compose_mod
        monkeypatch.setattr(compose_mod, "ATTACHMENT_MAX_BYTES", 100)
        big = tmp_path / "big.bin"
        big.write_bytes(b"x" * 200)
        service = _make_mock_service()
        with pytest.raises(ValueError, match="exceeds"):
            GmailComposer(service).create_draft_reply(
                thread_id="t1", to=["a@b.com"], body="hi",
                attachments=[str(big)],
            )

    def test_real_35mb_constant_value(self):
        from elsa_runtime.tools.gmail.compose import ATTACHMENT_MAX_BYTES
        # Pin the documented limit (Gmail OAuth API base64-encoded cap).
        assert ATTACHMENT_MAX_BYTES == 35 * 1024 * 1024


class TestHtmlBodyAndAttachmentsCombined:
    """When both html_body AND attachments are set, the layout is
    multipart/mixed → multipart/alternative → text/plain + text/html,
    plus attachment parts at the outer level."""

    def test_full_layout(self, tmp_path):
        pdf = tmp_path / "doc.pdf"
        pdf.write_bytes(b"PDF")

        service = _make_mock_service()
        GmailComposer(service).create_draft_reply(
            thread_id="t1", to=["a@b.com"], body="plain",
            html_body="<p>html</p>",
            attachments=[str(pdf)],
        )
        outer = _decode_raw(service._captured_body)
        assert outer.get_content_type() == "multipart/mixed"
        outer_parts = outer.get_payload()
        assert len(outer_parts) == 2  # alternative + 1 attachment

        alt = outer_parts[0]
        assert alt.get_content_type() == "multipart/alternative"
        alt_types = sorted(p.get_content_type() for p in alt.get_payload())
        assert alt_types == ["text/html", "text/plain"]

        att = outer_parts[1]
        assert att.get_content_type() == "application/pdf"
        assert att.get_filename() == "doc.pdf"


class TestExpandUserPath:
    """Attachment paths support ~ expansion."""

    def test_tilde_in_path_expanded(self, tmp_path, monkeypatch):
        # Pretend home = tmp_path, then attach using "~/file.txt"
        monkeypatch.setenv("HOME", str(tmp_path))
        f = tmp_path / "file.txt"
        f.write_text("hi", encoding="utf-8")
        service = _make_mock_service()
        GmailComposer(service).create_draft_reply(
            thread_id="t1", to=["a@b.com"], body="hi",
            attachments=["~/file.txt"],
        )
        att = _decode_raw(service._captured_body).get_payload()[1]
        assert att.get_filename() == "file.txt"

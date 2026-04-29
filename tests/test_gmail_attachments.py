"""Tests for Gmail attachment list + download."""

from __future__ import annotations

import base64
from unittest.mock import MagicMock

import pytest

from elsa_runtime.tools.gmail.client import GmailClient


def _payload_with_attachment(filename: str, mime: str, att_id: str, size: int) -> dict:
    """Build a Gmail API payload that contains an attachment part."""
    return {
        "mimeType": "multipart/mixed",
        "parts": [
            {
                "mimeType": "text/plain",
                "body": {"data": base64.urlsafe_b64encode(b"body").decode()},
            },
            {
                "mimeType": mime,
                "filename": filename,
                "body": {"attachmentId": att_id, "size": size},
            },
        ],
    }


def _make_service(message_payload: dict, attachment_data: bytes = b""):
    """Mock service: messages.get returns message_payload, attachments.get returns attachment_data."""
    service = MagicMock()
    msg_get = MagicMock()
    msg_get.execute.return_value = {
        "id": "msg1",
        "threadId": "thr1",
        "payload": message_payload,
    }
    service.users.return_value.messages.return_value.get.return_value = msg_get

    att_get = MagicMock()
    att_get.execute.return_value = {
        "data": base64.urlsafe_b64encode(attachment_data).decode(),
        "size": len(attachment_data),
    }
    service.users.return_value.messages.return_value.attachments.return_value.get.return_value = att_get
    return service


class TestListAttachments:
    def test_single_attachment(self):
        payload = _payload_with_attachment(
            "report.pdf", "application/pdf", "att-abc", 12345
        )
        service = _make_service(payload)
        client = GmailClient(service)
        atts = client.list_attachments("msg1")
        assert len(atts) == 1
        assert atts[0]["filename"] == "report.pdf"
        assert atts[0]["mime_type"] == "application/pdf"
        assert atts[0]["attachment_id"] == "att-abc"
        assert atts[0]["size_bytes"] == 12345

    def test_no_attachments(self):
        payload = {
            "mimeType": "text/plain",
            "body": {"data": base64.urlsafe_b64encode(b"hi").decode()},
        }
        service = _make_service(payload)
        client = GmailClient(service)
        atts = client.list_attachments("msg1")
        assert atts == []

    def test_multiple_attachments(self):
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": base64.urlsafe_b64encode(b"body").decode()},
                },
                {
                    "mimeType": "application/pdf",
                    "filename": "a.pdf",
                    "body": {"attachmentId": "att1", "size": 100},
                },
                {
                    "mimeType": "image/png",
                    "filename": "b.png",
                    "body": {"attachmentId": "att2", "size": 200},
                },
            ],
        }
        service = _make_service(payload)
        client = GmailClient(service)
        atts = client.list_attachments("msg1")
        assert len(atts) == 2
        assert {a["filename"] for a in atts} == {"a.pdf", "b.png"}

    def test_nested_multipart(self):
        """Some emails wrap attachments inside multipart/alternative."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {
                            "mimeType": "text/plain",
                            "body": {
                                "data": base64.urlsafe_b64encode(b"hi").decode()
                            },
                        },
                    ],
                },
                {
                    "mimeType": "application/pdf",
                    "filename": "deep.pdf",
                    "body": {"attachmentId": "deep-att", "size": 999},
                },
            ],
        }
        service = _make_service(payload)
        atts = GmailClient(service).list_attachments("msg1")
        assert len(atts) == 1
        assert atts[0]["filename"] == "deep.pdf"

    def test_attachment_without_filename_not_listed(self):
        """An attachment part without filename (rare, e.g. inline image)
        should not show up in the user-facing list."""
        payload = {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "image/png",
                    "filename": "",
                    "body": {"attachmentId": "inline-1", "size": 50},
                },
            ],
        }
        atts = GmailClient(_make_service(payload)).list_attachments("msg1")
        assert atts == []


class TestDownloadAttachment:
    def test_writes_bytes_to_disk(self, tmp_path):
        content = b"PDF-1.4 fake binary content"
        payload = _payload_with_attachment(
            "report.pdf", "application/pdf", "att-abc", len(content)
        )
        service = _make_service(payload, attachment_data=content)
        client = GmailClient(service)

        save_dir = tmp_path / "msg1"
        path = client.download_attachment(
            message_id="msg1",
            attachment_id="att-abc",
            save_dir=save_dir,
            filename="report.pdf",
        )
        assert path == save_dir / "report.pdf"
        assert path.read_bytes() == content

    def test_creates_save_dir(self, tmp_path):
        content = b"data"
        service = _make_service({}, attachment_data=content)
        client = GmailClient(service)
        nested = tmp_path / "deeply" / "nested" / "msg1"
        path = client.download_attachment("msg1", "att-x", nested, "x.bin")
        assert path.parent == nested
        assert path.exists()

    def test_raises_when_no_data(self, tmp_path):
        service = MagicMock()
        att_get = MagicMock()
        att_get.execute.return_value = {"data": "", "size": 0}
        service.users.return_value.messages.return_value.attachments.return_value.get.return_value = att_get
        client = GmailClient(service)
        with pytest.raises(ValueError, match="returned no data"):
            client.download_attachment("msg1", "att-y", tmp_path, "x.bin")

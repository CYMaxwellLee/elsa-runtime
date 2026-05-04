"""Tests for Google Docs reader/composer + Drive reader (mocked services)."""

from __future__ import annotations

from unittest.mock import MagicMock


from elsa_runtime.tools.gdocs.reader import GoogleDocsReader, GoogleDriveReader
from elsa_runtime.tools.gdocs.composer import GoogleDocsComposer


# ── helpers ────────────────────────────────────────────────────────────


def _docs_service(doc_dict: dict, batch_response: dict | None = None) -> MagicMock:
    """Mock docs service. documents().get() returns doc_dict."""
    svc = MagicMock()
    get = MagicMock()
    get.execute.return_value = doc_dict
    svc.documents.return_value.get.return_value = get

    batch = MagicMock()
    batch.execute.return_value = batch_response or {
        "documentId": doc_dict.get("documentId", "doc1"),
        "replies": [{}],
    }
    svc.documents.return_value.batchUpdate.return_value = batch
    return svc


def _drive_service(files_list: list[dict]) -> MagicMock:
    """Mock drive service. files().list() returns dict with 'files'."""
    svc = MagicMock()
    lst = MagicMock()
    lst.execute.return_value = {"files": files_list}
    svc.files.return_value.list.return_value = lst

    get = MagicMock()
    get.execute.return_value = files_list[0] if files_list else {}
    svc.files.return_value.get.return_value = get
    return svc


def _make_paragraph(text: str, named_style: str = "NORMAL_TEXT") -> dict:
    return {
        "paragraph": {
            "elements": [{"textRun": {"content": text}}],
            "paragraphStyle": {"namedStyleType": named_style},
        }
    }


# ── GoogleDocsReader tests ─────────────────────────────────────────────


class TestGoogleDocsReader:
    def test_simple_doc(self):
        doc = {
            "documentId": "doc1",
            "title": "My Paper",
            "revisionId": "rev42",
            "body": {
                "content": [
                    _make_paragraph("Introduction\n", "HEADING_1"),
                    _make_paragraph("This is intro text.\n", "NORMAL_TEXT"),
                    _make_paragraph("Method\n", "HEADING_1"),
                    _make_paragraph("We propose X.\n", "NORMAL_TEXT"),
                ]
            },
        }
        reader = GoogleDocsReader(_docs_service(doc))
        out = reader.read("doc1")
        assert out["id"] == "doc1"
        assert out["title"] == "My Paper"
        assert out["revision_id"] == "rev42"
        assert "Introduction" in out["text"]
        assert "We propose X" in out["text"]
        # Only HEADING_* paragraphs become structural headings
        assert len(out["headings"]) == 2
        assert out["headings"][0]["text"] == "Introduction"
        assert out["headings"][0]["level"] == 1
        assert out["char_count"] == len(out["text"])

    def test_skip_empty_paragraphs(self):
        doc = {
            "title": "T",
            "body": {
                "content": [
                    _make_paragraph("real content\n"),
                    _make_paragraph("   \n"),
                    _make_paragraph(""),
                ]
            },
        }
        reader = GoogleDocsReader(_docs_service(doc))
        out = reader.read("doc1")
        assert "real content" in out["text"]
        # Empty / whitespace paragraphs filtered
        lines = [line for line in out["text"].split("\n") if line.strip()]
        assert len(lines) == 1

    def test_no_body(self):
        doc = {"title": "Empty"}
        reader = GoogleDocsReader(_docs_service(doc))
        out = reader.read("doc1")
        assert out["text"] == ""
        assert out["headings"] == []
        assert out["char_count"] == 0

    def test_heading_level_extraction(self):
        doc = {
            "title": "Levels",
            "body": {
                "content": [
                    _make_paragraph("H1\n", "HEADING_1"),
                    _make_paragraph("H2\n", "HEADING_2"),
                    _make_paragraph("H3\n", "HEADING_3"),
                ]
            },
        }
        out = GoogleDocsReader(_docs_service(doc)).read("doc1")
        levels = [h["level"] for h in out["headings"]]
        assert levels == [1, 2, 3]


# ── GoogleDriveReader tests ────────────────────────────────────────────


class TestGoogleDriveReader:
    def test_search_results_formatted(self):
        files = [
            {
                "id": "f1",
                "name": "NSTC report.docx",
                "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "modifiedTime": "2026-04-29T10:00:00Z",
                "webViewLink": "https://docs.google.com/document/d/f1",
                "owners": [
                    {"displayName": "Lee", "emailAddress": "lee@example.com"}
                ],
                "size": "12345",
            },
        ]
        reader = GoogleDriveReader(_drive_service(files))
        items = reader.search(query="name contains 'NSTC'")
        assert len(items) == 1
        item = items[0]
        assert item["id"] == "f1"
        assert item["name"] == "NSTC report.docx"
        assert item["size_bytes"] == 12345
        assert item["url"].startswith("https://")
        assert "Lee" in item["owners"][0]

    def test_mime_shortcut_translation(self):
        files = []
        svc = _drive_service(files)
        reader = GoogleDriveReader(svc)
        reader.search(mime_type="doc")
        # Inspect what query was passed to Drive API
        call = svc.files.return_value.list.call_args
        q = call.kwargs.get("q", "")
        assert "application/vnd.google-apps.document" in q
        assert "trashed = false" in q

    def test_full_mime_passes_through(self):
        svc = _drive_service([])
        GoogleDriveReader(svc).search(mime_type="application/pdf")
        q = svc.files.return_value.list.call_args.kwargs["q"]
        assert "application/pdf" in q

    def test_get_metadata(self):
        f = {
            "id": "abc",
            "name": "A.doc",
            "mimeType": "application/vnd.google-apps.document",
            "modifiedTime": "2026-04-29T11:00:00Z",
            "webViewLink": "https://x",
            "owners": [{"displayName": "U", "emailAddress": "u@x"}],
            "size": "42",
            "parents": ["folder1"],
        }
        reader = GoogleDriveReader(_drive_service([f]))
        out = reader.get_metadata("abc")
        assert out["id"] == "abc"
        assert out["parents"] == ["folder1"]
        assert out["size_bytes"] == 42


# ── GoogleDocsComposer tests ───────────────────────────────────────────


class TestGoogleDocsComposer:
    def test_append_text_uses_end_index(self):
        # Doc with one paragraph; endIndex 25
        doc = {
            "documentId": "doc1",
            "body": {"content": [{"endIndex": 25}]},
        }
        svc = _docs_service(doc)
        composer = GoogleDocsComposer(svc)
        composer.append_text("doc1", "Hello world", with_newline=False)

        # Verify batchUpdate was called with the correct insertText request
        batch_call = svc.documents.return_value.batchUpdate.call_args
        body = batch_call.kwargs["body"]
        req = body["requests"][0]
        assert "insertText" in req
        # endIndex 25 - 1 = 24 (insert before final newline)
        assert req["insertText"]["location"]["index"] == 24
        assert req["insertText"]["text"] == "Hello world"

    def test_append_with_newline_prepends_newline(self):
        doc = {"body": {"content": [{"endIndex": 5}]}}
        svc = _docs_service(doc)
        GoogleDocsComposer(svc).append_text("doc1", "x", with_newline=True)
        text = svc.documents.return_value.batchUpdate.call_args.kwargs["body"][
            "requests"
        ][0]["insertText"]["text"]
        assert text.startswith("\n")

    def test_append_skips_newline_if_already_starts_with_newline(self):
        doc = {"body": {"content": [{"endIndex": 5}]}}
        svc = _docs_service(doc)
        GoogleDocsComposer(svc).append_text("doc1", "\nalready", with_newline=True)
        text = svc.documents.return_value.batchUpdate.call_args.kwargs["body"][
            "requests"
        ][0]["insertText"]["text"]
        # Should NOT have double newline
        assert not text.startswith("\n\n")

    def test_append_empty_doc_uses_index_1(self):
        doc = {"body": {"content": []}}
        svc = _docs_service(doc)
        GoogleDocsComposer(svc).append_text("doc1", "first", with_newline=False)
        idx = svc.documents.return_value.batchUpdate.call_args.kwargs["body"][
            "requests"
        ][0]["insertText"]["location"]["index"]
        assert idx == 1

    def test_replace_text_calls_replaceAllText(self):
        svc = _docs_service(
            {},
            batch_response={
                "replies": [{"replaceAllText": {"occurrencesChanged": 3}}]
            },
        )
        result = GoogleDocsComposer(svc).replace_text(
            "doc1", "foo", "bar", match_case=True
        )
        req = svc.documents.return_value.batchUpdate.call_args.kwargs["body"][
            "requests"
        ][0]
        assert "replaceAllText" in req
        assert req["replaceAllText"]["containsText"]["text"] == "foo"
        assert req["replaceAllText"]["containsText"]["matchCase"] is True
        assert req["replaceAllText"]["replaceText"] == "bar"
        # Result passes through
        assert result["replies"][0]["replaceAllText"]["occurrencesChanged"] == 3


# ── Read/write boundary check (matches Gmail compose pattern) ──────────


class TestSecurityBoundary:
    def test_reader_has_no_write_methods(self):
        reader = GoogleDocsReader(_docs_service({"body": {"content": []}}))
        write_like = [
            m for m in dir(reader)
            if not m.startswith("_")
            and any(w in m.lower() for w in ("append", "replace", "create", "delete", "write", "update"))
        ]
        assert write_like == [], (
            f"GoogleDocsReader must stay read-only, found write methods: {write_like}"
        )

    def test_drive_reader_has_no_write_methods(self):
        reader = GoogleDriveReader(_drive_service([]))
        write_like = [
            m for m in dir(reader)
            if not m.startswith("_")
            and any(w in m.lower() for w in ("create", "delete", "move", "trash", "share", "write", "update"))
        ]
        assert write_like == [], (
            f"GoogleDriveReader must stay read-only, found write methods: {write_like}"
        )

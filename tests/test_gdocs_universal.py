"""Tests for UniversalDocReader (mocked Drive + Docs services)."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from elsa_runtime.tools.gdocs.universal import (
    MIME_DOC_LEGACY,
    MIME_DOCX,
    MIME_GOOGLE_DOC,
    MIME_MARKDOWN,
    MIME_PDF,
    MIME_TXT,
    UniversalDocReader,
)


# ── helpers ────────────────────────────────────────────────────────────


def _drive_service(meta: dict, blob: bytes = b"") -> MagicMock:
    """Mock drive service. files().get(fields=...).execute() returns meta;
    files().get_media() backs MediaIoBaseDownload reading blob."""
    svc = MagicMock()

    get = MagicMock()
    get.execute.return_value = meta
    svc.files.return_value.get.return_value = get

    # Stash blob for the patched MediaIoBaseDownload.
    svc._blob = blob
    return svc


def _docs_service(doc_dict: dict) -> MagicMock:
    svc = MagicMock()
    get = MagicMock()
    get.execute.return_value = doc_dict
    svc.documents.return_value.get.return_value = get
    return svc


def _patched_download(monkeypatch, blob: bytes):
    """Patch MediaIoBaseDownload so _download_media writes blob into the buffer.

    The reader does:
        buf = io.BytesIO()
        downloader = MediaIoBaseDownload(buf, request)
        while not done: _, done = downloader.next_chunk()
    """

    class _FakeDownloader:
        def __init__(self, fd, _request):
            self._fd = fd
            self._done = False

        def next_chunk(self):
            self._fd.write(blob)
            self._done = True
            return None, True

    monkeypatch.setattr(
        "googleapiclient.http.MediaIoBaseDownload",
        _FakeDownloader,
    )


def _make_minimal_docx_bytes(paragraphs: list[tuple[str, str]]) -> bytes:
    """Build a real .docx in-memory using python-docx.

    paragraphs: list of (text, style_name). style_name "Normal" or "Heading 1" ...
    """
    import docx as docx_lib

    doc = docx_lib.Document()
    for text, style in paragraphs:
        p = doc.add_paragraph(text)
        if style and style != "Normal":
            p.style = doc.styles[style]
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# ── Native Google Doc routing ──────────────────────────────────────────


class TestNativeGoogleDoc:
    def test_routes_to_docs_reader(self, tmp_path: Path):
        meta = {"id": "doc1", "name": "My Paper", "mimeType": MIME_GOOGLE_DOC}
        drive = _drive_service(meta)

        # Build a Docs-API doc body
        docs_doc = {
            "documentId": "doc1",
            "title": "My Paper",
            "revisionId": "rev1",
            "body": {
                "content": [
                    {
                        "paragraph": {
                            "elements": [{"textRun": {"content": "Intro\n"}}],
                            "paragraphStyle": {"namedStyleType": "HEADING_1"},
                        }
                    },
                    {
                        "paragraph": {
                            "elements": [{"textRun": {"content": "body text\n"}}],
                            "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"},
                        }
                    },
                ]
            },
        }
        docs = _docs_service(docs_doc)
        reader = UniversalDocReader(drive, docs, temp_dir=tmp_path)

        out = reader.read("doc1")
        assert out["id"] == "doc1"
        assert out["mime_type"] == MIME_GOOGLE_DOC
        assert out["method"] == "google_docs"
        assert "Intro" in out["text"]
        assert out["headings"][0]["text"] == "Intro"
        # Native Docs are not saved locally
        assert out["saved_to"] is None
        assert out["char_count"] == len(out["text"])


# ── DOCX routing ───────────────────────────────────────────────────────


class TestDocxRouting:
    def test_parses_real_docx_and_saves_blob(self, tmp_path: Path, monkeypatch):
        blob = _make_minimal_docx_bytes(
            [
                ("Section A", "Heading 1"),
                ("First paragraph.", "Normal"),
                ("Subsection", "Heading 2"),
                ("More body.", "Normal"),
                ("", "Normal"),  # empty -> skipped
            ]
        )
        meta = {"id": "f1", "name": "doc.docx", "mimeType": MIME_DOCX}
        drive = _drive_service(meta, blob=blob)
        docs = _docs_service({})
        _patched_download(monkeypatch, blob)

        reader = UniversalDocReader(drive, docs, temp_dir=tmp_path)
        out = reader.read("f1")

        assert out["mime_type"] == MIME_DOCX
        assert out["method"] == "docx"
        assert "First paragraph." in out["text"]
        assert "More body." in out["text"]
        # Headings extracted with levels 1 and 2
        levels = [h["level"] for h in out["headings"]]
        assert 1 in levels and 2 in levels
        # Blob saved to disk
        saved = Path(out["saved_to"])
        assert saved.exists()
        assert saved.read_bytes() == blob
        # Path under temp_dir/<file_id>/
        assert saved.parent == tmp_path / "f1"

    def test_legacy_doc_mime_routes_to_docx(self, tmp_path: Path, monkeypatch):
        # We can still feed a real .docx blob — python-docx will parse it.
        blob = _make_minimal_docx_bytes([("Hi", "Normal")])
        meta = {"id": "f2", "name": "old.doc", "mimeType": MIME_DOC_LEGACY}
        drive = _drive_service(meta, blob=blob)
        _patched_download(monkeypatch, blob)

        reader = UniversalDocReader(drive, _docs_service({}), temp_dir=tmp_path)
        out = reader.read("f2")
        assert out["method"] == "docx"
        assert "Hi" in out["text"]


# ── PDF routing ────────────────────────────────────────────────────────


class TestPdfRouting:
    def test_parses_pdf(self, tmp_path: Path, monkeypatch):
        # Build a tiny PDF in-memory using pymupdf
        import pymupdf

        pdf_doc = pymupdf.open()
        page = pdf_doc.new_page()
        page.insert_text((72, 72), "Hello PDF World")
        blob = pdf_doc.tobytes()
        pdf_doc.close()

        meta = {"id": "p1", "name": "report.pdf", "mimeType": MIME_PDF}
        drive = _drive_service(meta, blob=blob)
        _patched_download(monkeypatch, blob)

        reader = UniversalDocReader(drive, _docs_service({}), temp_dir=tmp_path)
        out = reader.read("p1")

        assert out["mime_type"] == MIME_PDF
        assert out["method"] == "pdf"
        assert "Hello PDF World" in out["text"]
        assert out["headings"] == []
        saved = Path(out["saved_to"])
        assert saved.exists()
        assert saved.read_bytes() == blob


# ── Plain text / Markdown routing ──────────────────────────────────────


class TestTextRouting:
    def test_plain_text(self, tmp_path: Path, monkeypatch):
        blob = "Hello world\nLine 2\n".encode("utf-8")
        meta = {"id": "t1", "name": "notes.txt", "mimeType": MIME_TXT}
        drive = _drive_service(meta, blob=blob)
        _patched_download(monkeypatch, blob)

        reader = UniversalDocReader(drive, _docs_service({}), temp_dir=tmp_path)
        out = reader.read("t1")

        assert out["mime_type"] == MIME_TXT
        assert out["method"] == "text"
        assert out["text"] == "Hello world\nLine 2\n"
        assert out["char_count"] == len(out["text"])
        assert Path(out["saved_to"]).exists()

    def test_markdown(self, tmp_path: Path, monkeypatch):
        blob = "# Title\n\nbody\n".encode("utf-8")
        meta = {"id": "m1", "name": "doc.md", "mimeType": MIME_MARKDOWN}
        drive = _drive_service(meta, blob=blob)
        _patched_download(monkeypatch, blob)

        reader = UniversalDocReader(drive, _docs_service({}), temp_dir=tmp_path)
        out = reader.read("m1")
        assert out["method"] == "text"
        assert "# Title" in out["text"]

    def test_other_text_mime_still_decoded(self, tmp_path: Path, monkeypatch):
        blob = b"key=value\n"
        meta = {"id": "c1", "name": "config.toml", "mimeType": "text/x-toml"}
        drive = _drive_service(meta, blob=blob)
        _patched_download(monkeypatch, blob)

        reader = UniversalDocReader(drive, _docs_service({}), temp_dir=tmp_path)
        out = reader.read("c1")
        assert out["method"] == "text"
        assert "key=value" in out["text"]


# ── Unknown mime: download but don't error ─────────────────────────────


class TestUnknownMime:
    def test_unknown_mime_downloads_blob(self, tmp_path: Path, monkeypatch):
        blob = b"\x00\x01binary"
        meta = {
            "id": "u1",
            "name": "thing.bin",
            "mimeType": "application/octet-stream",
        }
        drive = _drive_service(meta, blob=blob)
        _patched_download(monkeypatch, blob)

        reader = UniversalDocReader(drive, _docs_service({}), temp_dir=tmp_path)
        out = reader.read("u1")

        assert out["method"] == "downloaded_only"
        assert out["text"] == ""
        assert Path(out["saved_to"]).read_bytes() == blob
        assert "note" in out

    def test_unknown_mime_download_failure_returns_error(
        self, tmp_path: Path, monkeypatch
    ):
        meta = {"id": "u2", "name": "x", "mimeType": "application/x-weird"}
        drive = _drive_service(meta)

        # Patch download to raise
        class _FailDownloader:
            def __init__(self, *a, **k):
                pass

            def next_chunk(self):
                raise RuntimeError("nope")

        monkeypatch.setattr(
            "googleapiclient.http.MediaIoBaseDownload", _FailDownloader
        )

        reader = UniversalDocReader(drive, _docs_service({}), temp_dir=tmp_path)
        out = reader.read("u2")
        assert out["method"] == "unsupported"
        assert out["saved_to"] is None
        assert "error" in out


# ── Filename safety ────────────────────────────────────────────────────


class TestSavePath:
    def test_filename_separators_sanitized(self, tmp_path: Path, monkeypatch):
        blob = b"x"
        meta = {
            "id": "s1",
            "name": "weird/name\\with.txt",
            "mimeType": MIME_TXT,
        }
        drive = _drive_service(meta, blob=blob)
        _patched_download(monkeypatch, blob)

        reader = UniversalDocReader(drive, _docs_service({}), temp_dir=tmp_path)
        out = reader.read("s1")
        saved = Path(out["saved_to"])
        # No subdirectory created from the slash/backslash in the name
        assert saved.parent == tmp_path / "s1"
        assert saved.name == "weird_name_with.txt"

"""
Read-only access to Google Docs and Drive.

GoogleDocsReader: extract text + structure from a Doc by ID.
GoogleDriveReader: search / list / inspect Drive items.

Read operations are Tier C (auto-allowed). Writing happens via the separate
GoogleDocsComposer class to enforce a clean read/write boundary.
"""

from __future__ import annotations

from typing import Any


class GoogleDocsReader:
    """Read-only Doc content extraction. Never writes."""

    def __init__(self, docs_service: Any) -> None:
        self._svc = docs_service

    def read(self, document_id: str) -> dict:
        """Fetch a Doc by ID. Returns title, body text (plain), and structure.

        Output shape:
          {
            "id": str,
            "title": str,
            "revision_id": str,
            "text": str,                # newline-joined plain text
            "headings": [{"level": int, "text": str, "index": int}],
            "char_count": int,
          }
        """
        doc = self._svc.documents().get(documentId=document_id).execute()
        body = doc.get("body", {})
        text_parts: list[str] = []
        headings: list[dict] = []

        for element in body.get("content", []) or []:
            if "paragraph" not in element:
                continue
            para = element["paragraph"]
            style = (para.get("paragraphStyle") or {}).get("namedStyleType", "")
            text = self._collect_paragraph_text(para)
            if not text.strip():
                continue

            text_parts.append(text)

            if style.startswith("HEADING_"):
                try:
                    level = int(style.removeprefix("HEADING_"))
                except ValueError:
                    level = 1
                headings.append(
                    {
                        "level": level,
                        "text": text.strip(),
                        "index": element.get("startIndex", 0),
                    }
                )

        full_text = "\n".join(text_parts)
        return {
            "id": doc.get("documentId", document_id),
            "title": doc.get("title", ""),
            "revision_id": doc.get("revisionId", ""),
            "text": full_text,
            "headings": headings,
            "char_count": len(full_text),
        }

    @staticmethod
    def _collect_paragraph_text(paragraph: dict) -> str:
        """Concatenate textRun.content from a paragraph element."""
        out_parts: list[str] = []
        for elem in paragraph.get("elements", []) or []:
            text_run = elem.get("textRun")
            if text_run:
                out_parts.append(text_run.get("content", ""))
        return "".join(out_parts)


class GoogleDriveReader:
    """Read-only Drive search / list / metadata. Never writes."""

    def __init__(self, drive_service: Any) -> None:
        self._svc = drive_service

    def search(
        self,
        query: str = "",
        max_results: int = 20,
        mime_type: str | None = None,
    ) -> list[dict]:
        """Search Drive with optional MIME filter.

        Args:
            query: Drive query syntax. Common forms:
                "name contains 'NSTC'"
                "modifiedTime > '2026-01-01T00:00:00'"
                "'<folder_id>' in parents"
            mime_type: shortcut filter, e.g. 'doc' / 'sheet' / 'pdf' / a full
                MIME like 'application/vnd.google-apps.document'.
            max_results: page size (max 100 in API).
        """
        q_parts: list[str] = []
        if query:
            q_parts.append(query)
        if mime_type:
            mime_full = self._resolve_mime(mime_type)
            q_parts.append(f"mimeType = '{mime_full}'")
        # Always exclude trashed items
        q_parts.append("trashed = false")
        q = " and ".join(q_parts) if q_parts else None

        kwargs: dict = {
            "pageSize": min(max_results, 100),
            "fields": (
                "files(id, name, mimeType, modifiedTime, "
                "webViewLink, owners(emailAddress, displayName), size)"
            ),
        }
        if q:
            kwargs["q"] = q

        resp = self._svc.files().list(**kwargs).execute()
        return [self._format_file(f) for f in resp.get("files", [])]

    def get_metadata(self, file_id: str) -> dict:
        """Fetch metadata for a single Drive item."""
        f = (
            self._svc.files()
            .get(
                fileId=file_id,
                fields=(
                    "id, name, mimeType, modifiedTime, webViewLink, "
                    "owners(emailAddress, displayName), size, parents"
                ),
            )
            .execute()
        )
        return self._format_file(f)

    @staticmethod
    def _format_file(f: dict) -> dict:
        owners = [
            f"{o.get('displayName', '')} <{o.get('emailAddress', '')}>"
            for o in (f.get("owners") or [])
        ]
        return {
            "id": f.get("id", ""),
            "name": f.get("name", ""),
            "mime_type": f.get("mimeType", ""),
            "modified": f.get("modifiedTime", ""),
            "size_bytes": int(f.get("size", 0)) if f.get("size") else None,
            "owners": owners,
            "url": f.get("webViewLink", ""),
            "parents": f.get("parents", []),
        }

    _MIME_SHORTCUTS = {
        "doc": "application/vnd.google-apps.document",
        "docs": "application/vnd.google-apps.document",
        "sheet": "application/vnd.google-apps.spreadsheet",
        "sheets": "application/vnd.google-apps.spreadsheet",
        "slide": "application/vnd.google-apps.presentation",
        "slides": "application/vnd.google-apps.presentation",
        "folder": "application/vnd.google-apps.folder",
        "pdf": "application/pdf",
    }

    @classmethod
    def _resolve_mime(cls, mime_type: str) -> str:
        return cls._MIME_SHORTCUTS.get(mime_type.lower(), mime_type)

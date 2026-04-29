"""
Write operations to Google Docs.

⚠ All methods here are Tier A under C25-DESTRUCTIVE-OPS-PROTOCOL: the MCP
tools that wrap them MUST be in `permissions.ask` in Elsa's settings.json,
because writes to user docs are irreversible from the agent's perspective.

This class is intentionally separated from GoogleDocsReader to make the
read/write boundary obvious in code review and grep.
"""

from __future__ import annotations

from typing import Any


class GoogleDocsComposer:
    """Write methods for Google Docs. Always Tier A — gate at MCP layer."""

    def __init__(self, docs_service: Any) -> None:
        self._svc = docs_service

    def append_text(
        self,
        document_id: str,
        text: str,
        *,
        with_newline: bool = True,
    ) -> dict:
        """Append plain text to the end of a Doc.

        Args:
            document_id: Doc to write to.
            text: Plain text to append.
            with_newline: prepend "\\n" so appended text starts on its own line.

        Returns the API response (contains revisionId etc.).
        """
        if with_newline and not text.startswith("\n"):
            text = "\n" + text

        # Find current end-of-body index by fetching the doc.
        doc = self._svc.documents().get(documentId=document_id).execute()
        end_index = self._end_index(doc)

        requests = [
            {
                "insertText": {
                    "location": {"index": end_index},
                    "text": text,
                }
            }
        ]
        return (
            self._svc.documents()
            .batchUpdate(documentId=document_id, body={"requests": requests})
            .execute()
        )

    def replace_text(
        self,
        document_id: str,
        find_text: str,
        replace_with: str,
        *,
        match_case: bool = True,
    ) -> dict:
        """Find and replace all occurrences of `find_text` in the Doc.

        Be careful — this is irreversible without restore. The caller (MCP
        tool) must enforce ASK before invoking.
        """
        requests = [
            {
                "replaceAllText": {
                    "containsText": {
                        "text": find_text,
                        "matchCase": match_case,
                    },
                    "replaceText": replace_with,
                }
            }
        ]
        return (
            self._svc.documents()
            .batchUpdate(documentId=document_id, body={"requests": requests})
            .execute()
        )

    @staticmethod
    def _end_index(doc: dict) -> int:
        """Return the end index of the document body for insertion."""
        body = doc.get("body", {})
        content = body.get("content", []) or []
        if not content:
            # Fresh doc: index 1 is the safe starting position
            return 1
        last = content[-1]
        # endIndex from API is exclusive; subtract 1 to insert before the
        # final newline that Docs auto-keeps.
        return max(1, last.get("endIndex", 1) - 1)

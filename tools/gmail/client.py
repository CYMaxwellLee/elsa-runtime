"""
Gmail API client wrapper.

All methods return plain dicts/lists ready for formatting.
Read-only: never mutates the mailbox (gmail.readonly scope).
"""

import base64
import re
from typing import Optional


class GmailClient:
    """Stateless Gmail reader. One instance per CLI invocation."""

    def __init__(self, service):
        self._svc = service
        self._user = "me"

    def list_messages(
        self,
        max_results: int = 10,
        label: str = "INBOX",
        query: Optional[str] = None,
    ) -> list[dict]:
        """List messages with headers (no body)."""
        kwargs = {
            "userId": self._user,
            "maxResults": max_results,
        }
        if query:
            kwargs["q"] = query
        else:
            kwargs["labelIds"] = [label]

        resp = self._svc.users().messages().list(**kwargs).execute()
        messages = resp.get("messages", [])

        results = []
        for msg_stub in messages:
            msg = (
                self._svc.users()
                .messages()
                .get(
                    userId=self._user,
                    id=msg_stub["id"],
                    format="metadata",
                    metadataHeaders=["From", "To", "Subject", "Date"],
                )
                .execute()
            )
            results.append(self._extract_headers(msg))
        return results

    def list_unread(self, max_results: int = 10) -> list[dict]:
        """List unread messages."""
        return self.list_messages(
            max_results=max_results, query="is:unread"
        )

    def search(self, query: str, max_results: int = 10) -> list[dict]:
        """Search with Gmail query syntax."""
        return self.list_messages(
            max_results=max_results, query=query
        )

    def read_message(self, message_id: str) -> dict:
        """Read full message body + headers."""
        msg = (
            self._svc.users()
            .messages()
            .get(userId=self._user, id=message_id, format="full")
            .execute()
        )

        result = self._extract_headers(msg)
        result["body"] = self._extract_body(msg.get("payload", {}))
        result["labels"] = msg.get("labelIds", [])
        return result

    def list_labels(self) -> list[dict]:
        """List all labels."""
        resp = (
            self._svc.users()
            .labels()
            .list(userId=self._user)
            .execute()
        )
        labels = resp.get("labels", [])
        return [
            {
                "id": lb["id"],
                "name": lb["name"],
                "type": lb.get("type", "user"),
            }
            for lb in sorted(labels, key=lambda x: x["name"])
        ]

    # -- Private helpers --

    @staticmethod
    def _extract_headers(msg: dict) -> dict:
        """Extract standard headers into flat dict."""
        headers = {
            h["name"].lower(): h["value"]
            for h in msg.get("payload", {}).get("headers", [])
        }
        return {
            "id": msg["id"],
            "thread_id": msg.get("threadId"),
            "from": headers.get("from", ""),
            "to": headers.get("to", ""),
            "subject": headers.get("subject", "(no subject)"),
            "date": headers.get("date", ""),
            "snippet": msg.get("snippet", ""),
        }

    @staticmethod
    def _extract_body(payload: dict) -> str:
        """Extract plaintext body, falling back to HTML tag stripping."""
        # Direct body (non-multipart)
        if payload.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(
                payload["body"]["data"]
            ).decode("utf-8", errors="replace")

        # Multipart: prefer text/plain
        for part in payload.get("parts", []):
            if part.get("mimeType") == "text/plain":
                data = part.get("body", {}).get("data", "")
                if data:
                    return base64.urlsafe_b64decode(data).decode(
                        "utf-8", errors="replace"
                    )

        # Fallback: text/html (strip tags, no extra dependency)
        for part in payload.get("parts", []):
            if part.get("mimeType") == "text/html":
                data = part.get("body", {}).get("data", "")
                if data:
                    html = base64.urlsafe_b64decode(data).decode(
                        "utf-8", errors="replace"
                    )
                    text = re.sub(r"<[^>]+>", "", html)
                    text = re.sub(r"\s+", " ", text).strip()
                    return text

        # Nested multipart (e.g. multipart/alternative inside multipart/mixed)
        for part in payload.get("parts", []):
            if part.get("parts"):
                body = GmailClient._extract_body(part)
                if body != "(no body)":
                    return body

        return "(no body)"

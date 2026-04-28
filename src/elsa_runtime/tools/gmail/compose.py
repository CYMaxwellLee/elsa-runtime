"""
Gmail compose helper — create draft replies attached to existing threads.

Solves the Anthropic-managed Gmail connector limitation: its `create_draft`
tool has no `threadId` parameter, so every AI-drafted reply becomes an
orphaned new thread. This module uses the underlying Gmail REST API
(`users.drafts.create` with `threadId`) to put drafts into the original
thread, where the user expects to find them.

Scope requirement: `https://www.googleapis.com/auth/gmail.compose`
(narrower scopes don't allow draft creation).

Security note: gmail.compose technically also allows send, but this
module deliberately does NOT expose any send method. The agent can only
draft; sending always requires the user to click Send in Gmail UI.
"""

from __future__ import annotations

import base64
from email.mime.text import MIMEText
from email.utils import formataddr
from typing import Any


class GmailComposer:
    """Stateful Gmail draft composer. Draft-only; never sends."""

    def __init__(self, service: Any) -> None:
        self._svc = service
        self._user = "me"

    def get_message_headers(self, message_id: str) -> dict[str, str]:
        """Fetch headers needed for proper threading (Message-ID, References).

        The Message-ID header is required for `In-Reply-To` to work — it
        threads our reply correctly even in clients other than Gmail.
        """
        msg = (
            self._svc.users()
            .messages()
            .get(
                userId=self._user,
                id=message_id,
                format="metadata",
                metadataHeaders=[
                    "Message-ID",
                    "References",
                    "Subject",
                    "From",
                    "To",
                    "Cc",
                ],
            )
            .execute()
        )
        headers = msg.get("payload", {}).get("headers", [])
        return {h["name"]: h["value"] for h in headers}

    def create_draft_reply(
        self,
        thread_id: str,
        to: list[str] | str,
        body: str,
        *,
        subject: str | None = None,
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        in_reply_to_message_id: str | None = None,
        from_addr: str | None = None,
    ) -> dict:
        """Create a draft attached to an existing thread.

        Args:
            thread_id: Gmail thread ID (e.g. from search_threads results).
            to: Primary recipient(s).
            body: Plain-text body of the reply.
            subject: Optional subject. If omitted and `in_reply_to_message_id`
                is set, will fetch the original subject and prefix "Re:".
            cc, bcc: Optional CC/BCC.
            in_reply_to_message_id: RFC 2822 Message-ID of the email being
                replied to. Sets `In-Reply-To` and extends `References`
                headers for clean threading in non-Gmail clients.
            from_addr: Optional sender. Defaults to authenticated account.

        Returns:
            Gmail API draft object: {"id": ..., "message": {...}}

        Raises:
            googleapiclient.errors.HttpError on API failure.
        """
        # Auto-derive subject if missing + we have the source message
        if subject is None and in_reply_to_message_id:
            try:
                src_headers = self.get_message_headers(in_reply_to_message_id)
                src_subject = src_headers.get("Subject", "")
                subject = (
                    src_subject
                    if src_subject.lower().startswith("re:")
                    else f"Re: {src_subject}"
                )
            except Exception:
                subject = "Re: (no subject)"
        elif subject is None:
            subject = "(no subject)"

        msg = MIMEText(body, _charset="utf-8")
        msg["To"] = self._fmt_addrs(to)
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = self._fmt_addrs(cc)
        if bcc:
            msg["Bcc"] = self._fmt_addrs(bcc)
        if from_addr:
            msg["From"] = from_addr

        # Threading headers — let mail clients other than Gmail thread correctly.
        if in_reply_to_message_id:
            msg["In-Reply-To"] = in_reply_to_message_id
            # Append to References if we can fetch the existing chain.
            try:
                src_headers = self.get_message_headers(in_reply_to_message_id)
                existing_refs = src_headers.get("References", "").strip()
                src_msg_id = src_headers.get("Message-ID", "").strip()
                refs = " ".join(filter(None, [existing_refs, src_msg_id]))
                if refs:
                    msg["References"] = refs
            except Exception:
                msg["References"] = in_reply_to_message_id

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")

        draft_body = {
            "message": {
                "threadId": thread_id,
                "raw": raw,
            }
        }
        return (
            self._svc.users()
            .drafts()
            .create(userId=self._user, body=draft_body)
            .execute()
        )

    @staticmethod
    def _fmt_addrs(addrs: list[str] | str) -> str:
        if isinstance(addrs, str):
            return addrs
        return ", ".join(addrs)

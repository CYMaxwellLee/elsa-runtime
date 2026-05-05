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

v3.51-A.x (2026-05-05): added html_body + attachments support per
the elsa-knowledge friction proposal (5/3, PROPOSAL-MCP-DRAFT-HTMLBODY).
- html_body: when set, builds multipart/alternative so mail clients
  can render HTML and avoid RFC 2822 line folding (78-char hard wrap)
  on plain-text bodies.
- attachments: list of absolute paths; each file goes through
  mimetypes.guess_type for MIME inference, base64-encoded into a
  multipart/mixed message. Per-file size cap 35 MB (Gmail OAuth API
  base64-encoded limit).
"""

from __future__ import annotations

import base64
import mimetypes
from email import encoders
from email.mime.audio import MIMEAudio
from email.mime.base import MIMEBase
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any


# Gmail OAuth API base64-encoded message size limit. Files this big or
# larger get rejected at compose time so we don't do the work and then
# get the API error.
ATTACHMENT_MAX_BYTES = 35 * 1024 * 1024  # 35 MiB


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
        html_body: str | None = None,
        attachments: list[str] | None = None,
    ) -> dict:
        """Create a draft attached to an existing thread.

        Args:
            thread_id: Gmail thread ID (e.g. from search_threads results).
            to: Primary recipient(s).
            body: Plain-text body of the reply. Always required (used as
                text/plain fallback even when html_body is set).
            subject: Optional subject. If omitted and `in_reply_to_message_id`
                is set, will fetch the original subject and prefix "Re:".
            cc, bcc: Optional CC/BCC.
            in_reply_to_message_id: RFC 2822 Message-ID of the email being
                replied to. Sets `In-Reply-To` and extends `References`
                headers for clean threading in non-Gmail clients.
            from_addr: Optional sender. Defaults to authenticated account.
            html_body: Optional HTML version of the body. When set, the
                message is built as multipart/alternative with the plain
                `body` as the text/plain part and `html_body` as the
                text/html part. Mail clients render the HTML preferentially,
                bypassing the RFC 2822 78-char hard-wrap that mangles
                long plain-text paragraphs (5/3 LOR-to-Cathy incident).
            attachments: Optional list of absolute paths to local files.
                Each file is base64-encoded into the message; MIME type
                guessed from extension via mimetypes. Per-file size cap
                35 MiB (ATTACHMENT_MAX_BYTES). Missing files raise
                FileNotFoundError; oversized files raise ValueError.

        Returns:
            Gmail API draft object: {"id": ..., "message": {...}}

        Raises:
            FileNotFoundError: an attachment path does not exist.
            ValueError: an attachment exceeds ATTACHMENT_MAX_BYTES.
            googleapiclient.errors.HttpError: API failure.
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

        # Validate attachments up-front (cheap; before any MIME work).
        attachment_paths: list[Path] = []
        if attachments:
            for raw_path in attachments:
                p = Path(raw_path).expanduser().resolve()
                if not p.exists() or not p.is_file():
                    raise FileNotFoundError(
                        f"attachment not found or not a regular file: {raw_path}"
                    )
                size = p.stat().st_size
                if size > ATTACHMENT_MAX_BYTES:
                    raise ValueError(
                        f"attachment exceeds 35 MiB limit "
                        f"({size / 1024 / 1024:.1f} MiB): {raw_path}"
                    )
                attachment_paths.append(p)

        # Build the message. Three structural cases:
        #   (A) plain-text only, no attachments → MIMEText (legacy path)
        #   (B) html_body OR attachments present → multipart
        #
        # In (B) the layout is:
        #   multipart/mixed
        #     ├── multipart/alternative
        #     │     ├── text/plain (body)
        #     │     └── text/html  (html_body)  [only if html_body set]
        #     └── attachment 1
        #     └── attachment 2
        #     └── ...
        # If only html_body is set with no attachments, we collapse to:
        #   multipart/alternative
        #     ├── text/plain (body)
        #     └── text/html  (html_body)
        # If only attachments are set with no html_body:
        #   multipart/mixed
        #     ├── text/plain (body)
        #     └── attachment 1, 2, ...
        if not html_body and not attachment_paths:
            msg: Any = MIMEText(body, _charset="utf-8")
        else:
            msg = self._build_multipart(body, html_body, attachment_paths)

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

    # ------------------------------------------------------------------
    # Multipart assembly helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_alternative(body: str, html_body: str | None) -> MIMEMultipart:
        """Build multipart/alternative (text/plain + optional text/html)."""
        alt = MIMEMultipart("alternative")
        alt.attach(MIMEText(body, _subtype="plain", _charset="utf-8"))
        if html_body:
            alt.attach(MIMEText(html_body, _subtype="html", _charset="utf-8"))
        return alt

    @classmethod
    def _build_multipart(
        cls,
        body: str,
        html_body: str | None,
        attachment_paths: list[Path],
    ) -> MIMEMultipart:
        """Top-level multipart message including body alternatives + attachments.

        See create_draft_reply docstring for layout decisions.
        """
        if not attachment_paths:
            # Pure body alternative case.
            return cls._build_alternative(body, html_body)

        # Has attachments: outer is multipart/mixed; body either plain
        # text/plain part or nested multipart/alternative.
        outer = MIMEMultipart("mixed")
        if html_body:
            outer.attach(cls._build_alternative(body, html_body))
        else:
            outer.attach(MIMEText(body, _subtype="plain", _charset="utf-8"))

        for path in attachment_paths:
            outer.attach(cls._build_attachment_part(path))
        return outer

    @staticmethod
    def _build_attachment_part(path: Path) -> MIMEBase:
        """Read file and wrap as a MIME attachment with guessed MIME type."""
        ctype, encoding = mimetypes.guess_type(str(path))
        if ctype is None or encoding is not None:
            # Unknown or compressed; treat as opaque binary.
            ctype = "application/octet-stream"

        maintype, subtype = ctype.split("/", 1)
        data = path.read_bytes()

        if maintype == "text":
            try:
                part: MIMEBase = MIMEText(
                    data.decode("utf-8"), _subtype=subtype, _charset="utf-8"
                )
            except UnicodeDecodeError:
                part = MIMEBase(maintype, subtype)
                part.set_payload(data)
                encoders.encode_base64(part)
        elif maintype == "image":
            part = MIMEImage(data, _subtype=subtype)
        elif maintype == "audio":
            part = MIMEAudio(data, _subtype=subtype)
        else:
            part = MIMEBase(maintype, subtype)
            part.set_payload(data)
            encoders.encode_base64(part)

        # Force attachment (vs inline) display + preserve filename.
        part.add_header(
            "Content-Disposition",
            "attachment",
            filename=path.name,
        )
        return part

    @staticmethod
    def _fmt_addrs(addrs: list[str] | str) -> str:
        if isinstance(addrs, str):
            return addrs
        return ", ".join(addrs)

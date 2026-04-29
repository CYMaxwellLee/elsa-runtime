#!/usr/bin/env python3
"""
Gmail Reader CLI for Elsa System
=================================
Stateless CLI tool (no persistent process) for reading Gmail.
Called by Elsa via OpenClaw shell execution.

Usage:
    python3.11 tools/gmail/gmail_tool.py list [--max N]
    python3.11 tools/gmail/gmail_tool.py unread [--max N]
    python3.11 tools/gmail/gmail_tool.py search "query" [--max N]
    python3.11 tools/gmail/gmail_tool.py read <message_id>
    python3.11 tools/gmail/gmail_tool.py labels
    python3.11 tools/gmail/gmail_tool.py auth
    python3.11 tools/gmail/gmail_tool.py health

Credentials: ~/.elsa-system/gmail/credentials.json + token.json
Scope: gmail.readonly (read-only, never sends/deletes)
"""

import argparse
import sys
from pathlib import Path

# Configuration
GMAIL_DIR = Path.home() / ".elsa-system" / "gmail"
CREDENTIALS_FILE = GMAIL_DIR / "credentials.json"
TOKEN_FILE = GMAIL_DIR / "token.json"
# 2026-04-28: added `gmail.compose` for create_draft_reply (threadId-aware
# draft creation that the Anthropic-managed connector lacks).
# 2026-04-29: added `documents` (read+write Google Docs, gated as Tier A
# at MCP layer per C25-DESTRUCTIVE-OPS-PROTOCOL) + `drive.readonly`
# (search + list Drive items, no writes).
# Token shared at ~/.elsa-system/gmail/token.json — same Google account.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/drive.readonly",
]
DEFAULT_MAX_RESULTS = 10


def main():
    parser = argparse.ArgumentParser(
        description="Gmail Reader for Elsa System",
        prog="gmail_tool",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # auth
    sub.add_parser("auth", help="Run OAuth flow (opens browser)")

    # health
    sub.add_parser("health", help="Check auth + connectivity")

    # list
    p_list = sub.add_parser("list", help="Recent emails")
    p_list.add_argument("--max", type=int, default=DEFAULT_MAX_RESULTS)
    p_list.add_argument("--label", type=str, default="INBOX")
    p_list.add_argument("--json", action="store_true", help="JSON output")

    # unread
    p_unread = sub.add_parser("unread", help="Unread emails")
    p_unread.add_argument("--max", type=int, default=DEFAULT_MAX_RESULTS)
    p_unread.add_argument("--json", action="store_true")

    # search
    p_search = sub.add_parser("search", help="Search emails")
    p_search.add_argument("query", type=str)
    p_search.add_argument("--max", type=int, default=DEFAULT_MAX_RESULTS)
    p_search.add_argument("--json", action="store_true")

    # read
    p_read = sub.add_parser("read", help="Read email body")
    p_read.add_argument("message_id", type=str)
    p_read.add_argument("--json", action="store_true")

    # labels
    p_labels = sub.add_parser("labels", help="List labels")
    p_labels.add_argument("--json", action="store_true")

    args = parser.parse_args()

    try:
        if args.command == "auth":
            from auth import run_auth_flow
            run_auth_flow(CREDENTIALS_FILE, TOKEN_FILE, SCOPES)
            return

        if args.command == "health":
            from auth import check_health
            check_health(CREDENTIALS_FILE, TOKEN_FILE, SCOPES)
            return

        # All other commands need authenticated client
        from auth import get_service
        from client import GmailClient
        from formatter import format_output

        service = get_service(CREDENTIALS_FILE, TOKEN_FILE, SCOPES)
        client = GmailClient(service)

        if args.command == "list":
            results = client.list_messages(
                max_results=args.max, label=args.label
            )
        elif args.command == "unread":
            results = client.list_unread(max_results=args.max)
        elif args.command == "search":
            results = client.search(args.query, max_results=args.max)
        elif args.command == "read":
            results = client.read_message(args.message_id)
        elif args.command == "labels":
            results = client.list_labels()
        else:
            parser.print_help()
            return

        use_json = getattr(args, "json", False)
        print(format_output(results, args.command, use_json=use_json))

    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print(
            "Run setup first: bash tools/gmail/setup.sh",
            file=sys.stderr,
        )
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

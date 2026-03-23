"""
Output formatting for Gmail CLI.

Two modes:
- Human-readable (default): structured text for Elsa to parse and summarize
- JSON (--json flag): for programmatic use
"""

import json


def format_output(data, command: str, use_json: bool = False) -> str:
    """Format API response for CLI output."""
    if use_json:
        return json.dumps(data, ensure_ascii=False, indent=2)

    if command in ("list", "unread", "search"):
        return _format_message_list(data, command)
    elif command == "read":
        return _format_full_message(data)
    elif command == "labels":
        return _format_labels(data)
    return json.dumps(data, ensure_ascii=False, indent=2)


def _format_message_list(messages: list[dict], command: str) -> str:
    if not messages:
        label = "unread " if command == "unread" else ""
        return f"No {label}messages found."

    header = {
        "unread": "Unread",
        "search": "Search results",
        "list": "Recent",
    }.get(command, "Messages")

    lines = [f"{header} ({len(messages)}):", ""]
    for i, msg in enumerate(messages, 1):
        lines.append(f"  [{i}] {msg['subject']}")
        lines.append(f"      From: {msg['from']}")
        lines.append(f"      Date: {msg['date']}")
        lines.append(f"      ID: {msg['id']}")
        if msg.get("snippet"):
            snippet = msg["snippet"][:120]
            lines.append(f"      Preview: {snippet}")
        lines.append("")
    return "\n".join(lines)


def _format_full_message(msg: dict) -> str:
    lines = [
        f"Subject: {msg['subject']}",
        f"From: {msg['from']}",
        f"To: {msg['to']}",
        f"Date: {msg['date']}",
        f"ID: {msg['id']}",
        f"Labels: {', '.join(msg.get('labels', []))}",
        "---",
        msg.get("body", "(no body)"),
    ]
    return "\n".join(lines)


def _format_labels(labels: list[dict]) -> str:
    lines = [f"Labels ({len(labels)}):", ""]
    for lb in labels:
        tag = f" [{lb['type']}]" if lb["type"] == "system" else ""
        lines.append(f"  {lb['name']}{tag}  (id: {lb['id']})")
    return "\n".join(lines)

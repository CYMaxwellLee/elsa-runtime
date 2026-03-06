#!/bin/bash
# Gmail Reader — One-command setup for Elsa System
# Usage: bash setup.sh
# Run from anywhere (uses absolute paths).
set -euo pipefail

GMAIL_DIR="$HOME/.elsa-system/gmail"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$HOME/.openclaw/workspace/skills/gmail-reader"

echo "=== Gmail Reader Setup for Elsa System ==="
echo ""

# 1. Python dependencies
echo "--- [1/5] Installing Python dependencies ---"
python3.11 -m pip install --break-system-packages -q \
  -r "$SCRIPT_DIR/requirements.txt"
echo "  OK"

# 2. Create credentials directory
echo "--- [2/5] Creating credentials directory ---"
mkdir -p "$GMAIL_DIR"
chmod 700 "$GMAIL_DIR"
echo "  Created: $GMAIL_DIR"

# 3. Check for credentials.json
echo "--- [3/5] Checking credentials.json ---"
if [ -f "$GMAIL_DIR/credentials.json" ]; then
    echo "  Found: $GMAIL_DIR/credentials.json"
    chmod 600 "$GMAIL_DIR/credentials.json"
else
    echo "  NOT FOUND: $GMAIL_DIR/credentials.json"
    echo ""
    echo "  You need to:"
    echo "  1. Go to https://console.cloud.google.com"
    echo "  2. Create a project (or select existing: 'Elsa System')"
    echo "  3. Enable Gmail API (APIs & Services > Library)"
    echo "  4. Create OAuth 2.0 credentials (Desktop app)"
    echo "  5. Download the JSON file"
    echo "  6. Place at: $GMAIL_DIR/credentials.json"
    echo ""
    echo "  After placing credentials.json, re-run this script or run:"
    echo "    python3.11 $SCRIPT_DIR/gmail_tool.py auth"
    echo ""
fi

# 4. Deploy OpenClaw skill
echo "--- [4/5] Deploying OpenClaw skill ---"
mkdir -p "$SKILL_DIR"

cat > "$SKILL_DIR/SKILL.md" << 'SKILL_EOF'
---
name: gmail-reader
description: "Read Gmail inbox: list, search, read emails (read-only)"
---

# Gmail Reader

Read the user's Gmail inbox. **Read-only access only**: never sends, deletes, or modifies emails.

## Available Commands

Run via shell. All commands return structured text you can parse and summarize for the user.

```bash
# List recent inbox emails
python3.11 ~/Projects/elsa-runtime/tools/gmail/gmail_tool.py list [--max 10]

# List unread emails
python3.11 ~/Projects/elsa-runtime/tools/gmail/gmail_tool.py unread [--max 10]

# Search emails (Gmail query syntax)
python3.11 ~/Projects/elsa-runtime/tools/gmail/gmail_tool.py search "from:someone@example.com"
python3.11 ~/Projects/elsa-runtime/tools/gmail/gmail_tool.py search "subject:ICML after:2026/01/01"
python3.11 ~/Projects/elsa-runtime/tools/gmail/gmail_tool.py search "is:important newer_than:3d"

# Read specific email body (use message ID from list/search)
python3.11 ~/Projects/elsa-runtime/tools/gmail/gmail_tool.py read <message_id>

# List all labels
python3.11 ~/Projects/elsa-runtime/tools/gmail/gmail_tool.py labels

# Check auth status
python3.11 ~/Projects/elsa-runtime/tools/gmail/gmail_tool.py health
```

## Gmail Search Syntax

- `from:someone@example.com` — from specific sender
- `to:me subject:meeting` — sent to me about meetings
- `is:unread is:important` — unread important
- `newer_than:7d` — last 7 days
- `has:attachment filename:pdf` — emails with PDF attachments
- `after:2026/03/01 before:2026/03/07` — date range
- `-category:promotions -category:social` — exclude noise

## When to Use

- **Daily briefing**: `unread --max 20` then summarize important ones
- **Meeting prep**: `search "from:participant newer_than:30d"`
- **Finding specific email**: `search "subject:keyword"`
- **Checking replies**: `search "from:specific-person newer_than:7d"`

## Output Format

Commands return structured text. Add `--json` for JSON output.

## Rules

1. **Read-only**: only READ emails. Never attempt to send, delete, or modify.
2. **Privacy**: do not share email contents with anyone other than the user.
3. **Summarize**: when reporting, summarize by default. Full body only if asked.
4. **Filter noise**: skip newsletters, promotions, automated notifications unless asked.
5. **ID preservation**: always include message ID so the user can ask for details.
SKILL_EOF

cat > "$SKILL_DIR/_meta.json" << META_EOF
{
  "owner": "elsa-system",
  "slug": "gmail-reader",
  "displayName": "Gmail Reader",
  "latest": {
    "version": "1.0.0",
    "publishedAt": $(date +%s)000,
    "commit": "local"
  },
  "history": []
}
META_EOF

echo "  Deployed to: $SKILL_DIR"

# 5. Auth
echo "--- [5/5] Authentication ---"
if [ -f "$GMAIL_DIR/credentials.json" ] && [ ! -f "$GMAIL_DIR/token.json" ]; then
    echo "  Running OAuth flow (browser will open)..."
    cd "$SCRIPT_DIR" && python3.11 gmail_tool.py auth
elif [ -f "$GMAIL_DIR/token.json" ]; then
    echo "  Token exists. Running health check..."
    cd "$SCRIPT_DIR" && python3.11 gmail_tool.py health
else
    echo "  SKIP: No credentials.json yet."
    echo "  Place it at $GMAIL_DIR/credentials.json, then run:"
    echo "    cd $SCRIPT_DIR && python3.11 gmail_tool.py auth"
fi

echo ""
echo "=== Setup complete ==="
echo ""
echo "Quick test:"
echo "  cd $SCRIPT_DIR && python3.11 gmail_tool.py health"
echo "  cd $SCRIPT_DIR && python3.11 gmail_tool.py unread --max 3"

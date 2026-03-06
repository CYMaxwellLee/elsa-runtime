# Gmail Reader Tool

Read-only Gmail CLI tool for the Elsa System.

## Quick Start

```bash
# One-command setup (installs deps + deploys skill)
bash setup.sh

# Place Google Cloud credentials (see setup.sh output for instructions)
# Then authenticate:
python3.11 gmail_tool.py auth

# Test
python3.11 gmail_tool.py health
python3.11 gmail_tool.py unread --max 3
```

## Architecture

```
tools/gmail/
  gmail_tool.py      CLI entry point (argparse)
  auth.py            OAuth 2.0 flow + token management
  client.py          Gmail API wrapper (GmailClient class)
  formatter.py       Output formatting (human-readable + JSON)
  requirements.txt   Python dependencies
  setup.sh           One-command installer

~/.elsa-system/gmail/
  credentials.json   Google Cloud OAuth client (portable, chmod 600)
  token.json         Per-machine auth token (chmod 600)
```

## Commands

| Command | Description |
|---------|-------------|
| `auth` | First-time OAuth flow (opens browser) |
| `health` | Check credentials + token + API connection |
| `list [--max N]` | Recent inbox emails |
| `unread [--max N]` | Unread emails |
| `search "query" [--max N]` | Gmail query syntax search |
| `read <message_id>` | Full email body |
| `labels` | List all labels |

All data commands support `--json` for structured output.

## New Machine Deployment

1. `git clone` this repo
2. Copy `credentials.json` from 1Password to `~/.elsa-system/gmail/`
3. `bash setup.sh`
4. Browser opens for Google login (cymaxwelllee@gmail.com)
5. Done (~5 minutes)

## Security

- Read-only scope (`gmail.readonly`)
- Credentials never in repo (stored in `~/.elsa-system/gmail/`, chmod 600)
- No background process (CLI tool, runs and exits)

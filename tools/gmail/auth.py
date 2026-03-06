"""
OAuth 2.0 authentication for Gmail API.

Token stored at ~/.elsa-system/gmail/token.json (per-machine).
credentials.json is portable across machines (download from Google Cloud Console).

Scope: gmail.readonly (read-only, never sends/deletes).
"""

import os
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build


def get_credentials(
    credentials_file: Path, token_file: Path, scopes: list[str]
) -> Credentials:
    """Load or refresh OAuth credentials.

    Raises FileNotFoundError if credentials.json is missing or
    token.json is missing/expired without a refresh token.
    """
    if not credentials_file.exists():
        raise FileNotFoundError(
            f"credentials.json not found at {credentials_file}. "
            f"Download from Google Cloud Console."
        )

    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(
            str(token_file), scopes
        )

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        _save_token(creds, token_file)
        return creds

    raise FileNotFoundError(
        f"No valid token at {token_file}. "
        f"Run: python3.11 gmail_tool.py auth"
    )


def run_auth_flow(
    credentials_file: Path, token_file: Path, scopes: list[str]
) -> None:
    """Interactive OAuth flow. Opens browser for consent."""
    if not credentials_file.exists():
        print(
            f"ERROR: credentials.json not found at {credentials_file}",
            file=sys.stderr,
        )
        print(
            "Download from Google Cloud Console > APIs > Credentials",
            file=sys.stderr,
        )
        sys.exit(1)

    token_file.parent.mkdir(parents=True, exist_ok=True)

    flow = InstalledAppFlow.from_client_secrets_file(
        str(credentials_file), scopes
    )
    creds = flow.run_local_server(port=0)
    _save_token(creds, token_file)
    print(f"Auth successful. Token saved to {token_file}")
    print("Verifying...")

    service = build("gmail", "v1", credentials=creds)
    profile = service.users().getProfile(userId="me").execute()
    print(f"Authenticated as: {profile['emailAddress']}")
    print(f"Total messages: {profile.get('messagesTotal', 'N/A')}")


def get_service(
    credentials_file: Path, token_file: Path, scopes: list[str]
):
    """Return an authenticated Gmail API service object."""
    creds = get_credentials(credentials_file, token_file, scopes)
    return build("gmail", "v1", credentials=creds)


def check_health(
    credentials_file: Path, token_file: Path, scopes: list[str]
) -> None:
    """Verify auth + connectivity. Print status to stdout."""
    checks = {
        "credentials.json": credentials_file.exists(),
        "token.json": token_file.exists(),
    }

    if all(checks.values()):
        try:
            service = get_service(credentials_file, token_file, scopes)
            profile = (
                service.users().getProfile(userId="me").execute()
            )
            checks["api_connection"] = True
            checks["email"] = profile["emailAddress"]
            checks["token_valid"] = True
        except Exception as e:
            checks["api_connection"] = False
            checks["error"] = str(e)

    status = "OK" if checks.get("token_valid") else "FAIL"
    print(f"Gmail Health: {status}")
    for k, v in checks.items():
        print(f"  {k}: {v}")


def _save_token(creds: Credentials, token_file: Path) -> None:
    """Save credentials to token file with restrictive permissions."""
    token_file.write_text(creds.to_json())
    os.chmod(token_file, 0o600)

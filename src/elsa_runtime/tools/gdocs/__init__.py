"""Google Docs / Drive helpers for Elsa.

Public API:
- GoogleDocsReader: read-only Doc content extraction (Tier C, auto-allowed)
- GoogleDriveReader: search / list Drive items (Tier C, auto-allowed)
- GoogleDocsComposer: write operations (Tier A — must be ASK in settings.json)

Scopes required (added to gmail/auth.py SCOPES):
- https://www.googleapis.com/auth/documents (read+write Docs)
- https://www.googleapis.com/auth/drive.readonly (search/list Drive)

Token shared with Gmail tool: ~/.elsa-system/gmail/token.json
(same Google account, single re-auth covers all four scopes.)
"""

from .reader import GoogleDocsReader, GoogleDriveReader
from .composer import GoogleDocsComposer
from .universal import UniversalDocReader, DEFAULT_TEMP_DIR

__all__ = [
    "GoogleDocsReader",
    "GoogleDriveReader",
    "GoogleDocsComposer",
    "UniversalDocReader",
    "DEFAULT_TEMP_DIR",
]

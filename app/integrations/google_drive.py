"""Google Drive adapter - pure HTTP client, no database access.

v1 scope: only text-extractable files - Google Docs (exported as plain
text) and plain-text files. PDFs, Sheets, images etc. are out of scope,
same "text notes only" limitation Knowledge Base already has for
directly-pasted notes (see docs/ARCHITECTURE.md) - extending to real
document parsing is a separate, bigger step.
"""

import httpx

from app.integrations.google_oauth import raise_for_status

DRIVE_API_BASE = "https://www.googleapis.com/drive/v3"
GOOGLE_DOC_MIME = "application/vnd.google-apps.document"
PLAIN_TEXT_MIME = "text/plain"
SUPPORTED_MIME_TYPES = (GOOGLE_DOC_MIME, PLAIN_TEXT_MIME)


async def search_files(access_token: str, name_query: str, limit: int = 5) -> list[dict]:
    escaped = name_query.replace("\\", "\\\\").replace("'", "\\'")
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{DRIVE_API_BASE}/files",
            headers={"Authorization": f"Bearer {access_token}"},
            params={
                "q": f"name contains '{escaped}' and trashed = false",
                "fields": "files(id,name,mimeType)",
                "pageSize": limit,
            },
        )
    raise_for_status(response, "search_files")
    return response.json().get("files", [])


async def get_file_text(access_token: str, file_id: str, mime_type: str) -> str:
    if mime_type == GOOGLE_DOC_MIME:
        url = f"{DRIVE_API_BASE}/files/{file_id}/export"
        params = {"mimeType": "text/plain"}
    else:
        url = f"{DRIVE_API_BASE}/files/{file_id}"
        params = {"alt": "media"}

    async with httpx.AsyncClient() as client:
        response = await client.get(
            url, headers={"Authorization": f"Bearer {access_token}"}, params=params
        )
    raise_for_status(response, "get_file_text")
    return response.text

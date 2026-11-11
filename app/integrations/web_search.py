"""Web search adapter (Serper.dev - Google Search API wrapper) - pure HTTP
client, no database access.

App-level key (app/config.py), not per-user OAuth like the Google
integrations - search isn't scoped to any one user's account.

Replaces Tavily (blocked, 2026-08-12) and a brief detour through Yandex
Search API (2026-08-14, dropped once Serper's account signup - initially
refused - succeeded on a later attempt the same day).
"""

import httpx

from app.config import get_settings

API_URL = "https://google.serper.dev/search"


class WebSearchError(Exception):
    pass


async def search(query: str, max_results: int = 5) -> dict:
    settings = get_settings()
    async with httpx.AsyncClient() as client:
        response = await client.post(
            API_URL,
            headers={"X-API-KEY": settings.serper_api_key, "Content-Type": "application/json"},
            json={"q": query, "num": max_results},
        )
    if response.status_code >= 400:
        raise WebSearchError(f"search failed: {response.status_code} {response.text}")
    body = response.json()

    # Serper has no single "answer" field like Tavily did - closest
    # equivalent is the answer box or knowledge graph description, when
    # Google actually returns one for the query.
    answer = None
    answer_box = body.get("answerBox")
    if answer_box:
        answer = answer_box.get("answer") or answer_box.get("snippet")
    elif body.get("knowledgeGraph", {}).get("description"):
        answer = body["knowledgeGraph"]["description"]

    results = [
        {"title": item.get("title", ""), "content": item.get("snippet", ""), "url": item.get("link", "")}
        for item in body.get("organic", [])[:max_results]
    ]
    return {"answer": answer, "results": results}

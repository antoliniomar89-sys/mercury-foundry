"""Recupero segnali di mercato da fonti pubbliche via HTTP.

Usa httpx (già dipendenza del progetto).
Non fa mai scraping aggressivo: solo API JSON pubbliche e feed ufficiali.
Se tutte le fonti falliscono, ritorna un dict vuoto — il chiamante
gestirà lo stato BLOCKED_NO_WEB_ACCESS.
"""
from __future__ import annotations

from typing import Any

# Fonti pubbliche con API JSON senza autenticazione.
_SIGNAL_SOURCES = [
    {
        "id": "hn_algolia",
        "url": (
            "https://hn.algolia.com/api/v1/search"
            "?tags=ask_hn&query=problem+frustrated+need+help&hitsPerPage=15"
        ),
        "items_path": "hits",
        "text_fields": ["title", "story_text"],
    },
    {
        "id": "reddit_entrepreneur",
        "url": (
            "https://www.reddit.com/r/entrepreneur/search.json"
            "?q=problem+struggling+need+help+service&sort=relevance&limit=10&t=month"
        ),
        "items_path": "data.children",
        "text_fields": ["data.title", "data.selftext"],
    },
    {
        "id": "reddit_smallbusiness",
        "url": (
            "https://www.reddit.com/r/smallbusiness/search.json"
            "?q=problem+help+frustrated+paying&sort=relevance&limit=10&t=month"
        ),
        "items_path": "data.children",
        "text_fields": ["data.title", "data.selftext"],
    },
]

_HEADERS = {
    "User-Agent": "MercuryFoundry/0.1 (opportunity research; non-commercial)"
}
_TIMEOUT_SECONDS = 15.0
_MAX_TEXT_PER_ITEM = 400
_MAX_ITEMS_PER_SOURCE = 20


def _get_nested(obj: Any, dotted_path: str) -> Any:
    """Naviga un percorso dot-separated in un dict/list annidato."""
    for key in dotted_path.split("."):
        if isinstance(obj, dict):
            obj = obj.get(key)
        elif isinstance(obj, list):
            obj = [item.get(key) if isinstance(item, dict) else None for item in obj]
        else:
            return None
        if obj is None:
            return None
    return obj


def _items_to_text(items: Any, text_fields: list[str]) -> str:
    if not isinstance(items, list):
        return ""
    parts: list[str] = []
    for item in items[:_MAX_ITEMS_PER_SOURCE]:
        for field in text_fields:
            val = _get_nested(item, field)
            if val and isinstance(val, str) and val.strip():
                parts.append(val.strip()[:_MAX_TEXT_PER_ITEM])
    return "\n---\n".join(parts)


def fetch_market_signals() -> dict[str, str]:
    """Recupera segnali di mercato da fonti pubbliche.

    Ritorna {source_id: testo} per ogni fonte raggiungibile.
    Ritorna dict vuoto se nessuna fonte è raggiungibile.
    Non rilancia mai eccezioni: i fallimenti individuali sono ignorati silenziosamente.
    """
    try:
        import httpx
    except ImportError:
        return {}

    results: dict[str, str] = {}
    timeout = httpx.Timeout(_TIMEOUT_SECONDS)

    for source in _SIGNAL_SOURCES:
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                response = client.get(source["url"], headers=_HEADERS)
                response.raise_for_status()
                data = response.json()
                items = _get_nested(data, source["items_path"])
                text = _items_to_text(
                    items if isinstance(items, list) else [],
                    source["text_fields"],
                )
                if text.strip():
                    results[source["id"]] = text
        except Exception:
            # Fallimento singola fonte: continua con le altre.
            continue

    return results

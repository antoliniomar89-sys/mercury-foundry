"""Recupero di lead candidati da fonti pubbliche.

Fonti usate:
- GitHub Users API (pubblica, no auth, rate limit 10 req/min unauthenticated)
- HN Algolia API (pubblica, no auth)

Nessuna nuova dipendenza: usa httpx già presente nel repository.
"""
from __future__ import annotations

import time
from typing import Any

_GH_API_BASE = "https://api.github.com"
_HN_API_BASE = "https://hn.algolia.com/api/v1"
_HEADERS = {"User-Agent": "MercuryFoundry/0.1 (lead research; non-commercial)"}
_TIMEOUT = 15.0
_MAX_PER_QUERY = 8


# ------------------------------------------------------------------
# Query building (deterministica, no LLM)
# ------------------------------------------------------------------

def build_search_queries(opportunity: dict) -> list[str]:
    """Costruisce query GitHub-bio dalla descrizione del target.

    Usa keyword matching su termini italiani/inglesi comuni.
    Non usa LLM: è deterministico e non consuma token.
    Ritorna massimo 4 query per limitare le chiamate API.
    """
    target = (opportunity.get("target_customer") or "").lower()
    offer = (opportunity.get("proposed_offer") or "").lower()
    combined = target + " " + offer

    pool: list[str] = []

    # Scrittura / copywriting
    if any(kw in combined for kw in ["scri", "copy", "writer", "content", "text", "email", "document"]):
        pool.append("freelance copywriter in:bio")
        pool.append("content writer freelance in:bio")

    # Marketing / comunicazione
    if any(kw in combined for kw in ["market", "social", "brand", "communic", "comunicaz"]):
        pool.append("email marketer freelance in:bio")

    # Professionisti / consulenti
    if any(kw in combined for kw in ["profess", "freelan", "consulen", "advisor", "independent"]):
        pool.append("freelance professional writer in:bio")

    # Ristorazione / locale
    if any(kw in combined for kw in ["ristor", "bar ", "hotel", "hospit", "food", "local"]):
        pool.append("restaurant owner small business in:bio")
        pool.append("hospitality small business in:bio")

    # Fallback generico
    if not pool:
        pool = ["freelance professional in:bio", "independent consultant writer in:bio"]

    return pool[:4]


# ------------------------------------------------------------------
# GitHub Users API
# ------------------------------------------------------------------

def _fetch_github_users(query: str, max_results: int = _MAX_PER_QUERY) -> list[dict]:
    """Cerca utenti GitHub per bio keyword. Ritorna profili base."""
    try:
        import httpx
    except ImportError:
        return []

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            r = client.get(
                f"{_GH_API_BASE}/search/users",
                params={"q": query, "per_page": max_results},
                headers=_HEADERS,
            )
            r.raise_for_status()
            return r.json().get("items", [])
    except Exception:
        return []


def _fetch_github_profile(login: str) -> dict | None:
    """Recupera il profilo completo di un utente GitHub."""
    try:
        import httpx
    except ImportError:
        return None

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            r = client.get(f"{_GH_API_BASE}/users/{login}", headers=_HEADERS)
            if r.status_code == 200:
                return r.json()
    except Exception:
        pass
    return None


def _github_user_to_candidate(profile: dict, query: str) -> dict:
    """Converte un profilo GitHub in un candidato lead strutturato."""
    blog = (profile.get("blog") or "").strip()
    if blog and not blog.startswith("http"):
        blog = "https://" + blog
    return {
        "source": "github",
        "source_url": profile.get("html_url", ""),
        "name": profile.get("name") or profile.get("login", ""),
        "login": profile.get("login", ""),
        "bio": (profile.get("bio") or "").strip(),
        "website": blog or profile.get("html_url", ""),
        "location": (profile.get("location") or "").strip(),
        "company": (profile.get("company") or "").strip(),
        "email": (profile.get("email") or "").strip(),
        "search_query": query,
    }


# ------------------------------------------------------------------
# HN Algolia — segnali contestuali
# ------------------------------------------------------------------

def _fetch_hn_candidates(opportunity: dict) -> list[dict]:
    """Recupera post HN rilevanti come contesto per la qualificazione."""
    try:
        import httpx
    except ImportError:
        return []

    problem = (opportunity.get("problem") or "")[:80]
    query = problem.split(".")[0][:60] if "." in problem else problem[:60]

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            r = client.get(
                f"{_HN_API_BASE}/search",
                params={"query": query, "tags": "ask_hn", "hitsPerPage": 5},
                headers=_HEADERS,
            )
            r.raise_for_status()
            hits = r.json().get("hits", [])
    except Exception:
        return []

    candidates = []
    for hit in hits:
        author = hit.get("author", "")
        if not author:
            continue
        candidates.append({
            "source": "hn",
            "source_url": f"https://news.ycombinator.com/user?id={author}",
            "name": author,
            "login": author,
            "bio": hit.get("title", ""),
            "website": f"https://news.ycombinator.com/user?id={author}",
            "location": "",
            "company": "",
            "email": "",
            "search_query": query,
        })
    return candidates


# ------------------------------------------------------------------
# Entry point principale
# ------------------------------------------------------------------

def fetch_leads_for_opportunity(opportunity: dict) -> dict[str, list[dict]]:
    """Recupera lead candidati da GitHub e HN per l'opportunità data.

    Ritorna {source_id: [candidati]}.
    Ritorna dict vuoto se tutte le fonti sono irraggiungibili.
    Non rilancia mai eccezioni.
    """
    queries = build_search_queries(opportunity)
    results: dict[str, list[dict]] = {}
    seen_logins: set[str] = set()
    github_candidates: list[dict] = []

    for i, query in enumerate(queries):
        if i > 0:
            # Piccola pausa tra query GitHub per rispettare rate limit
            time.sleep(0.5)

        raw_users = _fetch_github_users(query, max_results=_MAX_PER_QUERY)
        for user in raw_users:
            login = user.get("login", "")
            if login in seen_logins:
                continue
            seen_logins.add(login)

            profile = _fetch_github_profile(login)
            if profile:
                candidate = _github_user_to_candidate(profile, query)
                # Scarta profili vuoti (nessun bio e nessun sito)
                if candidate["bio"] or candidate["website"]:
                    github_candidates.append(candidate)
            time.sleep(0.1)  # rispetta rate limit

        if len(github_candidates) >= 15:
            break  # abbastanza candidati

    if github_candidates:
        results["github"] = github_candidates

    hn_candidates = _fetch_hn_candidates(opportunity)
    if hn_candidates:
        results["hn_context"] = hn_candidates

    return results

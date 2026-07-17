"""Arricchimento leggero di lead tramite fonti pubbliche.

Principi:
- Max 3 fonti consultate per lead (regola QB).
- Nessuna automazione browser, nessun aggiro di login o CAPTCHA.
- Solo HEAD/GET su URL pubblici e GitHub API.
- Nessuna nuova dipendenza: usa httpx già presente.
"""
from __future__ import annotations

import time
from typing import Any

_HEADERS = {
    "User-Agent": "MercuryFoundry/0.1 (lead enrichment; non-commercial)"
}
_TIMEOUT = 8.0
_GH_API_BASE = "https://api.github.com"


def _verify_url(url: str, client: Any) -> bool:
    """Verifica se un URL è raggiungibile (HEAD, poi GET come fallback)."""
    if not url or not url.startswith("http"):
        return False
    try:
        r = client.head(url, follow_redirects=True, timeout=_TIMEOUT)
        return r.status_code < 400
    except Exception:
        pass
    try:
        r = client.get(url, follow_redirects=True, timeout=_TIMEOUT)
        return r.status_code < 400
    except Exception:
        return False


def _fetch_github_secondary(login: str, client: Any) -> dict | None:
    """Recupera profilo GitHub completo per un login noto."""
    if not login:
        return None
    try:
        r = client.get(
            f"{_GH_API_BASE}/users/{login}",
            headers={**_HEADERS, "Accept": "application/vnd.github+json"},
            timeout=_TIMEOUT,
        )
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def _extract_login_from_github_url(url: str) -> str:
    """Estrae il login GitHub da un URL profilo."""
    if "github.com/" not in url:
        return ""
    parts = url.rstrip("/").split("github.com/")
    if len(parts) < 2:
        return ""
    login = parts[1].split("/")[0]
    return login if login and "?" not in login else ""


def enrich_lead(lead: dict, client: Any) -> dict:
    """Arricchisce un singolo lead con al massimo 3 fonti pubbliche.

    Ritorna:
    {
        "lead_id": str,
        "sources_checked": [url, ...],   # max 3
        "is_reachable": bool,            # almeno una fonte raggiungibile
        "secondary_profiles": [url, ...],
        "extra_evidence": str,           # info aggiuntive trovate
    }
    """
    lead_id = str(lead.get("id", "")).strip()
    sources_checked: list[str] = []
    secondary_profiles: list[str] = []
    is_reachable = False
    extra_evidence_parts: list[str] = []

    source_url = str(lead.get("source_url", "")).strip()
    website = str(lead.get("website", "")).strip()
    public_contact = str(lead.get("public_contact", "")).strip()

    # ── Fonte 1: source_url ──────────────────────────────────────────
    if source_url and len(sources_checked) < 3:
        sources_checked.append(source_url)
        if "github.com/" in source_url and "//" in source_url:
            # GitHub profile: sempre accessibile pubblicamente
            is_reachable = True
            login = _extract_login_from_github_url(source_url)
            if login:
                # Tenta fetch profilo completo via API
                profile = _fetch_github_secondary(login, client)
                if profile:
                    blog = (profile.get("blog") or "").strip()
                    if blog and blog != website:
                        secondary_profiles.append(
                            blog if blog.startswith("http") else "https://" + blog
                        )
                    company = (profile.get("company") or "").strip()
                    if company:
                        extra_evidence_parts.append(f"company: {company}")
        else:
            if _verify_url(source_url, client):
                is_reachable = True

    # ── Fonte 2: website (se distinto da source_url) ─────────────────
    if website and website not in sources_checked and len(sources_checked) < 3:
        sources_checked.append(website)
        if _verify_url(website, client):
            is_reachable = True
            time.sleep(0.1)  # cortesia verso server

    # ── Fonte 3: public_contact (se distinto dalle precedenti) ───────
    if (
        public_contact
        and public_contact not in sources_checked
        and len(sources_checked) < 3
    ):
        sources_checked.append(public_contact)
        if not is_reachable and _verify_url(public_contact, client):
            is_reachable = True

    return {
        "lead_id": lead_id,
        "sources_checked": sources_checked,
        "is_reachable": is_reachable,
        "secondary_profiles": secondary_profiles,
        "extra_evidence": "; ".join(extra_evidence_parts),
    }


def enrich_leads(leads: list[dict]) -> dict[str, dict]:
    """Arricchisce una lista di lead. Richiede httpx (già dipendenza).

    Ritorna {lead_id: enrichment_data}.
    Non rilancia mai eccezioni.
    """
    empty: dict[str, dict] = {
        str(l.get("id", "")): {
            "lead_id": str(l.get("id", "")),
            "sources_checked": [],
            "is_reachable": False,
            "secondary_profiles": [],
            "extra_evidence": "",
        }
        for l in leads
    }

    try:
        import httpx
    except ImportError:
        return empty

    results: dict[str, dict] = {}
    try:
        with httpx.Client(
            headers=_HEADERS,
            follow_redirects=True,
            timeout=_TIMEOUT,
        ) as client:
            for lead in leads:
                lead_id = str(lead.get("id", "")).strip()
                try:
                    results[lead_id] = enrich_lead(lead, client)
                except Exception:
                    results[lead_id] = {
                        "lead_id": lead_id,
                        "sources_checked": [],
                        "is_reachable": False,
                        "secondary_profiles": [],
                        "extra_evidence": "",
                    }
    except Exception:
        return empty

    # Assicura che tutti i lead abbiano una entry
    for lead in leads:
        lead_id = str(lead.get("id", "")).strip()
        if lead_id not in results:
            results[lead_id] = empty.get(lead_id, {
                "lead_id": lead_id,
                "sources_checked": [],
                "is_reachable": False,
                "secondary_profiles": [],
                "extra_evidence": "",
            })

    return results

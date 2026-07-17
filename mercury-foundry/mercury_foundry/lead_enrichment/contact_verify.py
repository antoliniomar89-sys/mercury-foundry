"""Verifica reale dei canali di contatto per lead arricchiti.

Ciclo: ENRICHED LEAD → OPEN PUBLIC PAGES → FIND CONTACT CHANNEL → VERIFY → UPDATE

Regole chiave:
- Max 4 pagine per sito (homepage + max 3 pagine interne con keyword di contatto).
- Solo dati realmente trovati — mai inventati, mai derivati da nome/dominio.
- GitHub da solo → NONE (nessun canale diretto).
- LinkedIn /in/ → INDIRECT (connection request pubblica, nessun login).
- mailto: trovato → DIRECT.
- Form HTML (input/textarea + submit) → DIRECT.
- Social pubblico non-GitHub confermato → INDIRECT.
- Semplice HTTP 200 → non basta per DIRECT.

Nessuna nuova dipendenza: usa httpx + stdlib (re, urllib.parse, html.parser).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

from mercury_foundry.lead_enrichment.models import (
    Contactability,
    EnrichedLeadStatus,
)

# ── Costanti ────────────────────────────────────────────────────────────────

_TIMEOUT = 10.0
_MAX_PAGES = 4
_MIN_CONTACTABLE = 3   # soglia per BLOCKED_INSUFFICIENT_CONTACTABLE_LEADS

_HEADERS = {
    "User-Agent": "MercuryFoundry/0.1 (contact-verify; non-commercial)",
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Accept-Language": "en,it;q=0.9",
}

# Keyword per riconoscere link a pagine di contatto interne
_CONTACT_KEYWORDS = frozenset({
    "contact", "contacts", "contatti", "contattaci", "contattami",
    "about", "about-me", "su-di-me", "chi-sono",
    "hire", "hire-me", "work-with-me", "lavora-con-me",
    "get-in-touch", "reach", "reach-me", "scrivimi",
    "touch", "email", "write",
})

# Pattern social riconosciuti (non GitHub)
_SOCIAL_PATTERNS: dict[str, str] = {
    "linkedin":  "linkedin.com/in/",
    "instagram": "instagram.com/",
    "facebook":  "facebook.com/",
    "tiktok":    "tiktok.com/@",
    "twitter":   "twitter.com/",
    "x_com":     "x.com/",
}

# Regex
_MAILTO_RE     = re.compile(r'mailto:([^\s\'">\?\#&]+)', re.IGNORECASE)
_HREF_RE       = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
_TAG_RE        = re.compile(r'<[^>]+>')
_EMAIL_RE      = re.compile(
    r'\b([a-zA-Z0-9][a-zA-Z0-9._%+\-]{1,}@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})\b'
)
# Form: cerca <form ...> ... </form> e dentro verifica input/textarea + submit
_FORM_OPEN_RE  = re.compile(r'<form\b[^>]*>', re.IGNORECASE)
_FORM_CLOSE_RE = re.compile(r'</form\s*>', re.IGNORECASE)
_INPUT_RE      = re.compile(r'<(?:input|textarea)\b', re.IGNORECASE)
_SUBMIT_RE     = re.compile(
    r'type=["\']submit["\']|<button\b[^>]*>\s*[^<]{1,40}',
    re.IGNORECASE
)

# Domini da non visitare come pagine interne
_SKIP_DOMAINS = frozenset({
    "github.com", "linkedin.com", "instagram.com", "facebook.com",
    "tiktok.com", "twitter.com", "x.com", "google.com", "youtube.com",
})


# ── Dataclass risultato per singolo lead ─────────────────────────────────────

@dataclass
class ContactResult:
    """Risultato della verifica canali per un singolo lead."""
    contactability: Contactability = Contactability.NONE
    contact_page_url: str = ""
    verified_email: str = ""
    verified_form_url: str = ""
    verified_social_url: str = ""
    verification_evidence: str = ""


# ── Tipi iniettabili ─────────────────────────────────────────────────────────

ContactVerifyFn = Callable[[list[dict]], list[dict]]


# ── Funzioni di parsing HTML (stdlib re, nessuna dipendenza) ─────────────────

def _extract_emails(html: str) -> list[str]:
    """Estrae email da mailto: e dal testo visibile."""
    from_mailto: list[str] = []
    for m in _MAILTO_RE.findall(html):
        addr = m.strip().rstrip(".,;>)\"]")
        if "@" in addr and "." in addr.split("@")[-1]:
            from_mailto.append(addr.lower())

    text = _TAG_RE.sub(" ", html)
    from_text = [
        e.lower() for e in _EMAIL_RE.findall(text)
        if len(e.split("@")[0]) >= 2 and "." in e.split("@")[1]
        # Escludi falsi positivi comuni (es. file PNG, URL parziali)
        and not e.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".css", ".js"))
    ]
    return list(dict.fromkeys(from_mailto + from_text))


def _has_contact_form(html: str) -> bool:
    """Controlla se la pagina contiene un form con input/textarea e pulsante submit."""
    # Trova tutti i blocchi <form>...</form>
    starts = [m.start() for m in _FORM_OPEN_RE.finditer(html)]
    ends   = [m.end()   for m in _FORM_CLOSE_RE.finditer(html)]

    if not starts:
        return False

    # Abbina ogni apertura alla chiusura successiva
    for s in starts:
        relevant_ends = [e for e in ends if e > s]
        end = relevant_ends[0] if relevant_ends else len(html)
        block = html[s:end]
        if _INPUT_RE.search(block) and _SUBMIT_RE.search(block):
            return True
    return False


def _extract_social(html: str) -> dict[str, str]:
    """Estrae il primo link trovato per ogni piattaforma sociale (non GitHub)."""
    found: dict[str, str] = {}
    for href in _HREF_RE.findall(html):
        href_l = href.lower()
        for platform, pattern in _SOCIAL_PATTERNS.items():
            if platform not in found and pattern in href_l:
                found[platform] = href
    return found


def _extract_contact_links(html: str, base_url: str) -> list[str]:
    """Estrae link interni verso pagine di contatto/about."""
    links: list[str] = []
    base_domain = urlparse(base_url).netloc.lower()

    for href in _HREF_RE.findall(html):
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        href_l = href.lower().rstrip("/")

        # Keyword nel path
        path_parts = href_l.replace("-", " ").replace("_", " ").replace("/", " ").split()
        if not any(kw in path_parts or kw in href_l for kw in _CONTACT_KEYWORDS):
            continue

        # Risolvi URL relativo
        full = urljoin(base_url, href) if not href.startswith("http") else href
        parsed = urlparse(full)

        # Solo stesso dominio, no domini social
        link_domain = parsed.netloc.lower()
        if link_domain != base_domain:
            continue
        if any(skip in link_domain for skip in _SKIP_DOMAINS):
            continue

        links.append(full)

    return list(dict.fromkeys(links))


# ── Fetch di una singola pagina ───────────────────────────────────────────────

def _fetch_page(url: str, client: Any) -> tuple[str, bool]:
    """GET su url. Ritorna (html, ok). Non rilancia mai."""
    try:
        r = client.get(url, follow_redirects=True, timeout=_TIMEOUT, headers=_HEADERS)
        if r.status_code < 400:
            content_type = r.headers.get("content-type", "")
            if "html" in content_type or not content_type:
                return r.text, True
        return "", False
    except Exception:
        return "", False


# ── Classificatori helper ─────────────────────────────────────────────────────

def _is_github_only(url: str) -> bool:
    return bool(url) and "github.com" in url.lower() and "linkedin" not in url.lower()


def _best_social(social: dict[str, str]) -> str:
    """Restituisce il miglior social trovato (LinkedIn > Instagram > altri)."""
    for platform in ("linkedin", "instagram", "facebook", "tiktok", "twitter", "x_com"):
        if platform in social:
            return social[platform]
    return ""


# ── Verifica singolo lead ─────────────────────────────────────────────────────

def verify_single_lead(lead: dict, client: Any) -> ContactResult:
    """Verifica i canali di contatto per un singolo lead. Max _MAX_PAGES pagine.

    client: httpx.Client o mock iniettabile per i test.
    """
    primary  = (lead.get("primary_website") or "").strip()
    pub_cont = (lead.get("public_contact")  or "").strip()
    website  = primary or pub_cont

    # ── Caso 1: nessun sito ───────────────────────────────────────────────────
    if not website:
        return ContactResult(
            contactability=Contactability.NONE,
            verification_evidence="Nessun URL disponibile per la verifica.",
        )

    # ── Caso 2: GitHub solo ───────────────────────────────────────────────────
    if _is_github_only(website):
        return ContactResult(
            contactability=Contactability.NONE,
            verification_evidence=(
                "Solo profilo GitHub. GitHub non offre messaggistica diretta "
                "tra utenti non connessi."
            ),
        )

    # ── Caso 3: LinkedIn /in/ ─────────────────────────────────────────────────
    if "linkedin.com/in/" in website.lower():
        return ContactResult(
            contactability=Contactability.INDIRECT,
            verified_social_url=website,
            verification_evidence=(
                "Profilo LinkedIn /in/ pubblico — "
                "contattabile tramite connection request."
            ),
        )

    # ── Caso 4: sito personale/aziendale — visita reale ──────────────────────
    pages_visited = 0
    emails:       list[str] = []
    form_url:     str       = ""
    social:       dict[str, str] = {}
    contact_page: str       = ""

    # Visita homepage
    html, ok = _fetch_page(website, client)
    if not ok:
        return ContactResult(
            contactability=Contactability.NONE,
            verification_evidence=f"Homepage non raggiungibile: {website}",
        )
    pages_visited += 1

    emails.extend(_extract_emails(html))
    if not form_url and _has_contact_form(html):
        form_url = website
    social.update(_extract_social(html))
    contact_links = _extract_contact_links(html, website)

    # Visita pagine interne di contatto (max 3, max _MAX_PAGES totali)
    for link in contact_links:
        if pages_visited >= _MAX_PAGES:
            break
        if emails or form_url:   # già abbastanza per DIRECT
            break
        if link == website:
            continue

        time.sleep(0.15)  # cortesia verso il server
        html2, ok2 = _fetch_page(link, client)
        if not ok2:
            continue
        pages_visited += 1

        new_emails = _extract_emails(html2)
        if new_emails:
            emails.extend(new_emails)
            if not contact_page:
                contact_page = link
        if not form_url and _has_contact_form(html2):
            form_url = link
            if not contact_page:
                contact_page = link
        social.update(_extract_social(html2))

    # ── Classifica contactability ─────────────────────────────────────────────
    email      = emails[0] if emails else ""
    social_url = _best_social(social)

    if email or form_url:
        contactability = Contactability.DIRECT
    elif social_url:
        contactability = Contactability.INDIRECT
    else:
        contactability = Contactability.NONE

    # Costruisci evidenza
    parts: list[str] = []
    if email:
        parts.append(f"Email: {email}")
    if form_url:
        parts.append(f"Form: {form_url}")
    if social_url:
        parts.append(f"Social: {social_url}")
    if not parts:
        parts.append(
            f"Nessun canale trovato su {pages_visited} "
            f"pagina{'e' if pages_visited > 1 else ''} visitate."
        )

    return ContactResult(
        contactability=contactability,
        contact_page_url=contact_page,
        verified_email=email,
        verified_form_url=form_url,
        verified_social_url=social_url,
        verification_evidence="; ".join(parts),
    )


# ── Aggiornamento dict lead ───────────────────────────────────────────────────

def _qualification_after_verify(
    current_status: str,
    contactability: Contactability,
) -> str:
    """Aggiorna qualification_status in base alla contactability verificata.

    Regola spec: non usare HIGH_FIT con contactability = NONE.
    """
    qs = current_status.upper()
    if contactability == Contactability.NONE:
        if qs in ("HIGH_FIT", "PLAUSIBLE"):
            return EnrichedLeadStatus.NEEDS_REVIEW.value
    return qs


def _apply_verification(lead: dict, result: ContactResult) -> dict:
    """Restituisce una copia aggiornata del lead dict con i dati verificati."""
    updated = dict(lead)
    old_qs = str(lead.get("qualification_status", "NEEDS_REVIEW"))
    new_qs = _qualification_after_verify(old_qs, result.contactability)

    updated["contactability"]        = result.contactability.value
    updated["contact_page_url"]      = result.contact_page_url
    updated["verified_email"]        = result.verified_email
    updated["verified_form_url"]     = result.verified_form_url
    updated["verified_social_url"]   = result.verified_social_url
    updated["verification_evidence"] = result.verification_evidence
    updated["qualification_status"]  = new_qs

    # Aggiorna public_contact e contact_type con dati più precisi
    if result.verified_email:
        updated["public_contact"] = f"mailto:{result.verified_email}"
        updated["contact_type"]   = "email"
    elif result.verified_form_url:
        updated["public_contact"] = result.verified_form_url
        updated["contact_type"]   = "website_form"
    elif result.verified_social_url:
        updated["public_contact"] = result.verified_social_url
        # identifica la piattaforma
        for platform, pattern in _SOCIAL_PATTERNS.items():
            if pattern in result.verified_social_url.lower():
                updated["contact_type"] = platform
                break
        else:
            updated["contact_type"] = "social_profile"

    # next_action coerente
    if result.contactability == Contactability.DIRECT:
        if result.verified_email:
            updated["next_action"] = f"Inviare email a {result.verified_email}"
        elif result.verified_form_url:
            updated["next_action"] = f"Compilare il form su {result.verified_form_url}"
        else:
            updated["next_action"] = "Contattare tramite canale diretto verificato."
    elif result.contactability == Contactability.INDIRECT:
        updated["next_action"] = f"Inviare connection request su {result.verified_social_url or updated.get('public_contact', '')}"
    else:
        updated["next_action"] = "Nessun canale pubblico trovato — lead da rivedere manualmente."

    return updated


# ── Funzione principale (injectable nei test) ────────────────────────────────

def verify_contacts(leads: list[dict]) -> list[dict]:
    """Verifica i canali di contatto per una lista di lead arricchiti.

    Usa httpx (già dipendenza del progetto).
    Non rilancia mai eccezioni: lead non verificabili restano invariati.
    """
    try:
        import httpx
    except ImportError:
        # httpx non disponibile: restituisce lead invariati
        return leads

    updated: list[dict] = []
    try:
        with httpx.Client(
            follow_redirects=True,
            timeout=_TIMEOUT,
            headers=_HEADERS,
        ) as client:
            for lead in leads:
                try:
                    result = verify_single_lead(lead, client)
                    updated.append(_apply_verification(lead, result))
                except Exception:
                    updated.append(dict(lead))
    except Exception:
        return leads

    return updated


# ── ContactVerifier (wrapper con DI per test e CLI) ──────────────────────────

class ContactVerifier:
    """Wrapper per la verifica contatti, con verify_fn iniettabile.

    Uso in produzione:
        verifier = ContactVerifier()
        result = verifier.run(enriched_result_dict)

    Uso nei test:
        verifier = ContactVerifier(verify_fn=fake_fn)
        result = verifier.run(enriched_result_dict)
    """

    def __init__(self, verify_fn: ContactVerifyFn | None = None) -> None:
        self._verify_fn: ContactVerifyFn = (
            verify_fn if verify_fn is not None else verify_contacts
        )
        self._last_result: dict | None = None

    @property
    def last_result(self) -> dict | None:
        return self._last_result

    def run(self, enriched_result: dict) -> dict:
        """Verifica i canali e ritorna un dict aggiornato.

        enriched_result: output di EnrichedLeadResult.to_dict()
        """
        from datetime import datetime, timezone

        timestamp = datetime.now(timezone.utc).isoformat()

        all_leads: list[dict] = enriched_result.get("enriched_leads", []) + [
            d for d in enriched_result.get("rejected_leads", [])
        ]

        if not all_leads:
            result = {
                **enriched_result,
                "status": "BLOCKED_INSUFFICIENT_CONTACTABLE_LEADS",
                "timestamp": timestamp,
                "block_reason": "Nessun lead disponibile per la verifica.",
                "next_action": "Eseguire prima Lead Enrichment Agent.",
            }
            self._last_result = result
            return result

        # Esegui verifica
        updated_leads = self._verify_fn(all_leads)

        # Separa usabili da non-contattabili
        usable   = [l for l in updated_leads if l.get("contactability") != "NONE"]
        noneable = [l for l in updated_leads if l.get("contactability") == "NONE"]

        # Conta per status
        direct_count   = sum(1 for l in updated_leads if l.get("contactability") == "DIRECT")
        indirect_count = sum(1 for l in updated_leads if l.get("contactability") == "INDIRECT")
        contactable    = direct_count + indirect_count

        # Determina status
        if contactable < _MIN_CONTACTABLE:
            status       = "BLOCKED_INSUFFICIENT_CONTACTABLE_LEADS"
            block_reason = (
                f"Solo {contactable} lead con canale verificato "
                f"(DIRECT + INDIRECT), minimo richiesto: {_MIN_CONTACTABLE}."
            )
            next_action = (
                "Ampliare la ricerca di lead o cercare canali aggiuntivi manualmente."
            )
        else:
            has_review  = any(
                l.get("qualification_status") == "NEEDS_REVIEW" for l in usable
            )
            status       = "COMPLETED_WITH_REVIEW" if has_review else "COMPLETED"
            block_reason = None
            next_action  = (
                "Procedere al primo contatto per i lead DIRECT. "
                "Revisionare manualmente i lead INDIRECT e NEEDS_REVIEW."
            )

        result = {
            **enriched_result,
            "status":         status,
            "timestamp":      timestamp,
            "enriched_leads": usable,
            "rejected_leads": noneable,
            "block_reason":   block_reason,
            "next_action":    next_action,
            "high_fit_count":      sum(1 for l in usable if l.get("qualification_status") == "HIGH_FIT"),
            "plausible_count":     sum(1 for l in usable if l.get("qualification_status") == "PLAUSIBLE"),
            "needs_review_count":  sum(1 for l in usable if l.get("qualification_status") == "NEEDS_REVIEW"),
            "rejected_count":      len(noneable),
            "direct_count":        direct_count,
            "indirect_count":      indirect_count,
        }

        self._last_result = result
        return result

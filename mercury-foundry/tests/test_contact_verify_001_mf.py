"""Test MF-QB-CONTACT-VERIFY-001 — Verifica reale canali di contatto.

8 test obbligatori da spec:
1. HTTP 200 da solo non produce DIRECT.
2. mailto: produce DIRECT.
3. Un form reale produce DIRECT.
4. GitHub da solo produce NONE.
5. LinkedIn pubblico produce INDIRECT.
6. Lead NONE non è HIGH_FIT.
7. Nessun contatto inventato.
8. Risultato persistito.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from mercury_foundry.lead_enrichment.contact_verify import (
    ContactVerifier,
    ContactResult,
    _apply_verification,
    _extract_emails,
    _has_contact_form,
    _extract_social,
    verify_single_lead,
)
from mercury_foundry.lead_enrichment.models import Contactability, EnrichedLeadStatus


# ------------------------------------------------------------------
# Mock client per test senza rete reale
# ------------------------------------------------------------------

class MockResponse:
    def __init__(self, text: str = "", status_code: int = 200, content_type: str = "text/html"):
        self.text = text
        self.status_code = status_code
        self.headers = {"content-type": content_type}


class MockClient:
    """Client HTTP mock con risposte configurabili per URL."""

    def __init__(self, responses: dict[str, MockResponse] | None = None, default_status: int = 200):
        self._responses = responses or {}
        self._default_status = default_status
        self.calls: list[str] = []

    def get(self, url: str, **kwargs) -> MockResponse:
        self.calls.append(url)
        if url in self._responses:
            return self._responses[url]
        return MockResponse(text="<html><body>Simple page</body></html>", status_code=self._default_status)


def _make_lead(
    lead_id: str = "test01",
    primary_website: str = "https://example.com",
    public_contact: str = "https://example.com",
    contact_type: str = "website_form",
    source_url: str = "https://github.com/testuser",
    qualification_status: str = "HIGH_FIT",
    contactability: str = "DIRECT",
) -> dict:
    return {
        "lead_id": lead_id,
        "name": "Test User",
        "verified_role_or_business": "Freelance copywriter",
        "target_match": "strong",
        "primary_website": primary_website,
        "public_contact": public_contact,
        "contact_type": contact_type,
        "secondary_profiles": [],
        "evidence_summary": "Bio conferma ruolo.",
        "source_urls": [source_url],
        "fit_reason": "Compatibile con il target.",
        "contactability": contactability,
        "qualification_status": qualification_status,
        "rejection_reason": "",
        "next_action": "Contattare.",
    }


def _make_enriched_result(leads: list[dict]) -> dict:
    return {
        "status": "COMPLETED",
        "timestamp": "2026-07-17T10:31:31+00:00",
        "lead_result_summary": {
            "problem": "Errori di scrittura.",
            "target_customer": "Freelancer.",
            "proposed_offer": "Servizio AI.",
        },
        "enriched_leads": leads,
        "rejected_leads": [],
        "sources_consulted": [],
        "next_action": "Contattare i lead.",
        "block_reason": None,
        "high_fit_count": len(leads),
        "plausible_count": 0,
        "needs_review_count": 0,
        "rejected_count": 0,
    }


# ------------------------------------------------------------------
# Test 1: HTTP 200 da solo non produce DIRECT
# ------------------------------------------------------------------

def test_http_200_alone_does_not_produce_direct():
    """Una pagina che risponde 200 senza email/form/social → NONE."""
    html_no_contact = "<html><body><h1>Welcome</h1><p>Hello world.</p></body></html>"
    client = MockClient(responses={
        "https://example.com": MockResponse(text=html_no_contact, status_code=200),
    })
    lead = _make_lead(primary_website="https://example.com")
    result = verify_single_lead(lead, client)

    assert result.contactability == Contactability.NONE, (
        "HTTP 200 da solo non deve produrre DIRECT."
    )
    assert result.verified_email == ""
    assert result.verified_form_url == ""


# ------------------------------------------------------------------
# Test 2: mailto: produce DIRECT
# ------------------------------------------------------------------

def test_mailto_produces_direct():
    """Una pagina con mailto: → DIRECT con email verificata."""
    html_with_mailto = """
    <html><body>
    <h1>Contact me</h1>
    <a href="mailto:hello@example.com">Write to me</a>
    </body></html>
    """
    client = MockClient(responses={
        "https://example.com": MockResponse(text=html_with_mailto),
    })
    lead = _make_lead(primary_website="https://example.com")
    result = verify_single_lead(lead, client)

    assert result.contactability == Contactability.DIRECT, (
        "Presenza di mailto: deve produrre DIRECT."
    )
    assert result.verified_email == "hello@example.com"
    assert "hello@example.com" in result.verification_evidence


# ------------------------------------------------------------------
# Test 3: Form reale produce DIRECT
# ------------------------------------------------------------------

def test_real_form_produces_direct():
    """Pagina con form HTML (input + submit) → DIRECT con form URL."""
    html_with_form = """
    <html><body>
    <h1>Contact</h1>
    <form action="/send" method="post">
      <input type="text" name="name" placeholder="Your name" />
      <textarea name="message" placeholder="Your message"></textarea>
      <button type="submit">Send</button>
    </form>
    </body></html>
    """
    client = MockClient(responses={
        "https://example.com": MockResponse(text=html_with_form),
    })
    lead = _make_lead(primary_website="https://example.com")
    result = verify_single_lead(lead, client)

    assert result.contactability == Contactability.DIRECT, (
        "Form HTML reale deve produrre DIRECT."
    )
    assert result.verified_form_url != "", "verified_form_url deve essere popolato."
    assert "Form" in result.verification_evidence


# ------------------------------------------------------------------
# Test 4: GitHub da solo produce NONE
# ------------------------------------------------------------------

def test_github_only_produces_none():
    """Un lead con solo GitHub come primary_website → NONE."""
    lead = _make_lead(
        primary_website="https://github.com/someuser",
        public_contact="https://github.com/someuser",
        source_url="https://github.com/someuser",
    )
    client = MockClient()  # non viene chiamato per GitHub
    result = verify_single_lead(lead, client)

    assert result.contactability == Contactability.NONE, (
        "GitHub da solo deve produrre NONE."
    )
    assert result.verified_email == ""
    assert result.verified_form_url == ""
    assert "GitHub" in result.verification_evidence


# ------------------------------------------------------------------
# Test 5: LinkedIn pubblico produce INDIRECT
# ------------------------------------------------------------------

def test_linkedin_produces_indirect():
    """Un lead con profilo LinkedIn /in/ → INDIRECT."""
    lead = _make_lead(
        primary_website="https://www.linkedin.com/in/testuser/",
        public_contact="https://www.linkedin.com/in/testuser/",
    )
    client = MockClient()  # non viene chiamato per LinkedIn
    result = verify_single_lead(lead, client)

    assert result.contactability == Contactability.INDIRECT, (
        "Profilo LinkedIn /in/ deve produrre INDIRECT."
    )
    assert result.verified_social_url != ""
    assert "LinkedIn" in result.verification_evidence or "linkedin" in result.verified_social_url.lower()


# ------------------------------------------------------------------
# Test 6: Lead NONE non è HIGH_FIT
# ------------------------------------------------------------------

def test_none_contactability_cannot_be_high_fit():
    """Dopo verifica con NONE, un lead HIGH_FIT deve essere declassato."""
    lead = _make_lead(qualification_status="HIGH_FIT", contactability="DIRECT")

    # verify_fn che restituisce contactability NONE per tutti i lead
    def fake_verify_fn(leads: list[dict]) -> list[dict]:
        updated = []
        for l in leads:
            updated_lead = dict(l)
            updated_lead["contactability"] = "NONE"
            updated_lead["verification_evidence"] = "Nessun canale trovato."
            updated_lead["verified_email"] = ""
            updated_lead["verified_form_url"] = ""
            updated_lead["verified_social_url"] = ""
            # _apply_verification si occupa di declassare qualification_status
            from mercury_foundry.lead_enrichment.contact_verify import (
                ContactResult, _apply_verification
            )
            cr = ContactResult(
                contactability=Contactability.NONE,
                verification_evidence="Nessun canale trovato.",
            )
            updated.append(_apply_verification(l, cr))
        return updated

    enriched_result = _make_enriched_result([lead])
    verifier = ContactVerifier(verify_fn=fake_verify_fn)
    result = verifier.run(enriched_result)

    all_leads = result.get("enriched_leads", []) + result.get("rejected_leads", [])
    for l in all_leads:
        if l.get("contactability") == "NONE":
            assert l.get("qualification_status") != "HIGH_FIT", (
                "Lead con contactability NONE non può avere qualification_status HIGH_FIT."
            )


# ------------------------------------------------------------------
# Test 7: Nessun contatto inventato
# ------------------------------------------------------------------

def test_no_invented_contact_data():
    """L'agente non deve inventare email, form o social non trovati."""
    html_empty = "<html><body><p>Welcome to my site.</p></body></html>"
    client = MockClient(responses={
        "https://emptysite.example.com": MockResponse(text=html_empty),
    })
    lead = _make_lead(
        primary_website="https://emptysite.example.com",
        public_contact="https://emptysite.example.com",
    )
    result = verify_single_lead(lead, client)

    # Nessun dato inventato
    assert result.verified_email == "", "Nessuna email deve essere inventata."
    assert result.verified_form_url == "", "Nessun form deve essere inventato."
    assert result.verified_social_url == "", "Nessun social deve essere inventato."
    assert result.contactability == Contactability.NONE

    # Verifica che _apply_verification non aggiunga dati non presenti
    updated = _apply_verification(lead, result)
    assert updated["verified_email"] == ""
    assert updated["verified_form_url"] == ""
    assert updated["verified_social_url"] == ""


# ------------------------------------------------------------------
# Test 8: Risultato persistito
# ------------------------------------------------------------------

def test_result_persisted():
    """last_result deve essere popolato e il dict deve poter essere salvato su disco."""

    def fake_verify_fn(leads: list[dict]) -> list[dict]:
        updated = []
        for l in leads:
            from mercury_foundry.lead_enrichment.contact_verify import (
                ContactResult, _apply_verification
            )
            cr = ContactResult(
                contactability=Contactability.DIRECT,
                verified_email="test@example.com",
                verification_evidence="Email: test@example.com",
            )
            updated.append(_apply_verification(l, cr))
        return updated

    leads = [_make_lead(lead_id=f"p{i}") for i in range(3)]
    enriched_result = _make_enriched_result(leads)

    verifier = ContactVerifier(verify_fn=fake_verify_fn)
    assert verifier.last_result is None

    result = verifier.run(enriched_result)
    assert verifier.last_result is result, "last_result deve puntare all'ultimo risultato."
    assert result is not None

    # Verifica serializzazione su disco
    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir) / "contact_verified.json"
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        assert out.exists()
        loaded = json.loads(out.read_text(encoding="utf-8"))
        assert "status" in loaded
        assert "enriched_leads" in loaded


# ------------------------------------------------------------------
# Test helper: verifica parser HTML (sanity check)
# ------------------------------------------------------------------

def test_extract_emails_from_mailto():
    """_extract_emails deve trovare email da mailto: e dal testo."""
    html = '<a href="mailto:user@domain.it">Scrivimi</a>'
    emails = _extract_emails(html)
    assert "user@domain.it" in emails


def test_has_contact_form_detects_form():
    """_has_contact_form deve rilevare form con input + submit."""
    html_form = """
    <form action="/send">
      <input type="text" name="name" />
      <textarea name="msg"></textarea>
      <button type="submit">Invia</button>
    </form>
    """
    assert _has_contact_form(html_form) is True

    html_no_form = "<div><p>No form here.</p></div>"
    assert _has_contact_form(html_no_form) is False


def test_extract_social_finds_linkedin():
    """_extract_social deve trovare link LinkedIn."""
    html = '<a href="https://linkedin.com/in/testuser">LinkedIn</a>'
    social = _extract_social(html)
    assert "linkedin" in social
    assert "linkedin.com/in/testuser" in social["linkedin"]

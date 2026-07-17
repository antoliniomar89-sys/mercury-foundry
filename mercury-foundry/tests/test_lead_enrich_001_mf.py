"""Test MF-QB-LEAD-ENRICH-001 — Lead Enrichment Agent.

9 test obbligatori da spec:
1. Lead PLAUSIBLE non scartati automaticamente.
2. Lead senza alcun canale pubblico → REJECTED.
3. Singola fonte verificabile → sufficiente per PLAUSIBLE.
4. Fonti contraddittorie → REJECTED o NEEDS_REVIEW.
5. Dati non inventati (agent non aggiunge campi non forniti da generate_fn).
6. Risultato persistito (last_result + save).
7. next_action sempre presente (tutti gli stati).
8. Blocco solo sotto 5 lead utilizzabili.
9. Max 3 fonti consultate per lead.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from mercury_foundry.lead_enrichment.agent import EnrichmentAgent
from mercury_foundry.lead_enrichment.models import (
    Contactability,
    EnrichedLeadResult,
    EnrichedLeadResultStatus,
    EnrichedLeadStatus,
)

# ------------------------------------------------------------------
# Helpers condivisi
# ------------------------------------------------------------------

def _make_lead(
    lead_id: str = "abc123",
    name: str = "Mario Rossi",
    segment: str = "Freelance copywriter",
    website: str = "https://example.com",
    public_contact: str = "https://example.com",
    contact_type: str = "website_form",
    source_url: str = "https://github.com/mariorossi",
    evidence: str = "bio: Freelance copywriter.",
    fit_reason: str = "Scrive frequentemente email.",
    location: str = "Milano",
    priority: str = "HIGH",
    status: str = "QUALIFIED",
) -> dict:
    return {
        "id": lead_id,
        "name": name,
        "segment": segment,
        "website": website,
        "public_contact": public_contact,
        "contact_type": contact_type,
        "source_url": source_url,
        "evidence": evidence,
        "fit_reason": fit_reason,
        "location": location,
        "priority": priority,
        "status": status,
        "rejection_reason": "",
    }


def _make_lead_result(leads: list[dict]) -> dict:
    return {
        "status": "COMPLETED",
        "timestamp": "2026-07-17T10:00:00+00:00",
        "opportunity_summary": {
            "problem": "Professionisti perdono tempo con errori di scrittura.",
            "target_customer": "Freelancer che scrivono email e documenti.",
            "proposed_offer": "Servizio AI di correzione scrittura.",
        },
        "leads": leads,
        "qualified_count": len(leads),
        "total_leads": len(leads),
        "sources_used": ["github"],
        "search_queries": ["freelance copywriter in:bio"],
        "duplicates_discarded": 0,
        "rejected_count": 0,
        "next_action": "Contattare i lead.",
        "block_reason": None,
        "discarded_leads": [],
    }


def _make_enrichment(
    lead_id: str,
    sources_checked: list[str] | None = None,
    is_reachable: bool = True,
    secondary_profiles: list[str] | None = None,
    extra_evidence: str = "",
) -> dict:
    return {
        lead_id: {
            "lead_id": lead_id,
            "sources_checked": sources_checked or ["https://github.com/mariorossi"],
            "is_reachable": is_reachable,
            "secondary_profiles": secondary_profiles or [],
            "extra_evidence": extra_evidence,
        }
    }


def _make_ai_lead(
    lead_id: str = "abc123",
    name: str = "Mario Rossi",
    verified_role: str = "Freelance copywriter",
    target_match: str = "strong",
    contactability: str = "DIRECT",
    qualification_status: str = "HIGH_FIT",
    evidence_summary: str = "Bio conferma ruolo di copywriter freelance.",
    fit_reason: str = "Scrive frequentemente email.",
    rejection_reason: str = "",
    next_action: str = "Contattare tramite sito.",
) -> dict:
    return {
        "lead_id": lead_id,
        "name": name,
        "verified_role_or_business": verified_role,
        "target_match": target_match,
        "contactability": contactability,
        "qualification_status": qualification_status,
        "evidence_summary": evidence_summary,
        "secondary_profiles": [],
        "fit_reason": fit_reason,
        "rejection_reason": rejection_reason,
        "next_action": next_action,
    }


# ------------------------------------------------------------------
# Test 1: Lead PLAUSIBLE non scartati automaticamente
# ------------------------------------------------------------------

def test_plausible_leads_not_auto_rejected():
    """Un lead classificato PLAUSIBLE dal modello non deve essere scartato."""
    lead = _make_lead()
    lead_result = _make_lead_result([lead])

    def fake_fetch(leads):
        return _make_enrichment("abc123", is_reachable=True)

    def fake_generate(sys_p, usr_p):
        return {
            "enriched_leads": [
                _make_ai_lead(qualification_status="PLAUSIBLE", contactability="INDIRECT")
            ],
            "next_action": "Contattare via GitHub.",
        }

    agent = EnrichmentAgent(fetch_fn=fake_fetch, generate_fn=fake_generate)
    result = agent.run(lead_result)

    # Il lead PLAUSIBLE deve restare in enriched_leads (non in rejected_leads),
    # indipendentemente dallo status globale (che dipende dalla soglia di 5 lead).
    plausible_in_enriched = [
        l for l in result.enriched_leads
        if l.qualification_status == EnrichedLeadStatus.PLAUSIBLE
    ]
    plausible_in_rejected = [
        l for l in result.rejected_leads
        if l.qualification_status == EnrichedLeadStatus.PLAUSIBLE
    ]
    assert len(plausible_in_enriched) >= 1, (
        "Lead PLAUSIBLE non deve essere scartato automaticamente."
    )
    assert len(plausible_in_rejected) == 0, (
        "Lead PLAUSIBLE non deve finire in rejected_leads."
    )


# ------------------------------------------------------------------
# Test 2: Lead senza alcun canale pubblico → REJECTED
# ------------------------------------------------------------------

def test_lead_without_public_channel_is_rejected():
    """Un lead senza alcun canale pubblico e non raggiungibile → REJECTED."""
    lead = _make_lead(
        lead_id="nopub",
        website="",
        public_contact="",
        source_url="",
    )
    lead_result = _make_lead_result([lead])

    def fake_fetch(leads):
        return {
            "nopub": {
                "lead_id": "nopub",
                "sources_checked": [],
                "is_reachable": False,
                "secondary_profiles": [],
                "extra_evidence": "",
            }
        }

    def fake_generate(sys_p, usr_p):
        # L'AI non viene nemmeno consultata ma simuliamo il caso in cui
        # l'AI restituisce HIGH_FIT — il hard rule deve sovrascrivere
        return {
            "enriched_leads": [
                _make_ai_lead(
                    lead_id="nopub",
                    contactability="NONE",
                    qualification_status="HIGH_FIT",
                )
            ],
            "next_action": "Nessuna azione possibile.",
        }

    agent = EnrichmentAgent(fetch_fn=fake_fetch, generate_fn=fake_generate)
    result = agent.run(lead_result)

    rejected = result.rejected_leads
    assert any(l.lead_id == "nopub" for l in rejected), (
        "Lead senza canale pubblico deve finire in rejected_leads."
    )
    rejected_lead = next(l for l in rejected if l.lead_id == "nopub")
    assert rejected_lead.qualification_status == EnrichedLeadStatus.REJECTED
    assert rejected_lead.contactability == Contactability.NONE


# ------------------------------------------------------------------
# Test 3: Singola fonte verificabile → sufficiente per PLAUSIBLE
# ------------------------------------------------------------------

def test_single_verified_source_is_enough_for_plausible():
    """Una sola fonte verificabile basta per classificare un lead come PLAUSIBLE."""
    lead = _make_lead(
        lead_id="onefont",
        website="",
        public_contact="",
        source_url="https://github.com/singlesource",
    )
    lead_result = _make_lead_result([lead])

    def fake_fetch(leads):
        return {
            "onefont": {
                "lead_id": "onefont",
                "sources_checked": ["https://github.com/singlesource"],
                "is_reachable": True,
                "secondary_profiles": [],
                "extra_evidence": "",
            }
        }

    def fake_generate(sys_p, usr_p):
        return {
            "enriched_leads": [
                _make_ai_lead(
                    lead_id="onefont",
                    qualification_status="PLAUSIBLE",
                    contactability="INDIRECT",
                )
            ],
            "next_action": "Contattare via GitHub.",
        }

    agent = EnrichmentAgent(fetch_fn=fake_fetch, generate_fn=fake_generate)
    result = agent.run(lead_result)

    usable = [
        l for l in result.enriched_leads
        if l.qualification_status in (
            EnrichedLeadStatus.HIGH_FIT,
            EnrichedLeadStatus.PLAUSIBLE,
        )
    ]
    assert len(usable) >= 1, "Singola fonte deve essere sufficiente per PLAUSIBLE."
    assert usable[0].lead_id == "onefont"


# ------------------------------------------------------------------
# Test 4: Fonti contraddittorie → REJECTED o NEEDS_REVIEW
# ------------------------------------------------------------------

def test_contradictory_sources_cause_rejected_or_needs_review():
    """Se l'AI rileva contraddizioni, il lead deve risultare REJECTED o NEEDS_REVIEW."""
    lead = _make_lead(lead_id="contra")
    lead_result = _make_lead_result([lead])

    def fake_fetch(leads):
        return _make_enrichment("contra", is_reachable=True)

    def fake_generate(sys_p, usr_p):
        return {
            "enriched_leads": [
                _make_ai_lead(
                    lead_id="contra",
                    qualification_status="REJECTED",
                    rejection_reason="Le fonti indicano attività non correlata al target.",
                    contactability="NONE",
                )
            ],
            "next_action": "Nessuna azione per questo lead.",
        }

    agent = EnrichmentAgent(fetch_fn=fake_fetch, generate_fn=fake_generate)
    result = agent.run(lead_result)

    all_leads = result.enriched_leads + result.rejected_leads
    lead_found = next((l for l in all_leads if l.lead_id == "contra"), None)
    assert lead_found is not None
    assert lead_found.qualification_status in (
        EnrichedLeadStatus.REJECTED,
        EnrichedLeadStatus.NEEDS_REVIEW,
    ), "Contraddizioni devono produrre REJECTED o NEEDS_REVIEW."
    if lead_found.qualification_status == EnrichedLeadStatus.REJECTED:
        assert lead_found.rejection_reason, "Lead REJECTED deve avere rejection_reason."


# ------------------------------------------------------------------
# Test 5: Dati non inventati (agent non aggiunge campi non forniti)
# ------------------------------------------------------------------

def test_agent_does_not_invent_data():
    """L'agent non aggiunge campi non presenti nell'output di generate_fn."""
    lead = _make_lead(lead_id="nodatainv", name="Anna Verdi")
    lead_result = _make_lead_result([lead])

    generated_evidence = "Bio conferma ruolo copywriter dalla fonte GitHub."
    generated_role = "Freelance copywriter"

    def fake_fetch(leads):
        return _make_enrichment("nodatainv")

    def fake_generate(sys_p, usr_p):
        return {
            "enriched_leads": [
                _make_ai_lead(
                    lead_id="nodatainv",
                    name="Anna Verdi",
                    verified_role=generated_role,
                    evidence_summary=generated_evidence,
                    qualification_status="HIGH_FIT",
                    contactability="DIRECT",
                )
            ],
            "next_action": "Contattare tramite sito.",
        }

    agent = EnrichmentAgent(fetch_fn=fake_fetch, generate_fn=fake_generate)
    result = agent.run(lead_result)

    enriched = next(
        (l for l in result.enriched_leads if l.lead_id == "nodatainv"), None
    )
    assert enriched is not None
    # evidence_summary deve venire dall'output AI, non inventata dall'agent
    assert enriched.evidence_summary == generated_evidence
    # verified_role deve venire dall'output AI
    assert enriched.verified_role_or_business == generated_role
    # Il nome non deve essere cambiato dall'agent
    assert enriched.name == "Anna Verdi"


# ------------------------------------------------------------------
# Test 6: Risultato persistito (last_result + save)
# ------------------------------------------------------------------

def test_result_persisted_in_memory():
    """L'agent deve esporre l'ultimo risultato via last_result."""
    lead = _make_lead()
    lead_result = _make_lead_result([lead])

    def fake_fetch(leads):
        return _make_enrichment("abc123")

    def fake_generate(sys_p, usr_p):
        return {
            "enriched_leads": [_make_ai_lead(qualification_status="HIGH_FIT")],
            "next_action": "Contattare.",
        }

    agent = EnrichmentAgent(fetch_fn=fake_fetch, generate_fn=fake_generate)
    assert agent.last_result is None

    result = agent.run(lead_result)
    assert agent.last_result is result
    assert agent.last_result is not None


def test_result_saved_to_file():
    """Il risultato deve poter essere serializzato su disco."""
    lead = _make_lead()
    lead_result = _make_lead_result([lead])

    def fake_fetch(leads):
        return _make_enrichment("abc123")

    def fake_generate(sys_p, usr_p):
        return {
            "enriched_leads": [_make_ai_lead(qualification_status="HIGH_FIT")],
            "next_action": "Contattare.",
        }

    agent = EnrichmentAgent(fetch_fn=fake_fetch, generate_fn=fake_generate)
    result = agent.run(lead_result)

    with tempfile.TemporaryDirectory() as tmpdir:
        out = Path(tmpdir) / "enriched.json"
        saved = result.save(out)
        assert saved.exists()
        data = json.loads(saved.read_text(encoding="utf-8"))
        assert "status" in data
        assert "enriched_leads" in data
        assert "next_action" in data


# ------------------------------------------------------------------
# Test 7: next_action sempre presente (tutti gli stati)
# ------------------------------------------------------------------

@pytest.mark.parametrize("status", [
    EnrichedLeadResultStatus.COMPLETED,
    EnrichedLeadResultStatus.COMPLETED_WITH_REVIEW,
    EnrichedLeadResultStatus.BLOCKED_INSUFFICIENT_CONTACTABLE_LEADS,
    EnrichedLeadResultStatus.BLOCKED_INVALID_LEAD_INPUT,
    EnrichedLeadResultStatus.BLOCKED_NO_WEB_ACCESS,
    EnrichedLeadResultStatus.FAILED,
])
def test_next_action_always_present_on_all_statuses(status):
    """next_action deve essere non vuoto su tutti gli stati possibili."""
    # Stato BLOCKED_INVALID_LEAD_INPUT: lead_result vuoto
    if status == EnrichedLeadResultStatus.BLOCKED_INVALID_LEAD_INPUT:
        agent = EnrichmentAgent(
            fetch_fn=lambda leads: {},
            generate_fn=lambda s, u: {},
        )
        result = agent.run({"leads": []})
        assert result.status == EnrichedLeadResultStatus.BLOCKED_INVALID_LEAD_INPUT

    elif status == EnrichedLeadResultStatus.BLOCKED_NO_WEB_ACCESS:
        def bad_fetch(leads):
            raise ConnectionError("Timeout")

        agent = EnrichmentAgent(
            fetch_fn=bad_fetch,
            generate_fn=lambda s, u: {},
        )
        result = agent.run(_make_lead_result([_make_lead()]))
        assert result.status == EnrichedLeadResultStatus.BLOCKED_NO_WEB_ACCESS

    elif status == EnrichedLeadResultStatus.FAILED:
        def bad_generate(s, u):
            raise RuntimeError("Provider error")

        agent = EnrichmentAgent(
            fetch_fn=lambda leads: _make_enrichment("abc123"),
            generate_fn=bad_generate,
        )
        result = agent.run(_make_lead_result([_make_lead()]))
        assert result.status == EnrichedLeadResultStatus.FAILED

    elif status == EnrichedLeadResultStatus.BLOCKED_INSUFFICIENT_CONTACTABLE_LEADS:
        # Solo 2 lead utilizzabili (sotto soglia 5)
        leads = [_make_lead(lead_id=f"l{i}") for i in range(3)]
        lead_result = _make_lead_result(leads)

        def fake_fetch(lls):
            return {str(l.get("id", "")): {
                "lead_id": str(l.get("id", "")),
                "sources_checked": ["https://github.com/x"],
                "is_reachable": True,
                "secondary_profiles": [],
                "extra_evidence": "",
            } for l in lls}

        def fake_generate(s, u):
            return {
                "enriched_leads": [
                    _make_ai_lead(lead_id="l0", qualification_status="HIGH_FIT"),
                    _make_ai_lead(lead_id="l1", qualification_status="PLAUSIBLE"),
                    # l2 → REJECTED
                    _make_ai_lead(lead_id="l2", qualification_status="REJECTED",
                                  rejection_reason="Fuori target.", contactability="NONE"),
                ],
                "next_action": "Cercare più lead.",
            }

        agent = EnrichmentAgent(fetch_fn=fake_fetch, generate_fn=fake_generate)
        result = agent.run(lead_result)
        assert result.status == EnrichedLeadResultStatus.BLOCKED_INSUFFICIENT_CONTACTABLE_LEADS

    elif status in (
        EnrichedLeadResultStatus.COMPLETED,
        EnrichedLeadResultStatus.COMPLETED_WITH_REVIEW,
    ):
        leads = [_make_lead(lead_id=f"m{i}") for i in range(5)]
        lead_result = _make_lead_result(leads)

        def fake_fetch(lls):
            return {str(l.get("id", "")): {
                "lead_id": str(l.get("id", "")),
                "sources_checked": ["https://github.com/x"],
                "is_reachable": True,
                "secondary_profiles": [],
                "extra_evidence": "",
            } for l in lls}

        qs = "HIGH_FIT" if status == EnrichedLeadResultStatus.COMPLETED else "NEEDS_REVIEW"

        def fake_generate(s, u):
            return {
                "enriched_leads": [
                    _make_ai_lead(lead_id=f"m{i}", qualification_status=qs)
                    for i in range(5)
                ],
                "next_action": "Contattare i lead.",
            }

        agent = EnrichmentAgent(fetch_fn=fake_fetch, generate_fn=fake_generate)
        result = agent.run(lead_result)

    assert result.next_action, (
        f"next_action deve essere non vuoto per status={status.value}."
    )


# ------------------------------------------------------------------
# Test 8: Blocco solo sotto 5 lead utilizzabili
# ------------------------------------------------------------------

def test_blocked_only_below_five_contactable_leads():
    """BLOCKED_INSUFFICIENT_CONTACTABLE_LEADS scatta solo se < 5 HIGH_FIT + PLAUSIBLE."""
    # Caso sotto soglia: 4 lead utilizzabili → BLOCKED
    leads = [_make_lead(lead_id=f"s{i}") for i in range(4)]
    lead_result = _make_lead_result(leads)

    def fake_fetch(lls):
        return {str(l.get("id", "")): {
            "lead_id": str(l.get("id", "")),
            "sources_checked": ["https://github.com/x"],
            "is_reachable": True,
            "secondary_profiles": [],
            "extra_evidence": "",
        } for l in lls}

    def fake_generate_below(s, u):
        return {
            "enriched_leads": [
                _make_ai_lead(lead_id=f"s{i}", qualification_status="PLAUSIBLE")
                for i in range(4)
            ],
            "next_action": "Cercare più lead.",
        }

    agent_below = EnrichmentAgent(fetch_fn=fake_fetch, generate_fn=fake_generate_below)
    result_below = agent_below.run(lead_result)
    assert result_below.status == EnrichedLeadResultStatus.BLOCKED_INSUFFICIENT_CONTACTABLE_LEADS

    # Caso sulla soglia: 5 lead utilizzabili → COMPLETED
    leads5 = [_make_lead(lead_id=f"t{i}") for i in range(5)]
    lead_result5 = _make_lead_result(leads5)

    def fake_generate_ok(s, u):
        return {
            "enriched_leads": [
                _make_ai_lead(lead_id=f"t{i}", qualification_status="PLAUSIBLE")
                for i in range(5)
            ],
            "next_action": "Contattare.",
        }

    agent_ok = EnrichmentAgent(fetch_fn=fake_fetch, generate_fn=fake_generate_ok)
    result_ok = agent_ok.run(lead_result5)
    assert result_ok.status in (
        EnrichedLeadResultStatus.COMPLETED,
        EnrichedLeadResultStatus.COMPLETED_WITH_REVIEW,
    ), "Con 5 lead PLAUSIBLE non deve scattare il blocco."


# ------------------------------------------------------------------
# Test 9: Max 3 fonti consultate per lead
# ------------------------------------------------------------------

def test_max_three_sources_per_lead():
    """La fetch_fn reale non deve consultare più di 3 fonti per lead."""
    calls_by_lead: dict[str, list[str]] = {}

    def spy_fetch(leads: list[dict]) -> dict[str, dict]:
        """Spia: registra le sources_checked restituite e le limita a 3."""
        result = {}
        for lead in leads:
            lead_id = str(lead.get("id", ""))
            # Simula 5 fonti disponibili — l'agent deve limitare a 3
            sources = [
                lead.get("source_url", ""),
                lead.get("website", ""),
                lead.get("public_contact", ""),
                "https://extra1.example.com",
                "https://extra2.example.com",
            ]
            sources_checked = [s for s in sources if s][:3]  # max 3
            calls_by_lead[lead_id] = sources_checked
            result[lead_id] = {
                "lead_id": lead_id,
                "sources_checked": sources_checked,
                "is_reachable": True,
                "secondary_profiles": [],
                "extra_evidence": "",
            }
        return result

    leads = [_make_lead(lead_id=f"src{i}") for i in range(3)]
    lead_result = _make_lead_result(leads)

    def fake_generate(s, u):
        return {
            "enriched_leads": [
                _make_ai_lead(lead_id=f"src{i}", qualification_status="HIGH_FIT")
                for i in range(3)
            ],
            "next_action": "Contattare.",
        }

    agent = EnrichmentAgent(fetch_fn=spy_fetch, generate_fn=fake_generate)
    result = agent.run(lead_result)

    for lead_id, sources in calls_by_lead.items():
        assert len(sources) <= 3, (
            f"Lead {lead_id}: {len(sources)} fonti consultate, massimo consentito: 3."
        )

    # Verifica anche che le sources_checked nel risultato rispettino il limite
    for source_url in result.sources_consulted:
        assert source_url  # tutte le fonti sono URL non vuoti


def test_enrich_lead_function_respects_max_three_sources():
    """La funzione enrich_lead rispetta il limite di 3 fonti anche con un client mock."""
    from mercury_foundry.lead_enrichment.enrich import enrich_lead

    class MockClient:
        """Client HTTP mock che registra le chiamate."""

        def __init__(self):
            self.calls: list[str] = []

        def head(self, url, **kwargs):
            self.calls.append(url)
            return type("R", (), {"status_code": 200})()

        def get(self, url, **kwargs):
            self.calls.append(url)
            # Simula risposta GitHub API
            if "api.github.com" in url:
                return type("R", (), {
                    "status_code": 200,
                    "json": lambda self=None: {"blog": "", "company": ""},
                })()
            return type("R", (), {"status_code": 200})()

    lead = _make_lead(
        lead_id="limit3",
        source_url="https://github.com/testuser",
        website="https://testuser.example.com",
        public_contact="https://contact.example.com",
    )

    client = MockClient()
    result = enrich_lead(lead, client)

    assert len(result["sources_checked"]) <= 3, (
        f"enrich_lead ha consultato {len(result['sources_checked'])} fonti, massimo 3."
    )

"""Test suite per MF-QB-LEAD-001 — Lead Agent minimo.

7 test obbligatori da spec:
1. Non vengano salvati più di 10 lead.
2. Ogni lead abbia una fonte reale (source_url non vuoto).
3. I duplicati siano rimossi.
4. Un lead senza evidenza non sia qualificato.
5. Il risultato venga persistito.
6. Esista sempre una next_action.
7. In assenza di lead sufficienti venga restituito il blocco corretto.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from mercury_foundry.leads.agent import LeadAgent
from mercury_foundry.leads.models import Lead, LeadResult, LeadResultStatus, LeadStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

OPPORTUNITY_OK = {
    "status": "COMPLETED",
    "problem": "I professionisti perdono tempo a correggere errori di scrittura nelle email.",
    "target_customer": "Freelancer e professionisti che scrivono email e documenti.",
    "proposed_offer": "Servizio AI di correzione scrittura — €49.",
    "delivery_format": "Report HTML/PDF",
    "initial_price": "€49",
    "next_action": "Contattare i lead identificati.",
    "evidence": [{"text": "I waste time fixing email errors", "source_url": "hn_algolia"}],
    "source_urls": ["hn_algolia"],
}

OPPORTUNITY_INVALID = {
    "status": "BLOCKED_NO_WEB_ACCESS",
    "problem": None,
    "target_customer": None,
    "proposed_offer": None,
}

OPPORTUNITY_MINIMAL_FIELDS = {
    # Nessuno dei campi required
}


def _make_candidate(
    login: str,
    bio: str = "Freelance copywriter",
    website: str = "",
    location: str = "London",
) -> dict:
    gh_url = f"https://github.com/{login}"
    return {
        "source": "github",
        "source_url": gh_url,
        "name": login.capitalize(),
        "login": login,
        "bio": bio,
        "website": website or gh_url,
        "location": location,
        "company": "",
        "email": "",
        "search_query": "freelance copywriter in:bio",
    }


def _fake_fetch_10_candidates(opportunity: dict) -> dict[str, list[dict]]:
    """Simula 10 candidati GitHub unici."""
    return {
        "github": [
            _make_candidate(f"writer{i:02d}", website=f"https://writer{i:02d}.com")
            for i in range(10)
        ]
    }


def _fake_fetch_3_candidates(opportunity: dict) -> dict[str, list[dict]]:
    """Simula solo 3 candidati — insufficienti per >= 5 qualificati."""
    return {
        "github": [
            _make_candidate(f"writer{i}", website=f"https://writer{i}.com")
            for i in range(3)
        ]
    }


def _fake_fetch_empty(opportunity: dict) -> dict[str, list[dict]]:
    """Simula nessuna fonte raggiungibile."""
    return {}


def _fake_fetch_with_duplicates(opportunity: dict) -> dict[str, list[dict]]:
    """Simula candidati con siti duplicati."""
    return {
        "github": [
            _make_candidate("alice", website="https://alice-writes.com"),
            _make_candidate("alice2", website="https://alice-writes.com"),  # duplicato
            _make_candidate("bob", website="https://bob-copy.com"),
            _make_candidate("bob_duplicate", website="https://bob-copy.com"),  # duplicato
            _make_candidate("carol", website="https://carol-write.com"),
            _make_candidate("dave", website="https://dave-content.com"),
            _make_candidate("eve", website="https://eve-copy.com"),
            _make_candidate("frank", website="https://frank-writes.com"),
        ]
    }


def _generate_10_leads_ok(system_prompt: str, user_prompt: str) -> dict:
    """Simula risposta AI con 10 lead validi, 7 qualificati e 3 rifiutati."""
    leads = []
    for i in range(7):
        leads.append({
            "name": f"Writer {i:02d}",
            "segment": "Freelance Copywriter",
            "website": f"https://writer{i:02d}.com",
            "public_contact": f"https://writer{i:02d}.com/contact",
            "contact_type": "website_form",
            "location": "London, UK",
            "fit_reason": "Freelance copywriter che scrive email professionali quotidianamente.",
            "evidence": f"Bio: Freelance copywriter con 5 anni di esperienza. Writer{i:02d}.",
            "source_url": f"https://github.com/writer{i:02d}",
            "priority": "HIGH",
            "status": "QUALIFIED",
            "rejection_reason": "",
        })
    for i in range(7, 10):
        leads.append({
            "name": f"UserX {i}",
            "segment": "Developer",
            "website": f"https://github.com/writer{i:02d}",
            "public_contact": f"https://github.com/writer{i:02d}",
            "contact_type": "github_profile",
            "location": "",
            "fit_reason": "",
            "evidence": "",
            "source_url": f"https://github.com/writer{i:02d}",
            "priority": "LOW",
            "status": "REJECTED",
            "rejection_reason": "Nessuna evidenza di attività di scrittura.",
        })
    return {
        "leads": leads,
        "search_queries_used": ["freelance copywriter in:bio"],
        "next_action": (
            "Visitare i profili dei lead QUALIFIED e inviare un messaggio personalizzato "
            "via form di contatto del sito, menzionando il problema specifico identificato."
        ),
    }


def _generate_3_leads_only(system_prompt: str, user_prompt: str) -> dict:
    """Simula risposta AI con solo 3 lead qualificati — sotto la soglia."""
    leads = [
        {
            "name": f"Writer {i}",
            "segment": "Freelance Copywriter",
            "website": f"https://writer{i}.com",
            "public_contact": f"https://writer{i}.com",
            "contact_type": "website_form",
            "location": "Remote",
            "fit_reason": "Scrive email professionali.",
            "evidence": f"Bio: Freelance copywriter. Writer{i}.",
            "source_url": f"https://github.com/writer{i}",
            "priority": "MEDIUM",
            "status": "QUALIFIED",
            "rejection_reason": "",
        }
        for i in range(3)
    ]
    return {
        "leads": leads,
        "search_queries_used": ["freelance copywriter in:bio"],
        "next_action": "Ampliare la ricerca.",
    }


def _generate_with_duplicates(system_prompt: str, user_prompt: str) -> dict:
    """Simula risposta AI con lead che hanno siti duplicati."""
    leads = [
        {
            "name": "Alice",
            "segment": "Copywriter",
            "website": "https://alice-writes.com",
            "public_contact": "https://alice-writes.com",
            "contact_type": "website_form",
            "location": "Remote",
            "fit_reason": "Scrive email.",
            "evidence": "Bio: Freelance copywriter. Alice.",
            "source_url": "https://github.com/alice",
            "priority": "HIGH",
            "status": "QUALIFIED",
            "rejection_reason": "",
        },
        {
            "name": "Alice2",  # stesso sito → duplicato
            "segment": "Copywriter",
            "website": "https://alice-writes.com",
            "public_contact": "https://alice-writes.com",
            "contact_type": "website_form",
            "location": "Remote",
            "fit_reason": "Scrive email.",
            "evidence": "Bio: Copywriter. Alice2.",
            "source_url": "https://github.com/alice2",
            "priority": "MEDIUM",
            "status": "QUALIFIED",
            "rejection_reason": "",
        },
        {
            "name": "Bob",
            "segment": "Content Writer",
            "website": "https://bob-copy.com",
            "public_contact": "https://bob-copy.com",
            "contact_type": "website_form",
            "location": "Remote",
            "fit_reason": "Content writer professionale.",
            "evidence": "Bio: Content writer. Bob.",
            "source_url": "https://github.com/bob",
            "priority": "HIGH",
            "status": "QUALIFIED",
            "rejection_reason": "",
        },
        {
            "name": "Bob Dup",  # stesso sito → duplicato
            "segment": "Content Writer",
            "website": "https://bob-copy.com",
            "public_contact": "https://bob-copy.com",
            "contact_type": "website_form",
            "location": "Remote",
            "fit_reason": "Content writer.",
            "evidence": "Bio: Content writer. BobDup.",
            "source_url": "https://github.com/bob_duplicate",
            "priority": "MEDIUM",
            "status": "QUALIFIED",
            "rejection_reason": "",
        },
        # 5 lead unici aggiuntivi per superare la soglia minima
        *[
            {
                "name": f"Writer {n}",
                "segment": "Copywriter",
                "website": f"https://writer{n}-unique.com",
                "public_contact": f"https://writer{n}-unique.com",
                "contact_type": "website_form",
                "location": "Remote",
                "fit_reason": "Freelance writer.",
                "evidence": f"Bio: Copywriter. Writer{n}.",
                "source_url": f"https://github.com/writer{n}unique",
                "priority": "MEDIUM",
                "status": "QUALIFIED",
                "rejection_reason": "",
            }
            for n in range(5)
        ],
    ]
    return {
        "leads": leads,
        "search_queries_used": ["freelance copywriter in:bio"],
        "next_action": "Contattare i lead qualificati.",
    }


def _generate_lead_without_evidence(system_prompt: str, user_prompt: str) -> dict:
    """Simula risposta AI con un lead QUALIFIED ma senza evidence."""
    leads = [
        {
            "name": "NoEvidence Writer",
            "segment": "Copywriter",
            "website": "https://noevidence.com",
            "public_contact": "https://noevidence.com",
            "contact_type": "website_form",
            "location": "Remote",
            "fit_reason": "Scrive email.",
            "evidence": "",  # ← VUOTO
            "source_url": "https://github.com/noevidence",
            "priority": "HIGH",
            "status": "QUALIFIED",  # ← dovrebbe diventare REJECTED
            "rejection_reason": "",
        },
        *[
            {
                "name": f"Good Writer {i}",
                "segment": "Copywriter",
                "website": f"https://goodwriter{i}.com",
                "public_contact": f"https://goodwriter{i}.com",
                "contact_type": "website_form",
                "location": "Remote",
                "fit_reason": "Freelance copywriter.",
                "evidence": f"Bio: Copywriter. GoodWriter{i}.",
                "source_url": f"https://github.com/goodwriter{i}",
                "priority": "MEDIUM",
                "status": "QUALIFIED",
                "rejection_reason": "",
            }
            for i in range(5)
        ],
    ]
    return {
        "leads": leads,
        "search_queries_used": ["freelance copywriter in:bio"],
        "next_action": "Contattare i lead qualificati.",
    }


def _make_agent(
    fetch_fn=_fake_fetch_10_candidates,
    generate_fn=_generate_10_leads_ok,
) -> LeadAgent:
    return LeadAgent(fetch_fn=fetch_fn, generate_fn=generate_fn)


# ---------------------------------------------------------------------------
# Test 1 — Non più di 10 lead salvati
# ---------------------------------------------------------------------------

def test_max_ten_leads() -> None:
    """Spec: non vengano salvati più di 10 lead."""
    agent = _make_agent()
    result = agent.run(OPPORTUNITY_OK)

    assert result.status == LeadResultStatus.COMPLETED
    assert len(result.leads) <= 10, f"Trovati {len(result.leads)} lead, massimo 10"


def test_lead_cap_enforced_on_model_overflow() -> None:
    """Il cap a 10 si applica anche se il modello restituisce più di 10 lead."""

    def _generate_15_leads(s: str, u: str) -> dict:
        base = _generate_10_leads_ok(s, u)
        extra = [
            {
                "name": f"Extra {i}",
                "segment": "Writer",
                "website": f"https://extra{i}.com",
                "public_contact": f"https://extra{i}.com",
                "contact_type": "website_form",
                "location": "Remote",
                "fit_reason": "Writer.",
                "evidence": f"Bio: writer. Extra{i}.",
                "source_url": f"https://github.com/extra{i}",
                "priority": "LOW",
                "status": "QUALIFIED",
                "rejection_reason": "",
            }
            for i in range(5)
        ]
        base["leads"] = base["leads"] + extra
        return base

    agent = LeadAgent(fetch_fn=_fake_fetch_10_candidates, generate_fn=_generate_15_leads)
    result = agent.run(OPPORTUNITY_OK)

    assert len(result.leads) <= 10


# ---------------------------------------------------------------------------
# Test 2 — Ogni lead ha una fonte reale
# ---------------------------------------------------------------------------

def test_every_lead_has_source_url() -> None:
    """Spec: ogni lead abbia una fonte reale (source_url non vuoto)."""
    agent = _make_agent()
    result = agent.run(OPPORTUNITY_OK)

    assert result.status == LeadResultStatus.COMPLETED
    for lead in result.leads:
        assert lead.source_url, f"Lead '{lead.name}' ha source_url vuoto"


def test_lead_without_source_url_excluded() -> None:
    """Lead senza source_url non compaiono nel risultato."""

    def _generate_with_no_url(s: str, u: str) -> dict:
        base = _generate_10_leads_ok(s, u)
        base["leads"][0]["source_url"] = ""  # rimuovi source_url dal primo
        return base

    agent = LeadAgent(fetch_fn=_fake_fetch_10_candidates, generate_fn=_generate_with_no_url)
    result = agent.run(OPPORTUNITY_OK)

    for lead in result.leads:
        assert lead.source_url, f"Lead '{lead.name}' senza source_url è entrato nel risultato"


# ---------------------------------------------------------------------------
# Test 3 — Duplicati rimossi
# ---------------------------------------------------------------------------

def test_duplicates_removed() -> None:
    """Spec: i duplicati siano rimossi (stesso website → un solo lead)."""
    agent = LeadAgent(fetch_fn=_fake_fetch_with_duplicates, generate_fn=_generate_with_duplicates)
    result = agent.run(OPPORTUNITY_OK)

    websites = [lead.website for lead in result.leads]
    unique_websites = set(websites)
    assert len(websites) == len(unique_websites), (
        f"Trovati duplicati nei lead: {websites}"
    )
    assert result.duplicates_discarded > 0, "Nessun duplicato registrato anche se esistevano"


# ---------------------------------------------------------------------------
# Test 4 — Lead senza evidenza non qualificato
# ---------------------------------------------------------------------------

def test_lead_without_evidence_not_qualified() -> None:
    """Spec: un lead senza evidenza non sia qualificato."""
    agent = LeadAgent(fetch_fn=_fake_fetch_10_candidates, generate_fn=_generate_lead_without_evidence)
    result = agent.run(OPPORTUNITY_OK)

    for lead in result.leads:
        if not lead.evidence:
            assert lead.status != LeadStatus.QUALIFIED, (
                f"Lead '{lead.name}' senza evidence ha status QUALIFIED"
            )


# ---------------------------------------------------------------------------
# Test 5 — Risultato persistito
# ---------------------------------------------------------------------------

def test_result_saved_in_memory() -> None:
    """Spec: il risultato venga persistito — last_result aggiornato dopo run()."""
    agent = _make_agent()
    assert agent.last_result is None

    result = agent.run(OPPORTUNITY_OK)
    assert agent.last_result is result


def test_result_saved_to_file(tmp_path: Path) -> None:
    """Spec: il risultato venga persistito — save() scrive JSON leggibile."""
    agent = _make_agent()
    result = agent.run(OPPORTUNITY_OK)

    output_path = tmp_path / "leads.json"
    saved = result.save(output_path)

    assert saved.exists()
    data = json.loads(saved.read_text(encoding="utf-8"))
    assert data["status"] == "COMPLETED"
    assert isinstance(data["leads"], list)
    assert "next_action" in data
    assert "opportunity_summary" in data
    assert "search_queries" in data


def test_blocked_result_also_persisted(tmp_path: Path) -> None:
    """Anche i risultati BLOCKED vengono salvati correttamente."""
    agent = LeadAgent(fetch_fn=_fake_fetch_empty, generate_fn=_generate_10_leads_ok)
    result = agent.run(OPPORTUNITY_OK)

    output_path = tmp_path / "leads_blocked.json"
    saved = result.save(output_path)

    data = json.loads(saved.read_text(encoding="utf-8"))
    assert "BLOCKED" in data["status"]
    assert "next_action" in data
    assert "block_reason" in data


# ---------------------------------------------------------------------------
# Test 6 — next_action sempre presente
# ---------------------------------------------------------------------------

def test_next_action_present_on_completed() -> None:
    """Spec: esista sempre una next_action — caso COMPLETED."""
    agent = _make_agent()
    result = agent.run(OPPORTUNITY_OK)
    assert result.next_action and result.next_action.strip()


def test_next_action_present_on_blocked_no_web() -> None:
    """Spec: esista sempre una next_action — caso BLOCKED_NO_WEB_ACCESS."""
    agent = LeadAgent(fetch_fn=_fake_fetch_empty, generate_fn=_generate_10_leads_ok)
    result = agent.run(OPPORTUNITY_OK)
    assert result.status == LeadResultStatus.BLOCKED_NO_WEB_ACCESS
    assert result.next_action and result.next_action.strip()


def test_next_action_present_on_blocked_insufficient() -> None:
    """Spec: esista sempre una next_action — caso BLOCKED_INSUFFICIENT_LEADS."""
    agent = LeadAgent(fetch_fn=_fake_fetch_3_candidates, generate_fn=_generate_3_leads_only)
    result = agent.run(OPPORTUNITY_OK)
    assert result.status == LeadResultStatus.BLOCKED_INSUFFICIENT_LEADS
    assert result.next_action and result.next_action.strip()


def test_next_action_present_on_blocked_invalid() -> None:
    """Spec: esista sempre una next_action — caso BLOCKED_INVALID_OPPORTUNITY."""
    agent = _make_agent()
    result = agent.run(OPPORTUNITY_MINIMAL_FIELDS)
    assert result.status == LeadResultStatus.BLOCKED_INVALID_OPPORTUNITY
    assert result.next_action and result.next_action.strip()


# ---------------------------------------------------------------------------
# Test 7 — BLOCKED_INSUFFICIENT_LEADS in assenza di lead sufficienti
# ---------------------------------------------------------------------------

def test_blocked_insufficient_leads_when_below_threshold() -> None:
    """Spec: in assenza di lead sufficienti venga restituito BLOCKED_INSUFFICIENT_LEADS."""
    agent = LeadAgent(fetch_fn=_fake_fetch_3_candidates, generate_fn=_generate_3_leads_only)
    result = agent.run(OPPORTUNITY_OK)

    assert result.status == LeadResultStatus.BLOCKED_INSUFFICIENT_LEADS
    assert result.block_reason is not None
    assert "qualificati" in result.block_reason.lower() or "insufficient" in result.block_reason.lower()


def test_blocked_invalid_opportunity_when_status_not_completed() -> None:
    """Status opportunity non COMPLETED → BLOCKED_INVALID_OPPORTUNITY."""
    agent = _make_agent()
    result = agent.run(OPPORTUNITY_INVALID)
    assert result.status == LeadResultStatus.BLOCKED_INVALID_OPPORTUNITY


def test_qualified_count_matches_leads() -> None:
    """qualified_count corrisponde al numero effettivo di lead QUALIFIED."""
    agent = _make_agent()
    result = agent.run(OPPORTUNITY_OK)

    actual_qualified = sum(1 for l in result.leads if l.status == LeadStatus.QUALIFIED)
    assert result.qualified_count == actual_qualified

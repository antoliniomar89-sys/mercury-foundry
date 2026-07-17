"""Test suite per MF-QB-OPPORTUNITY-001 — Opportunity Agent minimo.

6 test obbligatori da spec:
1. Non vengano restituiti più di 3 problemi candidati.
2. Esista una sola offerta finale (campo scalare, non lista).
3. Ogni evidenza abbia una fonte (source_url non vuoto).
4. In assenza di accesso reale il sistema non inventi dati (BLOCKED_NO_WEB_ACCESS).
5. Il risultato venga salvato.
6. Esista sempre una next_action.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from mercury_foundry.opportunity.agent import OpportunityAgent
from mercury_foundry.opportunity.models import Evidence, OpportunityResult, OpportunityStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _fake_fetch_ok() -> dict[str, str]:
    """Simula una fetch riuscita con testo di segnale realistico."""
    return {
        "hn_algolia": (
            "I'm a small restaurant owner and I'm losing customers because I have no "
            "online presence. My reviews on Google are terrible and I don't know how to respond.\n---\n"
            "Ask HN: How do I get more bookings for my bar? We have great cocktails but nobody knows us.\n---\n"
            "Struggling with social media for my cafe — I pay someone €200/month and it does nothing."
        ),
        "reddit_entrepreneur": (
            "My small business has terrible Google reviews and I don't know how to fix them.\n---\n"
            "Anyone know how to improve local SEO for a restaurant? Losing to competitors.\n---\n"
            "Need help writing copy for my menu — customers say it sounds boring."
        ),
    }


def _fake_fetch_empty() -> dict[str, str]:
    """Simula una fetch fallita (nessuna fonte raggiungibile)."""
    return {}


def _fake_generate_valid(system_prompt: str, user_prompt: str) -> dict:
    """Simula una risposta AI valida con 3 candidati e 1 offerta."""
    return {
        "status": "COMPLETED",
        "candidates": [
            {
                "problem": "I ristoratori locali non sanno rispondere alle recensioni negative su Google.",
                "target_customer": "Titolari di bar e ristoranti con 10-50 dipendenti.",
                "evidence": [
                    {
                        "text": "My reviews on Google are terrible and I don't know how to respond.",
                        "source_url": "hn_algolia",
                    },
                    {
                        "text": "My small business has terrible Google reviews and I don't know how to fix them.",
                        "source_url": "reddit_entrepreneur",
                    },
                ],
                "frequency_signal": "Emerge in più thread diversi su HN e Reddit nello stesso periodo.",
                "urgency_signal": "Le recensioni negative riducono direttamente le prenotazioni.",
                "willingness_to_pay_signal": "Già spendono €200/mese per social media con risultati nulli.",
            },
            {
                "problem": "I bar non riescono a farsi conoscere online nonostante prodotti di qualità.",
                "target_customer": "Proprietari di bar indipendenti in città medie.",
                "evidence": [
                    {
                        "text": "How do I get more bookings for my bar? We have great cocktails but nobody knows us.",
                        "source_url": "hn_algolia",
                    }
                ],
                "frequency_signal": "Segnale ricorrente su piattaforme di domande business.",
                "urgency_signal": "La stagione estiva aumenta la pressione competitiva.",
                "willingness_to_pay_signal": "Implicitamente disposti a pagare per visibilità immediata.",
            },
            {
                "problem": "I menu di ristoranti hanno copy non persuasivo che riduce l'average ticket.",
                "target_customer": "Ristoratori con menu digitale o stampato aggiornabile.",
                "evidence": [
                    {
                        "text": "Need help writing copy for my menu — customers say it sounds boring.",
                        "source_url": "reddit_entrepreneur",
                    }
                ],
                "frequency_signal": "Presente su Reddit entrepreneur come pain point specifico.",
                "urgency_signal": "Impatto diretto sullo scontrino medio.",
                "willingness_to_pay_signal": "Disponibilità implicita per servizio di copywriting.",
            },
        ],
        "selected_index": 0,
        "proposed_offer": (
            "Audit delle recensioni Google + 5 risposte professionali personalizzate "
            "consegnate entro 24h — €79."
        ),
        "delivery_format": "Documento HTML/PDF consegnato via email entro 24h.",
        "initial_price": "€79 lancio / €149 standard",
        "why_testable_fast": (
            "Un campione può essere prodotto in 2h con AI: bastano le recensioni pubbliche "
            "del cliente e il nome del locale."
        ),
        "risks": [
            "Il titolare potrebbe non avere accesso alle risposte Google.",
            "Difficile scalare senza un minimo di automazione del delivery.",
        ],
        "next_action": (
            "Identificare 5 ristoranti nella propria città con recensioni negative su Google "
            "e contattarli con un campione gratuito entro 48h."
        ),
    }


def _fake_generate_blocked(system_prompt: str, user_prompt: str) -> dict:
    """Simula una risposta AI con evidenze insufficienti."""
    return {
        "status": "BLOCKED_NO_EVIDENCE",
    }


def _make_agent_ok() -> OpportunityAgent:
    return OpportunityAgent(
        fetch_fn=_fake_fetch_ok,
        generate_fn=_fake_generate_valid,
    )


# ---------------------------------------------------------------------------
# Test 1 — Non più di 3 problemi candidati
# ---------------------------------------------------------------------------

def test_max_three_candidate_problems() -> None:
    """Spec: non vengano restituiti più di 3 problemi candidati."""
    agent = _make_agent_ok()
    result = agent.run("test mandate")

    assert result.status == OpportunityStatus.COMPLETED
    assert len(result.candidates_considered) <= 3, (
        f"Attesi max 3 candidati, trovati {len(result.candidates_considered)}"
    )


def test_candidate_cap_enforced_on_model_overflow() -> None:
    """Il modello applica il cap anche se generate_fn restituisce più di 3 candidati."""

    def _greedy_generate(s: str, u: str) -> dict:
        base = _fake_generate_valid(s, u)
        # Inietta 5 candidati invece di 3
        extra = {
            "problem": f"Problema extra {i}",
            "target_customer": "Target X",
            "evidence": [{"text": f"evidenza {i}", "source_url": f"https://example.com/{i}"}],
            "frequency_signal": "bassa",
            "urgency_signal": "media",
            "willingness_to_pay_signal": "incerta",
        }
        base["candidates"] = base["candidates"] + [extra, {**extra, "problem": "Problema extra 2"}]
        return base

    agent = OpportunityAgent(fetch_fn=_fake_fetch_ok, generate_fn=_greedy_generate)
    result = agent.run()
    assert len(result.candidates_considered) <= 3


# ---------------------------------------------------------------------------
# Test 2 — Una sola offerta finale
# ---------------------------------------------------------------------------

def test_single_final_offer() -> None:
    """Spec: esista una sola offerta finale."""
    agent = _make_agent_ok()
    result = agent.run()

    assert result.status == OpportunityStatus.COMPLETED
    # proposed_offer deve essere una stringa scalare, non una lista
    assert isinstance(result.proposed_offer, str), (
        f"proposed_offer deve essere str, trovato {type(result.proposed_offer)}"
    )
    assert result.proposed_offer.strip(), "proposed_offer non deve essere vuota"


# ---------------------------------------------------------------------------
# Test 3 — Ogni evidenza ha una fonte
# ---------------------------------------------------------------------------

def test_every_evidence_has_source_url() -> None:
    """Spec: ogni evidenza abbia una fonte (source_url non vuoto)."""
    agent = _make_agent_ok()
    result = agent.run()

    assert result.status == OpportunityStatus.COMPLETED
    assert result.evidence, "Ci si aspetta almeno un'evidenza nel risultato COMPLETED"

    for i, ev in enumerate(result.evidence):
        assert ev.source_url, f"Evidenza {i} ha source_url vuoto"
        assert ev.text, f"Evidenza {i} ha text vuoto"


def test_evidence_without_source_url_is_excluded() -> None:
    """Il parsing scarta evidenze senza source_url: non devono comparire nel risultato."""

    def _generate_with_blank_url(s: str, u: str) -> dict:
        base = _fake_generate_valid(s, u)
        # Aggiungi un'evidenza con source_url vuoto al primo candidato
        base["candidates"][0]["evidence"].append({"text": "testo senza fonte", "source_url": ""})
        return base

    agent = OpportunityAgent(fetch_fn=_fake_fetch_ok, generate_fn=_generate_with_blank_url)
    result = agent.run()

    for ev in result.evidence:
        assert ev.source_url, "Un'evidenza senza source_url è entrata nel risultato"


# ---------------------------------------------------------------------------
# Test 4 — Nessun dato inventato in assenza di accesso reale
# ---------------------------------------------------------------------------

def test_blocked_when_no_web_access() -> None:
    """Spec: in assenza di accesso reale il sistema non inventi dati → BLOCKED_NO_WEB_ACCESS."""
    agent = OpportunityAgent(fetch_fn=_fake_fetch_empty, generate_fn=_fake_generate_valid)
    result = agent.run()

    assert result.status == OpportunityStatus.BLOCKED_NO_WEB_ACCESS
    # Nessun dato inventato: i campi di opportunità devono essere None
    assert result.problem is None
    assert result.proposed_offer is None
    assert result.evidence == []


def test_blocked_when_fetch_raises() -> None:
    """fetch_fn che lancia eccezione → BLOCKED_NO_WEB_ACCESS, nessun dato inventato."""

    def _raising_fetch() -> dict[str, str]:
        raise ConnectionError("Connessione rifiutata")

    agent = OpportunityAgent(fetch_fn=_raising_fetch, generate_fn=_fake_generate_valid)
    result = agent.run()

    assert result.status == OpportunityStatus.BLOCKED_NO_WEB_ACCESS
    assert result.problem is None
    assert result.proposed_offer is None


def test_blocked_no_evidence_when_ai_insufficient() -> None:
    """Se l'AI dichiara BLOCKED_NO_EVIDENCE il sistema rispetta la dichiarazione."""
    agent = OpportunityAgent(fetch_fn=_fake_fetch_ok, generate_fn=_fake_generate_blocked)
    result = agent.run()

    assert result.status == OpportunityStatus.BLOCKED_NO_EVIDENCE
    assert result.problem is None
    assert result.proposed_offer is None


# ---------------------------------------------------------------------------
# Test 5 — Il risultato viene salvato
# ---------------------------------------------------------------------------

def test_result_saved_in_memory() -> None:
    """Spec: il risultato venga salvato — last_result aggiornato dopo run()."""
    agent = _make_agent_ok()
    assert agent.last_result is None

    result = agent.run()
    assert agent.last_result is result


def test_result_saved_to_file(tmp_path: Path) -> None:
    """Spec: il risultato venga salvato — save() scrive JSON leggibile su disco."""
    agent = _make_agent_ok()
    result = agent.run()

    output_path = tmp_path / "opportunity.json"
    saved = result.save(output_path)

    assert saved.exists()
    data = json.loads(saved.read_text(encoding="utf-8"))
    assert data["status"] == "COMPLETED"
    assert data["problem"] == result.problem
    assert "next_action" in data


def test_blocked_result_also_saved(tmp_path: Path) -> None:
    """Anche i risultati BLOCKED vengono salvati correttamente."""
    agent = OpportunityAgent(fetch_fn=_fake_fetch_empty, generate_fn=_fake_generate_valid)
    result = agent.run()

    output_path = tmp_path / "blocked.json"
    saved = result.save(output_path)

    assert saved.exists()
    data = json.loads(saved.read_text(encoding="utf-8"))
    assert data["status"] == "BLOCKED_NO_WEB_ACCESS"
    assert "block_reason" in data
    assert "next_action" in data


# ---------------------------------------------------------------------------
# Test 6 — next_action sempre presente
# ---------------------------------------------------------------------------

def test_next_action_present_on_completed() -> None:
    """Spec: esista sempre una next_action — caso COMPLETED."""
    agent = _make_agent_ok()
    result = agent.run()

    assert result.next_action
    assert isinstance(result.next_action, str)
    assert result.next_action.strip()


def test_next_action_present_on_blocked_no_web() -> None:
    """Spec: esista sempre una next_action — caso BLOCKED_NO_WEB_ACCESS."""
    agent = OpportunityAgent(fetch_fn=_fake_fetch_empty, generate_fn=_fake_generate_valid)
    result = agent.run()

    assert result.next_action
    assert result.next_action.strip()


def test_next_action_present_on_blocked_no_evidence() -> None:
    """Spec: esista sempre una next_action — caso BLOCKED_NO_EVIDENCE."""
    agent = OpportunityAgent(fetch_fn=_fake_fetch_ok, generate_fn=_fake_generate_blocked)
    result = agent.run()

    assert result.next_action
    assert result.next_action.strip()


def test_next_action_present_on_failed() -> None:
    """Spec: esista sempre una next_action — caso FAILED."""

    def _raising_generate(s: str, u: str) -> dict:
        raise RuntimeError("quota esaurita")

    agent = OpportunityAgent(fetch_fn=_fake_fetch_ok, generate_fn=_raising_generate)
    result = agent.run()

    assert result.status == OpportunityStatus.FAILED
    assert result.next_action
    assert result.next_action.strip()


# ---------------------------------------------------------------------------
# Test aggiuntivi — invarianti modello
# ---------------------------------------------------------------------------

def test_evidence_dataclass_rejects_empty_url() -> None:
    """Evidence lancia ValueError se source_url è vuoto."""
    with pytest.raises(ValueError, match="source_url"):
        Evidence(text="testo valido", source_url="")


def test_evidence_dataclass_rejects_empty_text() -> None:
    """Evidence lancia ValueError se text è vuoto."""
    with pytest.raises(ValueError, match="text"):
        Evidence(text="", source_url="https://example.com")


def test_opportunity_result_rejects_empty_next_action() -> None:
    """OpportunityResult lancia ValueError se next_action è vuoto."""
    with pytest.raises(ValueError, match="next_action"):
        OpportunityResult(
            status=OpportunityStatus.BLOCKED_NO_WEB_ACCESS,
            mandate="test",
            next_action="",
        )

"""Test MF-QB-OUTREACH-001 — Primo contatto commerciale reale.

10 test obbligatori da spec:
1.  Solo lead DIRECT vengono selezionati.
2.  Non vengono preparati o inviati più di 4 messaggi.
3.  Ogni messaggio contiene personalizzazione reale.
4.  Nessun dato inventato.
5.  Preview non invia nulla.
6.  Send senza provider → BLOCKED_EMAIL_PROVIDER_NOT_CONFIGURED.
7.  Invio SMTP riuscito → SENT.
8.  Errore SMTP → FAILED.
9.  Data di follow-up salvata (sent_at + 3 giorni).
10. next_action sempre presente.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mercury_foundry.outreach.agent import OutreachAgent, _select_leads
from mercury_foundry.outreach.models import (
    DeliveryStatus,
    OutreachMessage,
    OutreachResultStatus,
    ResponseStatus,
)


# ── Fixture base ─────────────────────────────────────────────────────────────

def _make_lead(
    lead_id: str = "lead01",
    name: str = "Maria Rossi",
    contactability: str = "DIRECT",
    qualification_status: str = "HIGH_FIT",
    verified_email: str = "maria@example.com",
    verified_form_url: str = "",
    role: str = "Freelance copywriter",
    website: str = "https://mariarossi.com",
    evidence: str = "Bio: professional copywriter with 5 years of experience.",
    fit_reason: str = "Target esatto: copywriter freelance.",
) -> dict:
    return {
        "lead_id": lead_id,
        "name": name,
        "verified_role_or_business": role,
        "primary_website": website,
        "public_contact": f"mailto:{verified_email}" if verified_email else website,
        "contact_type": "email" if verified_email else "website_form",
        "contactability": contactability,
        "qualification_status": qualification_status,
        "verified_email": verified_email,
        "verified_form_url": verified_form_url,
        "evidence_summary": evidence,
        "fit_reason": fit_reason,
        "source_urls": ["https://github.com/testuser"],
        "secondary_profiles": [],
        "rejection_reason": "",
        "next_action": "Contattare.",
        "verification_evidence": "Email trovata.",
    }


def _make_verified_result(leads: list[dict]) -> dict:
    return {
        "status": "COMPLETED",
        "timestamp": "2026-07-17T11:00:00+00:00",
        "enriched_leads": leads,
        "rejected_leads": [],
        "next_action": "Procedere con l'outreach.",
    }


def _make_opportunity() -> dict:
    return {
        "problem": "Freelancer lose time fixing writing errors in emails and documents.",
        "target_customer": "Freelancers and professionals who write frequently.",
        "proposed_offer": "AI service that auto-corrects writing errors and suggests improvements.",
        "delivery_format": "PDF/HTML report with correction suggestions.",
        "initial_price": "49 euro",
        "evidence": [
            {
                "text": "I write emails and double-check them three or four times.",
                "source_url": "hn_algolia",
            }
        ],
    }


def _make_generate_fn(name_in_message: bool = True) -> callable:
    """Genera un messaggio fake con personalizzazione reale (nome del lead)."""
    def generate(system_prompt: str, user_prompt: str) -> dict:
        # Estrai il nome dal prompt
        import re
        name_match = re.search(r"Nome: (.+)", user_prompt)
        name = name_match.group(1).strip() if name_match else "there"
        first = name.split()[0]

        message = (
            f"Hi {first if name_in_message else ''},\n\n"
            "I noticed your work as a professional writer — "
            "you likely spend time double-checking emails and documents for errors.\n\n"
            "We built an AI service that auto-corrects writing errors in real time. "
            "It's available for 49 EUR and delivers a PDF report with suggestions.\n\n"
            "Would you be open to a quick look?\n\n"
            "Best,\nMercury\n\n"
            "Reply 'stop' to not receive further messages."
        )
        return {
            "subject": f"Quick question about your writing workflow, {first}",
            "message": message,
            "follow_up_message": (
                f"Hi {first}, just a brief follow-up. Still happy to connect "
                "if useful. Reply 'stop' to opt out."
            ),
            "next_action": f"Wait 3 days for reply from {name}, then send follow-up.",
        }
    return generate


# ── Test 1: Solo lead DIRECT ─────────────────────────────────────────────────

def test_only_direct_leads_selected():
    """Solo lead con contactability=DIRECT vengono selezionati."""
    leads = [
        _make_lead("d1", contactability="DIRECT"),
        _make_lead("d2", contactability="INDIRECT"),
        _make_lead("d3", contactability="NONE"),
        _make_lead("d4", contactability="DIRECT"),
    ]
    verified = _make_verified_result(leads)
    selected = _select_leads(verified)

    assert all(
        l["contactability"] == "DIRECT" for l in selected
    ), "Solo lead DIRECT devono essere selezionati."
    lead_ids = {l["lead_id"] for l in selected}
    assert "d2" not in lead_ids, "Lead INDIRECT non deve essere selezionato."
    assert "d3" not in lead_ids, "Lead NONE non deve essere selezionato."


# ── Test 2: Max 4 messaggi ────────────────────────────────────────────────────

def test_max_4_messages_prepared():
    """Non vengono preparati più di 4 messaggi anche con più lead disponibili."""
    leads = [
        _make_lead(f"d{i}", name=f"User {i}", verified_email=f"user{i}@ex.com")
        for i in range(6)
    ]
    verified = _make_verified_result(leads)
    opportunity = _make_opportunity()
    agent = OutreachAgent(generate_fn=_make_generate_fn())

    result = agent.prepare(verified, opportunity)

    prepared = [
        m for m in result.messages
        if m.delivery_status == DeliveryStatus.PREPARED
    ]
    assert len(prepared) <= 4, (
        f"Non devono essere preparati più di 4 messaggi, trovati {len(prepared)}."
    )


def test_max_4_messages_sent():
    """Non vengono inviati più di 4 messaggi."""
    leads = [
        _make_lead(f"d{i}", name=f"User {i}", verified_email=f"user{i}@ex.com")
        for i in range(6)
    ]

    # smtp_fn che registra le chiamate
    sent_calls: list[str] = []

    def fake_smtp(msg: OutreachMessage) -> tuple[str, str]:
        sent_calls.append(msg.recipient)
        return f"msg-id-{msg.lead_id}", ""

    agent = OutreachAgent(generate_fn=_make_generate_fn(), smtp_fn=fake_smtp)
    verified = _make_verified_result(leads)
    opportunity = _make_opportunity()

    prepared = agent.prepare(verified, opportunity)
    sent_result = agent.send_prepared(prepared.to_dict())

    assert len(sent_calls) <= 4, (
        f"Non devono essere inviati più di 4 messaggi, trovati {len(sent_calls)}."
    )


# ── Test 3: Personalizzazione reale ──────────────────────────────────────────

def test_each_message_has_real_personalization():
    """Ogni messaggio contiene il nome reale del lead (personalizzazione verificabile)."""
    leads = [
        _make_lead("d1", name="Chiara Verdi", verified_email="chiara@ex.com"),
        _make_lead("d2", name="Luca Bianchi", verified_email="luca@ex.com"),
    ]
    verified = _make_verified_result(leads)
    opportunity = _make_opportunity()
    agent = OutreachAgent(generate_fn=_make_generate_fn(name_in_message=True))

    result = agent.prepare(verified, opportunity)

    for msg in result.messages:
        if msg.delivery_status != DeliveryStatus.PREPARED:
            continue
        lead_id = msg.lead_id
        matching_lead = next(l for l in leads if l["lead_id"] == lead_id)
        first_name = matching_lead["name"].split()[0]
        assert first_name in msg.message or first_name in msg.subject, (
            f"Il messaggio per {matching_lead['name']} non contiene il nome del lead."
        )


# ── Test 4: Nessun dato inventato ────────────────────────────────────────────

def test_no_invented_data():
    """Il messaggio non contiene dati non presenti nei lead o nell'opportunity."""
    lead = _make_lead(
        "inv01",
        name="Test User",
        verified_email="test@domain.com",
        role="Content writer",
        website="https://testuser.com",
    )
    verified = _make_verified_result([lead])
    opportunity = _make_opportunity()

    # generate_fn che usa solo dati dal prompt
    def strict_generate(system_prompt: str, user_prompt: str) -> dict:
        # Verifica che il nome del lead sia nel prompt (non inventato)
        assert "Test User" in user_prompt, "Il nome del lead deve essere nel prompt."
        assert "test@domain.com" in user_prompt, "L'email verificata deve essere nel prompt."
        return {
            "subject": "A question about your writing workflow",
            "message": (
                "Hi Test,\n\n"
                "I came across your profile as a Content writer.\n\n"
                "We offer an AI service for writing corrections at 49 EUR.\n\n"
                "Would you be open to learning more?\n\n"
                "Best,\nMercury\n\n"
                "Reply 'stop' to not receive further messages."
            ),
            "follow_up_message": "Hi Test, brief follow-up. Reply 'stop' to opt out.",
            "next_action": "Wait 3 days, then follow up.",
        }

    agent = OutreachAgent(generate_fn=strict_generate)
    result = agent.prepare(verified, opportunity)

    assert result.messages, "Deve esserci almeno un messaggio preparato."
    msg = result.messages[0]
    # Verifica che non compaiano dati inventati tipici
    assert "CEO" not in msg.message, "Dato inventato 'CEO' non deve apparire."
    assert "500.000" not in msg.message, "Cifre inventate non devono apparire."


# ── Test 5: Preview non invia nulla ──────────────────────────────────────────

def test_preview_does_not_send():
    """--prepare-latest non deve chiamare smtp_fn (nessun invio)."""
    smtp_calls: list[str] = []

    def should_not_be_called(msg: OutreachMessage) -> tuple[str, str]:
        smtp_calls.append(msg.recipient)
        return "", "ERRORE: smtp_fn non deve essere chiamata in prepare!"

    lead = _make_lead("p01", verified_email="preview@ex.com")
    verified = _make_verified_result([lead])
    opportunity = _make_opportunity()

    agent = OutreachAgent(generate_fn=_make_generate_fn(), smtp_fn=should_not_be_called)
    agent.prepare(verified, opportunity)

    assert smtp_calls == [], (
        "smtp_fn non deve essere chiamata durante la fase di prepare (preview)."
    )


# ── Test 6: Send senza provider → BLOCKED ────────────────────────────────────

def test_send_without_provider_returns_blocked(monkeypatch):
    """send_prepared senza SMTP configurato → BLOCKED_EMAIL_PROVIDER_NOT_CONFIGURED."""
    # Rimuovi tutte le variabili SMTP dall'ambiente
    smtp_vars = [
        "SMTP_HOST", "SMTP_PORT", "SMTP_USERNAME",
        "SMTP_PASSWORD", "SMTP_FROM_EMAIL", "SMTP_FROM_NAME",
    ]
    for v in smtp_vars:
        monkeypatch.delenv(v, raising=False)

    lead = _make_lead("b01", verified_email="block@ex.com")
    prepared_result = {
        "status": "PREPARED",
        "timestamp": "2026-07-17T11:00:00+00:00",
        "messages": [
            OutreachMessage(
                lead_id="b01",
                recipient="block@ex.com",
                subject="Test",
                message="Test message. Reply 'stop' to opt out.",
                channel="email",
                prepared_at="2026-07-17T11:00:00+00:00",
                delivery_status=DeliveryStatus.PREPARED,
            ).to_dict()
        ],
    }

    # Agent senza smtp_fn iniettato → usa SMTP reale → blocca perché non configurato
    agent = OutreachAgent(generate_fn=_make_generate_fn(), smtp_fn=None)
    result = agent.send_prepared(prepared_result)

    assert result.status == OutreachResultStatus.BLOCKED_EMAIL_PROVIDER_NOT_CONFIGURED, (
        f"Senza SMTP configurato il risultato deve essere BLOCKED, non {result.status}."
    )
    assert result.block_reason, "block_reason deve essere presente."
    assert result.missing_env_vars, "missing_env_vars deve elencare le variabili mancanti."
    assert result.expected_provider == "smtp", "expected_provider deve essere 'smtp'."


# ── Test 7: SMTP success → SENT ───────────────────────────────────────────────

def test_smtp_success_recorded_as_sent():
    """Un invio SMTP riuscito deve aggiornare delivery_status a SENT."""
    def ok_smtp(msg: OutreachMessage) -> tuple[str, str]:
        return f"<msg-id-{msg.lead_id}@host>", ""

    prepared_result = {
        "status": "PREPARED",
        "timestamp": "2026-07-17T11:00:00+00:00",
        "messages": [
            OutreachMessage(
                lead_id="s01",
                recipient="success@ex.com",
                subject="Subject",
                message="Body. Reply 'stop' to not receive further messages.",
                channel="email",
                prepared_at="2026-07-17T11:00:00+00:00",
                delivery_status=DeliveryStatus.PREPARED,
                next_action="Send.",
            ).to_dict()
        ],
    }

    agent = OutreachAgent(generate_fn=_make_generate_fn(), smtp_fn=ok_smtp)
    result = agent.send_prepared(prepared_result)

    sent = [m for m in result.messages if m.delivery_status == DeliveryStatus.SENT]
    assert len(sent) == 1, "Il messaggio deve essere registrato come SENT."
    assert sent[0].provider_message_id, "provider_message_id deve essere popolato."
    assert sent[0].sent_at, "sent_at deve essere popolato."


# ── Test 8: Errore SMTP → FAILED ─────────────────────────────────────────────

def test_smtp_error_recorded_as_failed():
    """Un errore SMTP deve aggiornare delivery_status a FAILED con error popolato."""
    def fail_smtp(msg: OutreachMessage) -> tuple[str, str]:
        return "", "Connection refused: SMTP host unreachable."

    prepared_result = {
        "status": "PREPARED",
        "timestamp": "2026-07-17T11:00:00+00:00",
        "messages": [
            OutreachMessage(
                lead_id="f01",
                recipient="fail@ex.com",
                subject="Subject",
                message="Body. Reply 'stop' to not receive further messages.",
                channel="email",
                prepared_at="2026-07-17T11:00:00+00:00",
                delivery_status=DeliveryStatus.PREPARED,
            ).to_dict()
        ],
    }

    agent = OutreachAgent(generate_fn=_make_generate_fn(), smtp_fn=fail_smtp)
    result = agent.send_prepared(prepared_result)

    failed = [m for m in result.messages if m.delivery_status == DeliveryStatus.FAILED]
    assert len(failed) == 1, "Il messaggio deve essere registrato come FAILED."
    assert "unreachable" in failed[0].error or "refused" in failed[0].error, (
        "L'errore SMTP deve essere riportato nel campo 'error'."
    )


# ── Test 9: Data di follow-up salvata ────────────────────────────────────────

def test_followup_date_saved_after_send():
    """follow_up_due deve essere impostato a sent_at + 3 giorni."""
    from datetime import timedelta

    def ok_smtp(msg: OutreachMessage) -> tuple[str, str]:
        return "<msg-id-fu>", ""

    prepared_result = {
        "status": "PREPARED",
        "timestamp": "2026-07-17T11:00:00+00:00",
        "messages": [
            OutreachMessage(
                lead_id="fu01",
                recipient="followup@ex.com",
                subject="Follow-up test",
                message="Body. Reply 'stop' to not receive further messages.",
                channel="email",
                prepared_at="2026-07-17T11:00:00+00:00",
                delivery_status=DeliveryStatus.PREPARED,
            ).to_dict()
        ],
    }

    agent = OutreachAgent(generate_fn=_make_generate_fn(), smtp_fn=ok_smtp)
    result = agent.send_prepared(prepared_result)

    sent = [m for m in result.messages if m.delivery_status == DeliveryStatus.SENT]
    assert sent, "Deve esserci almeno un messaggio SENT."

    msg = sent[0]
    assert msg.follow_up_due, "follow_up_due deve essere impostato."
    assert msg.sent_at, "sent_at deve essere impostato."

    sent_dt    = datetime.fromisoformat(msg.sent_at)
    followup_dt = datetime.fromisoformat(msg.follow_up_due)
    delta = followup_dt - sent_dt

    assert delta.days == 3, (
        f"follow_up_due deve essere 3 giorni dopo sent_at, trovato {delta.days} giorni."
    )


# ── Test 10: next_action sempre presente ─────────────────────────────────────

def test_next_action_always_present():
    """next_action deve essere presente in tutti i casi (prepare, send, blocked)."""
    lead = _make_lead("na01", verified_email="na@ex.com")
    verified = _make_verified_result([lead])
    opportunity = _make_opportunity()
    agent = OutreachAgent(generate_fn=_make_generate_fn())

    # Prepare
    prepared = agent.prepare(verified, opportunity)
    assert prepared.next_action, "next_action deve essere presente dopo prepare()."
    for msg in prepared.messages:
        assert msg.next_action, (
            f"next_action deve essere presente in ogni messaggio (lead_id={msg.lead_id})."
        )

    # No-leads scenario
    empty_verified = _make_verified_result([])
    blocked = agent.prepare(empty_verified, opportunity)
    assert blocked.next_action, "next_action deve essere presente anche con nessun lead."

    # BLOCKED send
    agent_no_smtp = OutreachAgent(generate_fn=_make_generate_fn(), smtp_fn=None)
    no_smtp_result = agent_no_smtp.send_prepared({"messages": []})
    assert no_smtp_result.next_action, (
        "next_action deve essere presente anche in stato BLOCKED."
    )

"""Outreach Agent — core logic — MF-QB-OUTREACH-001.

Ciclo: VERIFIED LEADS → PERSONALIZE → VALIDATE → SEND → SAVE → FOLLOW-UP DATE

Dipendenze iniettabili per i test:
- generate_fn: (system_prompt: str, user_prompt: str) -> dict
- smtp_fn:     (msg: OutreachMessage) -> tuple[str, str]  # (message_id, error)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from mercury_foundry.outreach.models import (
    DeliveryStatus,
    OutreachMessage,
    OutreachResult,
    OutreachResultStatus,
    ResponseStatus,
)
from mercury_foundry.outreach.prompts import SYSTEM_PROMPT, build_message_prompt

GenerateFn = Callable[[str, str], dict]
SmtpFn = Callable[["OutreachMessage"], tuple[str, str]]

_MAX_MESSAGES = 4
_FOLLOWUP_DAYS = 3


# ── Costanti di qualificazione ────────────────────────────────────────────────

_DIRECT_CONTACTABILITY = "DIRECT"
_ALLOWED_QS = {"HIGH_FIT", "PLAUSIBLE"}


# ── Provider AI reale ─────────────────────────────────────────────────────────

def _build_real_generate_fn() -> GenerateFn:
    """Ritorna una generate_fn che chiama il provider AI configurato."""
    from mercury_foundry.ai.provider_config import ProviderConfigError, load_real_provider_config
    import openai

    try:
        config = load_real_provider_config()
    except ProviderConfigError as exc:
        raise RuntimeError(
            f"Provider AI non configurato per Outreach Agent: {exc}\n"
            "Imposta MERCURY_AI_PROVIDER=openai_compatible e le variabili associate.\n"
            "Nei test usa OutreachAgent(generate_fn=<fixture_callable>)."
        ) from exc

    client_kwargs: dict[str, Any] = {"api_key": config.api_key}
    if config.base_url:
        client_kwargs["base_url"] = config.base_url
    client = openai.OpenAI(**client_kwargs)

    def generate(system_prompt: str, user_prompt: str) -> dict:
        response = client.chat.completions.create(
            model=config.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            timeout=config.timeout_seconds,
        )
        raw = response.choices[0].message.content
        return json.loads(raw)

    return generate


# ── Selezione lead ────────────────────────────────────────────────────────────

def _select_leads(verified_result: dict) -> list[dict]:
    """Seleziona lead DIRECT + HIGH_FIT|PLAUSIBLE, max _MAX_MESSAGES.

    Legge sia 'enriched_leads' (formato arricchito) che 'messages' (formato già preparato).
    Usa contact_verified_latest.json se disponibile, altrimenti enriched_latest.json.
    """
    candidates: list[dict] = verified_result.get("enriched_leads", [])

    selected: list[dict] = []
    for lead in candidates:
        if len(selected) >= _MAX_MESSAGES:
            break
        contactability = str(lead.get("contactability", "")).upper()
        qs = str(lead.get("qualification_status", "")).upper()
        has_email = bool(lead.get("verified_email", "").strip())
        has_form  = bool(lead.get("verified_form_url", "").strip())

        if contactability != _DIRECT_CONTACTABILITY:
            continue
        if qs not in _ALLOWED_QS:
            continue
        if not has_email and not has_form:
            continue

        selected.append(lead)

    return selected


# ── Validazione messaggio ─────────────────────────────────────────────────────

def _validate_message(message: str, lead: dict) -> list[str]:
    """Controlla il messaggio generato. Ritorna lista di errori (vuota se ok)."""
    errors: list[str] = []

    # Lunghezza
    word_count = len(message.split())
    if word_count > 150:
        errors.append(f"Messaggio troppo lungo: {word_count} parole (max ~120).")

    # Opt-out
    if "stop" not in message.lower() and "unsubscribe" not in message.lower():
        errors.append("Messaggio manca dell'opzione opt-out.")

    return errors


# ── Follow-up message ─────────────────────────────────────────────────────────

def _default_followup(name: str, subject: str) -> str:
    first_name = name.split()[0] if name else "there"
    return (
        f"Hi {first_name},\n\n"
        f"Just following up briefly on my previous message about '{subject}'.\n\n"
        "If you're not interested, no problem at all — simply reply 'stop' "
        "and I won't contact you again.\n\n"
        "Best,\nMercury"
    )


# ── OutreachAgent ─────────────────────────────────────────────────────────────

class OutreachAgent:
    """Agent per la preparazione e l'invio del primo contatto commerciale.

    generate_fn: iniettabile per i test (default: provider AI reale).
    smtp_fn:     iniettabile per i test (default: send_via_smtp reale).
                 Se None in __init__ e non configurato → BLOCKED.
    """

    def __init__(
        self,
        generate_fn: GenerateFn | None = None,
        smtp_fn: SmtpFn | None = None,
    ) -> None:
        self._generate_fn: GenerateFn = generate_fn or _build_real_generate_fn()
        self._smtp_fn: SmtpFn | None  = smtp_fn  # None = usa SMTP reale se disponibile
        self._last_result: OutreachResult | None = None

    @property
    def last_result(self) -> OutreachResult | None:
        return self._last_result

    @classmethod
    def with_real_provider(cls) -> OutreachAgent:
        return cls(generate_fn=None, smtp_fn=None)

    # ── prepare ──────────────────────────────────────────────────────────────

    def prepare(
        self,
        verified_result: dict,
        opportunity_result: dict,
    ) -> OutreachResult:
        """Personalizza i messaggi per i lead DIRECT selezionati.

        Non invia nulla. Ritorna OutreachResult con status=PREPARED.
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        leads = _select_leads(verified_result)

        if not leads:
            result = OutreachResult(
                status=OutreachResultStatus.BLOCKED_NO_DIRECT_LEADS,
                timestamp=timestamp,
                messages=[],
                next_action=(
                    "Nessun lead DIRECT con email verificata disponibile. "
                    "Eseguire prima --verify-contacts-latest."
                ),
                block_reason=(
                    "Nessun lead soddisfa i criteri: contactability=DIRECT + "
                    "qualification_status in HIGH_FIT|PLAUSIBLE + email o form verificato."
                ),
                prepared_count=0,
            )
            self._last_result = result
            return result

        messages: list[OutreachMessage] = []
        opportunity = opportunity_result

        for lead in leads:
            name      = lead.get("name", "lead")
            email     = lead.get("verified_email", "").strip()
            form      = lead.get("verified_form_url", "").strip()
            recipient = email or form
            channel   = "email" if email else "website_form"

            try:
                user_prompt = build_message_prompt(lead, opportunity)
                raw = self._generate_fn(SYSTEM_PROMPT, user_prompt)
            except Exception as exc:
                # Falliamo chiuso: se l'AI non risponde, non mandiamo nulla
                messages.append(OutreachMessage(
                    lead_id=lead.get("lead_id", ""),
                    recipient=recipient,
                    subject="[ERRORE generazione]",
                    message="",
                    channel=channel,
                    prepared_at=timestamp,
                    delivery_status=DeliveryStatus.FAILED,
                    error=f"Errore generazione AI: {exc}",
                    next_action="Riprovare dopo aver verificato il provider AI.",
                ))
                continue

            subject        = str(raw.get("subject", "")).strip()
            message_body   = str(raw.get("message", "")).strip()
            followup_msg   = str(raw.get("follow_up_message", "")).strip()
            next_action    = str(raw.get("next_action", "")).strip()

            if not followup_msg:
                followup_msg = _default_followup(name, subject)
            if not next_action:
                next_action = (
                    f"Attendere {_FOLLOWUP_DAYS} giorni per eventuali risposte, "
                    f"poi inviare il follow-up."
                )

            # Validazione
            errors = _validate_message(message_body, lead)
            if errors:
                # Aggiunge opt-out se mancante invece di fallire
                if any("opt-out" in e for e in errors):
                    message_body += (
                        "\n\nReply 'stop' to not receive further messages."
                    )

            messages.append(OutreachMessage(
                lead_id=lead.get("lead_id", ""),
                recipient=recipient,
                subject=subject,
                message=message_body,
                channel=channel,
                prepared_at=timestamp,
                delivery_status=DeliveryStatus.PREPARED,
                follow_up_message=followup_msg,
                next_action=next_action,
            ))

        prepared_count = sum(
            1 for m in messages if m.delivery_status == DeliveryStatus.PREPARED
        )

        result = OutreachResult(
            status=OutreachResultStatus.PREPARED,
            timestamp=timestamp,
            messages=messages,
            prepared_count=prepared_count,
            sent_count=0,
            failed_count=sum(
                1 for m in messages if m.delivery_status == DeliveryStatus.FAILED
            ),
            next_action=(
                f"{prepared_count} messaggi pronti. "
                "Rivedere e poi eseguire --send-latest per inviare."
            ),
        )
        self._last_result = result
        return result

    # ── send_prepared ─────────────────────────────────────────────────────────

    def send_prepared(self, prepared_result: dict) -> OutreachResult:
        """Invia fino a _MAX_MESSAGES messaggi PREPARED.

        Se il provider SMTP non è configurato, ritorna BLOCKED_EMAIL_PROVIDER_NOT_CONFIGURED.
        Non simula invii. Non rilancia eccezioni SMTP.
        """
        from mercury_foundry.outreach.smtp import (
            get_missing_smtp_vars,
            is_smtp_configured,
            send_via_smtp,
        )

        timestamp = datetime.now(timezone.utc).isoformat()

        # Determina smtp_fn da usare
        use_real_smtp = self._smtp_fn is None

        # Controlla provider
        if use_real_smtp and not is_smtp_configured():
            missing = get_missing_smtp_vars()
            result = OutreachResult(
                status=OutreachResultStatus.BLOCKED_EMAIL_PROVIDER_NOT_CONFIGURED,
                timestamp=timestamp,
                messages=[],
                next_action=(
                    "Configurare le variabili SMTP mancanti nell'ambiente "
                    "e rieseguire --send-latest."
                ),
                block_reason=(
                    "Provider email SMTP non configurato: "
                    f"variabili mancanti: {', '.join(missing)}."
                ),
                expected_provider="smtp",
                missing_env_vars=missing,
            )
            self._last_result = result
            return result

        smtp_fn: SmtpFn = self._smtp_fn or send_via_smtp

        # Ricostruisci messaggi dalla dict
        raw_messages = prepared_result.get("messages", [])
        messages: list[OutreachMessage] = [
            OutreachMessage.from_dict(m) for m in raw_messages
        ]

        # Filtra solo PREPARED (max 4)
        to_send = [
            m for m in messages if m.delivery_status == DeliveryStatus.PREPARED
        ][:_MAX_MESSAGES]

        sent_count  = 0
        failed_count = 0
        now = datetime.now(timezone.utc)

        for msg in to_send:
            msg_id, error = smtp_fn(msg)
            if error:
                msg.delivery_status = DeliveryStatus.FAILED
                msg.error           = error
                failed_count += 1
            else:
                msg.delivery_status       = DeliveryStatus.SENT
                msg.sent_at               = now.isoformat()
                msg.provider_message_id   = msg_id
                msg.follow_up_due         = (now + timedelta(days=_FOLLOWUP_DAYS)).isoformat()
                sent_count += 1

        # Aggiorna lista completa
        sent_ids = {m.lead_id for m in to_send}
        final_messages = to_send + [
            m for m in messages if m.lead_id not in sent_ids
        ]

        if sent_count > 0 and failed_count == 0:
            status = OutreachResultStatus.COMPLETED
        elif sent_count > 0:
            status = OutreachResultStatus.PARTIAL
        elif failed_count > 0:
            status = OutreachResultStatus.PARTIAL
        else:
            status = OutreachResultStatus.COMPLETED

        result = OutreachResult(
            status=status,
            timestamp=timestamp,
            messages=final_messages,
            prepared_count=len(to_send),
            sent_count=sent_count,
            failed_count=failed_count,
            next_action=(
                f"Inviati {sent_count}/{len(to_send)} messaggi. "
                f"Follow-up previsto tra {_FOLLOWUP_DAYS} giorni per i messaggi SENT."
                if sent_count > 0 else
                "Nessun messaggio inviato. Verificare la configurazione SMTP."
            ),
        )
        self._last_result = result
        return result

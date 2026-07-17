"""Adapter SMTP minimo — nessuna dipendenza esterna, solo stdlib Python.

Variabili d'ambiente richieste:
    SMTP_HOST          — hostname del server SMTP
    SMTP_PORT          — porta (587 per STARTTLS, 465 per SSL)
    SMTP_USERNAME      — username autenticazione SMTP
    SMTP_PASSWORD      — password autenticazione SMTP
    SMTP_FROM_EMAIL    — indirizzo mittente (es. hello@example.com)
    SMTP_FROM_NAME     — nome mittente (es. "Marco Rossi")

Non salvare mai credenziali nel repository.
Questo modulo non fa fallback silenziosi: se le variabili mancano, ritorna errore esplicito.
"""
from __future__ import annotations

import os
import smtplib
import uuid
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mercury_foundry.outreach.models import OutreachMessage

# ── Variabili d'ambiente richieste ───────────────────────────────────────────

_REQUIRED_VARS = [
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USERNAME",
    "SMTP_PASSWORD",
    "SMTP_FROM_EMAIL",
    "SMTP_FROM_NAME",
]


def is_smtp_configured() -> bool:
    """True se tutte le variabili SMTP sono presenti nell'ambiente."""
    return all(os.environ.get(v) for v in _REQUIRED_VARS)


def get_missing_smtp_vars() -> list[str]:
    """Restituisce la lista delle variabili SMTP mancanti."""
    return [v for v in _REQUIRED_VARS if not os.environ.get(v)]


# ── Config ───────────────────────────────────────────────────────────────────

@dataclass
class SmtpConfig:
    host: str
    port: int
    username: str
    password: str
    from_email: str
    from_name: str

    @classmethod
    def from_env(cls) -> SmtpConfig:
        """Carica la configurazione dalle variabili d'ambiente.

        Solleva RuntimeError se una variabile è mancante.
        """
        missing = get_missing_smtp_vars()
        if missing:
            raise RuntimeError(
                f"Variabili SMTP mancanti: {', '.join(missing)}. "
                "Configurare le variabili d'ambiente prima di inviare email."
            )
        return cls(
            host=os.environ["SMTP_HOST"],
            port=int(os.environ["SMTP_PORT"]),
            username=os.environ["SMTP_USERNAME"],
            password=os.environ["SMTP_PASSWORD"],
            from_email=os.environ["SMTP_FROM_EMAIL"],
            from_name=os.environ["SMTP_FROM_NAME"],
        )


# ── Invio reale ───────────────────────────────────────────────────────────────

def send_via_smtp(msg: OutreachMessage) -> tuple[str, str]:
    """Invia il messaggio via SMTP. Ritorna (message_id, error).

    message_id è vuoto in caso di errore.
    error è vuoto in caso di successo.
    Non rilancia mai eccezioni: ogni errore è catturato e ritornato come stringa.
    """
    try:
        config = SmtpConfig.from_env()
    except RuntimeError as exc:
        return "", str(exc)

    # Costruisci MIME
    mime = MIMEMultipart("alternative")
    msg_id = f"<{uuid.uuid4().hex}@mercuryfoundry>"
    mime["Message-ID"] = msg_id
    mime["From"]       = f"{config.from_name} <{config.from_email}>"
    mime["To"]         = msg.recipient
    mime["Subject"]    = msg.subject
    mime.attach(MIMEText(msg.message, "plain", "utf-8"))

    try:
        if config.port == 465:
            # SSL diretto
            with smtplib.SMTP_SSL(config.host, config.port) as server:
                server.login(config.username, config.password)
                server.sendmail(config.from_email, [msg.recipient], mime.as_string())
        else:
            # STARTTLS (porta 587 o altra)
            with smtplib.SMTP(config.host, config.port) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(config.username, config.password)
                server.sendmail(config.from_email, [msg.recipient], mime.as_string())

        return msg_id, ""

    except smtplib.SMTPAuthenticationError as exc:
        return "", f"Autenticazione SMTP fallita: {exc.smtp_error!r}"
    except smtplib.SMTPRecipientsRefused as exc:
        return "", f"Destinatario rifiutato: {exc.recipients}"
    except smtplib.SMTPException as exc:
        return "", f"Errore SMTP: {exc}"
    except OSError as exc:
        return "", f"Errore di rete SMTP: {exc}"
    except Exception as exc:
        return "", f"Errore imprevisto durante l'invio: {exc}"

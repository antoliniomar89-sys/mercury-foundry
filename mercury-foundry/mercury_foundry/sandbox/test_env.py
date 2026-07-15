"""Ambiente sanitizzato per l'esecuzione REALE dei test generati, e redazione
dei segreti dall'output prima che venga persistito.

Il TestRunner non deve MAI ereditare l'intero `os.environ` del processo
Replit (che può contenere OPENAI_API_KEY, SESSION_SECRET, e altre variabili
sensibili dell'host): costruisce l'ambiente da un'ALLOWLIST minima, non da
una blocklist applicata a una copia integrale.
"""

from __future__ import annotations

import os
from pathlib import Path

# Nomi espliciti da rimuovere sempre, indipendentemente da come sarebbero
# altrimenti classificati (elenco esplicito richiesto).
EXPLICIT_SECRET_NAMES = {"OPENAI_API_KEY", "SESSION_SECRET"}

# Sottostringhe (case-insensitive) che, se presenti nel NOME di una
# variabile, la marcano come sensibile ovunque compaia: nell'ambiente reale
# del processo, in un allowlist esplicito, o in un override richiesto da un
# literal_constraint. Nessuna di queste può mai finire nell'ambiente
# sanitizzato, anche se qualcuno la richiedesse esplicitamente.
SENSITIVE_NAME_SUBSTRINGS = (
    "KEY",
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "PASSWD",
    "CREDENTIAL",
    "PRIVATE",
    "AUTH",
)

# Variabili Replit note per contenere materiale sensibile o identificativo
# dell'ambiente ospite, escluse anche quando non contengono le sottostringhe sopra.
REPLIT_SENSITIVE_NAMES = {
    "REPLIT_DB_URL",
    "REPL_IDENTITY",
    "REPL_IDENTITY_KEY",
    "REPLIT_TOKEN",
    "REPLIT_ACCESS_TOKEN",
    "REPLIT_CLI_TOKEN",
}

# Variabili tecniche minime concesse di default: mai segrete (solo percorsi
# filesystem/preferenze dell'interprete), servono a far risolvere davvero
# l'installazione di pytest di questo ambiente (senza, `pytest` risolto da
# PATH trova l'eseguibile ma non il suo pacchetto `_pytest`, perché quello
# vive fuori dalla stdlib e richiede PYTHONPATH/PYTHONUSERBASE per essere
# importato). Nessuna di queste contiene credenziali.
_DEFAULT_TECHNICAL_ALLOWLIST = (
    "PATH",
    "LANG",
    "LC_ALL",
    "PYTHONPATH",
    "PYTHONUSERBASE",
    "UV_PYTHON_PREFERENCE",
    "UV_PYTHON_DOWNLOADS",
    "UV_PROJECT_ENVIRONMENT",
)

_REDACTED = "[REDACTED]"


def is_sensitive_name(name: str) -> bool:
    """True se il NOME di una variabile la marca come segreta/sensibile.

    Applicato sia in fase di costruzione dell'ambiente (per escluderla) sia
    come difesa in profondità finale su qualunque sorgente (allowlist
    esplicito, override di un literal_constraint)."""
    upper = name.upper()
    if name in EXPLICIT_SECRET_NAMES or upper in REPLIT_SENSITIVE_NAMES:
        return True
    return any(marker in upper for marker in SENSITIVE_NAME_SUBSTRINGS)


def build_sanitized_test_env(
    *,
    home_dir: Path,
    tmp_dir: Path,
    extra: dict[str, str] | None = None,
    allowlist_extra_names: tuple[str, ...] = (),
    source_environ: dict[str, str] | None = None,
) -> dict[str, str]:
    """Costruisce l'ambiente per l'esecuzione dei test SOLO tramite allowlist.

    NON parte mai da una copia di `os.environ`: `source_environ` (di default
    l'`os.environ` del processo corrente) viene consultato solo per leggere
    il VALORE delle poche variabili tecniche esplicitamente ammesse, mai per
    copiarne il contenuto integrale. Ogni nome — dell'allowlist di default,
    di `allowlist_extra_names`, o di `extra` — passa comunque attraverso
    `is_sensitive_name` prima di poter entrare nell'ambiente risultante.
    """
    source = source_environ if source_environ is not None else os.environ

    env: dict[str, str] = {}
    for name in (*_DEFAULT_TECHNICAL_ALLOWLIST, *allowlist_extra_names):
        if is_sensitive_name(name):
            continue
        if name in source:
            env[name] = source[name]

    home_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    env["HOME"] = str(home_dir)
    env["TMPDIR"] = str(tmp_dir)
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    if extra:
        for name, value in extra.items():
            if is_sensitive_name(name):
                continue
            env[name] = value

    # Difesa in profondità finale: rimuove qualunque nome sensibile fosse
    # comunque riuscito a entrare (non dovrebbe mai accadere dati i filtri
    # sopra, ma l'ambiente finale non deve MAI dipendere solo da quello).
    for name in list(env):
        if is_sensitive_name(name):
            del env[name]

    return env


def collect_secret_values_to_redact(
    source_environ: dict[str, str] | None = None, extra_names: tuple[str, ...] = ()
) -> list[str]:
    """Ritorna i VALORI (mai i nomi da soli — servono per la sostituzione
    testuale) delle variabili sensibili attualmente presenti nell'ambiente
    REALE del processo. Uso esclusivo: individuare e redigere eventuali
    comparse di quei valori nell'output dei test — MAI per popolare
    l'ambiente sanitizzato in cui i test girano."""
    source = source_environ if source_environ is not None else os.environ
    values: list[str] = []
    for name, value in source.items():
        if not value:
            continue
        if is_sensitive_name(name) or name in extra_names:
            values.append(value)
    return values


def redact_secrets(text: str, secret_values: list[str]) -> str:
    """Sostituisce ogni comparsa letterale di un valore segreto con un
    segnaposto. Non stampa mai i valori rimossi."""
    redacted = text
    for value in secret_values:
        if value:
            redacted = redacted.replace(value, _REDACTED)
    return redacted


def truncate_output(text: str, max_chars: int) -> tuple[str, bool]:
    """Limita la lunghezza di un output prima della persistenza, segnalando
    esplicitamente se è avvenuto un troncamento (mai silenzioso)."""
    if len(text) <= max_chars:
        return text, False
    return (
        text[:max_chars] + f"\n...[TRUNCATED: output originale {len(text)} caratteri, mostrati {max_chars}]",
        True,
    )


def sanitize_test_output(text: str, max_chars: int = 20_000) -> tuple[str, bool]:
    """Pipeline unica applicata a stdout/stderr PRIMA di qualunque
    persistenza (DB o audit log): prima redazione dei segreti noti
    dell'ambiente reale, poi troncamento a lunghezza massima."""
    redacted = redact_secrets(text, collect_secret_values_to_redact())
    return truncate_output(redacted, max_chars)

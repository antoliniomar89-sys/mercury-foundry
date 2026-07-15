"""Percorsi e costanti condivise di Mercury Foundry V0."""

from __future__ import annotations

import os
from pathlib import Path

# Radice del progetto mercury-foundry (questa cartella)
BASE_DIR = Path(__file__).resolve().parent.parent

# Sandbox su cui Builder ed Evaluator operano davvero (mai fuori da qui)
TARGET_PROJECT_DIR = BASE_DIR / "target_project"

# Stato persistente
DATA_DIR = BASE_DIR / "data"
DEFAULT_DB_PATH = DATA_DIR / "mercury_foundry.db"

# Radice sotto cui vive lo staging isolato per-tentativo (mai target_project):
# ogni tentativo scrive in STAGING_BASE_DIR/<run_id>/<attempt_id>/, una copia
# fisicamente separata di TARGET_PROJECT_DIR. Solo l'Approval Gate, dopo
# un'approvazione umana esplicita, applica le differenze dallo staging al
# target reale (vedi `sandbox.staging.promote_staging`).
STAGING_BASE_DIR = DATA_DIR / "staging"

SCHEMA_PATH = BASE_DIR / "mercury_foundry" / "state" / "schema.sql"

# Vincolo esplicito richiesto: default 3 tentativi automatici per task.
# Sovrascrivibile per singola run con la variabile d'ambiente
# MERCURY_FOUNDRY_MAX_ATTEMPTS (stesso pattern degli altri limiti
# MERCURY_AI_* già usati per il provider reale): nessun default nascosto
# diverso da 3, e un valore non valido (<1 o non intero) fa fallire subito
# l'importazione invece di silenziarsi.
_MAX_ATTEMPTS_ENV = "MERCURY_FOUNDRY_MAX_ATTEMPTS"


def _load_max_attempts() -> int:
    raw = os.environ.get(_MAX_ATTEMPTS_ENV)
    if not raw:
        return 3
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(
            f"{_MAX_ATTEMPTS_ENV} deve essere un intero, ricevuto: {raw!r}"
        ) from exc
    if value < 1:
        raise ValueError(f"{_MAX_ATTEMPTS_ENV} deve essere >= 1, ricevuto: {value}")
    return value


MAX_ATTEMPTS = _load_max_attempts()

# Timeout (secondi) per l'esecuzione reale dei test — evita loop appesi
TEST_TIMEOUT_SECONDS = 60

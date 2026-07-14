"""Percorsi e costanti condivise di Mercury Foundry V0."""

from __future__ import annotations

from pathlib import Path

# Radice del progetto mercury-foundry (questa cartella)
BASE_DIR = Path(__file__).resolve().parent.parent

# Sandbox su cui Builder ed Evaluator operano davvero (mai fuori da qui)
TARGET_PROJECT_DIR = BASE_DIR / "target_project"

# Stato persistente
DATA_DIR = BASE_DIR / "data"
DEFAULT_DB_PATH = DATA_DIR / "mercury_foundry.db"

SCHEMA_PATH = BASE_DIR / "mercury_foundry" / "state" / "schema.sql"

# Vincolo esplicito richiesto: massimo 3 tentativi automatici per task
MAX_ATTEMPTS = 3

# Timeout (secondi) per l'esecuzione reale dei test — evita loop appesi
TEST_TIMEOUT_SECONDS = 60

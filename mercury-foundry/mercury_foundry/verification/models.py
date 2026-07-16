"""Modelli tipizzati per MF-VERIFY-001 — Adaptive Verification Governor.

Invarianti:
- VerificationLevel è ordinale (STATIC < TARGETED < IMPACTED < FULL).
- RiskClass determina il livello minimo di verifica.
- CostBudget è configurabile per missione — mai globale.
- Nessuna rete esterna, nessun pagamento, nessuna modifica al progetto.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum, Enum


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# VerificationLevel
# ---------------------------------------------------------------------------

class VerificationLevel(IntEnum):
    """Livello di verifica ordinale — più alto = più costoso ma più sicuro."""
    STATIC   = 0  # syntax/import/config — nessuna suite funzionale
    TARGETED = 1  # test direttamente associati ai file modificati
    IMPACTED = 2  # test del modulo + dipendenti + test critici pertinenti
    FULL     = 3  # suite completa + Doctor — solo per milestone/release

    def label(self) -> str:
        return self.name.upper()

    @classmethod
    def from_str(cls, s: str) -> "VerificationLevel":
        s = s.strip().upper()
        for level in cls:
            if level.name == s:
                return level
        raise ValueError(f"VerificationLevel sconosciuto: {s!r}. Validi: {[l.name for l in cls]}")


# ---------------------------------------------------------------------------
# RiskClass
# ---------------------------------------------------------------------------

class RiskClass(str, Enum):
    """Classe di rischio di una modifica."""
    LOW      = "low"       # documentazione, messaggi, non-comportamentale
    MEDIUM   = "medium"    # servizio isolato, endpoint locale, logica limitata
    HIGH     = "high"      # DB, migration, resource, budget, policy, lifecycle
    CRITICAL = "critical"  # pagamenti, autorizzazione autonoma, schema economico


# ---------------------------------------------------------------------------
# FileClassification
# ---------------------------------------------------------------------------

@dataclass
class FileClassification:
    """Classificazione di un singolo file modificato."""
    path:       str
    domain:     str
    risk_class: RiskClass
    reason:     str


# ---------------------------------------------------------------------------
# VerificationPlan
# ---------------------------------------------------------------------------

@dataclass
class VerificationPlan:
    """Piano di verifica prodotto da TestSelector."""
    plan_id:              str
    level:                VerificationLevel
    risk_class:           RiskClass
    changed_files:        list[str]
    classified_files:     list[FileClassification]
    selected_tests:       list[str]         # percorsi file test o marcatori
    selection_reasons:    list[str]         # una riga per ogni selezione
    full_suite_skipped:   bool
    full_suite_skip_reason: str | None
    requires_full_at_milestone: bool        # LEVEL 3 richiesto prima del commit finale
    estimated_ops_cost:   int               # operational units (1 unit ≈ 1 test file run)
    created_at:           str

    def summary(self) -> str:
        lines = [
            f"Piano [{self.plan_id[:8]}] — Livello: {self.level.label()} | Rischio: {self.risk_class.value.upper()}",
            f"File modificati: {len(self.changed_files)}",
            f"Test selezionati: {len(self.selected_tests)}",
            f"Costo stimato: {self.estimated_ops_cost} op-unit",
        ]
        if self.full_suite_skipped:
            lines.append(f"Suite completa SALTATA: {self.full_suite_skip_reason}")
        if self.requires_full_at_milestone:
            lines.append("⚠ Suite completa richiesta prima del commit di milestone")
        lines.append("")
        lines.append("Test selezionati:")
        for t, r in zip(self.selected_tests, self.selection_reasons):
            lines.append(f"  • {t}")
            lines.append(f"    → {r}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# TestRunRecord
# ---------------------------------------------------------------------------

@dataclass
class TestRunRecord:
    """Record di una singola esecuzione di test."""
    run_id:           str
    plan_id:          str
    command:          list[str]
    level:            VerificationLevel
    started_at:       str
    completed_at:     str | None
    passed:           int
    failed:           int
    errors:           int
    duration_seconds: float
    failed_test_ids:  list[str]
    from_cache:       bool = False
    exit_code:        int = 0
    output_summary:   str = ""


# ---------------------------------------------------------------------------
# ProgressSnapshot
# ---------------------------------------------------------------------------

@dataclass
class ProgressSnapshot:
    """Snapshot dello stato di progresso dopo un tentativo fallito."""
    attempt:           int
    failed_count:      int
    failed_test_ids:   list[str]
    error_types:       list[str]
    duration_seconds:  float
    is_improvement:    bool
    improvement_reason: str | None = None


# ---------------------------------------------------------------------------
# CostBudget
# ---------------------------------------------------------------------------

@dataclass
class CostBudget:
    """Budget operativo per una missione di sviluppo.

    Configurabile per missione — mai globale. Default conservativi.
    """
    mission_id:                       str
    max_iterations:                   int   = 8
    max_test_runs:                    int   = 12
    max_full_suite_runs:              int   = 1
    max_failed_runs_without_improvement: int = 3
    max_elapsed_seconds:              int | None = None
    max_ai_calls:                     int | None = None
    max_estimated_cost_minor:         int | None = None
    stop_on_budget_exhaustion:        bool  = True
    require_human_approval_on_exhaustion: bool = False

    def validate(self) -> list[str]:
        errors = []
        if self.max_iterations < 1:
            errors.append("max_iterations deve essere >= 1")
        if self.max_test_runs < 1:
            errors.append("max_test_runs deve essere >= 1")
        if self.max_full_suite_runs < 0:
            errors.append("max_full_suite_runs deve essere >= 0")
        if self.max_failed_runs_without_improvement < 1:
            errors.append("max_failed_runs_without_improvement deve essere >= 1")
        return errors


# ---------------------------------------------------------------------------
# BudgetStatus
# ---------------------------------------------------------------------------

@dataclass
class BudgetStatus:
    """Stato corrente del budget per una missione."""
    mission_id:                       str
    iterations_used:                  int
    test_runs_used:                   int
    full_suite_runs_used:             int
    failed_runs_without_improvement:  int
    elapsed_seconds:                  float
    ai_calls_used:                    int
    exhausted:                        bool
    exhaustion_reason:                str | None
    requires_human_approval:          bool

    def remaining_test_runs(self, budget: CostBudget) -> int:
        return max(0, budget.max_test_runs - self.test_runs_used)

    def remaining_full_suite_runs(self, budget: CostBudget) -> int:
        return max(0, budget.max_full_suite_runs - self.full_suite_runs_used)

    def can_run(self, budget: CostBudget) -> bool:
        return not self.exhausted


# ---------------------------------------------------------------------------
# EscalationReport
# ---------------------------------------------------------------------------

@dataclass
class EscalationReport:
    """Report di escalation prodotto quando il budget è esaurito o non c'è miglioramento."""
    report_id:                str
    mission_id:               str
    trigger:                  str            # es. "no_improvement_3_attempts"
    last_snapshots:           list[ProgressSnapshot]
    budget_status:            BudgetStatus
    recommendation:           str
    requires_human_decision:  bool
    created_at:               str

    def render(self) -> str:
        lines = [
            f"ESCALATION REPORT [{self.report_id[:8]}]",
            f"Mission: {self.mission_id}",
            f"Trigger: {self.trigger}",
            f"",
            f"Raccomandazione: {self.recommendation}",
            f"Richiede decisione umana: {self.requires_human_decision}",
            f"",
            f"Budget usato:",
            f"  Iterazioni: {self.budget_status.iterations_used}",
            f"  Test run: {self.budget_status.test_runs_used}",
            f"  Falliti senza miglioramento: {self.budget_status.failed_runs_without_improvement}",
        ]
        if self.last_snapshots:
            lines.append("")
            lines.append("Ultimi snapshot di progresso:")
            for s in self.last_snapshots[-3:]:
                lines.append(
                    f"  Tentativo {s.attempt}: {s.failed_count} falliti"
                    + (f" [{s.improvement_reason}]" if s.improvement_reason else "")
                )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# CheckpointRecord
# ---------------------------------------------------------------------------

@dataclass
class CheckpointRecord:
    """Checkpoint registrato prima di modifiche HIGH o CRITICAL."""
    checkpoint_id:  str
    mission_id:     str
    git_hash:       str
    risk_class:     RiskClass
    files_at_risk:  list[str]
    working_tree_clean: bool
    recorded_at:    str
    notes:          str = ""


# ---------------------------------------------------------------------------
# CacheKey / CacheEntry
# ---------------------------------------------------------------------------

@dataclass
class CacheKey:
    """Chiave composita per la cache dei risultati."""
    source_hash:      str   # SHA-256 dei file sorgente coinvolti
    test_hash:        str   # SHA-256 dei file di test
    command_hash:     str   # SHA-256 del comando
    python_version:   str
    lockfile_hash:    str   # SHA-256 del lockfile (uv.lock o requirements)
    config_hash:      str   # SHA-256 della configurazione costituzionale

    def composite(self) -> str:
        import hashlib
        parts = [
            self.source_hash, self.test_hash, self.command_hash,
            self.python_version, self.lockfile_hash, self.config_hash,
        ]
        return hashlib.sha256("|".join(parts).encode()).hexdigest()


@dataclass
class CacheEntry:
    """Voce della cache."""
    key:          str            # CacheKey.composite()
    result:       TestRunRecord
    cached_at:    str
    valid:        bool = True
    invalid_reason: str | None = None

"""Modelli tipizzati per il Constitutional Core — MF-CONST-001.

Tutti i modelli usano dataclass + enum per validazione forte al momento
della costruzione, coerentemente con le convenzioni del repository.
Nessun LLM, nessuna regola euristica: solo strutture dati e costanti.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


# ---------------------------------------------------------------------------
# Enumerazioni
# ---------------------------------------------------------------------------

class PrincipleLevel(str, Enum):
    """Livello gerarchico di un principio costituzionale."""
    IMMUTABLE      = "immutable"       # non modificabile senza procedura straordinaria
    CONSTITUTIONAL = "constitutional"  # fondante, cambia solo con supervisione umana
    OPERATIONAL    = "operational"     # guida le operazioni correnti, modificabile con approvazione
    LOCAL          = "local"           # specifico di un contesto, derogabile localmente


class PrincipleStatus(str, Enum):
    """Ciclo di vita di un principio."""
    CANDIDATE   = "candidate"    # proposto, non ancora valutato in produzione
    SHADOW      = "shadow"       # attivo in osservazione, non bloccante
    ACTIVE      = "active"       # pienamente vigente
    DEPRECATED  = "deprecated"   # sostituito o superato, non più valutato
    REJECTED    = "rejected"     # esplicitamente scartato


class PrincipleEnforcement(str, Enum):
    """Modalità di applicazione di un principio."""
    ADVISORY    = "advisory"     # suggerimento, nessun impatto operativo
    AUDIT_ONLY  = "audit_only"   # registra la violazione, non blocca
    BLOCKING    = "blocking"     # blocca l'operazione (solo in enforce mode)


class ValidationStatus(str, Enum):
    """Esito complessivo di una validazione costituzionale."""
    PASS               = "pass"
    PASS_WITH_WARNINGS = "pass_with_warnings"
    FAIL               = "fail"


class EnforcementAction(str, Enum):
    """Azione da intraprendere dopo la validazione."""
    ALLOW          = "allow"           # procede normalmente
    ALLOW_SHADOW   = "allow_shadow"    # procede, ma il risultato è osservato
    REQUIRE_REVIEW = "require_review"  # richiede revisione umana
    DENY           = "deny"            # nega l'operazione


# ---------------------------------------------------------------------------
# ConstitutionVersion
# ---------------------------------------------------------------------------

@dataclass
class ConstitutionVersion:
    version: str
    status: str               # draft | active | superseded
    effective_from: str       # ISO 8601
    created_at: str           # ISO 8601
    approved_by: str
    checksum: str             # SHA-256 del contenuto JSON (escluso il campo checksum)

    def __str__(self) -> str:
        return f"ConstitutionVersion(version={self.version!r}, status={self.status!r})"


# ---------------------------------------------------------------------------
# ConstitutionalPrinciple
# ---------------------------------------------------------------------------

@dataclass
class ConstitutionalPrinciple:
    principle_id: str
    title: str
    description: str
    level: PrincipleLevel
    status: PrincipleStatus
    enforcement: PrincipleEnforcement
    applies_to: list[str]      # ["all"] | ["budget_critical"] | ["constitutional_actions"] | ...
    required_evidence: list[str]
    source_refs: list[str]
    created_at: str
    updated_at: str

    def is_active(self) -> bool:
        """Un principio è valutato solo se in stato ACTIVE o SHADOW."""
        return self.status in (PrincipleStatus.ACTIVE, PrincipleStatus.SHADOW)

    def applies_to_request(
        self,
        *,
        action_type: str,
        budget_impact: float | None = None,
        authority_mode: str | None = None,
    ) -> bool:
        """Verifica se il principio è rilevante per la richiesta data."""
        for scope in self.applies_to:
            if scope == "all":
                return True
            if scope == "budget_critical" and budget_impact is not None and budget_impact > 0:
                return True
            if scope == "constitutional_actions" and "constitutional" in action_type.lower():
                return True
            if scope == "autonomous_actions" and authority_mode == "autonomous":
                return True
        return False

    @classmethod
    def from_dict(cls, d: dict) -> "ConstitutionalPrinciple":
        return cls(
            principle_id=d["principle_id"],
            title=d["title"],
            description=d["description"],
            level=PrincipleLevel(d["level"]),
            status=PrincipleStatus(d["status"]),
            enforcement=PrincipleEnforcement(d["enforcement"]),
            applies_to=d.get("applies_to", ["all"]),
            required_evidence=d.get("required_evidence", []),
            source_refs=d.get("source_refs", []),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
        )


# ---------------------------------------------------------------------------
# ConstitutionalValidationRequest
# ---------------------------------------------------------------------------

@dataclass
class ConstitutionalValidationRequest:
    decision_id: str           # correlation_id o decision_record_id come stringa
    organ_id: str
    action_type: str
    authority_mode: str
    evidence_refs: list[str]   # riferimenti a evidenze (path, doc ID, ecc.)
    business_cell_id: str | None = None
    budget_impact: float | None = None
    risk_level: str | None = None  # low | medium | high
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# ConstitutionalValidationResult
# ---------------------------------------------------------------------------

@dataclass
class ConstitutionalValidationResult:
    validation_id: str
    decision_id: str
    constitution_version: str
    status: ValidationStatus
    evaluated_principles: list[str]   # principle_ids valutati
    passed_principles: list[str]
    violated_principles: list[str]
    warnings: list[str]
    enforcement_action: EnforcementAction
    explanation: str
    evaluated_at: str

    def to_dict(self) -> dict:
        """Serializzazione machine-readable per audit e storage."""
        return {
            "validation_id": self.validation_id,
            "decision_id": self.decision_id,
            "constitution_version": self.constitution_version,
            "status": self.status.value,
            "evaluated_principles": self.evaluated_principles,
            "passed_principles": self.passed_principles,
            "violated_principles": self.violated_principles,
            "warnings": self.warnings,
            "enforcement_action": self.enforcement_action.value,
            "explanation": self.explanation,
            "evaluated_at": self.evaluated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ConstitutionalValidationResult":
        return cls(
            validation_id=d["validation_id"],
            decision_id=d["decision_id"],
            constitution_version=d["constitution_version"],
            status=ValidationStatus(d["status"]),
            evaluated_principles=d["evaluated_principles"],
            passed_principles=d["passed_principles"],
            violated_principles=d["violated_principles"],
            warnings=d["warnings"],
            enforcement_action=EnforcementAction(d["enforcement_action"]),
            explanation=d["explanation"],
            evaluated_at=d["evaluated_at"],
        )


# ---------------------------------------------------------------------------
# PrincipleEvaluationDetail (per esplicazione machine-readable)
# ---------------------------------------------------------------------------

@dataclass
class PrincipleEvaluationDetail:
    principle_id: str
    applicable: bool
    passed: bool | None        # None = non valutato (not applicable)
    warning: str | None
    violation_reason: str | None
    data_missing: str | None   # dato assente che impedisce la valutazione completa


# ---------------------------------------------------------------------------
# Eccezioni
# ---------------------------------------------------------------------------

class ConstitutionalViolationError(RuntimeError):
    """Sollevata in modalità enforce quando un principio BLOCKING è violato.

    In V0 nessun principio ha enforcement BLOCKING, quindi questa eccezione
    non viene mai sollevata in produzione. La struttura è predisposta per
    l'evoluzione futura.
    """


class ConstitutionLoadError(ValueError):
    """Errore non recuperabile durante il caricamento della Costituzione
    (file corrotto, schema non valido, checksum non corrispondente).

    In shadow mode viene intercettata e registrata in audit senza bloccare.
    """

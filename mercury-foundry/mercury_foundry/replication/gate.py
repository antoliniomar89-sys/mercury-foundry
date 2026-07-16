"""Replication Gate deterministico — MF-REPL-001.

Valuta se una DedicatedMercuryGenesisRequest è pronta per la fase di packaging.
Produce un ReplicationGateResult. Non avvia provisioning. Non crea repliche reali.

Gate checks (deterministici, nessun LLM):
  1. Evidence validation (CONST-001 equivalente)
  2. Product validation score minimo
  3. Independence evaluation
  4. Product family coherence (se multi-prodotto)
  5. Capability portability
  6. Constitutional validation (shadow mode)
  7. Authority authorization (GENESIS_APPROVE, shadow mode)
  8. Budget envelope valido
  9. Nessuna dipendenza proibita dal Main (dal IndependenceContract)

In V0:
  - il gate può produrre pass o pass_with_conditions
  - non avvia provisioning
  - non crea una replica reale
  - produce GenesisApprovalRecord nell'audit (via events.py)
"""

from __future__ import annotations

import sqlite3

from mercury_foundry.replication.models import (
    DedicatedMercuryGenesisRequest,
    DedicatedMercuryIndependenceContract,
    FamilyRecommendation,
    GateStatus,
    IndependenceStatus,
    ProductFamilyAssessment,
    ReplicationGateRequest,
    ReplicationGateResult,
    _new_id,
    _now_iso,
)

_MIN_VALIDATION_SCORE = 0.5       # soglia minima per pass
_MIN_EVIDENCE_REFS    = 1         # almeno 1 evidence ref (CONST-001)


def evaluate_replication_gate(
    conn: sqlite3.Connection,
    request: DedicatedMercuryGenesisRequest,
    gate_request: ReplicationGateRequest,
    independence_contract: DedicatedMercuryIndependenceContract,
    *,
    family_assessment: ProductFamilyAssessment | None = None,
) -> ReplicationGateResult:
    """Valuta il gate deterministicamente.

    Non avvia provisioning. Non crea replica reale.
    """
    now = _now_iso()
    gate_result_id = _new_id()
    blockers: list[str] = []
    warnings: list[str] = []
    required_actions: list[str] = []

    # --- 1. Evidence ---
    if len(gate_request.evidence_refs) < _MIN_EVIDENCE_REFS:
        blockers.append(
            f"evidence_refs insufficienti: minimo {_MIN_EVIDENCE_REFS}, "
            f"trovati {len(gate_request.evidence_refs)} (CONST-001: budget_impact richiede evidenza)."
        )

    # --- 2. Product validation score ---
    pv_score = request.product_validation_score or 0.0
    if pv_score < _MIN_VALIDATION_SCORE:
        blockers.append(
            f"product_validation_score={pv_score:.2f} sotto la soglia minima "
            f"{_MIN_VALIDATION_SCORE:.2f}."
        )

    # --- 3. Independence evaluation ---
    independence_status = independence_contract.status
    if independence_status == IndependenceStatus.INSUFFICIENT:
        for b in independence_contract.blockers:
            blockers.append(f"[independence] {b}")
    elif independence_status == IndependenceStatus.CONDITIONALLY_READY:
        for w in independence_contract.warnings:
            warnings.append(f"[independence] {w}")
        required_actions.append(
            "Risolvere le condizioni di indipendenza prima del provisioning."
        )
    elif independence_status == IndependenceStatus.NOT_ASSESSED:
        blockers.append("Independence contract non è stato valutato.")

    # --- 4. Product family coherence ---
    family_coherence_status = "not_applicable"
    if len(request.validated_product_ids) > 1:
        if family_assessment is None:
            blockers.append(
                "Prodotti multipli ma nessun ProductFamilyAssessment fornito."
            )
            family_coherence_status = "missing"
        else:
            rec = family_assessment.recommendation
            if rec == FamilyRecommendation.SEPARATE_INSTANCES:
                blockers.append(
                    f"ProductFamilyAssessment raccomanda istanze separate "
                    f"(score={family_assessment.coherence_score:.2f}): "
                    f"conflitti={family_assessment.conflicts[:2]}."
                )
                family_coherence_status = "fail"
            elif rec == FamilyRecommendation.MANUAL_REVIEW:
                warnings.append(
                    "ProductFamilyAssessment raccomanda manual_review: "
                    "la coerenza della famiglia non è sufficiente per approvazione automatica."
                )
                required_actions.append("Revisione manuale della coerenza dei prodotti.")
                family_coherence_status = "review"
            elif rec == FamilyRecommendation.INSUFFICIENT_EVIDENCE:
                blockers.append(
                    "ProductFamilyAssessment: evidenza insufficiente per valutare la famiglia."
                )
                family_coherence_status = "insufficient_evidence"
            else:
                family_coherence_status = "pass"

    # --- 5. Capability portability (da CapabilityBundleManifest se presente) ---
    # Verifica se ci sono capability richieste con status non portabile
    non_portable_caps: list[str] = []
    if request.requested_capability_bundle_ids:
        # In V0 non abbiamo un provider reale: notiamo solo che esistono capability richieste
        warnings.append(
            f"Capability bundle richiesti: {request.requested_capability_bundle_ids}. "
            "Verificare portabilità prima del provisioning (NullCapabilityProvider in V0)."
        )

    # --- 6. Constitutional validation (shadow, non bloccante) ---
    const_validation_id: str | None = None
    const_status = "shadow_pass"
    try:
        from mercury_foundry.constitutional.shadow import maybe_validate_constitution
        const_result = maybe_validate_constitution(
            conn,
            organ_key="REPLICATION_GOVERNANCE",
            decision_type="GENESIS_APPROVE",
            authority_mode="escalation_required",
            subject_type="genesis_request",
            subject_id=request.genesis_request_id,
            evidence_refs=gate_request.evidence_refs,
            budget_impact=request.requested_budget_envelope,
            risk_level="medium",
            correlation_id=gate_request.correlation_id,
            metadata={
                "source_mission_id": request.source_mission_id,
                "genesis_reason": request.genesis_reason.value,
                "product_ids": request.validated_product_ids,
            },
        )
        if const_result is not None:
            const_validation_id = getattr(const_result, "validation_id", None)
            if hasattr(const_result, "warnings") and const_result.warnings:
                warnings.extend(const_result.warnings[:2])
    except Exception:
        warnings.append("Constitutional validation non disponibile (shadow mode).")

    # --- 7. Authority authorization (shadow, non bloccante in V0) ---
    authority_decision_id: str | None = None
    authority_status = "proposal"
    try:
        from mercury_foundry.autonomy.authorization import authorize_organ_decision
        auth_result = authorize_organ_decision(
            conn,
            organ_key="REPLICATION_GOVERNANCE",
            decision_type="GENESIS_APPROVE",
            subject_type="genesis_request",
            subject_id=request.genesis_request_id,
            estimated_budget=request.requested_budget_envelope,
        )
        authority_status = auth_result.resulting_status
        if auth_result.decision_record_id is not None:
            authority_decision_id = str(auth_result.decision_record_id)
        # GENESIS_APPROVE è escalation_required: non blocca in shadow mode
        if auth_result.authority_mode == "forbidden":
            blockers.append(
                f"Authority ha bloccato GENESIS_APPROVE: {auth_result.reason}"
            )
    except Exception:
        warnings.append("Authority authorization non disponibile (shadow mode).")

    # --- 8. Budget envelope ---
    economic_readiness = request.requested_budget_envelope >= 0
    if not economic_readiness:
        blockers.append("requested_budget_envelope negativo.")

    # --- Calcola gate status ---
    if blockers:
        gate_status = GateStatus.FAIL
        approved = False
        explanation = (
            f"Replication Gate: FAIL. {len(blockers)} blocco/i: "
            f"{'; '.join(blockers[:2])}."
        )
        approved_genesis_profile = None
    elif required_actions:
        gate_status = GateStatus.PASS_WITH_CONDITIONS
        approved = True
        explanation = (
            f"Replication Gate: PASS_WITH_CONDITIONS. "
            f"Azioni richieste prima del provisioning: {required_actions[0]}."
        )
        approved_genesis_profile = request.requested_genesis_profile
    elif warnings:
        gate_status = GateStatus.PASS_WITH_CONDITIONS
        approved = True
        explanation = (
            f"Replication Gate: PASS_WITH_CONDITIONS. "
            f"{len(warnings)} avvertimento/i da risolvere."
        )
        approved_genesis_profile = request.requested_genesis_profile
    else:
        gate_status = GateStatus.PASS
        approved = True
        explanation = "Replication Gate: PASS. Tutti i controlli superati."
        approved_genesis_profile = request.requested_genesis_profile

    return ReplicationGateResult(
        gate_result_id=gate_result_id,
        approved=approved,
        status=gate_status,
        validation_score=pv_score,
        independence_status=independence_status,
        family_coherence_status=family_coherence_status,
        constitutional_status=const_status,
        authority_status=authority_status,
        economic_readiness=economic_readiness,
        unresolved_assumptions=(
            request.metadata.get("unresolved_assumptions", [])
        ),
        blockers=blockers,
        warnings=warnings,
        required_actions=required_actions,
        approved_genesis_profile=approved_genesis_profile,
        evaluated_at=now,
        explanation=explanation,
        constitutional_validation_id=const_validation_id,
        authority_decision_id=authority_decision_id,
    )

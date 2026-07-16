"""Policy Engine deterministico per decisioni di Outcome — MF-OUTCOME-001.

Valuta le metriche correnti di una Mission rispetto al suo EconomicOutcomePlan
e produce una OutcomeDecision con motivazioni machine-readable.

Ordine di priorità delle decisioni:
  STOP > REQUIRE_REVIEW > SCALE > PAUSE > CONTINUE

Nessun LLM. Nessun scale automatico di budget. Nessuna vendita reale.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from mercury_foundry.outcome.models import (
    DecisionType,
    EconomicOutcomePlan,
    OutcomeDecision,
    OutcomeMetricSnapshot,
    ResourceEnvelope,
    Reversibility,
    _new_id,
    _now_iso,
)
from mercury_foundry.outcome.scoring import OutcomeScorer, ScoringResult, ScoringWeights


# ---------------------------------------------------------------------------
# PolicyConfig
# ---------------------------------------------------------------------------

@dataclass
class PolicyConfig:
    """Configurazione del PolicyEvaluator — tutti i valori con default ragionevoli.

    Nessun numero magico disperso nel codice:
    tutti i threshold sono centralizzati qui.
    """
    # Soglia di rischio oltre cui STOP è triggerato
    risk_limit: float = 0.85

    # Frazione del budget oltre cui si considera "quasi esaurito" (→ PAUSE)
    budget_warning_ratio: float = 0.85

    # Impatto economico (minor units) sopra cui → REQUIRE_REVIEW
    economic_impact_threshold_minor: int = 1_000_000  # 10.000 EUR in centesimi

    # Dopo quanti secondi il mancato accumulo di evidenze diventa critico (→ STOP)
    evidence_timeout_seconds: int = 7 * 24 * 3600  # 7 giorni

    # Scorer (iniettabile)
    scorer: OutcomeScorer = field(default_factory=OutcomeScorer)


# ---------------------------------------------------------------------------
# PolicyEvaluationContext
# ---------------------------------------------------------------------------

@dataclass
class PolicyEvaluationContext:
    """Contesto aggiuntivo opzionale per la valutazione di policy."""
    prohibited_action:      bool = False
    dependency_unavailable: bool = False
    delivery_ready:         bool = False
    authority_change:       bool = False
    genesis_promotion:      bool = False
    correlation_id:         str = field(default_factory=lambda: str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# OutcomePolicyEvaluator
# ---------------------------------------------------------------------------

class OutcomePolicyEvaluator:
    """Valuta la policy di outcome in modo deterministico.

    Produce un OutcomeDecision con decision_type, score, reasons, blockers e
    required_actions — tutto machine-readable.

    Ordine di valutazione:
      1. STOP (condizioni hard — kill_deadline, budget, rischio, etc.)
      2. REQUIRE_REVIEW (condizioni che richiedono supervisione umana)
      3. SCALE (condizioni di successo abbastanza buone da scalare)
      4. PAUSE (condizioni di stallo temporaneo)
      5. CONTINUE (default — tutto entro i limiti)
    """

    def __init__(self, config: PolicyConfig | None = None) -> None:
        self._config = config or PolicyConfig()

    @property
    def config(self) -> PolicyConfig:
        return self._config

    def evaluate(
        self,
        plan: EconomicOutcomePlan,
        snapshot: OutcomeMetricSnapshot,
        envelope: ResourceEnvelope,
        context: PolicyEvaluationContext | None = None,
        *,
        now_iso: str | None = None,
    ) -> OutcomeDecision:
        """Valuta plan + snapshot + envelope e produce una OutcomeDecision.

        `now_iso` può essere iniettato nei test per controllare il "tempo corrente".
        """
        ctx = context or PolicyEvaluationContext()
        now_str = now_iso or _now_iso()
        cfg = self._config

        scoring_result = cfg.scorer.score(plan, snapshot)
        reasons:  list[str] = []
        blockers: list[str] = []
        required_actions: list[str] = []

        # Calcola risorse consumate (per confronto con envelope)
        # La politica usa solo i dati del snapshot per il costo (che è cumulativo)
        total_cost = snapshot.cost_minor

        decision_type, reasons, blockers, required_actions = self._decide(
            plan, snapshot, envelope, ctx, cfg, scoring_result,
            now_str, total_cost,
        )

        # Confidence: alta se c'è abbastanza evidenza e score sopra 50
        confidence = _compute_confidence(plan, snapshot, scoring_result)

        return OutcomeDecision(
            decision_id     = _new_id(),
            mission_id      = plan.mission_id,
            outcome_plan_id = plan.outcome_plan_id,
            decision_type   = decision_type,
            score           = scoring_result.score,
            confidence      = confidence,
            reasons         = reasons,
            blockers        = blockers,
            required_actions= required_actions,
            decided_at      = now_str,
            correlation_id  = ctx.correlation_id,
        )

    def _decide(
        self,
        plan: EconomicOutcomePlan,
        snapshot: OutcomeMetricSnapshot,
        envelope: ResourceEnvelope,
        ctx: PolicyEvaluationContext,
        cfg: PolicyConfig,
        scoring: ScoringResult,
        now_str: str,
        total_cost: int,
    ) -> tuple[str, list[str], list[str], list[str]]:
        """Ritorna (decision_type, reasons, blockers, required_actions)."""

        reasons: list[str] = []
        blockers: list[str] = []
        required_actions: list[str] = []

        # ----------------------------------------------------------------
        # 1. STOP — condizioni hard
        # ----------------------------------------------------------------
        stop_reasons: list[str] = []

        # 1a. kill_deadline superata
        try:
            kd = datetime.fromisoformat(plan.kill_deadline)
            now_dt = datetime.fromisoformat(now_str)
            # Rendi entrambi timezone-aware se non lo sono
            if kd.tzinfo is None:
                kd = kd.replace(tzinfo=timezone.utc)
            if now_dt.tzinfo is None:
                now_dt = now_dt.replace(tzinfo=timezone.utc)
            if now_dt >= kd:
                stop_reasons.append(
                    f"kill_deadline superata: deadline={plan.kill_deadline}, now={now_str}"
                )
        except (ValueError, TypeError):
            pass  # kill_deadline malformata — già validata al momento della creazione

        # 1b. Budget consumato oltre il massimo
        if total_cost > plan.maximum_cost_minor:
            stop_reasons.append(
                f"budget massimo superato: costo_accumulato={total_cost}, "
                f"maximum_cost_minor={plan.maximum_cost_minor}"
            )

        # 1c. Azione proibita
        if ctx.prohibited_action:
            stop_reasons.append("azione proibita rilevata: operazione non consentita dal mandato")

        # 1d. stop_threshold raggiunta
        if plan.stop_threshold is not None:
            if snapshot.risk_score >= plan.stop_threshold:
                stop_reasons.append(
                    f"stop_threshold raggiunta: risk_score={snapshot.risk_score:.3f} "
                    f">= stop_threshold={plan.stop_threshold}"
                )

        # 1e. Rischio oltre limite
        if snapshot.risk_score > cfg.risk_limit:
            stop_reasons.append(
                f"rischio oltre limite: risk_score={snapshot.risk_score:.3f} "
                f"> risk_limit={cfg.risk_limit}"
            )

        # 1f. Zero evidenze dopo il periodo minimo
        if (
            snapshot.evidence_count == 0
            and snapshot.elapsed_seconds >= cfg.evidence_timeout_seconds
            and plan.minimum_evidence_count > 0
        ):
            stop_reasons.append(
                f"zero evidenze dopo {snapshot.elapsed_seconds}s "
                f"(timeout={cfg.evidence_timeout_seconds}s)"
            )

        if stop_reasons:
            return (
                DecisionType.STOP.value,
                stop_reasons,
                stop_reasons,  # tutti i motivi di STOP sono anche blockers
                ["terminate_mission", "notify_authority", "document_learnings"],
            )

        # ----------------------------------------------------------------
        # 2. REQUIRE_REVIEW — supervisione umana richiesta
        # ----------------------------------------------------------------
        review_reasons: list[str] = []

        # 2a. Conflitto tra indicatori: score alto ma rischio alto
        if scoring.score >= 60.0 and snapshot.risk_score >= cfg.risk_limit * 0.7:
            review_reasons.append(
                f"conflitto indicatori: score={scoring.score:.1f} ma "
                f"risk_score={snapshot.risk_score:.3f} (soglia attivazione "
                f"{cfg.risk_limit * 0.7:.3f})"
            )

        # 2b. Impatto economico sopra soglia umana
        expected_total = plan.expected_revenue_minor or plan.maximum_cost_minor
        if expected_total >= cfg.economic_impact_threshold_minor:
            review_reasons.append(
                f"impatto economico sopra soglia: expected={expected_total} "
                f">= threshold={cfg.economic_impact_threshold_minor} minor units"
            )

        # 2c. Decisione irreversibile con esito positivo (scale potenziale)
        if (
            plan.reversibility == Reversibility.IRREVERSIBLE.value
            and snapshot.profit_minor > 0
        ):
            review_reasons.append(
                "decisione irreversibile con profitto positivo: "
                "richiesta approvazione umana prima di procedere"
            )

        # 2d. Modifica dell'autorità
        if ctx.authority_change:
            review_reasons.append("richiesta modifica autorità: escalation obbligatoria")

        # 2e. Promozione verso replication/genesis
        if ctx.genesis_promotion:
            review_reasons.append(
                "promozione verso Replication/Genesis rilevata: "
                "supervisione umana obbligatoria (V0 invariante)"
            )

        if review_reasons:
            return (
                DecisionType.REQUIRE_REVIEW.value,
                review_reasons,
                [],
                ["escalate_to_authority", "provide_decision_context"],
            )

        # ----------------------------------------------------------------
        # 3. SCALE — condizioni per scalare
        # ----------------------------------------------------------------
        scale_reasons: list[str] = []
        scale_blockers: list[str] = []

        # Condizioni scale (tutte devono essere soddisfatte)
        scale_threshold_met = (
            plan.scale_threshold is not None
            and snapshot.profit_minor > 0
            and scoring.score >= plan.scale_threshold
        )
        min_evidence_met = snapshot.evidence_count >= plan.minimum_evidence_count
        risk_ok = snapshot.risk_score <= cfg.risk_limit
        within_budget = total_cost <= plan.maximum_cost_minor
        delivery_ready = ctx.delivery_ready

        if scale_threshold_met and min_evidence_met and risk_ok and within_budget and delivery_ready:
            scale_reasons.append(
                f"scale threshold raggiunta: score={scoring.score:.1f} "
                f">= scale_threshold={plan.scale_threshold}"
            )
            scale_reasons.append(f"profitto positivo: profit_minor={snapshot.profit_minor}")
            scale_reasons.append(
                f"evidenze minime presenti: {snapshot.evidence_count} "
                f">= {plan.minimum_evidence_count}"
            )
            return (
                DecisionType.SCALE.value,
                scale_reasons,
                [],
                [
                    "propose_scale_to_authority",   # NON aumenta budget automaticamente
                    "document_scale_rationale",
                    "await_human_approval_for_budget",
                ],
            )

        # ----------------------------------------------------------------
        # 4. PAUSE — stallo temporaneo
        # ----------------------------------------------------------------
        pause_reasons: list[str] = []

        # 4a. Dati insufficienti per decidere (poca evidenza, elapsed basso)
        if (
            snapshot.evidence_count < plan.minimum_evidence_count
            and snapshot.elapsed_seconds < cfg.evidence_timeout_seconds
        ):
            pause_reasons.append(
                f"evidenze insufficienti: {snapshot.evidence_count} < "
                f"{plan.minimum_evidence_count} (timeout non ancora raggiunto)"
            )

        # 4b. Dipendenza obbligatoria indisponibile
        if ctx.dependency_unavailable:
            pause_reasons.append(
                "dipendenza obbligatoria non disponibile: attendere ripristino"
            )

        # 4c. Budget quasi esaurito (ma non superato)
        budget_used_ratio = total_cost / plan.maximum_cost_minor if plan.maximum_cost_minor > 0 else 0.0
        if budget_used_ratio >= cfg.budget_warning_ratio:
            pause_reasons.append(
                f"budget quasi esaurito: {budget_used_ratio:.0%} del massimo consumato "
                f"(warning threshold: {cfg.budget_warning_ratio:.0%})"
            )

        if pause_reasons:
            return (
                DecisionType.PAUSE.value,
                pause_reasons,
                [],
                ["await_more_data", "check_dependencies", "notify_resource_owner"],
            )

        # ----------------------------------------------------------------
        # 5. CONTINUE — default
        # ----------------------------------------------------------------
        continue_reasons: list[str] = [
            f"entro budget: costo={total_cost} / massimo={plan.maximum_cost_minor}",
            f"nessun blocker rilevato, score={scoring.score:.1f}",
        ]
        if snapshot.evidence_count > 0:
            continue_reasons.append(
                f"evidenza in crescita: {snapshot.evidence_count} evidence_count"
            )

        return (
            DecisionType.CONTINUE.value,
            continue_reasons,
            [],
            [],
        )


# ---------------------------------------------------------------------------
# Helper: confidence
# ---------------------------------------------------------------------------

def _compute_confidence(
    plan: EconomicOutcomePlan,
    snapshot: OutcomeMetricSnapshot,
    scoring: ScoringResult,
) -> float:
    """Confidence 0–1: quanto siamo sicuri della decisione.

    Alta se: evidenza sufficiente + elapsed > review_interval + score distante da 50.
    """
    evidence_ratio = (
        snapshot.evidence_count / plan.minimum_evidence_count
        if plan.minimum_evidence_count > 0
        else 1.0
    )
    evidence_confidence = min(1.0, evidence_ratio)

    elapsed_ratio = (
        snapshot.elapsed_seconds / plan.review_interval_seconds
        if plan.review_interval_seconds > 0
        else 1.0
    )
    time_confidence = min(1.0, elapsed_ratio)

    # Quanto lo score è lontano da 50 (zona di incertezza)
    score_distance = abs(scoring.score - 50.0) / 50.0
    score_confidence = min(1.0, score_distance)

    # Media pesata (dà più peso all'evidenza e al tempo)
    confidence = (
        evidence_confidence * 0.40
        + time_confidence    * 0.35
        + score_confidence   * 0.25
    )
    return round(max(0.0, min(1.0, confidence)), 3)

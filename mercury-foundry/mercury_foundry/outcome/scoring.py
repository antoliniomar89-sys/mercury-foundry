"""Scoring deterministico per OutcomePlan — MF-OUTCOME-001.

Formula esplicita, configurabile, testabile, priva di numeri magici dispersi.
Nessun LLM coinvolto.

Componenti positivi (0–100 ciascuno):
  economic_return_score  — ratio profitto atteso / costo massimo
  evidence_score         — evidenze raccolte vs minimo richiesto
  strategic_score        — strategic_value_score (0-1 → 0-100)
  learning_score         — learning_value_score (0-1 → 0-100)
  speed_score            — inversamente proporzionale alla durata massima

Penalità (0–100 ciascuna, sottratte):
  risk_penalty           — da risk_score (0-1 → 0-100)
  irreversibility_penalty — reversibile=0, parziale=50, irreversibile=100

Score finale = clamp(sum(component_i * weight_i) - sum(penalty_j * weight_j), 0, 100)

Con pesi di default:
  component weights sommano a 1.0 → max positivo = 100
  penalty weights sono aggiuntivi, sottraggono fino a 20 punti
  Score finale clampato a [0, 100].
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mercury_foundry.outcome.models import (
    EconomicOutcomePlan,
    OutcomeMetricSnapshot,
    Reversibility,
)


# ---------------------------------------------------------------------------
# Pesi di default — centralizzati, configurabili, documentati
# ---------------------------------------------------------------------------

# Pesi positivi (devono sommare a 1.0 per avere un max score di 100).
DEFAULT_COMPONENT_WEIGHTS: dict[str, float] = {
    "economic_return_score": 0.35,
    "evidence_score":        0.25,
    "strategic_score":       0.20,
    "learning_score":        0.10,
    "speed_score":           0.10,
}

# Penalità (applicate moltiplicando il peso per il valore 0-100 e sottraendo).
DEFAULT_PENALTY_WEIGHTS: dict[str, float] = {
    "risk_penalty":           0.15,
    "irreversibility_penalty": 0.05,
}

# Riferimenti di durata per speed_score (secondi)
SPEED_REFERENCE_SECONDS = {
    "max": 365 * 24 * 3600,  # 1 anno → score ≈ 0
    "min": 3600,              # 1 ora  → score = 100
}


# ---------------------------------------------------------------------------
# ScoringWeights — struttura configurabile
# ---------------------------------------------------------------------------

@dataclass
class ScoringWeights:
    """Pesi configurabili per OutcomeScorer.

    component_weights deve sommare a 1.0 (non forzato ma documentato).
    penalty_weights possono essere qualsiasi valore positivo.
    """
    component_weights: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_COMPONENT_WEIGHTS)
    )
    penalty_weights: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_PENALTY_WEIGHTS)
    )


# ---------------------------------------------------------------------------
# ScoringResult
# ---------------------------------------------------------------------------

@dataclass
class ScoringResult:
    """Risultato dello scoring con dettaglio per componente."""
    score: float                    # 0–100 finale clampato
    components: dict[str, float]    # nome → valore 0-100 prima di pesare
    weighted_components: dict[str, float]  # nome → valore pesato
    penalties: dict[str, float]     # nome → valore 0-100 prima di pesare
    weighted_penalties: dict[str, float]   # nome → penalità pesata
    raw_positive: float             # somma componenti pesate (prima del clamp)
    raw_penalty:  float             # somma penalità pesate
    raw_score:    float             # raw_positive - raw_penalty (prima del clamp)


# ---------------------------------------------------------------------------
# Calcolo componenti individuali
# ---------------------------------------------------------------------------

def _economic_return_score(plan: EconomicOutcomePlan) -> float:
    """Score 0–100: rapporto profitto atteso / costo massimo.

    Se expected_profit_minor > 0 usa direttamente profit/cost * 100.
    Se solo expected_revenue_minor: usa (revenue - cost) / cost * 100.
    Se nessun dato: 0.
    """
    if plan.maximum_cost_minor <= 0:
        return 50.0  # costo zero → tratta come neutro
    profit = plan.expected_profit_minor
    revenue = plan.expected_revenue_minor
    if profit is not None and profit > 0:
        ratio = profit / plan.maximum_cost_minor
    elif revenue is not None and revenue > 0:
        net = revenue - plan.maximum_cost_minor
        ratio = net / plan.maximum_cost_minor
    else:
        return 0.0
    return max(0.0, min(100.0, ratio * 100.0))


def _evidence_score(plan: EconomicOutcomePlan, snapshot: OutcomeMetricSnapshot) -> float:
    """Score 0–100: evidenze raccolte / minimo richiesto.

    Se minimum_evidence_count == 0 → score 100 (nessun minimo richiesto).
    """
    if plan.minimum_evidence_count <= 0:
        return 100.0
    ratio = snapshot.evidence_count / plan.minimum_evidence_count
    return max(0.0, min(100.0, ratio * 100.0))


def _strategic_score(plan: EconomicOutcomePlan) -> float:
    """Score 0–100: scala da strategic_value_score (0-1)."""
    return max(0.0, min(100.0, plan.strategic_value_score * 100.0))


def _learning_score(plan: EconomicOutcomePlan) -> float:
    """Score 0–100: scala da learning_value_score (0-1)."""
    return max(0.0, min(100.0, plan.learning_value_score * 100.0))


def _speed_score(plan: EconomicOutcomePlan) -> float:
    """Score 0–100: inversamente proporzionale a maximum_duration_seconds.

    Durata <= 1 ora → 100. Durata >= 1 anno → ≈ 0.
    Scala linearmente (su asse logaritmico implicito) tra i due estremi.
    """
    d = plan.maximum_duration_seconds
    if d <= 0:
        return 0.0
    min_s = SPEED_REFERENCE_SECONDS["min"]
    max_s = SPEED_REFERENCE_SECONDS["max"]
    if d <= min_s:
        return 100.0
    if d >= max_s:
        return 0.0
    # Scala lineare inversa: più lungo = punteggio minore
    ratio = (max_s - d) / (max_s - min_s)
    return max(0.0, min(100.0, ratio * 100.0))


def _risk_penalty(snapshot: OutcomeMetricSnapshot) -> float:
    """Penalità 0–100: scala da risk_score (0-1)."""
    return max(0.0, min(100.0, snapshot.risk_score * 100.0))


def _irreversibility_penalty(plan: EconomicOutcomePlan) -> float:
    """Penalità 0–100: reversibile=0, parziale=50, irreversibile=100."""
    mapping = {
        Reversibility.REVERSIBLE.value:           0.0,
        Reversibility.PARTIALLY_REVERSIBLE.value: 50.0,
        Reversibility.IRREVERSIBLE.value:         100.0,
    }
    return mapping.get(plan.reversibility, 50.0)


# ---------------------------------------------------------------------------
# OutcomeScorer
# ---------------------------------------------------------------------------

class OutcomeScorer:
    """Scorer deterministico per EconomicOutcomePlan + OutcomeMetricSnapshot.

    Uso:
        scorer = OutcomeScorer()  # pesi di default
        result = scorer.score(plan, snapshot)
        print(result.score)       # 0–100

    Personalizzazione pesi:
        weights = ScoringWeights(
            component_weights={"economic_return_score": 0.5, ...},
        )
        scorer = OutcomeScorer(weights=weights)
    """

    def __init__(self, weights: ScoringWeights | None = None) -> None:
        self._weights = weights or ScoringWeights()

    @property
    def weights(self) -> ScoringWeights:
        return self._weights

    def score(
        self,
        plan: EconomicOutcomePlan,
        snapshot: OutcomeMetricSnapshot,
    ) -> ScoringResult:
        """Calcola lo score per il piano dato il suo snapshot di metriche.

        Ritorna un ScoringResult con dettaglio completo per audit/debug.
        """
        # Calcola componenti grezze (0-100)
        components: dict[str, float] = {
            "economic_return_score": _economic_return_score(plan),
            "evidence_score":        _evidence_score(plan, snapshot),
            "strategic_score":       _strategic_score(plan),
            "learning_score":        _learning_score(plan),
            "speed_score":           _speed_score(plan),
        }
        # Calcola penalità grezze (0-100)
        penalties: dict[str, float] = {
            "risk_penalty":           _risk_penalty(snapshot),
            "irreversibility_penalty": _irreversibility_penalty(plan),
        }

        cw = self._weights.component_weights
        pw = self._weights.penalty_weights

        weighted_components = {
            k: components[k] * cw.get(k, 0.0)
            for k in components
        }
        weighted_penalties = {
            k: penalties[k] * pw.get(k, 0.0)
            for k in penalties
        }

        raw_positive = sum(weighted_components.values())
        raw_penalty  = sum(weighted_penalties.values())
        raw_score    = raw_positive - raw_penalty
        final_score  = max(0.0, min(100.0, raw_score))

        return ScoringResult(
            score               = round(final_score, 2),
            components          = components,
            weighted_components = weighted_components,
            penalties           = penalties,
            weighted_penalties  = weighted_penalties,
            raw_positive        = raw_positive,
            raw_penalty         = raw_penalty,
            raw_score           = raw_score,
        )

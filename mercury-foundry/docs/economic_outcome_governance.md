# Economic Outcome Governance V0

**Task:** MF-OUTCOME-001  
**Status:** READY_OUTCOME_SHADOW  
**Dipendenze:** MF-ARCH-008, MF-CONST-001, MF-MISSION-001, MF-REPL-001

---

## 1. Scopo

Il layer Economic Outcome Governance associa ogni Mission a un piano di outcome economico misurabile, governa i limiti di risorse, produce decisioni deterministiche (nessun LLM), e prepara il terreno per MF-ECO-001 (vertical slice economico completo).

**Invarianti V0:**
- Nessun LLM nelle decisioni di scoring o policy.
- `OUTCOME_BUDGET_INCREASE` è sempre `forbidden`.
- `SCALE` non modifica mai il budget automaticamente — produce solo una proposta.
- Tutti gli importi monetari sono **integer minor units** (es. centesimi di euro).

---

## 2. Struttura del package

```
mercury_foundry/outcome/
├── __init__.py          # package stub
├── models.py            # dataclass, enum, eccezioni
├── scoring.py           # OutcomeScorer — formula deterministica
├── policy.py            # OutcomePolicyEvaluator — decisioni CONTINUE/PAUSE/STOP/SCALE/REQUIRE_REVIEW
├── lifecycle.py         # transizioni di stato OutcomePlan
├── registry.py          # CRUD SQLite (6 tabelle)
├── allocator.py         # ResourceAllocator — budget in-memory + DB
├── events.py            # emit_outcome_event, 16 tipi evento
├── seed.py              # seed_economic_governance — organo + 8 mandati
└── service.py           # OutcomeService — orchestrazione
```

---

## 3. Schema DB (6 tabelle nuove)

| Tabella | Scopo |
|---------|-------|
| `economic_outcome_plans` | Piano economico per Mission (versioned, ottimistic locking) |
| `outcome_metric_snapshots` | Snapshot metriche economiche (append-only) |
| `resource_envelopes` | Envelope risorse allocate a una Mission |
| `resource_consumptions` | Consumo risorse (append-only, idempotente per `idempotency_key`) |
| `outcome_decisions` | Decisioni di policy (immutabili dopo INSERT) |
| `outcome_transition_records` | Log transizioni di stato OutcomePlan (append-only) |

---

## 4. Formula di Scoring

Il punteggio finale è deterministico, senza LLM, nel range [0, 100].

### 4.1 Componenti (peso massimo = 100 se tutti a 1.0)

| Componente | Peso | Descrizione |
|------------|------|-------------|
| `economic_return_score` | 0.35 | `profit_minor / max_cost` clampato in [0,1], poi × 0.35 × 100 |
| `evidence_score` | 0.25 | `evidence_count / minimum_evidence_count` clampato in [0,1] |
| `strategic_score` | 0.20 | `plan.strategic_value_score` |
| `learning_score` | 0.10 | `plan.learning_value_score` |
| `speed_score` | 0.10 | inverso della durata massima normalizzata |

**Raw positivo** = Σ(componente × peso × 100)

### 4.2 Penalità (sottratte dal raw)

| Penalità | Peso | Descrizione |
|----------|------|-------------|
| `risk_penalty` | 0.15 | `snapshot.risk_score` × 0.15 × 100 |
| `irreversibility_penalty` | 0.05 | 1.0 se `reversibility == "irreversible"`, else 0 |

**Punteggio finale** = `clamp(raw_positivo - penalità, 0, 100)`

### 4.3 Personalizzazione

```python
from mercury_foundry.outcome.scoring import OutcomeScorer, ScoringWeights

scorer = OutcomeScorer(weights=ScoringWeights(
    component_weights={
        "economic_return_score": 0.5,
        "evidence_score": 0.2,
        "strategic_score": 0.2,
        "learning_score": 0.05,
        "speed_score": 0.05,
    },
    penalty_weights={
        "risk_penalty": 0.20,
        "irreversibility_penalty": 0.10,
    },
))
result = scorer.score(plan, snapshot)
print(result.score)        # float in [0, 100]
print(result.breakdown)    # dict componente → valore
```

---

## 5. Policy — Decisioni

L'`OutcomePolicyEvaluator` valuta piano + snapshot + envelope e produce una `OutcomeDecision`.

**Ordine di priorità (STOP > REQUIRE_REVIEW > SCALE > PAUSE > CONTINUE):**

### 5.1 STOP
Condizioni (una qualunque è sufficiente):
- `kill_deadline` superata
- `snapshot.cost_minor > plan.maximum_cost_minor`
- `snapshot.risk_score > config.risk_limit` (default: 0.90)
- `plan.stop_threshold` definita e `score < stop_threshold`

Azioni prodotte: nessuna (termina la Mission via lifecycle).

### 5.2 REQUIRE_REVIEW
Condizioni:
- `context.authority_change == True`
- Piano irreversibile con `expected_profit_minor > config.economic_impact_threshold_minor`
- Score molto basso + evidence insufficiente su piano critico

Azioni prodotte: `["escalate_to_authority"]`

### 5.3 SCALE
Condizioni (tutte necessarie):
- `plan.scale_threshold` definita e `score >= scale_threshold`
- `evidence_count >= minimum_evidence_count`
- `context.delivery_ready == True`

**Azioni prodotte:** `["propose_scale_to_authority", "await_human_approval_for_budget"]`  
**Invariante:** il budget dell'envelope NON viene modificato automaticamente.

### 5.4 PAUSE
Condizioni:
- `evidence_count < minimum_evidence_count` e tempo trascorso insufficiente
- Score molto basso ma deadline non ancora superata

### 5.5 CONTINUE
Default: nessuna condizione di blocco soddisfatta.

---

## 6. Integrazione Mission

### Mapping decisione → transizione Mission

| Decisione | Transizione Mission |
|-----------|-------------------|
| CONTINUE | nessuna |
| PAUSE | `active → paused` |
| STOP | `active/paused/blocked → terminated` |
| SCALE | nessuna (proposta solo) |
| REQUIRE_REVIEW | nessuna |

### 6.1 Esempio completo

```python
from mercury_foundry.state.db import connect
from mercury_foundry.outcome.service import OutcomeService
from mercury_foundry.outcome.registry import create_outcome_plan, create_metric_snapshot

conn = connect("mercury_foundry.db")
svc = OutcomeService()

# 1. Crea piano
plan = create_outcome_plan(
    conn,
    mission_id               = "m-001",
    correlation_id           = "corr-001",
    objective                = "Validare domanda prodotto X",
    primary_metric           = "revenue_minor",
    target_value             = 100_000.0,
    target_operator          = ">=",
    maximum_cost_minor       = 50_000,        # 500,00 EUR
    maximum_duration_seconds = 30 * 24 * 3600,
    review_interval_seconds  = 7 * 24 * 3600,
    kill_deadline            = "2026-12-31T23:59:59+00:00",
    minimum_evidence_count   = 5,
    strategic_value_score    = 0.8,
    learning_value_score     = 0.7,
    reversibility            = "reversible",
    created_by               = "founder",
    expected_revenue_minor   = 150_000,
    expected_profit_minor    = 100_000,
)

# 2. Registra snapshot metriche
snap = create_metric_snapshot(
    conn,
    outcome_plan_id      = plan.outcome_plan_id,
    mission_id           = "m-001",
    revenue_minor        = 15_000,
    cost_minor           = 8_000,
    profit_minor         = 7_000,
    elapsed_seconds      = 7 * 24 * 3600,
    evidence_count       = 6,
    customer_count       = 4,
    knowledge_gain_score = 0.6,
    risk_score           = 0.15,
)

# 3. Valuta outcome
result = svc.evaluate(
    conn,
    outcome_plan_id = plan.outcome_plan_id,
    actor_id        = "governance_engine",
    correlation_id  = "corr-002",
)

print(result.decision.decision_type)    # "continue" | "pause" | "stop" | "scale" | "require_review"
print(result.decision.score)            # float in [0, 100]
print(result.mission_transition_applied) # True se Mission è stata transizionata
print(result.authority_mode)            # "proposal" | "escalation_required" | ...
print(result.constitutional_status)     # "shadow_passed" | "shadow_violation" | None
```

---

## 7. ResourceEnvelope

```python
from mercury_foundry.outcome.allocator import ResourceAllocator

allocator = ResourceAllocator()

# Alloca envelope
env = allocator.allocate(
    conn,
    mission_id   = "m-001",
    budget_minor = 50_000,          # 500,00 EUR in centesimi
    deadline     = "2026-12-31T23:59:59+00:00",
    allocated_by = "governance_engine",
)

# Consuma con idempotency
cons = allocator.consume(
    conn,
    envelope_id     = env.envelope_id,
    cost_minor      = 3_500,         # 35,00 EUR
    source_ref      = "llm_call_batch_1",
    idempotency_key = "batch-001-2026-07-16",
)

# Risorse residue
remaining = allocator.remaining(conn, envelope_id=env.envelope_id)
print(remaining.budget_remaining_minor)   # 46_500
print(remaining.total_consumed_minor)     # 3_500
print(remaining.exhausted)               # False

# Consumo oltre il limite → ResourceExhaustedError
# allocator.consume(conn, ..., cost_minor=100_000)  # raises ResourceExhaustedError
```

---

## 8. Authority e Constitution

Ogni chiamata `OutcomeService.evaluate()` consulta in ordine:

1. **Authority** (`authorize_organ_decision`) — verifica mandato `OUTCOME_EVALUATE` dell'organo `ECONOMIC_GOVERNANCE` (authority_mode: `proposal`)
2. **Constitutional Core** (`maybe_validate_constitution`) — shadow mode: registra l'evento senza bloccare

### Mandati ECONOMIC_GOVERNANCE

| decision_type | authority_mode |
|---------------|---------------|
| OUTCOME_PLAN_CREATE | proposal |
| RESOURCE_ALLOCATE | escalation_required |
| RESOURCE_CONSUME | proposal |
| OUTCOME_EVALUATE | proposal |
| OUTCOME_PAUSE | proposal |
| OUTCOME_STOP | escalation_required |
| OUTCOME_SCALE_PROPOSE | proposal |
| **OUTCOME_BUDGET_INCREASE** | **forbidden** |

---

## 9. Eventi di Audit

`emit_outcome_event` wrappa `log_action` e produce 16 tipi di evento:

| Tipo evento | Trigger |
|-------------|---------|
| `outcome.plan.created` | `create_plan()` |
| `outcome.plan.updated` | aggiornamento piano |
| `outcome.plan.activated` | attivazione piano |
| `outcome.plan.paused` | piano messo in pausa |
| `outcome.plan.stopped` | piano terminato |
| `outcome.snapshot.recorded` | `record_snapshot()` |
| `outcome.decision.continue` | decisione CONTINUE |
| `outcome.decision.pause` | decisione PAUSE |
| `outcome.decision.stop` | decisione STOP |
| `outcome.decision.scale_proposed` | decisione SCALE |
| `outcome.decision.review_required` | decisione REQUIRE_REVIEW |
| `outcome.resource.allocated` | `allocate()` |
| `outcome.resource.consumed` | `consume()` |
| `outcome.resource.reserved` | `reserve()` |
| `outcome.resource.released` | `release()` |
| `outcome.evaluation.completed` | fine ciclo evaluate |

---

## 10. Debito Tecnico (rinviato a MF-ECO-001)

### 10.1 Necessario prima del vertical slice economico

| Item | Motivo |
|------|--------|
| Persistenza `ResourceAllocator.reservations` nel DB | Le reservation sono in-memory: un restart le azzera |
| Migrazione `MissionBudget` a integer minor units | Attualmente usa `float` (EUR reali), incoerente con il dominio |
| `RESOURCE_ALLOCATE` come escalation_required end-to-end | Il mandato è corretto ma il percorso umano non è ancora wired |
| Aggregazione consumi multi-envelope per una Mission | `get_total_consumption` opera su singolo envelope |

### 10.2 Utile ma rimandabile

| Item | Motivo |
|------|--------|
| Snapshot aggregati (rolling window) | Ora ogni snapshot è puntuale; media mobile ridurrebbe il rumore |
| Configurazione pesi scorer via DB | Ora sono hardcoded in `ScoringWeights`; un organo potrebbe aggiornarli |
| OutcomePlan multi-metrica | Ora `primary_metric` è una stringa libera; potrebbe essere un enum validato |
| Grafici e storico decisioni | Nessuna API di query storica per serie temporali |
| Test di concorrenza | Idempotency e optimistic locking non sono stress-testati con connessioni concorrenti |

### 10.3 Dipendente da dati economici reali

| Item | Motivo |
|------|--------|
| Calibrazione soglie policy | `risk_limit`, `stop_threshold`, `scale_threshold` richiedono dati storici |
| Scoring formula adattativa | I pesi COMPONENT_WEIGHTS/PENALTY_WEIGHTS sono fissi in V0 |
| Revenue e profit tracking | Ora i valori provengono dagli snapshot manuali; richiedono integrazione con sistemi di billing |
| Portfolio orchestration | Coordinamento di multiple Mission concorrenti (MF-ECO-001) |
| Dedicated Mercury provisioning | Fuori scope in V0 per design |

# MF-OUTCOME-001 — Economic Outcome Governance V0
## Implementation Report

**Data:** 2026-07-16  
**Baseline:** MF-ARCH-008 + MF-CONST-001 + MF-MISSION-001 + MF-REPL-001 (commit 24b45ca, 410/410 test)  
**Stato finale:** READY_OUTCOME_SHADOW — 470/470 test verdi — zero regressioni

---

## 1. Obiettivo

Associare ogni Mission a un piano di outcome economico misurabile, governare budget e limiti di tempo, produrre decisioni deterministiche (CONTINUE / PAUSE / STOP / SCALE / REQUIRE_REVIEW) senza LLM, tracciare i consumi di risorse, preparare il terreno per MF-ECO-001.

---

## 2. File prodotti

### Nuovi (package `mercury_foundry/outcome/`)

| File | Righe | Contenuto |
|------|-------|-----------|
| `__init__.py` | 3 | Package stub |
| `models.py` | ~320 | Enums, dataclass, eccezioni |
| `scoring.py` | ~180 | OutcomeScorer, ScoringWeights, 7 funzioni componente/penalità |
| `policy.py` | ~210 | OutcomePolicyEvaluator, PolicyConfig, PolicyEvaluationContext |
| `lifecycle.py` | ~120 | ALLOWED_OUTCOME_TRANSITIONS, check_activation_readiness, apply_outcome_transition |
| `registry.py` | ~340 | CRUD 6 tabelle SQLite |
| `allocator.py` | ~200 | ResourceAllocator (allocate/reserve/consume/release/remaining) |
| `events.py` | ~90 | emit_outcome_event, 16 tipi evento |
| `seed.py` | ~80 | seed_economic_governance — organo + 8 mandati |
| `service.py` | ~500 | OutcomeService (create_plan, record_snapshot, evaluate, _apply_decision_to_mission) |

**Totale nuovo codice:** ~2.043 righe

### Modificati

| File | Modifica |
|------|---------|
| `mercury_foundry/state/schema.sql` | +128 righe — 6 nuove tabelle |
| `mercury_foundry/config.py` | +18 righe — `OUTCOME_AUTO_SCALE_ENABLED=False`, `OUTCOME_AUTO_BUDGET_INCREASE_ENABLED=False` |
| `mercury_foundry/state/db.py` | +46 righe — `_migrate_outcome_indexes()`, chiamata `seed_economic_governance()` |
| `mercury_foundry/diagnostics.py` | +231 righe — `_check_outcome_layer()` (12 check), `OVERALL_READY_OUTCOME_SHADOW` |
| `tests/test_doctor.py` | +4 righe — atteso `READY_OUTCOME_SHADOW` (upgrade da REPLICATION_CONTRACT_SHADOW) |
| `tests/test_arch_008_autonomy.py` | +4 righe — idem |
| `tests/test_mission_001_mf.py` | +4 righe — idem |
| `tests/test_repl_001_mf.py` | +4 righe — idem |

### Nuovi test

| File | Casi | Copertura |
|------|------|-----------|
| `tests/test_outcome_001_mf.py` | 60 | Domain, Scoring, Policy, Resources, Mission Integration, Autonomy, Constitution, Audit, Regression |

### Documentazione

| File | Contenuto |
|------|-----------|
| `docs/economic_outcome_governance.md` | Formula scoring, policy, esempi, debito tecnico |
| `reports/MF_OUTCOME_001_IMPLEMENTATION_REPORT.md` | Questo file |

---

## 3. Risultati test

```
Suite completa:   470/470 verdi — 0 falliti — 0 regressioni
Test MF-OUTCOME:   60/60 verdi
Test pre-esistenti: 410/410 verdi (invariati)
```

### Distribuzione MF-OUTCOME-001 (60 test)

| Gruppo | Intervallo | Descrizione |
|--------|-----------|-------------|
| Domain | 1–8 | Creazione piano, validazione, enums, serializzazione |
| Scoring | 9–15 | Formula scoring, pesi, componenti, clamp [0,100] |
| Policy | 16–23 | CONTINUE, PAUSE, STOP, SCALE, REQUIRE_REVIEW |
| Resources | 24–32 | Allocazione, consumo, limiti, idempotency, reservation |
| Mission Integration | 33–40 | Plan-mission link, transizioni, STOP termina mission, SCALE non auto-scala |
| Autonomy | 41–45 | ECONOMIC_GOVERNANCE seeding, BUDGET_INCREASE forbidden, STOP escalation |
| Constitution | 46–50 | Shadow mode, warnings, no unexpected block |
| Audit | 51–54 | Evento unico, immutabilità decisioni, auditabilità consumi, no DELETE |
| Regression | 55–60 | Doctor READY_OUTCOME_SHADOW, Mission layer, Constitutional shadow, no Dedicated Mercury, no vendita |

---

## 4. Doctor — READY_OUTCOME_SHADOW

47 check OK, 2 WARN pre-esistenti (provider simulato + idempotency index).

### Check outcome layer (tutti OK)

| Check | Risultato |
|-------|-----------|
| `outcome_schema` | 6 tabelle presenti |
| `outcome_indexes` | 4 indici presenti |
| `outcome_governance_organ` | ECONOMIC_GOVERNANCE presente |
| `outcome_governance_mandates` | 8 mandati verificati |
| `outcome_budget_increase_forbidden` | OUTCOME_BUDGET_INCREASE=forbidden |
| `outcome_no_auto_scale` | OUTCOME_AUTO_SCALE_ENABLED=False |
| `outcome_no_auto_budget_increase` | OUTCOME_AUTO_BUDGET_INCREASE_ENABLED=False |
| `outcome_scorer` | OutcomeScorer importabile |
| `outcome_policy_evaluator` | OutcomePolicyEvaluator importabile |
| `outcome_registry` | Registry inizializzabile |
| `outcome_resource_allocator` | ResourceAllocator importabile |
| `outcome_mission_integration` | OutcomeService importabile |
| `outcome_constitutional_core` | Constitutional Core raggiungibile |

---

## 5. Verifica invarianti V0

| Invariante | Stato | Meccanismo |
|-----------|-------|-----------|
| Nessun LLM nelle decisioni | ✅ | Formula deterministica — nessuna chiamata a provider |
| OUTCOME_BUDGET_INCREASE=forbidden | ✅ | Mandato DB + feature flag + test 42 |
| SCALE non auto-scala il budget | ✅ | Produce solo `propose_scale_to_authority` + `await_human_approval_for_budget` |
| Consumo non supera envelope | ✅ | `ResourceExhaustedError` se `cost > remaining` |
| Idempotency consumi | ✅ | `UNIQUE idempotency_key` + `ConsumptionIdempotencyReplay` |
| Decisioni immutabili | ✅ | Nessuna API di UPDATE su `outcome_decisions` |
| Nessuna vendita/pagamento | ✅ | test_60: forbidden_terms non trovati nel sorgente |
| Nessuna Dedicated Mercury | ✅ | test_59: genesis_requests count=0 |
| STOP usa lifecycle Mission | ✅ | `apply_transition(active→terminated)` via `_apply_decision_to_mission` |
| Authority consultata | ✅ | `authorize_organ_decision(ECONOMIC_GOVERNANCE, OUTCOME_EVALUATE)` |
| Constitution consultata | ✅ | `maybe_validate_constitution(shadow)` — non blocca |

---

## 6. Architettura delle decisioni

```
OutcomeService.evaluate()
│
├── 1. Recupera piano + snapshot più recente
├── 2. Calcola score (OutcomeScorer — deterministico, no LLM)
├── 3. Valuta policy (OutcomePolicyEvaluator)
│       STOP > REQUIRE_REVIEW > SCALE > PAUSE > CONTINUE
├── 4. Autorizza (ECONOMIC_GOVERNANCE / OUTCOME_EVALUATE → proposal)
├── 5. Valida Constitution (shadow mode — non blocca)
├── 6. Persiste decisione (immutabile)
├── 7. Applica transizione Mission (se PAUSE o STOP)
└── 8. Emette evento audit
```

---

## 7. Integrazione con layer esistenti

```
MF-ARCH-008   → Autonomy Boundary: ogni decisione passa per authorize_organ_decision
MF-CONST-001  → Constitutional Core: shadow validation su ogni evaluate()
MF-MISSION-001 → Mission lifecycle: PAUSE e STOP transitano la Mission
MF-REPL-001   → nessuna dipendenza diretta (layer indipendente)
```

---

## 8. Debito tecnico rinviato a MF-ECO-001

### Necessario prima del vertical slice economico
1. **Persistenza reservation nel DB** — ora in-memory; un restart perde le reservation aperte
2. **Migrazione MissionBudget a integer minor units** — ora usa float EUR, incoerente col dominio
3. **End-to-end RESOURCE_ALLOCATE escalation** — mandato corretto; percorso umano non ancora wired
4. **Aggregazione consumi multi-envelope** — `get_total_consumption` opera su singolo envelope

### Utile ma rimandabile
5. **Snapshot rolling-window** — medie mobili per ridurre rumore nei segnali economici
6. **Pesi scorer via DB** — ora hardcoded; un organo potrebbe aggiornarli dinamicamente
7. **OutcomePlan multi-metrica** — `primary_metric` è stringa libera; enum validato sarebbe più sicuro
8. **API query storica** — nessuna API per serie temporali di decisioni

### Dipendente da dati economici reali
9. **Calibrazione soglie policy** — `risk_limit`, `stop_threshold`, `scale_threshold` richiedono storico
10. **Scoring adattativo** — pesi fissi in V0; richiedono dati storici per calibrazione
11. **Revenue tracking reale** — snapshot manuali in V0; richiede integrazione con billing
12. **Portfolio orchestration** — coordinamento Mission concorrenti (scope MF-ECO-001)

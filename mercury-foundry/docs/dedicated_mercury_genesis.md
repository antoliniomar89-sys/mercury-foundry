# Dedicated Mercury Genesis Contract V0

**MF-REPL-001 — Replication Layer**

---

## Sommario

Mercury Foundry è un sistema madre (Main). Quando un prodotto è validato e supera un insieme di verifiche deterministiche, Mercury Main può emettere un **Genesis Contract** per una **Dedicated Mercury**: un'istanza autonoma specializzata con la propria identità, costituzione, governance, budget, memoria e audit.

In V0 questo è un **contratto puro**: nessuna replica viene creata fisicamente. L'activation è `forbidden` per mandato di governance (`GENESIS_ACTIVATE = forbidden`).

---

## Architettura del Flusso

```
Mission → Expedition → Evidence → Product Validation
        → Replication Gate → Genesis Contract → Dedicated Mercury (contratto V0)
```

### Componenti

| Componente | File | Scopo |
|------------|------|-------|
| `DedicatedMercuryGenesisRequest` | `replication/models.py` | Modello di intake strutturato |
| `MercuryGeneticPackage` | `replication/models.py` + `genetic_package.py` | Pacchetto genetico (immutabile dopo seal) |
| `DedicatedMercuryIndependenceContract` | `replication/independence.py` | Contratto autonomia operativa |
| `ProductFamilyAssessment` | `replication/family_assessment.py` | Coerenza famiglia prodotti |
| `ReplicationGate` | `replication/gate.py` | Valutazione deterministico gate |
| `GenesisService` | `replication/genesis_service.py` | Entry point flusso proposta |
| `MotherReplicaFederationContract` | `replication/federation.py` | Contratto federativo leggero |
| `REPLICATION_GOVERNANCE` | `replication/seed.py` | Organo con 8 mandati |

---

## Modello di Dominio

### GenesisStatus (state machine)

```
draft → proposed → under_review → approved → packaging → ready_for_provisioning
                              ↘ rejected → archived
                                                     ↘ aborted → archived
                                          ↘ failed → archived
```

**V0 bloccati**: `ready_for_provisioning → provisioning` e `provisioning → activated` sono nel grafo ma protetti da:
1. `ActivationBlockedError` nella state machine
2. `GENESIS_ACTIVATE = forbidden` in `REPLICATION_GOVERNANCE`
3. `REPLICATION_ACTIVATION_ENABLED=False` (feature flag)

### GenesisReason

`validated_product` | `validated_product_family` | `strategic_spinout` | `customer_dedicated_instance` | `regional_specialization` | `regulatory_isolation` | `capacity_isolation` | `experimental_replica` | `custom`

---

## Genetic Package

Il `MercuryGeneticPackage` è il corredo che una futura Dedicated Mercury riceverà alla nascita.

**Contiene:**
- `ConstitutionalPackage`: versione + principi ereditati + policy aggiornamenti
- `KernelManifest`: moduli obbligatori + vincoli compatibilità
- `GovernancePackage`: organi + mandati + budget limits + prohibited_actions
- `CapabilityBundleManifest`: bundle portabili (opzionale)
- `KnowledgePackageManifest`: pacchetti conoscenza trasferibili (opzionale)
- `MissionGenealogy`: traccia genealogica della missione di origine
- `ProductEvidencePackage`: evidenze di validazione del prodotto
- `IntegrityManifest`: checksums SHA-256 per componente + checksum totale
- `FederationContract`: stub del contratto con il Main
- `ReportingContract`: metriche consentite al Main
- `EconomicEnvelope`: budget approvato

**Invarianti:**
- Immutabile dopo `seal_genetic_package()`
- Serializzabile in JSON deterministico (`sort_keys=True`)
- Checksum verificabile via `validate_package_integrity()`
- Non contiene path assoluti del filesystem locale
- Non contiene credenziali o segreti

---

## IndependenceEvaluator

Valuta se la Dedicated Mercury può operare senza dipendenza operativa ordinaria dal Main.

**Parametri chiave:**
- `required_local_*`: strutture che la replica DEVE avere in locale (audit, governance, budget, storage)
- `allowed_main_dependencies`: dipendenze leggere permesse dal Main (devono essere nella whitelist `ALLOWED_MAIN_DEPENDENCIES`)
- `prohibited_main_dependencies`: dipendenze operative proibite che la replica ANCORA HA → ogni occorrenza produce un **blocker**

**Dipendenze proibite (`PROHIBITED_MAIN_DEPENDENCIES`):**
```
marketing_execution, sales_execution, product_delivery,
customer_support, finance_operations, daily_operational_decisions,
primary_operational_memory, shared_customer_database, permanent_main_agents
```

**Dipendenze consentite (`ALLOWED_MAIN_DEPENDENCIES`):**
```
constitutional_update_proposals, security_advisories,
optional_capability_update_packages, portfolio_level_reporting,
emergency_escalation, initial_bootstrap_artifacts
```

---

## Replication Gate

Valutazione deterministica (nessun LLM) che combina:

1. **Evidence validation** — almeno 1 evidence_ref (CONST-001)
2. **Product validation score** — ≥ 0.5
3. **Independence evaluation** — IndependenceStatus ≠ INSUFFICIENT
4. **Product family coherence** — se multi-prodotto, nessun `SEPARATE_INSTANCES`
5. **Capability portability** — warning se bundle richiesti ma NullProvider in V0
6. **Constitutional validation** — shadow mode (non blocca)
7. **Authority authorization** — `GENESIS_APPROVE` escalation_required (shadow)
8. **Budget envelope** — ≥ 0

**Output:** `GateStatus.PASS` | `PASS_WITH_CONDITIONS` | `FAIL` | `REQUIRE_REVIEW`

---

## ProductFamilyAssessment

Algoritmo deterministico su 8 dimensioni (1 pt ciascuna):

| Dimensione | Peso |
|------------|------|
| shared_customer | 1 |
| shared_market | 1 |
| shared_problem_space | 1 |
| shared_capabilities | 1 |
| shared_distribution | 1 |
| shared_business_model | 1 |
| shared_data_boundary | 1 |
| shared_regulatory_boundary | 1 |

**Override deterministici:**
- Clienti diversi + mercati diversi → `SEPARATE_INSTANCES`
- Business model incompatibile → `SEPARATE_INSTANCES`
- Dati non condivisibili → `SEPARATE_INSTANCES`
- Vincoli normativi incompatibili → `SEPARATE_INSTANCES`

---

## Federation Contract V0

Il `MotherReplicaFederationContract` definisce il rapporto federato leggero tra Main e Dedicated Mercury.

**Principi federativi (invarianti):**
1. La replica non richiede autorizzazione del Main per operazioni ordinarie entro mandato
2. Il Main non accede automaticamente ai dati clienti
3. La replica restituisce solo dati consentiti (reporting_obligations)
4. Aggiornamenti di capability e Kernel sono proposte versionate
5. La replica può rifiutare aggiornamenti incompatibili
6. Emergenze e violazioni costituzionali possono generare escalation

**Non implementa** networking o sincronizzazione. È solo un contratto strutturato.

---

## REPLICATION_GOVERNANCE

Organo con 8 mandati V0:

| Decision Type | Authority Mode |
|---------------|----------------|
| `GENESIS_PROPOSE` | `proposal` |
| `GENESIS_APPROVE` | `escalation_required` |
| `PACKAGE_CREATE` | `proposal` |
| `READY_FOR_PROVISIONING` | `escalation_required` |
| `GENESIS_ABORT` | `proposal` |
| **`GENESIS_ACTIVATE`** | **`forbidden`** ← V0 invariante |
| `GENETIC_PACKAGE_UPDATE` | `escalation_required` |
| `INDEPENDENCE_CONTRACT_CHANGE` | `escalation_required` |

---

## Schema DB (6 tabelle)

| Tabella | Scopo |
|---------|-------|
| `dedicated_mercury_genesis_requests` | Richieste genesis (idempotency_key UNIQUE) |
| `dedicated_mercury_genesis_transitions` | Log transizioni immutabile |
| `mercury_genetic_packages` | Pacchetti genetici (status: draft→sealed) |
| `dedicated_mercury_independence_contracts` | Contratti autonomia |
| `product_family_assessments` | Valutazioni coerenza prodotti |
| `replication_gate_results` | Risultati replication gate |

---

## Doctor Status

`READY_REPLICATION_CONTRACT_SHADOW` — superset di `READY_MISSION_SHADOW`

Indica che:
- Autonomy Boundary Layer attivo (shadow mode)
- Mission Layer attivo (shadow mode)
- Replication Layer attivo (V0, activation forbidden)

**12 check specifici Replication:**
`replication_schema`, `replication_indexes`, `replication_governance_organ`, `replication_governance_mandates`, `replication_activation_forbidden`, `replication_state_machine`, `replication_v0_blocked`, `replication_feature_flags`, `replication_genesis_service`, `replication_federation_contract`, `replication_independence_evaluator`, `replication_genetic_package_builder`

---

## Invarianti V0 (non negoziabili)

1. Nessuna Dedicated Mercury creata fisicamente (nessun repository, container, runtime, DB separato)
2. `GENESIS_ACTIVATE = forbidden` — non modificabile senza revisione governance
3. `REPLICATION_ACTIVATION_ENABLED=False` e `REPLICATION_PROVISIONING_ENABLED=False` (default)
4. Transizioni `ready_for_provisioning → provisioning` e `provisioning → activated` bloccate da `ActivationBlockedError`
5. Ogni transizione passa per la state machine esplicita `ALLOWED_TRANSITIONS`
6. Il Genetic Package è immutabile dopo `seal_genetic_package()`
7. Nessuna Business Cell creata a runtime
8. Tutta la logica di gate è deterministica — nessun LLM
9. Constitutional e Autonomy restano in shadow mode (non enforce bloccante)
10. 341 test pre-esistenti non regrediscono

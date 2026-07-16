# MF-REPL-001 Implementation Report
## Dedicated Mercury Genesis Contract V0

**Data:** 2026-07-16
**Suite:** 410/410 (341 pre-esistenti + 69 nuovi)
**Doctor:** `READY_REPLICATION_CONTRACT_SHADOW`

---

## Riepilogo

Implementato il contratto di genesis e il meccanismo di distacco di una Dedicated Mercury. In V0 è un sistema puramente contrattuale/dati: nessuna replica viene creata fisicamente. L'activation è `forbidden` per mandato di governance.

---

## Checkpoint completati

### A — Audit baseline e file plan ✓
- Baseline confermato: 341/341, commit `5feb1d3`, Doctor `READY_MISSION_SHADOW`
- File plan scritto in `.local/tasks/mf_repl_001_plan.md`

### B — Domain Model + Schema + Lifecycle ✓

**Nuovi file:**
- `mercury_foundry/replication/__init__.py`
- `mercury_foundry/replication/models.py` — 7 enum, 15+ dataclass, 5 exception types
- `mercury_foundry/replication/lifecycle.py` — ALLOWED_TRANSITIONS, apply_genesis_transition, list_genesis_transitions
- `mercury_foundry/replication/seed.py` — seed_replication_governance() idempotente
- `mercury_foundry/replication/events.py` — emit_replication_event()
- `mercury_foundry/replication/genetic_package.py` — build_genetic_package(), compute_checksum SHA-256, validate_package_integrity()
- `mercury_foundry/replication/registry.py` — CRUD per 6 tabelle
- `mercury_foundry/replication/independence.py` — IndependenceEvaluator deterministico
- `mercury_foundry/replication/family_assessment.py` — assess_product_family() deterministico

**Schema:** +6 tabelle in `schema.sql` (IF NOT EXISTS)

### C — Gate + GenesisService + Federation ✓

**Nuovi file:**
- `mercury_foundry/replication/gate.py` — ReplicationGate con 8 check deterministici
- `mercury_foundry/replication/genesis_service.py` — GenesisService, 10-step propose flow
- `mercury_foundry/replication/federation.py` — MotherReplicaFederationContract

### D — Infrastruttura + Diagnostics + Test ✓

**File modificati:**
- `mercury_foundry/config.py` — REPLICATION_ACTIVATION_ENABLED=False, REPLICATION_PROVISIONING_ENABLED=False
- `mercury_foundry/state/db.py` — _migrate_replication_indexes() + seed_replication_governance()
- `mercury_foundry/diagnostics.py` — _check_replication_layer() (12 check), READY_REPLICATION_CONTRACT_SHADOW, _compute_overall_status() aggiornato
- `tests/test_doctor.py` — Aggiornato a READY_REPLICATION_CONTRACT_SHADOW
- `tests/test_arch_008_autonomy.py` — Aggiornato a READY_REPLICATION_CONTRACT_SHADOW
- `tests/test_mission_001_mf.py` — Aggiornato a READY_REPLICATION_CONTRACT_SHADOW

**Nuovo test file:**
- `tests/test_repl_001_mf.py` — 69 test (65 spec + 4 bonus)

---

## Statistiche

| Metrica | Valore |
|---------|--------|
| File nuovi produzione | 10 |
| File modificati | 8 |
| Nuove righe di codice (stima) | ~2.800 |
| Tabelle DB aggiunte | 6 |
| Indici DB aggiunti | 7 |
| Enum nuovi | 7 |
| Dataclass nuovi | 16 |
| Exception types nuovi | 5 |
| Mandati REPLICATION_GOVERNANCE | 8 |
| Test nuovi | 69 |
| Test totali | 410 |
| Doctor status | READY_REPLICATION_CONTRACT_SHADOW |

---

## Fix intermedi durante l'implementazione

1. **`independence.py` semantica `prohibited_main_dependencies`** — il parametro rappresenta le dipendenze operative proibite che la replica ANCORA HA (non la lista delle cose proibite in generale). Default cambiato da `PROHIBITED_MAIN_DEPENDENCIES` a `[]`.

2. **`independence.py` fallback `or` vs `is not None`** — l'uso di `or` non distingueva `None` da `[]` esplicita. Corretto usando confronti `is not None` per tutti i parametri opzionali.

3. **`federation.py` e `models.py` check `data_isolation_policy`** — il check `"automatic_customer_data_access" in policy` faceva match anche sul suffisso negante "no_automatic_customer_data_access". Sostituito con check su pattern positivi espliciti.

---

## Invarianti V0 verificati

- [x] Nessuna Dedicated Mercury creata fisicamente
- [x] `GENESIS_ACTIVATE = forbidden` nel DB
- [x] `REPLICATION_ACTIVATION_ENABLED=False` (default env)
- [x] `ActivationBlockedError` su `ready_for_provisioning → provisioning`
- [x] `ActivationBlockedError` su `provisioning → activated`
- [x] Ogni transizione passa per state machine esplicita
- [x] Genetic Package immutabile dopo seal
- [x] Nessuna Business Cell creata a runtime
- [x] Gate deterministico (nessun LLM)
- [x] Constitutional + Autonomy in shadow mode
- [x] 341 test pre-esistenti non regrediscono

---

## Prossimi passi (fuori scope V0)

- **MF-GENESIS-001**: Provisioning runtime (quando REPLICATION_ACTIVATION_ENABLED=True sarà approvato)
- **MF-FED-001**: Federation protocol layer (networking opzionale tra Main e Dedicated)
- **MF-CAP-001**: Capability portability engine (verifica portabilità reale dei bundle)

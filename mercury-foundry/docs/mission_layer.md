# Mission Layer — MF-MISSION-001

## Riepilogo

Il Mission Layer definisce il primitivo `Mission`: un mandato strutturato orientato a un outcome economico o operativo. Non è un task, un goal, un esperimento o una Business Cell. È l'unità di intento del sistema Mercury Foundry che può, una volta completata, dar vita a una Business Cell (in un milestone successivo, MF-EXP-001).

---

## Architettura

```
mercury_foundry/
└── mission/
    ├── __init__.py              # package stub
    ├── models.py                # enumerazioni + dataclass (DTO puri)
    ├── lifecycle.py             # state machine + apply_transition()
    ├── registry.py              # CRUD SQLite su tabella missions
    ├── intake.py                # MissionIntakeService (10 step deterministici)
    ├── seed.py                  # seed_mission_control() (idempotente)
    ├── capability_contracts.py  # Protocol + NullProvider × 4
    ├── expedition.py            # ExpeditionRequest + assess_expedition_readiness()
    └── events.py                # emit_mission_event() (wrapper su audit_log)
```

---

## State Machine

```
draft → submitted → under_review → accepted → ready → active
                                 ↘ rejected         ↘ paused → active
                                                    ↘ blocked → active
                                                    ↘ completed → archived
                                                               ↘ promoted_to_business_cell → archived
                                                    ↘ failed → archived
                                                    ↘ terminated → archived
```

Regole:
- Ogni cambiamento di stato passa per `lifecycle.apply_transition()`. Nessuna mutazione diretta di `status`.
- Optimistic locking: ogni `UPDATE` include `WHERE version = ?` e incrementa `version`.
- Record di transizione immutabili nella tabella `mission_transitions`.
- Stati terminali: `rejected`, `archived`. Nessuna transizione uscente.

---

## MISSION_CONTROL — Organo e mandati

| Decision Type | Authority Mode |
|---|---|
| MISSION_CREATE | proposal |
| MISSION_SUBMIT | proposal |
| MISSION_ACCEPT | escalation_required |
| MISSION_ACTIVATE | escalation_required |
| MISSION_PAUSE | proposal |
| MISSION_TERMINATE | escalation_required |
| MISSION_COMPLETE | proposal |
| MISSION_PROMOTE_TO_BUSINESS_CELL | **forbidden** |
| MISSION_AUTHORITY_CHANGE | **forbidden** |

La promozione a Business Cell è **forbidden** in V0: nessun agente può eseguirla autonomamente. Produce solo un `get_promotion_proposal_event()` (intent record) nell'audit log.

---

## MissionIntakeService — 10 step

1. Idempotency check (replay sicuro per stessa chiave)
2. Validazione schema (campi obbligatori, enum, budget, deadline)
3. Rilevamento duplicati (titolo + origin_type + objective in stato attivo)
4. (Implicita) Origine autorizzata — validata al passo 2
5. Budget e risk profile validi
6. Criteri minimi di successo (≥1 required per tipi non-custom)
7. Capability gap detection (NullProvider di default)
8. Validazione costituzionale (shadow mode, non bloccante)
9. Autorizzazione Autonomy Boundary (MISSION_CREATE, shadow mode)
10. Creazione Mission nel registry + audit event

Il servizio **non solleva mai eccezioni per errori di validazione**: li raccoglie nel `MissionIntakeResult`. Solleva solo per errori di sistema (DB, ecc.).

---

## Capability Contracts

Quattro Protocol (strutturali, duck typing):

| Protocol | Null Implementation |
|---|---|
| `CapabilityProvider` | `NullCapabilityProvider` |
| `KnowledgeProvider` | `NullKnowledgeProvider` |
| `DiscoveryProvider` | `NullDiscoveryProvider` |
| `DeliveryProvider` | `NullDeliveryProvider` |

I Null provider restituiscono `ProviderStatus.NOT_IMPLEMENTED`. Non bloccano la Mission se la capability non è obbligatoria. Sono sostituibili via dependency injection nella costruzione di `MissionIntakeService`.

---

## Expedition Contract (V0)

`assess_expedition_readiness()` valuta la readiness di una Mission per avviare una Expedition **senza crearne una reale**. Produce un `ExpeditionReadinessResult` con:
- `ready: bool`
- `missing_capabilities: list[str]`
- `blockers: list[str]`
- `warnings: list[str]`

L'evento `expedition.requested` o `expedition.not_ready` viene scritto nell'audit log tramite `emit_expedition_event()`. È un intent record che documenta la proposta senza eseguirla.

---

## Schema DB (MF-MISSION-001)

**Tabella `missions`:**
- `id INTEGER PRIMARY KEY AUTOINCREMENT` — audit_log compat
- `mission_id TEXT UNIQUE` — UUID per riferimenti esterni
- `idempotency_key TEXT UNIQUE` — prevenzione duplicati
- Tutti i campi complessi come `*_json TEXT` (pattern del repo)
- `version INTEGER` — optimistic locking
- Budget come `REAL` — coerente con `max_budget REAL` in `decision_mandates`

**Tabella `mission_transitions`:**
- Log immutabile di ogni transizione
- `transition_id TEXT UNIQUE`, `from_status`, `to_status`, `requested_at`

**Indici aggiuntivi** (creati da `_migrate_mission_indexes`):
- `idx_missions_status`
- `idx_missions_origin_type`
- `idx_missions_business_scope`
- `idx_missions_correlation_id`
- `idx_mission_transitions_mission_id`

---

## Debito tecnico documentato

| Elemento | Nota |
|---|---|
| Budget come REAL | Consistente con `max_budget REAL` esistente. Migrare a INTEGER (minor units) se il dominio economico richiede precisione decimale garantita. |
| Constitutional check in intake | In V0 chiama `maybe_validate_constitution()` con parametri derivati dalla request. In futuro usare un'integrazione più profonda con il Constitutional Validator. |
| NullProvider non notificano | `report_capability_gap()` è no-op. In futuro iniettare un sistema di notifica. |

---

## Doctor

`run_doctor()` ora produce `READY_MISSION_SHADOW` (superset di `READY_SHADOW`) quando:
- Autonomy Boundary Layer inizializzato ✅
- Tabelle `missions` e `mission_transitions` presenti ✅
- 5 indici Mission presenti ✅
- `MISSION_CONTROL` organ e 9 mandati presenti ✅
- State machine valida (nessuna transizione da stati terminali) ✅
- Null provider caricabili ✅
- Expedition contract importabile ✅
- `MISSION_PROMOTE_TO_BUSINESS_CELL` = `forbidden` ✅

---

## Invarianti

- 287 test pre-esistenti non regrediscono.
- Nessuna Expedition creata in MF-MISSION-001.
- Nessuna Business Cell creata in MF-MISSION-001.
- Nessun blocco operativo in shadow mode.
- Ogni cambiamento di stato passa per la state machine.
- Idempotency garantita via `idempotency_key UNIQUE`.
- `_FailingConnProxy` in `test_candidate_integrity_and_coherent_approval.py` non impattato.

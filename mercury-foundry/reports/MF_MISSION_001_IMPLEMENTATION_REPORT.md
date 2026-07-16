# Implementation Report — MF-MISSION-001: Mission Intake & Expedition Contract V0

**Data:** 2026-07-16
**Status:** COMPLETATO
**Suite:** 341/341 (baseline 287 + 54 nuovi)
**Doctor:** READY_MISSION_SHADOW

---

## Riepilogo

MF-MISSION-001 introduce il Mission Layer: il primitivo `Mission` come mandato strutturato orientato a un outcome economico o operativo. Il layer è completo di stato persistente, state machine, intake deterministico, capability contracts (null provider), expedition contract (intent only), wiring con Constitutional Core e Autonomy Boundary, estensione Doctor.

---

## File prodotti

| File | Tipo | Descrizione |
|------|------|-------------|
| `mercury_foundry/mission/__init__.py` | Nuovo | Package stub |
| `mercury_foundry/mission/models.py` | Nuovo | 7 enum, 11 dataclass (modelli DTO puri) |
| `mercury_foundry/mission/lifecycle.py` | Nuovo | State machine + `apply_transition()` + `list_transitions()` |
| `mercury_foundry/mission/registry.py` | Nuovo | CRUD SQLite (create, get, list, update_metadata, archive) |
| `mercury_foundry/mission/intake.py` | Nuovo | `MissionIntakeService` — 10 step deterministici |
| `mercury_foundry/mission/seed.py` | Nuovo | `seed_mission_control()` — idempotente, 9 mandati |
| `mercury_foundry/mission/capability_contracts.py` | Nuovo | 4 Protocol + 4 NullProvider |
| `mercury_foundry/mission/expedition.py` | Nuovo | `ExpeditionRequest`, `ExpeditionReadinessResult`, `assess_expedition_readiness()` |
| `mercury_foundry/mission/events.py` | Nuovo | `emit_mission_event()` — wrapper su audit_log |
| `mercury_foundry/state/schema.sql` | Modificato | +2 tabelle (missions, mission_transitions) |
| `mercury_foundry/state/db.py` | Modificato | `_migrate_mission_indexes()` + call a `seed_mission_control()` |
| `mercury_foundry/diagnostics.py` | Modificato | `_check_mission_layer()` + `READY_MISSION_SHADOW` |
| `tests/test_mission_001_mf.py` | Nuovo | 54 test (spec 1-48 + extra) |
| `tests/test_doctor.py` | Modificato | Aggiornato a READY_MISSION_SHADOW |
| `tests/test_arch_008_autonomy.py` | Modificato | Aggiornato a READY_MISSION_SHADOW |
| `docs/mission_layer.md` | Nuovo | Architettura e contratti |

---

## Decisioni architetturali

| Decisione | Motivazione |
|-----------|-------------|
| Budget come REAL | Coerenza con `max_budget REAL` in `decision_mandates`. |
| `mission_id TEXT UUID` + `id INTEGER` | Compatibilità audit_log (entity_id INTEGER) + riferimenti esterni UUID. |
| Campi complessi come `*_json TEXT` | Pattern esistente del repo (literal_constraints_json, ecc.). |
| Optimistic locking via `version` | Prevenzione conflitti concorrenti senza lock SQL espliciti. |
| `idempotency_key UNIQUE` | Prevenzione duplicati su retry. |
| ALLOWED_TRANSITIONS dict esplicito | State machine deterministica, testabile, non dipende da enum order. |
| NullProvider di default | Fail-open per capability: non bloccano intake se capability non obbligatoria. |
| `MISSION_PROMOTE_TO_BUSINESS_CELL` = forbidden | V0: nessun agente crea Business Cell autonomamente. |
| Shadow mode per Constitutional + Autonomy | Wiring completo ma non bloccante in V0. |
| Expedition come intent only | Nessun runtime avviato: solo valutazione strutturata. |
| `_check_mission_layer()` in Doctor | Visibilità diretta dello stato del layer nelle diagnostiche. |
| `READY_MISSION_SHADOW` superset di `READY_SHADOW` | Segnala chiaramente che il Mission Layer è attivo. |

---

## Test prodotti (54)

| Range | Area |
|-------|------|
| 1-8 | Domain: costruzione modelli, validazione budget/deadline, enum roundtrip, optimistic locking |
| 9-17 | Intake: founder, autonomous_discovery, idempotency replay, duplicate detection, mandatory cap gap, optional cap gap warning, constitutional/authority result linkage |
| 18-25 | Lifecycle: transizioni valide/invalide, version conflict, path completo, pausa/ripresa, terminazione, archiviazione, promozione (intent only) |
| 26-30 | Constitution: shadow audit event, budget+evidence warning, rollback_plan mancante, constitutional indisponibile non blocca, no blocchi operativi |
| 31-35 | Autonomy: seed mandati, forbidden action rejected, escalation_required, proposal, budget boundary |
| 36-40 | Expedition contract: full readiness, capability gap, authority warning, audit event, no table expeditions |
| 41-44 | Audit: un solo event per intake, correlation_id preservato, transition records immutabili, no DELETE |
| 45-48 | Regression: wiring esistente, doctor READY_MISSION_SHADOW, shadow non blocca, FOUNDRY_GOVERNANCE invariato |
| extra | Null providers, listing registry, can_transition helper, expedition not ready su draft |

---

## Invarianti rispettati

- ✅ 287 test pre-esistenti non regrediscono
- ✅ Nessuna Expedition creata realmente
- ✅ Nessuna Business Cell creata realmente
- ✅ Nessun blocco operativo in shadow mode
- ✅ Ogni cambio stato passa per state machine
- ✅ `_FailingConnProxy` non impattato (gate.py non chiama `authorize_organ_decision`)
- ✅ Idempotency garantita via `idempotency_key UNIQUE`

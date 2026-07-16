# MF-CONST-001 — Constitutional Core Foundation
## Implementation Report

**Data:** 2026-07-16  
**Versione costituzione:** 1.0.0  
**Suite al completamento:** 287/287 ✅  
**Doctor status:** `READY_SHADOW`

---

## Obiettivo

Creare la fondazione del Constitutional Core di Mercury Foundry: un file di
principi versionato (JSON), un registry con verifica di integrità, evaluator
deterministici per principio, un validator aggregatore, e l'integrazione
nel flusso `authorize_organ_decision` tramite shadow mode.

---

## File creati

| File | Dimensione | Scopo |
|------|-----------|-------|
| `mercury_foundry/constitutional/__init__.py` | — | Package docstring |
| `mercury_foundry/constitutional/models.py` | 7 enumerazioni + 6 dataclass | Tipi condivisi (PrincipleLevel, PrincipleStatus, PrincipleEnforcement, ValidationStatus, EnforcementAction, ConstitutionVersion, ConstitutionalPrinciple, ConstitutionalValidationRequest, ConstitutionalValidationResult, PrincipleEvaluationDetail, ConstitutionalViolationError, ConstitutionLoadError) |
| `mercury_foundry/constitutional/constitution_v1.json` | 101 righe | Costituzione V1 con 7 principi CONST-001..007; `"checksum": "auto"` |
| `mercury_foundry/constitutional/evaluators/__init__.py` | — | Package docstring |
| `mercury_foundry/constitutional/evaluators/base.py` | ABC | `PrincipleEvaluator` con `principle_id` + `evaluate()` |
| `mercury_foundry/constitutional/evaluators/evidence.py` | — | CONST-001: budget_impact > 0 richiede evidence_refs non vuoto |
| `mercury_foundry/constitutional/evaluators/auditability.py` | — | CONST-002: decision_id obbligatorio e non vuoto |
| `mercury_foundry/constitutional/evaluators/authority_boundary.py` | — | CONST-003: authority_mode riconosciuto + organ_id presente |
| `mercury_foundry/constitutional/evaluators/reversibility.py` | — | CONST-004: warning su autonomous+high-risk senza rollback_plan |
| `mercury_foundry/constitutional/evaluators/constitutional_change.py` | — | CONST-007: action_type∋"constitutional" + authority_mode="autonomous" → violazione |
| `mercury_foundry/constitutional/registry.py` | 280 righe | `ConstitutionRegistry`: load, schema check, checksum SHA-256, singleton `get_default_registry()` |
| `mercury_foundry/constitutional/validator.py` | 200 righe | `ConstitutionalValidator`: orchestrazione evaluator, aggregazione, enforcement_action |
| `mercury_foundry/constitutional/shadow.py` | 265 righe | `maybe_validate_constitution()`: disabled/shadow/enforce; audit `constitution.*` |
| `tests/test_const_001_constitutional_core.py` | 36 test | Copertura completa dei casi spec |
| `docs/constitutional_core.md` | — | Documentazione: modalità, come aggiungere principi/evaluator, flusso integrazione |

## File modificati

| File | Modifica |
|------|----------|
| `mercury_foundry/config.py` | +`CONSTITUTIONAL_CORE_MODE` (env `MERCURY_CONSTITUTIONAL_CORE_MODE`, default `shadow`) + `CONSTITUTION_PATH` |
| `mercury_foundry/autonomy/authorization.py` | +hook `maybe_validate_constitution()` al passo 6 (dopo mandate/authority checks, prima di `_apply_authority_mode`) |

---

## Architettura

### Flusso di validazione

```
authorize_organ_decision(conn, organ_key, decision_type, ...)
  │
  ├─ [1..5] Organ / Mandate / Evidence / Risk / Budget checks (pre-esistenti)
  │
  ├─ [6] maybe_validate_constitution(conn, ...)         ← NUOVO
  │         │
  │         ├─ disabled  → None (no-op)
  │         ├─ shadow    → validate + audit, never raises
  │         └─ enforce   → validate + audit; raises ConstitutionalViolationError se DENY
  │
  └─ [7] _apply_authority_mode(...)                     (era [6])
```

### Separazione degli audit event

| Layer | Action prefix | Prodotto da |
|-------|--------------|-------------|
| Autonomy Boundary | `AUTONOMY_DECISION_*` | `authorization.py` / `shadow.py` autonomy |
| Constitutional Core | `constitution.*` | `constitutional/shadow.py` |

Nessuna duplicazione: ogni chiamata a `maybe_validate_constitution` produce
esattamente 1 evento `constitution.validation.completed` (+ eventualmente
1 evento `constitution.violation.detected` separato).

---

## Principi costituzionali (V0)

| ID | Titolo | Livello | Stato | Enforcement | Evaluator |
|----|--------|---------|-------|-------------|-----------|
| CONST-001 | Evidence Before Investment | constitutional | active | audit_only | ✅ `evidence.py` |
| CONST-002 | Auditability | constitutional | active | audit_only | ✅ `auditability.py` |
| CONST-003 | Bounded Autonomy | constitutional | active | audit_only | ✅ `authority_boundary.py` |
| CONST-004 | Reversible First | operational | active | advisory | ✅ `reversibility.py` |
| CONST-005 | Business Cell as Economic Unit | constitutional | shadow | advisory | — (informativo in V0) |
| CONST-006 | Knowledge Classification | operational | shadow | advisory | — (informativo in V0) |
| CONST-007 | Human Approval for Constitutional Change | immutable | active | audit_only | ✅ `constitutional_change.py` |

Nessun principio `blocking` in V0: `enforce` mode è predisposto ma
equivale funzionalmente a `shadow` nell'attuale costituzione.

---

## Copertura test (36 test)

| # | Nome test | Caso spec |
|---|-----------|-----------|
| 1 | `test_valid_constitution_loads_without_error` | Caricamento valido |
| 2 | `test_valid_constitution_default_path_loads` | Path di default |
| 3 | `test_constitution_loaded_event_on_audit` | Evento audit constitution.loaded |
| 4 | `test_invalid_json_raises_constitution_load_error` | JSON non valido |
| 5 | `test_missing_required_field_raises_constitution_load_error` | Campo obbligatorio mancante |
| 6 | `test_invalid_enum_value_raises_constitution_load_error` | Enum non riconosciuto |
| 7 | `test_missing_file_raises_constitution_load_error` | File inesistente |
| 8 | `test_duplicate_principle_id_raises_load_error` | principle_id duplicati |
| 9 | `test_shadow_mode_does_not_raise_on_corrupt_constitution` | Shadow + corruzione → None |
| 10 | `test_get_principle_by_id` | Ricerca per ID |
| 11 | `test_get_principle_returns_none_for_unknown_id` | Ricerca miss |
| 12 | `test_filter_principles_by_level` | Filtro per livello |
| 13 | `test_list_active_principles_excludes_inactive` | Esclusione deprecated |
| 14 | `test_decision_with_evidence_passes_const001` | Evidenza sufficiente |
| 15 | `test_decision_without_budget_impact_passes_const001` | budget_impact=None non valutato |
| 16 | `test_decision_without_evidence_violates_const001` | Evidenza assente con budget |
| 17 | `test_unknown_authority_mode_violates_const003` | authority_mode sconosciuto |
| 18 | `test_empty_organ_id_violates_const003` | organ_id vuoto |
| 19 | `test_autonomous_constitutional_action_violates_const007` | Modifica costituzionale autonoma |
| 20 | `test_non_autonomous_constitutional_action_passes_const007` | Modifica con escalation |
| 21 | `test_non_constitutional_action_is_not_evaluated_by_const007` | CONST-007 non applicabile |
| 22 | `test_disabled_mode_is_noop` | Disabled mode |
| 23 | `test_shadow_mode_produces_audit_event` | Shadow → audit |
| 24 | `test_shadow_mode_passes_for_clean_request` | Shadow + richiesta pulita |
| 25 | `test_shadow_mode_never_blocks_existing_flow` | No blocco flusso orchestrator |
| 26 | `test_shadow_mode_with_violation_does_not_block` | Violazione in shadow → no raise |
| 27 | `test_validation_result_serializes_to_dict` | Serializzazione + round-trip |
| 28 | `test_validation_result_has_all_required_fields` | Campi obbligatori nel result |
| 29 | `test_audit_event_generated_exactly_once` | Esattamente 1 evento per chiamata |
| 30 | `test_existing_suite_unaffected_by_constitutional_core` | Compatibilità test esistenti |
| 31 | `test_const004_warning_on_high_risk_autonomous_without_rollback` | CONST-004 warning |
| 32 | `test_const004_no_warning_with_rollback_plan` | CONST-004 OK con rollback_plan |
| 33 | `test_const002_fails_on_empty_decision_id` | CONST-002 violazione |
| 34 | `test_const002_passes_on_valid_decision_id` | CONST-002 conforme |
| 35 | `test_constitutional_validation_integrated_in_authorization` | Hook in authorize_organ_decision |
| 36 | `test_constitutional_validation_disabled_mode_no_audit_in_authorization` | Disabled → nessun audit |

---

## Risultati suite

```
287 passed, 3 warnings (PytestCollectionWarning pre-esistenti)
```

- 251 test pre-esistenti (MF-ARCH-001..008): tutti ✅
- 36 nuovi test MF-CONST-001: tutti ✅

**Doctor:** `READY_SHADOW` (invariato rispetto a MF-ARCH-008)

---

## Invarianti verificate

### Shadow mode non modifica il comportamento esistente
- `test_shadow_mode_never_blocks_existing_flow`: ciclo completo submit→run→approval
  con CONSTITUTIONAL_CORE_MODE=shadow produce lo stesso risultato di prima.
- `test_existing_suite_unaffected_by_constitutional_core`: i 251 test pre-MF-CONST-001
  passano invariati.
- Il hook in `authorization.py` è posizionato **dopo** tutti i controlli di mandato
  e **prima** di `_apply_authority_mode`: non modifica nessun path di ritorno esistente.
- Il `_FailingConnProxy` usato in `test_candidate_integrity_and_coherent_approval.py`
  non è impattato: gate.py non chiama `authorize_organ_decision`.

### Fail-closed sul registry
- File mancante → `ConstitutionLoadError`
- JSON non valido → `ConstitutionLoadError`
- Campo obbligatorio assente → `ConstitutionLoadError`
- Enum non riconosciuto → `ConstitutionLoadError`
- principle_id duplicati → `ConstitutionLoadError`
- In shadow mode: tutte queste eccezioni sono intercettate → audit
  `constitution.configuration.invalid` → ritorno None → flusso operativo non interrotto.

---

## Debito tecnico residuo

| Item | Priorità | Note |
|------|----------|------|
| Evaluator per CONST-005 (Business Cell) | Bassa | Principio informativo in V0; nessun criterio deterministico disponibile |
| Evaluator per CONST-006 (Knowledge Classification) | Bassa | Stesso motivo |
| Promozione CONST-004 da `advisory` ad `audit_only` | Media | Dipende dall'adozione del campo `rollback_plan` nel metadata |
| Attivazione `enforce` mode con almeno 1 principio `blocking` | Alta | Da pianificare dopo 1 ciclo completo di osservazione in shadow |
| Backup automatico di `constitution_v1.json` su modifica | Media | Da wiring a human_gate level |
| Checksum reale nel file (vs `"auto"`) | Bassa | Generare con `registry._compute_checksum()` prima del rilascio V2 |

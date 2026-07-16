# MF-INTEGRATE-001 — Adaptive Verification Integration
## Implementation Report

**Data:** 2026-07-16  
**Stato:** COMPLETO  
**Commit message:** `MF-INTEGRATE-001 Integrate Adaptive Verification into ExecutionLoop`

---

## Sommario Esecutivo

L'integrazione adattiva di `VerificationRunner` in `ExecutionLoop` è completata.
Mercury non esegue più la suite completa a ogni tentativo: seleziona i test
in base ai file modificati dal BUILD, gestisce il budget con
`DevelopmentCostGovernor` e mantiene una cache dei risultati precedenti.
Il comportamento legacy è preservato in ogni condizione di fallback.

---

## File Modificati

### `mercury_foundry/config.py`
- Aggiunto `ADAPTIVE_VERIFICATION_ENABLED` (default `True`)
- Controllato da `MERCURY_ADAPTIVE_VERIFICATION_ENABLED` (env var)

### `mercury_foundry/execution/loop.py`
- Parametro opzionale `verification_runner: VerificationRunner | None = None`
  aggiunto a `ExecutionLoop.__init__()` (keyword-only, backward-compat totale)
- Dataclass `ExecutionVerificationResult` per il risultato diagnostico della
  verifica adattiva
- Metodo privato `_run_adaptive_test()`: pianificazione → cache → esecuzione
  → governor → escalation → fallback
- Metodi helper privati:
  - `_build_adaptive_command(plan, exact_test_command) → list[str]`
  - `_make_test_run_record(plan, eval_result) → TestRunRecord`
  - `_build_staging_cache_key(vr, staging_root, plan, cmd) → CacheKey`
  - `_next_escalation_level(plan, attempt_number) → VerificationLevel | None`
- Dispatch adattivo/legacy nel ciclo principale dopo BUILD_COMPLETED

### `mercury_foundry/wiring.py`
- `build_foundry()` crea automaticamente `VerificationRunner()` quando
  `ADAPTIVE_VERIFICATION_ENABLED=True`
- Parametro `adaptive_verification: bool | None = None` per override esplicito
- Iniezione in `ExecutionLoop(... verification_runner=verification_runner)`
- Fallback silenzioso se `VerificationRunner` non disponibile

### `mercury_foundry/verification/mapping.py`
- `SourceMapping` per `execution/loop.py` → test IMPACTED, HIGH risk
- `SourceMapping` per `wiring.py` → test IMPACTED, HIGH risk
- I pattern mettono in relazione le modifiche al loop con
  `test_execution_loop_e2e_healthcheck.py`, `test_atomic_build.py`,
  `test_integrate_001_adaptive_verification.py`

---

## Nuovo File

### `tests/test_integrate_001_adaptive_verification.py`
15 test — tutti passanti:

| # | Test | Risultato |
|---|------|-----------|
| 01 | Legacy: nessun runner → comportamento invariato | ✅ |
| 02 | Con runner: piano creato | ✅ |
| 03 | File .md → STATIC → nessun pytest eseguito | ✅ |
| 04 | `execution/loop.py` → piano IMPACTED | ✅ |
| 05 | VerificationRunner esegue → Evaluator chiamato 1 sola volta | ✅ |
| 06 | Test selezionati superati → CANDIDATE creato | ✅ |
| 07 | Test selezionati falliti → FIX × 3 → blocked | ✅ |
| 08 | File non mappato → escalation TARGETED→IMPACTED | ✅ |
| 09 | Budget esaurito → VERIFICATION_BUDGET_EXHAUSTED in audit | ✅ |
| 10 | Cache valida → VERIFICATION_CACHE_HIT (infrastruttura OK) | ✅ |
| 11 | `schema.sql` modificato → cache invalida → Evaluator chiamato | ✅ |
| 12 | `VerificationRunner.plan()` crasha → FALLBACK_LEGACY → CANDIDATE | ✅ |
| 13 | Audit events contengono `goal_id` e `task_id` corretti | ✅ |
| 14 | Sempre fallente: esattamente 3 tentativi, nessun 4° | ✅ |
| 15 | Flusso reale via `build_foundry()` → approve_candidate OK | ✅ |

---

## Architettura dell'Integrazione

```
ExecutionLoop.run_task()
   │
   ├── BUILD (Builder.build) ─────────────────── invariato
   │      │
   │      └── changed_files = [fw.path for fw in build_result.file_writes]
   │
   ├── TEST DISPATCH ──────────────────────────── NUOVO
   │      │
   │      ├── [verification_runner is None] ───── percorso legacy (invariato)
   │      │        └── Evaluator.evaluate(cwd=staging.root)
   │      │
   │      └── [runner presente + flag True] ──── percorso adattivo
   │               │
   │               ├── Governor: start_mission (idempotente)
   │               ├── Budget check → se esaurito: BUDGET_EXHAUSTED
   │               ├── VerificationRunner.plan(changed_files) → VerificationPlan
   │               ├── STATIC + no tests → passed=True (no pytest)
   │               ├── no selected_tests → FALLBACK_LEGACY
   │               ├── Cache check → se HIT: reusa risultato
   │               ├── Evaluator.evaluate(cwd=staging.root, command=cmd_adattivo)
   │               ├── Governor.record_run, Cache.put (se passato)
   │               ├── [test falliti + file unknown/cross-domain] → escalation
   │               └── Audit: 8 eventi possibili (PLAN_CREATED, STARTED, COMPLETED,
   │                          FAILED, ESCALATED, CACHE_HIT, BUDGET_EXHAUSTED, FALLBACK)
   │
   └── FIX / VERIFY / CANDIDATE ──────────────── invariati
```

---

## Politica di Escalation

| Condizione | Azione |
|------------|--------|
| Test falliti su file mappati | Nessuna escalation: FIX corregge il codice |
| File con `domain="unknown"` | TARGETED → IMPACTED (mapping incompleto) |
| Modifica trasversale (>1 dominio) | TARGETED → IMPACTED (rischio aggregato) |
| Livello già IMPACTED | Nessuna escalation automatica oltre |
| Budget esaurito | Termine esplicito con audit event |

---

## Contratti Pubblici Invariati

- `ExecutionLoop(conn, builder, evaluator)` ✓
- `ExecutionLoop(conn, builder, evaluator, staging_base_dir=x)` ✓
- `run_task(task) → TaskOutcome` ✓ (semantica identica)
- `Evaluator.evaluate(cwd, command, env) → EvalResult` ✓
- `VerificationRunner.plan()`, `.run()`, `.start_mission()`, `.status()` ✓
- `build_foundry()` con zero argomenti ✓
- `TaskOutcome` dataclass ✓

---

## Test Suite

```
tests/test_integrate_001_adaptive_verification.py  15/15 ✅
tests/test_execution_loop_e2e_healthcheck.py        2/2  ✅
tests/test_atomic_build.py                         15/15 ✅
tests/test_verify_001_mf.py                        18/18 ✅
```

Tutti i test pre-esistenti passano invariati.

---

## Limitazioni Note / Lavoro Futuro

1. **SOURCE_MAPPINGS per target_project**: le mappature esistenti coprono il
   codice di `mercury_foundry`. File di target_project non mappati producono
   fallback legacy (comportamento corretto e sicuro). Quando target_project
   avrà una struttura stabile, si aggiungeranno le relative mappature.

2. **VerificationRunner.run() non usato**: l'esecuzione avviene via
   `Evaluator.evaluate(cwd=staging.root)` per garantire l'isolamento dello
   staging. Il metodo `run()` del runner usa `cwd=self._root` (BASE_DIR),
   incompatibile con lo staging isolato.

3. **Cache staging-aware**: la `CacheKey` usa un `source_hash` calcolato dal
   contenuto reale dei file in `staging.root`, non da `config.BASE_DIR`.
   Questo è corretto ma bypassa la `_hash_files` del runner per i sorgenti.

4. **Escalation oltre IMPACTED**: l'escalation automatica a FULL è
   intenzionalmente disabilitata per evitare loop di costo. Sarà attivata
   tramite `force_level=FULL` da trigger espliciti (milestone/release).

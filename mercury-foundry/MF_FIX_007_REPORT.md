# MF-FIX-007 — Audit Log Append-Only Triggers & Goal/Candidate Invariant

**Data:** 2026-07-16  
**Operazione:** MF-FIX-007  
**Stato finale:** COMPLETO — 221/221 test, DB produzione corretto

---

## Problema risolto

### Root Cause 1 — Audit log mutabile via SQL diretto

`audit_log` non aveva trigger SQLite. Qualsiasi processo con accesso al file del DB poteva eseguire `UPDATE` o `DELETE` direttamente, aggirando la convenzione append-only applicativa.

**Impatto:** rischio di manomissione della catena di audit — evidenza di produzione poteva essere alterata senza traccia.

### Root Cause 2 — Invariante goal/candidate inconsistente

`revoke_approval_incident` in `gate.py` (linee 381–499) aggiornava la candidate a `approval_revoked` e committava, ma non riaccomodava lo stato del goal. Dopo la revoca di una candidate approvata, il goal restava `done` pur non avendo più nessuna candidate approvata — stato impossibile per la state machine.

**Effetto osservato:** goal #5 in stato `done` con candidate #2 in stato `approval_revoked` (rilevato da MF-AUDIT-004 come HIGH).

---

## Modifiche implementate

### `mercury_foundry/state/schema.sql`
Aggiunto commento esplicativo: i trigger append-only sono installati via migrazione (`db._migrate_audit_log_triggers`) e non nel DDL del schema. Motivo tecnico: `conn.executescript()` divide il testo su ogni `;` inclusi quelli dentro i blocchi `BEGIN…END` dei trigger, producendo SQL malformato.

### `mercury_foundry/state/db.py`
Aggiunta funzione `_migrate_audit_log_triggers(conn)`:
- Installa `CREATE TRIGGER IF NOT EXISTS audit_log_no_update BEFORE UPDATE ON audit_log` 
- Installa `CREATE TRIGGER IF NOT EXISTS audit_log_no_delete BEFORE DELETE ON audit_log`
- Entrambi i trigger usano `RAISE(ABORT, message)` — abortisce l'istruzione corrente con messaggio `'audit_log is append-only: … is not permitted (MF-FIX-007)'`
- Chiamata da `init_schema()` → eseguita su ogni apertura di connessione, idempotente per `IF NOT EXISTS`
- Funziona sia su DB nuovi sia su DB pre-esistenti (legacy)

**Nota tecnica:** `RAISE(ABORT, ...)` solleva `sqlite3.IntegrityError` (non `OperationalError`) in Python 3.12 con libsqlite3 ≥ 3.39. Questo è un dettaglio di implementazione del binding Python, non un bug del trigger. I test usano `(sqlite3.OperationalError, sqlite3.IntegrityError)` per compatibilità cross-version.

### `mercury_foundry/state/models.py`
Aggiunta `update_goal_status_no_commit(conn, goal_id, status)`: identica a `update_goal_status` ma senza commit, per partecipare a transazioni atomiche multi-operazione.

### `mercury_foundry/approval/gate.py`
`revoke_approval_incident` ora:
1. Legge il goal (e salva `goal_was_done`) **prima** di qualsiasi DML
2. Dopo il `log_action` esistente per `CANDIDATE_APPROVAL_REVOKED_INCIDENT`:
   - Se `goal_was_done`, chiama `update_goal_status_no_commit(conn, goal_id, "awaiting_approval")`
   - Scrive audit event `GOAL_AWAITING_APPROVAL_REVERTED_AFTER_REVOKE` (`commit=False`)
3. Un unico `conn.commit()` finale chiude la transazione atomica

**Garanzia atomica:** candidate + decision + audit_candidate + goal + audit_goal vengono committati insieme o non affatto. Non è possibile avere goal `done` + candidate `approval_revoked` dopo una revoca andata a buon fine.

---

## Test aggiunti

### `tests/test_audit_log_immutability.py` — 5 test
| Test | Verifica |
|------|----------|
| `test_audit_log_insert_allowed` | INSERT rimane consentito con trigger installati |
| `test_audit_log_update_blocked` | UPDATE solleva eccezione con messaggio `append-only` |
| `test_audit_log_delete_blocked` | DELETE solleva eccezione con messaggio `append-only` |
| `test_audit_log_trigger_migration_is_idempotent` | 3 esecuzioni consecutive di `_migrate_audit_log_triggers` senza errori |
| `test_audit_log_triggers_installed_on_preexisting_db` | DB legacy (senza trigger) viene aggiornato correttamente da `db.connect` |

### `tests/test_goal_candidate_invariant.py` — 6 test
| Test | Verifica |
|------|----------|
| `test_revoke_with_done_goal_reverts_goal_to_awaiting_approval` | Goal DONE → awaiting_approval dopo revoca |
| `test_revoke_with_non_done_goal_does_not_alter_goal_status` | Goal non-done non viene alterato |
| `test_revoke_db_rollback_on_exception_before_commit` | Eccezione prima di commit → DB invariato (verifica via connessione fresca) |
| `test_revoke_produces_goal_revert_audit_event` | Audit `GOAL_AWAITING_APPROVAL_REVERTED_AFTER_REVOKE` prodotto correttamente |
| `test_goal_done_with_revoked_candidate_impossible_after_fix` | Stato inconsistente non può esistere dopo fix |
| `test_second_revoke_raises_invalid_candidate_state_error` | Seconda revoca → `InvalidCandidateStateError` (fail-closed) |

---

## Correzione DB di produzione

**Backup:** `data/mercury_foundry_backup_MF_FIX_007_20260716T094204Z.db`

**Stato pre-fix:**
- Goal #5: `done` — inconsistente
- Candidate #2: `approval_revoked`, goal_id=5

**Percorso applicativo usato (non SQL diretto):**
```python
models.update_goal_status(conn, 5, "awaiting_approval")
log_action(conn, entity_type="goal", entity_id=5,
           action="GOAL_AWAITING_APPROVAL_REVERTED_AFTER_REVOKE",
           actor="system", payload={...})
```
*(candidate #2 era già `approval_revoked` — `revoke_approval_incident` non applicabile)*

**Stato post-fix:**
- Goal #5: `awaiting_approval` ✓
- Candidate #2: `approval_revoked` ✓ (stato coerente)
- Audit event ID 63: `GOAL_AWAITING_APPROVAL_REVERTED_AFTER_REVOKE` a `2026-07-16T09:43:33Z`
- Trigger installati: `audit_log_no_update`, `audit_log_no_delete` ✓
- Audit log totale: 63 righe (invariato dopo migrazione)

---

## Risultati finali

```
221 passed, 3 warnings in 61.02s  (+11 nuovi test rispetto a 210 pre-FIX)
Doctor: READY_SIMULATED
```

**Invarianti garantiti post-MF-FIX-007:**
1. `audit_log` è append-only sia a livello applicativo (API) sia a livello DB (trigger SQLite)
2. Nessuna esecuzione di `revoke_approval_incident` può lasciare goal DONE + candidate approval_revoked
3. Tutta la transazione candidate + goal è atomica: o tutto committato o nulla

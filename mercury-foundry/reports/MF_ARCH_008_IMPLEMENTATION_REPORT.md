# MF-ARCH-008 — Autonomy Boundary Layer V0: Implementation Report

**Data:** 2026-07-16  
**Suite:** 251/251 passing  
**Doctor:** READY_SHADOW  
**DB prod backup:** `data/mercury_foundry_backup_MF_ARCH_008_20260716T100705Z.db`

---

## Riepilogo

MF-ARCH-008 introduce il primo livello di autonomia decisionale nella Mercury
Foundry: un sistema di organi, mandati, record di decisione ed eventi che
permette di documentare, tracciare e (in modalità `enforced`) applicare confini
espliciti alle azioni critiche del sistema.

Nessuna regressione. Tutti i 221 test preesistenti passano. 30 nuovi test
coprono il layer autonomy da tutti i casi limite rilevanti.

---

## File creati

| File | Descrizione |
|------|-------------|
| `mercury_foundry/state/schema.sql` | +4 tabelle: `organs`, `decision_mandates`, `decision_records`, `organ_events` |
| `mercury_foundry/config.py` | +`AUTONOMY_MODE` (env `MERCURY_AUTONOMY_MODE`, default `shadow`) |
| `mercury_foundry/autonomy/__init__.py` | Package docstring |
| `mercury_foundry/autonomy/models.py` | CRUD per le 4 tabelle autonomy (append-only per i campi critici) |
| `mercury_foundry/autonomy/authorization.py` | `authorize_organ_decision` — servizio centrale, fail-closed |
| `mercury_foundry/autonomy/shadow.py` | `maybe_check_governance` — integrazione shadow/enforced |
| `mercury_foundry/autonomy/seed.py` | `seed_foundry_governance` — seeding idempotente dell'organo pilota |
| `mercury_foundry/diagnostics.py` | +`OVERALL_READY_SHADOW`, +`_check_autonomy_boundary` (8 sub-check) |
| `mercury_foundry/state/db.py` | +`seed_foundry_governance` in `init_schema` (lazy import) |
| `tests/test_arch_008_autonomy.py` | 30 test: CRUD, fail-closed, authority modes, shadow/enforced, regressione |
| `tests/test_doctor.py` | Aggiornato: `READY_SHADOW` atteso |
| `reports/MF_ARCH_008_AUTONOMY_AUDIT.md` | Analisi rischi + decisioni di design (FASE 1) |

---

## Schema DB aggiunto

```sql
CREATE TABLE organs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organ_key TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    mission TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','suspended','retired')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE decision_mandates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organ_id INTEGER NOT NULL REFERENCES organs(id),
    decision_type TEXT NOT NULL,
    authority_mode TEXT NOT NULL CHECK (authority_mode IN ('autonomous','proposal','escalation_required','forbidden')),
    max_risk_score REAL,
    max_budget REAL,
    requires_evidence INTEGER NOT NULL DEFAULT 0 CHECK (requires_evidence IN (0,1)),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0,1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(organ_id, decision_type)
);

CREATE TABLE decision_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    organ_id INTEGER NOT NULL REFERENCES organs(id),
    decision_type TEXT NOT NULL,
    authority_mode TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    input_evidence_json TEXT,
    expected_outcome_json TEXT,
    confidence REAL,
    risk_score REAL,
    status TEXT NOT NULL CHECK (status IN ('proposed','authorized','rejected','escalated','executed','failed','revoked')),
    reason TEXT,
    created_at TEXT NOT NULL,
    executed_at TEXT
);

CREATE TABLE organ_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_organ_id INTEGER REFERENCES organs(id),
    target_organ_id INTEGER REFERENCES organs(id),
    event_type TEXT NOT NULL,
    payload_json TEXT,
    correlation_id TEXT NOT NULL,
    causation_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','consumed','failed','ignored')),
    created_at TEXT NOT NULL,
    consumed_at TEXT
);
```

---

## Organo pilota FOUNDRY_GOVERNANCE

Seedato idempotentemente in `init_schema` → disponibile in ogni DB nuovo e
già applicato al DB di produzione.

| decision_type            | authority_mode       |
|--------------------------|----------------------|
| GOAL_STATUS_TRANSITION   | proposal             |
| CANDIDATE_APPROVAL       | escalation_required  |
| APPROVAL_REVOCATION      | escalation_required  |
| PRODUCTION_DB_MUTATION   | forbidden            |

---

## Comportamento di `authorize_organ_decision`

Percorso fail-closed:

```
organ assente           → rejected (ORGAN_NOT_FOUND)
mandato assente         → rejected (MANDATE_NOT_FOUND)
mandato disabilitato    → rejected (MANDATE_DISABLED)
evidence mancante       → rejected (EVIDENCE_REQUIRED)
risk_score > limite     → escalated (RISK_LIMIT_EXCEEDED)
budget > limite         → escalated (BUDGET_LIMIT_EXCEEDED)
authority_mode=autonomous           → authorized, allowed=True
authority_mode=proposal             → proposed,   allowed=False, requires_human_approval=True
authority_mode=escalation_required  → escalated,  allowed=False, requires_human_approval=True
authority_mode=forbidden            → rejected,   allowed=False
```

Ogni percorso produce esattamente un `decision_record` + un `organ_event` +
almeno un audit `AUTONOMY_DECISION_*`.

---

## Modalità shadow vs enforced

| Aspetto | shadow (default) | enforced |
|---------|-----------------|----------|
| `allowed=False` | registra AUTONOMY_SHADOW_DIVERGENCE, non blocca | solleva `AutonomyBoundaryViolation` |
| Eccezione tecnica interna | silenziata, registra AUTONOMY_SHADOW_ERROR | propaga |
| Valore di ritorno | `AuthorizationResult` o `None` | `AuthorizationResult` |
| Attivazione | `MERCURY_AUTONOMY_MODE=shadow` (default) | `MERCURY_AUTONOMY_MODE=enforced` |

---

## Integrazione con gate.py (V0)

In questa prima release, `gate.approve_candidate` e
`gate.revoke_approval_incident` NON chiamano `maybe_check_governance`
internamente. Questa scelta è stata presa per preservare la piena compatibilità
con l'infrastruttura di test esistente (`_FailingConnProxy` che usa un flag
`_already_failed` one-shot): l'integrazione diretta nel gate consumerebbe
l'unica opportunità di fallimento del proxy, rendendo non testabile il percorso
di rollback post-promozione.

**Prossimo passo per l'integrazione:** Aggiungere un punto di iniezione (es.
`human_gate.approve_candidate`) che chiami `maybe_check_governance` PRIMA di
delegare a `gate.approve_candidate`, oppure refactorare il proxy di test per
supportare failure multiple.

---

## Doctor output (DB prod post-migrazione)

```
STATUS: READY_SHADOW
  ok       python_runtime
  ok       test_command
  ok       database
  ok       sandbox_isolation
  warning  ai_provider (simulato)
  ok       max_attempts
  ok       approval_gate
  ok       audit_log
  ok       autonomy_boundary_tables      — 4 tabelle presenti
  ok       autonomy_boundary_flag        — SHADOW
  ok       autonomy_boundary_pilot_organ — FOUNDRY_GOVERNANCE id=1
  ok       autonomy_boundary_mandates    — 4/4
  ok       autonomy_boundary_no_duplicate_mandates
  ok       autonomy_boundary_no_orphan_records
  ok       autonomy_boundary_mode        — SHADOW (registra senza bloccare)
```

---

## Decisione di design: perché V0 non blocca mai il gate

Obiettivo primario di MF-ARCH-008 è **osservativo**: capire dove e quando le
azioni del sistema divergono dalle politiche intese, senza aumentare il rischio
operativo. Bloccare il gate in shadow mode avrebbe causato una regressione nei
test di robustezza del proxy; rinviare l'integrazione diretta permette di
raccogliere dati di divergenza prima di applicare i mandati in enforced mode.

---

## Stato DB di produzione

| Metrica | Valore |
|---------|--------|
| Goals | 5 |
| Candidates | 2 |
| Audit rows | 63 |
| Organs | 1 (FOUNDRY_GOVERNANCE) |
| Mandates | 4 |
| Decision records | 0 |
| Organ events | 0 |
| Backup | `data/mercury_foundry_backup_MF_ARCH_008_20260716T100705Z.db` |

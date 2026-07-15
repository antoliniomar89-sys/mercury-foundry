-- Schema SQLite per Mercury Foundry V0.
-- Stato persistente: obiettivi, task, tentativi, risultati dei test,
-- decisioni umane, candidate e audit log append-only.

CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open', -- open | in_progress | awaiting_approval | done | blocked
    created_at TEXT NOT NULL,
    -- Vincoli letterali deterministici (JSON di `LiteralConstraints`, opzionale).
    -- Su un DB pre-esistente (creato prima di questa colonna) questa colonna
    -- viene aggiunta idempotentemente da `state.db._migrate_goals_columns`,
    -- perché `CREATE TABLE IF NOT EXISTS` non altera una tabella già creata.
    literal_constraints_json TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id INTEGER NOT NULL REFERENCES goals(id),
    order_index INTEGER NOT NULL,
    description TEXT NOT NULL,
    assigned_to TEXT NOT NULL, -- builder | evaluator
    status TEXT NOT NULL DEFAULT 'pending', -- pending | in_progress | passed | blocked
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    attempt_number INTEGER NOT NULL,
    phase TEXT NOT NULL, -- BUILD | TEST | FIX | VERIFY
    status TEXT NOT NULL, -- running | success | failure
    provider_name TEXT,
    is_simulated INTEGER,
    diff_summary TEXT,
    notes TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT
);

CREATE TABLE IF NOT EXISTS test_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attempt_id INTEGER NOT NULL REFERENCES attempts(id),
    test_name TEXT NOT NULL,
    passed INTEGER NOT NULL, -- 0/1
    output TEXT,
    duration_ms INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER REFERENCES tasks(id),
    candidate_id INTEGER REFERENCES candidates(id),
    decision_type TEXT NOT NULL, -- approve | reject | escalate
    actor TEXT NOT NULL, -- human | system
    rationale TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id INTEGER NOT NULL REFERENCES goals(id),
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    summary TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending_review', -- pending_review | approved | rejected
    -- Identità del provider che ha generato la patch verificata in questa candidate,
    -- e se si tratta di una simulazione. Denormalizzato qui (oltre a "attempts")
    -- perché una candidate deve poter essere ispezionata/approvata senza dover
    -- risalire manualmente all'attempt: previene che una candidate simulata sia
    -- scambiata per generazione AI reale.
    provider_name TEXT NOT NULL,
    is_simulated INTEGER NOT NULL,
    created_at TEXT NOT NULL
    -- Colonne di staging/manifest (run_id, attempt_id, staging_root,
    -- target_snapshot_hash, manifest_json) sono aggiunte idempotentemente da
    -- `state.db._migrate_candidates_columns`, per lo stesso motivo delle
    -- altre migrazioni in questo file: `CREATE TABLE IF NOT EXISTS` non
    -- altera una tabella `candidates` già esistente da una versione precedente.
);

-- Associazione candidate<->provider_calls, APPEND-ONLY: sostituisce il
-- pattern precedente (un UPDATE retroattivo di provider_calls.candidate_id),
-- che violava l'append-only-ness della tabella provider_calls. Una candidate
-- può riferirsi a più chiamate provider dello stesso task; ogni riga qui è
-- scritta una sola volta e mai aggiornata/cancellata (idempotente tramite
-- l'indice univoco sotto: un secondo tentativo di associare la stessa coppia
-- non crea una seconda riga).
CREATE TABLE IF NOT EXISTS candidate_provider_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL REFERENCES candidates(id),
    provider_call_id INTEGER NOT NULL REFERENCES provider_calls(id),
    created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_candidate_provider_calls_dedup
    ON candidate_provider_calls(candidate_id, provider_call_id);

-- Telemetria per-chiamata del provider AI (reale o simulato). Popolata SOLO
-- quando un provider produce un ProviderCallRecord (i provider simulati non
-- fanno chiamate esterne, quindi normalmente non generano righe qui).
--
-- Ogni chiamata REALE del provider (riuscita o fallita, in qualunque fase:
-- PLAN, PATCH, EVALUATION, CONNECTIVITY_CHECK) produce ESATTAMENTE una riga
-- qui: `run_id` identifica il run (oggi coincide con il goal_id: un goal
-- sottomesso è un run del Foundry), `operation` identifica la fase. La
-- combinazione (run_id, provider_name, call_number) è univoca: un secondo
-- tentativo di persistere lo STESSO ProviderCallRecord (stesso run/provider/
-- call_number) non crea una riga duplicata (vedi models.create_provider_call).
-- La tabella resta append-only: nessun codice della app la aggiorna o cancella.
CREATE TABLE IF NOT EXISTS provider_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    goal_id INTEGER REFERENCES goals(id),
    task_id INTEGER REFERENCES tasks(id),
    attempt_id INTEGER REFERENCES attempts(id),
    candidate_id INTEGER REFERENCES candidates(id),
    provider_name TEXT NOT NULL,
    model TEXT,
    is_simulated INTEGER NOT NULL,
    operation TEXT NOT NULL DEFAULT 'UNKNOWN', -- PLAN | PATCH | EVALUATION | CONNECTIVITY_CHECK
    call_number INTEGER NOT NULL,
    requested_at TEXT NOT NULL,
    responded_at TEXT,
    success INTEGER NOT NULL, -- 0/1
    usage_json TEXT,
    estimated_cost_usd REAL,
    error_summary TEXT, -- SEMPRE già redatto: mai segreti/prompt completi
    created_at TEXT NOT NULL
);

-- Difesa in profondità contro record duplicati per la stessa chiamata reale:
-- una riga per (run_id, provider_name, call_number). NULL è considerato
-- distinto da SQLite, quindi righe pre-migrazione con run_id NULL non
-- collidono tra loro; ogni nuova riga scritta da questo task valorizza
-- sempre run_id.
--
-- NOTA: l'indice NON viene creato qui con CREATE TABLE, perché questo script
-- viene eseguito anche contro DB pre-esistenti che non hanno ancora la
-- colonna `run_id` (aggiunta via ALTER TABLE in una migrazione idempotente
-- separata, vedi `state.db._migrate_provider_calls_columns`). Se l'indice
-- fosse qui, fallirebbe su un DB vecchio PRIMA che la migrazione possa
-- aggiungere la colonna. L'indice viene quindi creato da quella migrazione,
-- che gira DOPO l'ALTER TABLE, sia per DB nuovi sia per DB esistenti.

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL, -- goal | task | attempt | candidate | decision
    entity_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    actor TEXT NOT NULL, -- system | human
    payload_json TEXT,
    created_at TEXT NOT NULL
);

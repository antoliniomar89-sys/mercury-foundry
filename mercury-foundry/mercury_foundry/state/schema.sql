-- Schema SQLite per Mercury Foundry V0.
-- Stato persistente: obiettivi, task, tentativi, risultati dei test,
-- decisioni umane, candidate e audit log append-only.

CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    description TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open', -- open | in_progress | awaiting_approval | done | blocked
    created_at TEXT NOT NULL
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
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL, -- goal | task | attempt | candidate | decision
    entity_id INTEGER NOT NULL,
    action TEXT NOT NULL,
    actor TEXT NOT NULL, -- system | human
    payload_json TEXT,
    created_at TEXT NOT NULL
);

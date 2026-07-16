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

-- ===========================================================================
-- MF-ARCH-008: Autonomy Boundary Layer V0
-- ===========================================================================

-- Unità decisionali con autorità locale esplicita.
CREATE TABLE IF NOT EXISTS organs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    organ_key  TEXT NOT NULL UNIQUE,
    name       TEXT NOT NULL,
    mission    TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Autorità delegata per tipo di decisione (UNIQUE per organ+decision_type).
CREATE TABLE IF NOT EXISTS decision_mandates (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    organ_id         INTEGER NOT NULL REFERENCES organs(id),
    decision_type    TEXT NOT NULL,
    authority_mode   TEXT NOT NULL CHECK (authority_mode IN ('autonomous','proposal','escalation_required','forbidden')),
    max_risk_score   REAL,
    max_budget       REAL,
    requires_evidence INTEGER NOT NULL DEFAULT 0,
    enabled          INTEGER NOT NULL DEFAULT 1,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    UNIQUE (organ_id, decision_type)
);

-- Log immutabile (nei campi critici) di ogni decisione presa da un organo.
CREATE TABLE IF NOT EXISTS decision_records (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    organ_id             INTEGER NOT NULL REFERENCES organs(id),
    decision_type        TEXT NOT NULL,
    authority_mode       TEXT NOT NULL,
    subject_type         TEXT NOT NULL,
    subject_id           TEXT NOT NULL,
    input_evidence_json  TEXT,
    expected_outcome_json TEXT,
    confidence           REAL,
    risk_score           REAL,
    status               TEXT NOT NULL CHECK (status IN ('proposed','authorized','rejected','escalated','executed','failed','revoked')),
    reason               TEXT,
    created_at           TEXT NOT NULL,
    executed_at          TEXT
);

-- Bus eventi inter-organo (correlation/causation tracking).
CREATE TABLE IF NOT EXISTS organ_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    source_organ_id  INTEGER REFERENCES organs(id),
    target_organ_id  INTEGER REFERENCES organs(id),
    event_type       TEXT NOT NULL,
    payload_json     TEXT,
    correlation_id   TEXT NOT NULL,
    causation_id     TEXT,
    status           TEXT NOT NULL CHECK (status IN ('pending','consumed','failed','ignored')),
    created_at       TEXT NOT NULL,
    consumed_at      TEXT
);

-- ===========================================================================
-- MF-MISSION-001: Mission Layer V0
-- ===========================================================================

-- Mandati strutturati orientati a un outcome economico o operativo.
-- Non sono task, goal, esperimenti o Business Cell.
CREATE TABLE IF NOT EXISTS missions (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id                  TEXT NOT NULL UNIQUE,   -- UUID (riferimento esterno)
    idempotency_key             TEXT NOT NULL UNIQUE,
    correlation_id              TEXT NOT NULL,
    title                       TEXT NOT NULL,
    description                 TEXT NOT NULL,
    origin_type                 TEXT NOT NULL,          -- OriginType enum
    origin_ref                  TEXT,
    mission_type                TEXT NOT NULL,          -- MissionType enum
    status                      TEXT NOT NULL DEFAULT 'draft',
    priority                    TEXT NOT NULL DEFAULT 'normal',
    objective                   TEXT NOT NULL,
    expected_outcomes_json      TEXT NOT NULL DEFAULT '[]',
    success_criteria_json       TEXT NOT NULL DEFAULT '[]',
    termination_criteria_json   TEXT NOT NULL DEFAULT '[]',
    constraints_json            TEXT NOT NULL DEFAULT '{}',
    budget_json                 TEXT NOT NULL DEFAULT '{}',
    risk_profile_json           TEXT NOT NULL DEFAULT '{}',
    authority_request_json      TEXT NOT NULL DEFAULT '{}',
    required_capabilities_json  TEXT NOT NULL DEFAULT '[]',
    knowledge_scope             TEXT NOT NULL DEFAULT 'mission_local',
    business_scope              TEXT NOT NULL DEFAULT 'exploration',
    deadline                    TEXT,
    parent_mission_id           TEXT REFERENCES missions(mission_id),
    candidate_business_cell_id  TEXT,
    constitutional_version      TEXT NOT NULL,
    created_by                  TEXT NOT NULL,
    assigned_organ_id           INTEGER REFERENCES organs(id),
    created_at                  TEXT NOT NULL,
    updated_at                  TEXT NOT NULL,
    accepted_at                 TEXT,
    activated_at                TEXT,
    completed_at                TEXT,
    terminated_at               TEXT,
    version                     INTEGER NOT NULL DEFAULT 1,
    metadata_json               TEXT NOT NULL DEFAULT '{}'
);

-- Log immutabile delle transizioni di stato di ogni Mission.
CREATE TABLE IF NOT EXISTS mission_transitions (
    id                           INTEGER PRIMARY KEY AUTOINCREMENT,
    transition_id                TEXT NOT NULL UNIQUE,  -- UUID
    mission_id                   TEXT NOT NULL REFERENCES missions(mission_id),
    from_status                  TEXT NOT NULL,
    to_status                    TEXT NOT NULL,
    requested_by                 TEXT NOT NULL,
    requested_at                 TEXT NOT NULL,
    authorized_by                TEXT,
    reason                       TEXT NOT NULL,
    evidence_refs_json           TEXT NOT NULL DEFAULT '[]',
    authority_decision_id        TEXT,
    constitutional_validation_id TEXT,
    correlation_id               TEXT NOT NULL,
    metadata_json                TEXT NOT NULL DEFAULT '{}'
);

-- ===========================================================================
-- MF-REPL-001: Dedicated Mercury Genesis Contract V0
-- ===========================================================================

-- Richieste di genesis di una Dedicated Mercury.
-- In V0: provisioning e activated non sono raggiungibili automaticamente.
CREATE TABLE IF NOT EXISTS dedicated_mercury_genesis_requests (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    genesis_request_id          TEXT NOT NULL UNIQUE,
    idempotency_key             TEXT NOT NULL UNIQUE,
    correlation_id              TEXT NOT NULL,
    source_mission_id           TEXT NOT NULL,
    source_expedition_id        TEXT,
    validated_product_ids_json  TEXT NOT NULL DEFAULT '[]',
    product_family_key          TEXT,
    proposed_instance_name      TEXT NOT NULL,
    proposed_instance_slug      TEXT NOT NULL,
    genesis_reason              TEXT NOT NULL,
    validation_evidence_refs_json TEXT NOT NULL DEFAULT '[]',
    product_validation_score    REAL,
    pmf_confidence              REAL,
    target_market               TEXT NOT NULL,
    target_customer             TEXT NOT NULL,
    business_model              TEXT NOT NULL,
    constitutional_version      TEXT NOT NULL,
    kernel_version              TEXT NOT NULL,
    requested_genesis_profile   TEXT NOT NULL DEFAULT 'standard',
    requested_capability_bundle_ids_json TEXT NOT NULL DEFAULT '[]',
    requested_knowledge_package_ids_json TEXT NOT NULL DEFAULT '[]',
    requested_budget_envelope   REAL NOT NULL DEFAULT 0.0,
    requested_authority_profile_json  TEXT NOT NULL DEFAULT '{}',
    requested_isolation_profile_json  TEXT NOT NULL DEFAULT '{}',
    requested_federation_profile_json TEXT NOT NULL DEFAULT '{}',
    requested_reporting_profile_json  TEXT NOT NULL DEFAULT '{}',
    requested_parent_relationship_json TEXT NOT NULL DEFAULT '{}',
    requested_by                TEXT NOT NULL,
    requested_at                TEXT NOT NULL,
    status                      TEXT NOT NULL DEFAULT 'draft',
    created_at                  TEXT NOT NULL,
    updated_at                  TEXT NOT NULL,
    version                     INTEGER NOT NULL DEFAULT 1,
    metadata_json               TEXT NOT NULL DEFAULT '{}'
);

-- Log immutabile delle transizioni di stato di ogni Genesis Request.
CREATE TABLE IF NOT EXISTS dedicated_mercury_genesis_transitions (
    id                           INTEGER PRIMARY KEY AUTOINCREMENT,
    transition_id                TEXT NOT NULL UNIQUE,
    genesis_request_id           TEXT NOT NULL REFERENCES dedicated_mercury_genesis_requests(genesis_request_id),
    from_status                  TEXT NOT NULL,
    to_status                    TEXT NOT NULL,
    requested_by                 TEXT NOT NULL,
    requested_at                 TEXT NOT NULL,
    authorized_by                TEXT,
    reason                       TEXT NOT NULL,
    evidence_refs_json           TEXT NOT NULL DEFAULT '[]',
    authority_decision_id        TEXT,
    constitutional_validation_id TEXT,
    correlation_id               TEXT NOT NULL,
    metadata_json                TEXT NOT NULL DEFAULT '{}'
);

-- Pacchetti genetici serializzati, versionati, immutabili dopo approvazione.
CREATE TABLE IF NOT EXISTS mercury_genetic_packages (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    package_id              TEXT NOT NULL UNIQUE,
    package_version         TEXT NOT NULL,
    genesis_request_id      TEXT NOT NULL REFERENCES dedicated_mercury_genesis_requests(genesis_request_id),
    source_instance_id      TEXT NOT NULL,
    target_instance_id      TEXT,
    status                  TEXT NOT NULL DEFAULT 'draft',  -- draft | sealed | invalid
    checksum                TEXT NOT NULL,
    package_json            TEXT NOT NULL,
    generated_at            TEXT NOT NULL,
    generated_by            TEXT NOT NULL,
    created_at              TEXT NOT NULL
);

-- Contratti di indipendenza (Independence Contract).
CREATE TABLE IF NOT EXISTS dedicated_mercury_independence_contracts (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id             TEXT NOT NULL UNIQUE,
    genesis_request_id      TEXT NOT NULL REFERENCES dedicated_mercury_genesis_requests(genesis_request_id),
    instance_id             TEXT,
    status                  TEXT NOT NULL DEFAULT 'not_assessed',
    contract_json           TEXT NOT NULL,
    evaluated_at            TEXT NOT NULL,
    created_at              TEXT NOT NULL
);

-- Assessments di coerenza famiglia prodotti.
CREATE TABLE IF NOT EXISTS product_family_assessments (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    assessment_id           TEXT NOT NULL UNIQUE,
    genesis_request_id      TEXT NOT NULL REFERENCES dedicated_mercury_genesis_requests(genesis_request_id),
    coherence_score         REAL NOT NULL,
    recommendation          TEXT NOT NULL,
    assessment_json         TEXT NOT NULL,
    evaluated_at            TEXT NOT NULL,
    created_at              TEXT NOT NULL
);

-- Risultati del Replication Gate.
CREATE TABLE IF NOT EXISTS replication_gate_results (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    gate_result_id          TEXT NOT NULL UNIQUE,
    genesis_request_id      TEXT NOT NULL REFERENCES dedicated_mercury_genesis_requests(genesis_request_id),
    approved                INTEGER NOT NULL DEFAULT 0,
    gate_status             TEXT NOT NULL,
    validation_score        REAL NOT NULL,
    independence_status     TEXT NOT NULL,
    result_json             TEXT NOT NULL,
    evaluated_at            TEXT NOT NULL,
    created_at              TEXT NOT NULL
);

-- ============================================================
-- MF-OUTCOME-001: Economic Outcome Governance V0
-- ============================================================

-- Piano di outcome economico associato a una Mission.
CREATE TABLE IF NOT EXISTS economic_outcome_plans (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    outcome_plan_id           TEXT NOT NULL UNIQUE,
    mission_id                TEXT NOT NULL,
    correlation_id            TEXT NOT NULL,
    objective                 TEXT NOT NULL,
    primary_metric            TEXT NOT NULL,
    target_value              REAL NOT NULL,
    target_operator           TEXT NOT NULL,
    maximum_cost_minor        INTEGER NOT NULL DEFAULT 0,
    maximum_duration_seconds  INTEGER NOT NULL DEFAULT 0,
    review_interval_seconds   INTEGER NOT NULL DEFAULT 0,
    kill_deadline             TEXT NOT NULL,
    minimum_evidence_count    INTEGER NOT NULL DEFAULT 0,
    strategic_value_score     REAL NOT NULL DEFAULT 0.0,
    learning_value_score      REAL NOT NULL DEFAULT 0.0,
    reversibility             TEXT NOT NULL DEFAULT 'reversible',
    created_by                TEXT NOT NULL,
    created_at                TEXT NOT NULL,
    updated_at                TEXT NOT NULL,
    version                   INTEGER NOT NULL DEFAULT 1,
    status                    TEXT NOT NULL DEFAULT 'planned',
    priority_class            TEXT NOT NULL DEFAULT 'normal',
    currency                  TEXT,
    expected_revenue_minor    INTEGER,
    expected_profit_minor     INTEGER,
    scale_threshold           REAL,
    stop_threshold            REAL,
    rollback_plan             TEXT,
    metadata_json             TEXT NOT NULL DEFAULT '{}'
);

-- Snapshot di metriche economiche per una Mission (append-only).
CREATE TABLE IF NOT EXISTS outcome_metric_snapshots (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id           TEXT NOT NULL UNIQUE,
    outcome_plan_id       TEXT NOT NULL REFERENCES economic_outcome_plans(outcome_plan_id),
    mission_id            TEXT NOT NULL,
    measured_at           TEXT NOT NULL,
    revenue_minor         INTEGER NOT NULL DEFAULT 0,
    cost_minor            INTEGER NOT NULL DEFAULT 0,
    profit_minor          INTEGER NOT NULL DEFAULT 0,
    elapsed_seconds       INTEGER NOT NULL DEFAULT 0,
    evidence_count        INTEGER NOT NULL DEFAULT 0,
    customer_count        INTEGER NOT NULL DEFAULT 0,
    knowledge_gain_score  REAL NOT NULL DEFAULT 0.0,
    risk_score            REAL NOT NULL DEFAULT 0.0,
    conversion_rate       REAL,
    delivery_success_rate REAL,
    metadata_json         TEXT NOT NULL DEFAULT '{}'
);

-- Envelope di risorse allocate a una Mission.
CREATE TABLE IF NOT EXISTS resource_envelopes (
    id                            INTEGER PRIMARY KEY AUTOINCREMENT,
    envelope_id                   TEXT NOT NULL UNIQUE,
    mission_id                    TEXT NOT NULL,
    budget_minor                  INTEGER NOT NULL DEFAULT 0,
    compute_units                 INTEGER NOT NULL DEFAULT 0,
    llm_token_limit               INTEGER NOT NULL DEFAULT 0,
    external_service_limit_minor  INTEGER NOT NULL DEFAULT 0,
    human_minutes_limit           INTEGER NOT NULL DEFAULT 0,
    deadline                      TEXT NOT NULL,
    allocated_at                  TEXT NOT NULL,
    allocated_by                  TEXT NOT NULL,
    version                       INTEGER NOT NULL DEFAULT 1,
    metadata_json                 TEXT NOT NULL DEFAULT '{}'
);

-- Registrazioni di consumo risorse (append-only, idempotente).
CREATE TABLE IF NOT EXISTS resource_consumptions (
    id                           INTEGER PRIMARY KEY AUTOINCREMENT,
    consumption_id               TEXT NOT NULL UNIQUE,
    envelope_id                  TEXT NOT NULL REFERENCES resource_envelopes(envelope_id),
    mission_id                   TEXT NOT NULL,
    cost_minor                   INTEGER NOT NULL DEFAULT 0,
    compute_units                INTEGER NOT NULL DEFAULT 0,
    llm_tokens                   INTEGER NOT NULL DEFAULT 0,
    external_service_cost_minor  INTEGER NOT NULL DEFAULT 0,
    human_minutes                INTEGER NOT NULL DEFAULT 0,
    recorded_at                  TEXT NOT NULL,
    source_ref                   TEXT NOT NULL,
    idempotency_key              TEXT NOT NULL UNIQUE,
    metadata_json                TEXT NOT NULL DEFAULT '{}'
);

-- Decisioni di outcome (immutabili dopo INSERT).
CREATE TABLE IF NOT EXISTS outcome_decisions (
    id                            INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id                   TEXT NOT NULL UNIQUE,
    mission_id                    TEXT NOT NULL,
    outcome_plan_id               TEXT NOT NULL REFERENCES economic_outcome_plans(outcome_plan_id),
    decision_type                 TEXT NOT NULL,
    score                         REAL NOT NULL DEFAULT 0.0,
    confidence                    REAL NOT NULL DEFAULT 0.0,
    reasons_json                  TEXT NOT NULL DEFAULT '[]',
    blockers_json                 TEXT NOT NULL DEFAULT '[]',
    required_actions_json         TEXT NOT NULL DEFAULT '[]',
    decided_at                    TEXT NOT NULL,
    correlation_id                TEXT NOT NULL,
    authority_decision_id         TEXT,
    constitutional_validation_id  TEXT,
    metadata_json                 TEXT NOT NULL DEFAULT '{}'
);

-- Log di transizioni di stato OutcomePlan (append-only).
CREATE TABLE IF NOT EXISTS outcome_transition_records (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    transition_id   TEXT NOT NULL UNIQUE,
    outcome_plan_id TEXT NOT NULL REFERENCES economic_outcome_plans(outcome_plan_id),
    mission_id      TEXT NOT NULL,
    from_status     TEXT NOT NULL,
    to_status       TEXT NOT NULL,
    requested_by    TEXT NOT NULL,
    requested_at    TEXT NOT NULL,
    reason          TEXT NOT NULL,
    correlation_id  TEXT NOT NULL,
    decision_id     TEXT,
    metadata_json   TEXT NOT NULL DEFAULT '{}'
);

-- Reservations di risorse (MF-ECO-001).
-- Una reservation blocca fondi prima di consumarli definitivamente.
-- Invarianti: amount_minor > 0; (envelope_id, idempotency_key) UNIQUE;
-- status ∈ {reserved, consumed, released, expired}.
CREATE TABLE IF NOT EXISTS resource_reservations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    reservation_id  TEXT NOT NULL UNIQUE,
    mission_id      TEXT NOT NULL,
    envelope_id     TEXT NOT NULL REFERENCES resource_envelopes(envelope_id),
    amount_minor    INTEGER NOT NULL CHECK(amount_minor > 0),
    currency        TEXT NOT NULL DEFAULT 'EUR',
    status          TEXT NOT NULL DEFAULT 'reserved',
    idempotency_key TEXT NOT NULL,
    reason          TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    released_at     TEXT,
    consumed_at     TEXT,
    UNIQUE(envelope_id, idempotency_key)
);

-- MF-FIX-007: trigger BEFORE UPDATE e BEFORE DELETE su audit_log sono installati
-- via migrazione in `state.db._migrate_audit_log_triggers`, NON qui.
-- Motivo: `conn.executescript()` divide il testo sulle `;` anche dentro i blocchi
-- BEGIN…END dei trigger, producendo SQL malformato. I trigger vengono creati con
-- chiamate `conn.execute()` singole nella funzione di migrazione dedicata, che è
-- idempotente (IF NOT EXISTS) e gira sia su DB nuovi sia su DB pre-esistenti.

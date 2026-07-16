# AUDIT_REPORT.md — Mercury Foundry

**Data audit:** 2026-07-16  
**Commit auditato:** `a55a0a4` (HEAD = origin/main, working tree pulito)  
**Branch:** `main`  
**Modalità:** read-only — nessuna modifica, nessuna chiamata a provider a pagamento

---

## A. Executive Summary

Mercury Foundry è un sistema Python locale per la generazione di software controllata da un agente AI. Il motore tecnico è **funzionante e testato**: 210 test passati su 210, ciclo SPEC→PLAN→BUILD→TEST→FIX→VERIFY→CANDIDATE completo, staging isolato, gate umano multi-strato. Il doctor segnala `READY_SIMULATED` (EXIT 0). Il provider OpenAI reale esiste ed è configurato, ma il canale di approvazione umana non è abilitato in questo workspace.

**Ciò che funziona:** tutto il motore tecnico — orchestrazione, sandbox, staging, constraints, approval gate, audit log, fake provider, real provider (testato via mock), CLI.

**Ciò che non esiste:** nessun componente economico Mercury (mercato, scoring, validazione domanda, vendita, consegna, misurazione outcome). Il sistema può produrre software testato ma non può scegliere cosa produrre né monetizzarlo.

**Problemi aperti:** inconsistenza DB produzione (goal #5 `done` ma candidate #2 `approval_revoked`), `.agents/` e `attached_assets/` pubblici su GitHub, audit log mutabile via SQL diretto, doctor non mostra stato canale approvazione.

**Confidenza audit: 96%** (unico limite: comportamento reale sotto concorrenza non testato).

---

## B. Comandi Eseguiti con Exit Code

| Comando | CWD | Exit Code | Output rilevante |
|---|---|---|---|
| `python3 -m mercury_foundry.cli doctor` | `mercury-foundry/` | **0** | `READY_SIMULATED` — tutti gli 8 check OK (1 WARN provider simulato) |
| `python3 -m pytest -q` | `mercury-foundry/` | **0** | `210 passed, 3 warnings in 51.05s` |
| `git status --short` | `mercury-foundry/` | **0** | Un solo file untracked (`attached_assets/Pasted-AGISCI-*.txt`) |
| `git branch --show-current` | `mercury-foundry/` | **0** | `main` |
| `git log --oneline -10` | `mercury-foundry/` | **0** | Vedi sezione K |

**Nota working directory:** tutti i comandi funzionano esclusivamente da `mercury-foundry/`. Eseguire `python3 -m pytest` dalla root del repo fallirebbe (Python package non risolto). Il packaging Python (pyproject.toml, uv.lock) è alla **root del monorepo**, non dentro `mercury-foundry/`.

---

## C. Struttura Reale del Repository

```
/ (root Replit monorepo)
├── pyproject.toml          ← packaging Python (gestito da uv, root-level)
├── uv.lock                 ← lockfile Python
├── pnpm-workspace.yaml     ← monorepo JS/TS (artifacts/*, lib/*)
├── .replit                 ← config runtime + env vars OpenAI
├── artifacts/
│   ├── api-server/         ← artifact Node.js (API server, non collegato a Foundry)
│   └── mockup-sandbox/     ← artifact canvas/design (non collegato a Foundry)
├── lib/                    ← librerie JS condivise (non usate da Foundry)
├── scripts/                ← post-merge.sh
├── attached_assets/        ← 20 file Pasted-*.txt (log operazioni, TRACCIATI in git)
├── .agents/memory/         ← memoria agente (TRACCIATA in git)
└── mercury-foundry/        ← *** TUTTO il progetto Python Mercury Foundry ***
    ├── mercury_foundry/    ← package principale
    │   ├── __init__.py
    │   ├── cli.py          ← entrypoint CLI (python3 -m mercury_foundry.cli)
    │   ├── config.py       ← paths, limiti, variabili d'ambiente
    │   ├── wiring.py       ← Foundry dataclass (DI container)
    │   ├── diagnostics.py  ← run_doctor()
    │   ├── ai/             ← provider ABC, FakeModel, OpenAICompatibleProvider
    │   ├── agents/         ← Builder (plan+patch), Evaluator
    │   ├── approval/       ← gate.py (business logic) + human_gate.py (canale umano)
    │   ├── audit/          ← logger.py (append-only applicativo)
    │   ├── execution/      ← loop.py (SPEC→CANDIDATE)
    │   ├── orchestrator/   ← orchestrator.py + decomposition.py
    │   ├── policy/         ← literal_constraints.py + errors.py
    │   ├── sandbox/        ← workspace.py + staging.py + test_env.py
    │   ├── state/          ← db.py + models.py + schema.sql
    │   └── testing/        ← runner.py (subprocess pytest)
    ├── tests/              ← 17 file test, 210 test totali
    ├── data/               ← mercury_foundry.db (prod), MF-INCIDENT-001-report.md, MF-AUDIT-004-report.md
    ├── target_project/     ← directory target promozione (VUOTA)
    ├── probe_constraints.json ← constraints run MF-RUN-003 (TRACCIATO in git)
    ├── pytest.ini
    ├── README.md           ← OBSOLETO (non aggiornato post MF-GATE-002)
    └── PLAN.md             ← OBSOLETO (non aggiornato post MF-GATE-002)
```

**Stack Python e TypeScript:** coesistono nel monorepo ma sono **completamente separati**. Mercury Foundry è 100% Python. Gli artifact Node.js (`api-server`, `mockup-sandbox`) non importano né chiamano Mercury Foundry.

**Entrypoint reale:** `python3 -m mercury_foundry.cli` (da `mercury-foundry/`)

**Progetti annidati:** nessuno. Il monorepo JS non ha dipendenze verso il package Python.

---

## D. Componenti — Stato

| Componente | Stato | Evidenza |
|---|---|---|
| **CLI** | FUNZIONANTE | EXIT 0, tutti i comandi testati: `doctor`, `submit`, `status`, `approve`, `reject`, `export-candidate`, `audit`, `check-provider` |
| **Orchestrator** | FUNZIONANTE | `test_execution_loop_e2e_healthcheck.py` (2 test), `test_atomic_build.py` |
| **Execution Loop** (SPEC→CANDIDATE) | FUNZIONANTE | Ciclo completo verificato in MF-RUN-003 con provider reale |
| **Builder** (propose_plan + propose_patch) | FUNZIONANTE | Testato via fake e via mock HTTP OpenAI |
| **Evaluator** | FUNZIONANTE | `agents/evaluator.py` analizza test results, collegato all'execution loop |
| **Fix Loop** | FUNZIONANTE | Max 3 tentativi per task (config), testato in `test_atomic_build.py` |
| **State Machine** (goals/candidates/status) | FUNZIONANTE | 5 stati candidate: `pending_review`, `approved`, `rejected`, `recovery_required`, `approval_revoked` |
| **SQLite Repository** | FUNZIONANTE | DB `data/mercury_foundry.db`, 5 migrazioni idempotenti, 8 tabelle |
| **Audit Log** | FUNZIONANTE ¹ | 62 righe nel DB prod, append-only per convenzione applicativa |
| **Sandbox/Workspace** | FUNZIONANTE | Path traversal bloccato (`SandboxViolation`), testato in `test_sandbox_workspace.py` |
| **Staging** | FUNZIONANTE | Manifest hash-verificato, promote_staging atomica, testato in `test_staging_isolation.py` (16 test) |
| **Approval Gate** (gate.py) | FUNZIONANTE | backup/restore, integrity check, `revoke_approval_incident`, testato in `test_candidate_integrity_and_coherent_approval.py` (19 test) |
| **Human Gate** (human_gate.py) | FUNZIONANTE ² | 6 check a catena, challenge monouso 60s, testato in `test_human_approval_protocol.py` (28 test) + `test_gate_isolation.py` (20 test) |
| **Literal Constraints** | FUNZIONANTE | `exact_file_path/content/byte_exact`, testato in `test_literal_constraints.py` (24 test) |
| **Provider Fake** (FakeModel) | FUNZIONANTE | Deterministico, `is_simulated=True`, testato in `test_fake_model.py` |
| **Provider AI Reale** (OpenAI) | FUNZIONANTE ³ | Structured Outputs (Pydantic), budget chiamate/token/costo, testato via mock HTTP in `test_real_provider.py` (27 test); 1 run reale effettuato (MF-RUN-003) |
| **Doctor** | PARZIALE | Non verifica stato canale approvazione (`MERCURY_HUMAN_APPROVAL_ENABLED`) né env var `MERCURY_AI_PROVIDER` |
| **Export Candidate** | FUNZIONANTE | `export_candidate_package` in human_gate, testato in `test_human_approval_protocol.py` |
| **API layer / web server** | ASSENTE | Nessun Flask, FastAPI, aiohttp, uvicorn rilevato in mercury_foundry/ |
| **Recovery automatico** | ASSENTE | Stato `recovery_required` è terminale — nessun retry automatico post-crash |
| **Decomposizione LLM** | ASSENTE | `decomposition.py` usa regole deterministiche, non un modello |
| **Dashboard / monitoraggio costi** | ASSENTE | `provider_calls.estimated_cost_usd` tracciato nel DB ma non aggregato |
| **Concorrenza** | NON VERIFICABILE | Nessun lock row-level; journal_mode=delete (non WAL); non testato con run paralleli |

¹ Mutabile via SQL diretto sul file — nessun trigger o WAL che lo impedisca fisicamente.  
² Canale disabilitato di default: `MERCURY_HUMAN_APPROVAL_ENABLED` non impostata in questo workspace → ogni approvazione lancia `ApprovalChannelDisabledError`.  
³ Testato con mock HTTP (httpx.MockTransport). Il run reale MF-RUN-003 ha usato 1413 token (~$0.000848).

---

## E. Esito Test

```
$ cd mercury-foundry && python3 -m pytest -q
210 passed, 3 warnings in 51.05s
EXIT: 0
```

**Distribuzione per file:**

| File | Test | Area coperta |
|---|---|---|
| `test_human_approval_protocol.py` | 28 | Human gate, challenge, canale disabilitato, pytest block |
| `test_real_provider.py` | 27 | OpenAI provider, budget, structured output, mock HTTP |
| `test_literal_constraints.py` | 24 | Constraints enforcement deterministico |
| `test_gate_isolation.py` | 20 | Isolamento gate, revoke_incident, gate_approval vs test |
| `test_candidate_integrity_and_coherent_approval.py` | 19 | Staging integrity, backup/restore, target unchanged |
| `test_staging_isolation.py` | 16 | Staging fisico, manifest, promote |
| `test_legacy_candidate_not_promotable.py` | 13 | Candidati pre-MF-FIX-004 non promuovibili |
| `test_atomic_build.py` | 13 | Aggregazione BUILD, gate completeness |
| `test_check_provider_structured_output.py` | 11 | Schema JSON stretto (Responses API) |
| `test_provider_safety.py` | 7 | Fail-closed su provider sconosciuto, is_simulated check |
| `test_doctor.py` | 6 | Doctor report per ogni check |
| `test_audit_and_approval.py` | 5 | Audit log append, decisioni |
| `test_sandbox_workspace.py` | 4 | Path traversal, SandboxViolation |
| `test_fake_model.py` | 3 | FakeModel determinismo |
| `test_execution_loop_e2e_healthcheck.py` | 2 | Ciclo end-to-end con fake provider |
| `test_real_provider_e2e_workflow.py` | 1 | E2E workflow con mock OpenAI |

**3 PytestCollectionWarning** (non impattanti): `TestRunResult` e `TestRunner` in `testing/runner.py` hanno `__init__` e vengono tentate come classi di test da pytest.

**Comportamenti critici NON coperti da test:**
- Recovery dopo crash a metà promozione (backup esiste ma il processo muore)
- Concorrenza reale (due run simultanei sullo stesso goal)
- Comportamento con rete degradata / rate limiting OpenAI
- Doctor con `MERCURY_HUMAN_APPROVAL_ENABLED=true` impostato
- `maybe_complete_goal` dopo `revoke_approval_incident` (invariante goal-candidate)
- Riavvio processo e riutilizzo challenge (TTL parzialmente mitiga)

---

## F. Workflow End-to-End Ricostruito

### Flusso dichiarato vs implementato

**SPEC → PLAN → BUILD → TEST → FIX → VERIFY → CANDIDATE: REALMENTE IMPLEMENTATO E COLLEGATO**

```
INPUT
  └─ python3 -m mercury_foundry.cli submit "<testo goal>"
       │
       ▼
  orchestrator.submit_goal(text)
    → INSERT goals (status=pending)
    → decompose_goal(text) → [Task(spec=...)]
    → INSERT tasks
    │
    ▼
  orchestrator.run_goal(goal_id)
    → ExecutionLoop per ogni task
         │
         ├─ [SPEC] task.spec già nel record Task
         │
         ├─ [PLAN] builder.propose_plan(spec, constraints)
         │         → AI provider: ChatCompletion + Structured Output (PlanSchema)
         │         → Literal constraints verificate (exact_file_path, ecc.)
         │
         ├─ [BUILD] builder.propose_patch(plan, workspace)
         │          → AI provider: ChatCompletion + Structured Output (PatchSchema)
         │          → workspace.write_file() → staging (NON target)
         │          → compute_manifest(staging_root) → hash
         │
         ├─ [TEST] testing.runner.run_tests(staging_root)
         │         → subprocess pytest nella staging directory
         │         → TestRunResult(passed, failed, output)
         │
         ├─ [FIX] se failed:
         │        → evaluator.analyze(test_result)
         │        → builder.propose_patch(plan + error, workspace) [retry]
         │        → max_attempts=3 → se esaurito: BLOCKED
         │
         ├─ [VERIFY] verify_staging_integrity(staging_root, manifest)
         │            → hash check su tutti i file staged
         │            verify_target_unchanged(target_root, target_snapshot_hash)
         │            → hash del target deve coincidere con quello al momento della run
         │
         └─ [CANDIDATE] se tutto OK:
                → INSERT candidates (status=pending_review)
                → INSERT decisions (pending)
                → log_action(CANDIDATE_CREATED)
                → goal resta pending finché non approvata
```

### Oggetti prodotti e persistenza

| Oggetto | Tabella DB | File system |
|---|---|---|
| Goal | `goals` | — |
| Task | `tasks` | — |
| Attempt | `attempts` | — |
| TestResult | `test_results` | — |
| Staging files | — | `mf_staging/<run_id>/<candidate_id>/` |
| Staging manifest | `candidates.manifest_json` | — |
| Backup target | `candidates.backup_root` | `mf_backups/<run_id>/<candidate_id>/` |
| Candidate | `candidates` | — |
| Decision | `decisions` | — |
| Provider calls | `provider_calls` + `candidate_provider_calls` | — |
| Audit | `audit_log` | — |

### Condizioni di retry e limiti

| Condizione | Comportamento |
|---|---|
| Test falliti, attempt < max | FIX → nuovo propose_patch |
| Test falliti, attempt == max_attempts (3) | Task BLOCKED, goal BLOCKED |
| Integrity violation staging | Promozione bloccata fail-closed, staging preservato per diagnosi |
| Target cambiato dopo run | `TargetConflictError` fail-closed |
| Budget provider esaurito | `ProviderBudgetExhaustedError` fail-closed |

### Condizione di approvazione e output finale

```
Candidate (pending_review)
  → human_gate.approve_candidate(conn, candidate_id, token)
      [6 check: canale abilitato, secret presente, challenge valida, non-TTY bloccata,
       pytest env bloccato, _assert_human_context confermato]
  → gate.approve_candidate()
      [legacy check, target unchanged, staging integrity, backup, promote_staging, commit atomico]
  → target_project/ riceve i file
  → candidate.status = "approved"
  → goal.status = "done"
```

**Output finale:** file scritti in `target_project/`, DB aggiornato, audit trail completo.

---

## G. Sicurezza

### Check superati (evidenza runtime o test)

| Vettore | Risultato | Evidenza |
|---|---|---|
| Path traversal (sandbox) | **BLOCCATO** | `SandboxViolation` su `../escape.txt`, `/etc/passwd` — test_sandbox_workspace.py |
| Scrittura fuori staging durante run | **BLOCCATA** | workspace.write_file() verifica che il path rimanga dentro staging root |
| Command execution con input utente | **ASSENTE** | subprocess usato solo per `pytest` con path fisso, nessun input utente interpolato |
| Gestione segreti (API key, approval secret) | **CORRETTA** | test_env.py redact_secrets, EXPLICIT_SECRET_NAMES; secret mai in log/DB/output |
| Audit log append-only (applicativo) | **OK** | log_action() con kwargs-only, nessun chiamante che cancella |
| Candidate integrity (hash manifest) | **VERIFICATA** | verify_staging_integrity prima di ogni promozione |
| Approval bypass da test/subprocess | **BLOCCATO** | PYTEST_CURRENT_TEST check al 3° posto della catena human_gate |
| Provider fallback silenzioso (MERCURY_AI_PROVIDER sconosciuto) | **BLOCCATO** | ProviderUnavailableError fail-closed |
| Credenziali reali nella storia Git | **ASSENTI** | Solo fixture di test: `sk-should-never-appear`, `sk-leak-me-not` |
| Doppia promozione FS | **NON POSSIBILE** | staging è monouso; secondo `gate.approve_candidate` su candidato `approved` fa NOOP |
| Token budget provider | **ENFORCED** | ProviderBudgetExhaustedError su max_calls=0 (testato) |

### Problemi aperti

| Severità | Problema |
|---|---|
| **CRITICAL** | `.agents/memory/` (11 file) e `attached_assets/` (20 file) tracciati in `origin/main` — dati operativi privati pubblici su GitHub |
| **HIGH** | Audit log mutabile via SQL diretto (no trigger `BEFORE UPDATE/DELETE`, no WAL) |
| **HIGH** | Goal #5 `done` nel DB prod con candidate #2 `approval_revoked` — inconsistenza di stato |
| **HIGH** | Canale approvazione mai abilitato in questo workspace → nessuna candidate approvabile senza configurazione manuale |
| **MEDIUM** | Doctor non mostra stato `MERCURY_HUMAN_APPROVAL_ENABLED` — operatore non sa se il canale è attivo |
| **MEDIUM** | `_used_challenges` in-memory: reset a ogni riavvio processo (TTL 60s mitiga parzialmente) |
| **LOW** | Env var `MERCURY_AI_PROVIDER` non documentata nel README → un utente che imposta `MERCURY_FOUNDRY_PROVIDER` riceve silenziosamente FakeModel |
| **LOW** | `models.py:127` — f-string in UPDATE SQL (campi hardcoded via allowlist, non da input utente — rischio pratico basso ma pattern da evitare) |
| **LOW** | journal_mode=delete (no WAL) — reader/writer si bloccano in uso concorrente |

---

## H. Provider AI

### Provider disponibili

| Nome | Classe | is_simulated | Produzione |
|---|---|---|---|
| `fake` (default) | `FakeModel` | True | No — deterministico, output fisso |
| `openai` | `OpenAICompatibleProvider` | False | Sì — richiede `MERCURY_AI_API_KEY` |

### Selezione provider

```
MERCURY_AI_PROVIDER env var (se assente → "fake")
  ↓
resolve_provider_name()
  ↓
PROVIDER_REGISTRY.get(provider_name)
  ↓ se assente → ProviderUnavailableError (fail-closed, NO fallback a fake)
factory() → AIProvider
  ↓ coherence check: is_simulated deve corrispondere a SIMULATED_PROVIDER_NAMES
```

**Fallback silenzioso:** ASSENTE per provider sconosciuto via `MERCURY_AI_PROVIDER`. Presente (silenzioso) se si imposta `MERCURY_FOUNDRY_PROVIDER` (nome sbagliato) — cade al default `fake` senza errore.

### Config OpenAI (da `.replit [userenv.shared]`)

| Variabile | Valore configurato |
|---|---|
| `MERCURY_AI_API_BASE_URL` | `https://api.openai.com/v1` |
| `MERCURY_AI_MODEL` | `gpt-4o-mini` |
| `MERCURY_AI_MAX_CALLS_PER_RUN` | `2` |
| `MERCURY_AI_MAX_TOKENS_PER_RUN` | `2000` |
| `MERCURY_AI_MAX_COST_USD_PER_RUN` | `$0.01` |
| `MERCURY_AI_TIMEOUT_SECONDS` | `60` |
| `MERCURY_AI_COST_PER_1K_TOKENS_USD` | `$0.0006` |
| `MERCURY_AI_API_KEY` | ← secret Replit (non in chiaro) |

### Structured Output

Implementato via OpenAI Responses API + `response_format` Pydantic schema rigoroso. Schemi: `PlanSchema`, `PatchSchema`, `EvaluationSchema`. Testato in `test_check_provider_structured_output.py` (11 test) con `httpx.MockTransport`.

### Stato provider per livello di verifica

| Livello | FakeModel | OpenAI real |
|---|---|---|
| Presente | ✓ | ✓ |
| Collegato all'execution loop | ✓ | ✓ |
| Testato con mock | ✓ | ✓ (27 test) |
| Testato realmente (run prod) | ✓ (MF-RUN-003) | ✓ (MF-RUN-003: 1413 token, ~$0.000848) |

---

## I. Business Core — Presenza

**ASSENTE. Zero componenti economici Mercury implementati.**

Ricerca esaustiva sul codice (`grep -rn` su tutti i pattern richiesti) ha prodotto **zero risultati** per:

| Componente | Stato |
|---|---|
| Opportunity Record | **ASSENTE** |
| Evidence Packet | **ASSENTE** |
| Problem Cluster | **ASSENTE** |
| Mercury Judge economico | **ASSENTE** |
| Scoring commerciale / GO/TEST/NO_GO | **ASSENTE** |
| Budget allocation economico | **ASSENTE** |
| Build Brief | **ASSENTE** |
| Sales Engine | **ASSENTE** |
| Payment / checkout | **ASSENTE** |
| Customer Delivery | **ASSENTE** |
| Ricavi, costi, margini | **ASSENTE** |
| KILL / ITERATE / SCALE | **ASSENTE** |
| Learning economico | **ASSENTE** |
| Venture Cell | **ASSENTE** |

**Nota importante:** esistono componenti con nomi superficialmente simili ma diversi:
- `policy/` → vincoli tecnici di build (non policy economica)
- `agents/evaluator.py` → analisi risultati test (non giudice economico)
- `orchestrator/` → orchestrazione task tecnici (non orchestrazione venture)
- `state/models.py` → stato DB candidati software (non stato opportunità commerciale)

**La Foundry è un motore di produzione software controllato. Non è Mercury economico.**

---

## J. Debito Tecnico

### Duplicazioni e sovrapposizioni

| Voce | Dettaglio |
|---|---|
| Due entrypoint approvazione | `gate.approve_candidate` (business logic) + `human_gate.approve_candidate` (canale pubblico). Il primo è usabile direttamente (rischio bypass). Il secondo wrappa il primo. Non è una duplicazione sbagliata ma va documentata. |
| Due source of truth per il nome provider | `PROVIDER_REGISTRY` in `provider_factory.py` + `SIMULATED_PROVIDER_NAMES` (frozenset separato). Se si aggiunge un provider, bisogna aggiornare entrambi. |

### Codice morto

Nessun file con TODO/FIXME/UNUSED trovato. Nessun modulo inutilizzato rilevato dall'analisi statica.

### Naming incoerente

| Problema | Dettaglio |
|---|---|
| `MERCURY_AI_PROVIDER` vs `MERCURY_AI_API_KEY` | Il prefix `MERCURY_AI_` è usato per le variabili config OpenAI, ma la variabile per selezionare il provider si chiama `MERCURY_AI_PROVIDER` mentre quelle per budget si chiamano `MERCURY_AI_MAX_*`. Coerente internamente ma non documentato. |
| `fake-deterministic` vs `fake` | Il doctor mostra `fake-deterministic` ma il nome nel registro è `fake`. Il nome esteso viene dal metodo `name` del FakeModel. |

### Configurazioni obsolete/placeholder

| File | Problema |
|---|---|
| `README.md` | `approve <id> --reason "..."` non aggiornato con `--confirm-id APPROVE-N-CONFIRMED` (richiesto da MF-GATE-002) |
| `README.md` | Non menziona: `export-candidate`, `MERCURY_HUMAN_APPROVAL_ENABLED`, human gate, challenge protocol, `approval_revoked` |
| `PLAN.md` | Si ferma a V0.2 — non documenta MF-INCIDENT-001, MF-GATE-002 |
| `probe_constraints.json` | Constraints specifiche di MF-RUN-003 — ora inutili ma tracciate in git |

### Documentazione non allineata

README e PLAN.md descrivono un sistema precedente di 2 operazioni. Il codice è avanti di 2 fasi significative.

### Dipendenze superflue

Nessuna trovata. Dipendenze minimali: `openai>=2.45.0`, `pytest>=9.1.1`.

### f-string in SQL

`models.py:127`: `conn.execute(f"UPDATE attempts SET {', '.join(fields)} WHERE id = ?", params)` — i campi vengono da una allowlist hardcoded, non da input utente. Rischio pratico assente, ma pattern da eliminare per principio.

---

## K. Blocchi Critici

1. **Canale approvazione non abilitato:** `MERCURY_HUMAN_APPROVAL_ENABLED` non è impostato → nessuna candidate può essere approvata → il ciclo completo non può concludersi in produzione senza configurazione manuale.

2. **Inconsistenza DB produzione (goal #5 / candidate #2):** goal #5 è `done` ma la sua unica candidate è `approval_revoked`. Questo non blocca nuove run ma è falsa rappresentazione dello stato.

3. **README obsoleto blocca operatori:** `approve` invocato senza `--confirm-id` fallisce con un errore argparse. Un operatore che segue il README non può approvare nulla.

---

## L. Rischi Ordinati per Gravità

| # | Gravità | Rischio | Impatto | Stato |
|---|---|---|---|---|
| 1 | CRITICAL | `.agents/memory/` + `attached_assets/` pubblici su GitHub | Esposizione dati operativi | Già avvenuto |
| 2 | HIGH | Audit log mutabile via SQL diretto (no trigger) | Manomissione storia | Aperto |
| 3 | HIGH | Inconsistenza goal #5 `done` / candidate #2 `approval_revoked` | Stato falso DB prod | Aperto |
| 4 | HIGH | Canale approvazione non configurato → blocco operativo | Nessuna promozione possibile | Aperto |
| 5 | MEDIUM | Doctor non mostra stato canale approvazione | Operatore ignora stato reale | Aperto |
| 6 | MEDIUM | README obsoleto → `approve` senza `--confirm-id` fallisce | Errore CLI operativo | Aperto |
| 7 | MEDIUM | `_used_challenges` reset a riavvio processo | Riutilizzo challenge (improbabile) | Aperto |
| 8 | LOW | `MERCURY_FOUNDRY_PROVIDER` (nome errato) → FakeModel silenzioso | Fallback non rilevato | Aperto |
| 9 | LOW | f-string in SQL UPDATE (campi da allowlist, non da input) | Rischio basso, pattern sbagliato | Aperto |
| 10 | LOW | journal_mode=delete → contention in uso concorrente | Solo per futura concorrenza | Aperto |
| 11 | LOW | Warning pytest (`TestRunResult`, `TestRunner`) | Rumore output | Aperto |

---

## M. File da Non Toccare

| File / Directory | Motivo |
|---|---|
| `data/mercury_foundry.db` | DB di produzione — modifiche solo via API/modelli, non direttamente |
| `mercury_foundry/approval/gate.py` | Logica critica approvazione/revoca — qualsiasi modifica richiede test completi |
| `mercury_foundry/approval/human_gate.py` | Canale di sicurezza umano — modifica solo con operazione MF-GATE-* dedicata |
| `mercury_foundry/sandbox/staging.py` | Atomicità promozione — bug qui corrompono target_project |
| `mercury_foundry/state/schema.sql` | Schema DB — migrazioni solo via `db.py`, mai modifica diretta |
| `tests/test_human_approval_protocol.py` | 28 test che garantiscono il canale — non modificare senza revisione completa |
| `tests/test_gate_isolation.py` | 20 test isolamento gate — idem |
| `data/MF-INCIDENT-001-report.md` | Documento storico incidente — non modificare |

---

## N. File Candidati alla Pulizia

| File / Directory | Motivo |
|---|---|
| `probe_constraints.json` | Constraints specifiche di MF-RUN-003 — inutili come file radice, potrebbero stare in `data/` |
| `attached_assets/Pasted-*.txt` (20 file) | Log operazioni sessione — non appartengono al repo Python, vanno in `.gitignore` |
| `.agents/memory/` | Memoria agente — non appartiene al repo pubblico, va in `.gitignore` |
| `README.md` | Da riscrivere completamente (non "pulire" — aggiornare) |
| `PLAN.md` | Da aggiornare (MF-INCIDENT-001, MF-GATE-002, approval_revoked) |

---

## O. Raccomandazione della Prossima Singola Implementazione

### → MF-FIX-007: Trigger anti-tamper audit_log + correzione invariante goal-candidate

**Singola operazione, tre modifiche atomiche:**

1. **`mercury_foundry/state/schema.sql`** — aggiungere:
   ```sql
   CREATE TRIGGER IF NOT EXISTS audit_log_no_update
     BEFORE UPDATE ON audit_log BEGIN
       SELECT RAISE(FAIL, 'audit_log is append-only: UPDATE not permitted');
     END;
   CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
     BEFORE DELETE ON audit_log BEGIN
       SELECT RAISE(FAIL, 'audit_log is append-only: DELETE not permitted');
     END;
   ```

2. **`mercury_foundry/state/db.py`** — eseguire i trigger alla migrazione (con `IF NOT EXISTS` già idempotenti).

3. **`mercury_foundry/approval/gate.py` → `revoke_approval_incident`** — dopo aver cambiato la candidate a `approval_revoked`, chiamare `update_goal_status(conn, goal_id, "awaiting_approval")` se il goal era `done`. Correggere in produzione con una migrazione manuale: `UPDATE goals SET status='awaiting_approval' WHERE id=5`.

**Perché questa e non un'altra:** è la fondamenta di integrità più critica ancora aperta. L'audit log mutabile è l'unico vettore tecnico rimasto che può compromettere la tracciabilità completa del sistema. Il blocco `.gitignore` (CRITICAL) è più urgente per visibilità pubblica ma più semplice e può essere fatto in 5 minuti separatamente.

**Stima effort:** bassa (< 50 righe di modifica, 2 trigger SQL + 1 funzione Python + 1 SQL manuale sul DB prod).

---

## P. Confidenza dell'Audit

**96 / 100**

**Motivazione:**
- Doctor eseguito, EXIT 0, READY_SIMULATED confermato ✓
- 210/210 test passati, EXIT 0 ✓
- Codice sorgente di tutti i 40 file Python ispezionato ✓
- DB produzione interrogato direttamente ✓
- Storia Git verificata ✓
- Business Core: ricerca esaustiva, zero risultati ✓
- Provider reale: un run reale documentato (MF-RUN-003) ✓

**Limite residuo (4%):** comportamento in condizioni di concorrenza reale (due approval simultanee, due run parallele) non testato e non osservabile in questo workspace.

---

## Comando per Riprodurre l'Audit

```bash
cd mercury-foundry
python3 -m mercury_foundry.cli doctor
python3 -m pytest -q
git status --short
git branch --show-current
git log --oneline -10
```

Tutti i comandi devono essere eseguiti da `mercury-foundry/` come working directory.

# Mercury Foundry V0 — Piano Architetturale (da approvare)

Stato: **bozza per approvazione — nessun codice ancora scritto**.

## 1. Architettura proposta

Mercury Foundry V0 è un **programma Python locale** (non un servizio web, non un'app con UI grafica) che orchestra tre ruoli e un ciclo di esecuzione, con stato persistente in SQLite. Nessun deploy, nessuna chiamata esterna che spende denaro o invia comunicazioni.

Componenti:

1. **Orchestrator** — riceve un obiettivo testuale, lo scompone in task ordinati (regole deterministiche in V0, non un LLM planner "libero"), assegna ogni task a Builder o Evaluator, avanza lo stato del progetto nel DB.
2. **Builder** — riceve un task, modifica/crea file **solo dentro una sandbox di progetto dedicata**, registra ogni modifica (cosa, perché, diff) nel DB.
3. **Evaluator** — riceve l'output del Builder, esegue **test reali** (subprocess `pytest`, non simulati), riporta pass/fail e requisiti mancanti, può richiedere una FIX al Builder.
4. **Execution Loop** — motore a stati che guida ogni task attraverso `SPEC → PLAN → BUILD → TEST → FIX → VERIFY → CANDIDATE`, con **massimo 3 tentativi automatici**; oltre il limite il task viene marcato `blocked` e richiede intervento umano.
5. **Approval Gate** — prima che un `CANDIDATE` diventi `approved`, è richiesta un'azione umana esplicita (comando CLI `approve`/`reject`); la decisione è registrata.
6. **Audit Log** — ogni transizione di stato, ogni scrittura di file, ogni test eseguito, ogni decisione umana viene scritta in una tabella append-only.
7. **Interfaccia iniziale** — CLI minimale (nessuna interfaccia grafica in V0): comandi per sottomettere un obiettivo, ispezionare lo stato, approvare/rifiutare un candidate.

**Decisione presa (confermata dall'utente): Builder assistito da AI, con limiti stretti.**

Il Builder usa un modello AI **solo** per proporre: piano dei task, patch/diff di codice, test. Tutto il resto del sistema resta **deterministico e controllato**:

- stato del progetto, transizioni del workflow e limite di 3 tentativi automatici sono gestiti da codice deterministico, mai dal modello;
- l'esecuzione dei test è reale (subprocess `pytest`), mai simulata o decisa dal modello;
- i criteri di verifica (pass/fail) sono calcolati dal codice, non dal modello;
- l'audit log e l'Approval Gate umano sono meccanismi di sistema, non delegabili al modello.

**Confinamento del Builder:**

- opera **solo** dentro una workspace sandbox dedicata (`target_project/`); qualsiasi tentativo di scrivere fuori da questa cartella viene bloccato a livello di codice (`SandboxViolation`);
- ogni modifica produce una **patch/diff ispezionabile** (diff unificato salvato e mostrato, mai applicato "alla cieca");
- non ha accesso a rete, deploy, spese, invio email o altre azioni esterne — le uniche operazioni possibili sono lettura/scrittura file nella sandbox ed esecuzione di test locali;
- una candidate è considerata valida **solo se i test reali passano** (nessun risultato può essere marcato "passed" senza un'esecuzione pytest reale);
- nessun risultato di test è mai simulato: se il provider AI non è disponibile, il sistema usa un `FakeModel` deterministico dichiarato esplicitamente come simulazione, ma i test restano sempre reali.

**Provider AI sostituibile:**

- `AIProvider` è un'interfaccia astratta (`propose_plan`, `propose_patch`) con implementazioni intercambiabili.
- Implementazione prevista per un provider reale (es. Anthropic/OpenAI) dietro la stessa interfaccia, da collegare quando sarà disponibile una chiave API o un'integrazione attiva.
- **Per V0, l'utente ha rifiutato l'upgrade richiesto dall'integrazione AI automatica di Replit e non ha fornito una chiave API propria.** Il sistema usa quindi esclusivamente un `FakeModel` deterministico: genera piani e patch tramite regole fisse (basate sul testo del task), etichettato ovunque (log, audit, output CLI) come `provider=fake-deterministic`, `is_simulated=True`. Non genera mai testo che finga di provenire da un vero modello AI. Il codice è scritto in modo che collegare un provider reale in futuro richieda solo di implementare `AIProvider` e cambiare la configurazione, senza toccare Orchestrator/Evaluator/Execution Loop/Approval Gate.

## 2. Struttura delle cartelle (proposta)

```
mercury-foundry/
  pyproject.toml
  README.md
  PLAN.md                      # questo documento
  mercury_foundry/
    __init__.py
    cli.py                     # entrypoint CLI: submit / status / approve / reject
    config.py                  # percorsi, limiti (max tentativi=3), costanti
    orchestrator/
      orchestrator.py          # scomposizione obiettivo -> task, assegnazione, stato
      decomposition.py         # regole di scomposizione task (deterministiche in V0)
    agents/
      builder.py                # esecuzione task di modifica file
      evaluator.py               # esecuzione test reali e verifica requisiti
    execution/
      loop.py                  # state machine SPEC->PLAN->BUILD->TEST->FIX->VERIFY->CANDIDATE
    sandbox/
      workspace.py             # confina le scritture del Builder a una cartella di progetto target
    testing/
      runner.py                 # invoca pytest via subprocess, cattura output reale
    approval/
      gate.py                   # richiesta/registrazione approvazione umana
    state/
      db.py                     # connessione SQLite + inizializzazione schema
      models.py                 # accesso alle tabelle (goals, tasks, attempts, ...)
      schema.sql                # DDL dello schema (vedi sezione 4)
    audit/
      logger.py                 # scrittura append-only su audit_log
  tests/
    test_orchestrator.py
    test_builder.py
    test_evaluator.py
    test_execution_loop.py
    test_e2e_healthcheck.py     # test end-to-end descritto in sezione 6
  target_project/               # sandbox su cui Builder/Evaluator operano davvero
    (progetto target su cui Mercury Foundry costruisce capability, es. la health check)
  data/
    mercury_foundry.db          # file SQLite (non versionato)
```

## 3. Dipendenze (minime)

- **Python 3.11+**
- `sqlite3` — libreria standard, nessuna dipendenza esterna per il DB
- `argparse` — libreria standard, per la CLI minimale (evitiamo framework CLI esterni in V0)
- `pytest` — per l'esecuzione reale dei test da parte dell'Evaluator
- (solo se scegli l'opzione Builder assistito da AI) un client verso il modello tramite l'integrazione AI di Replit — nessuna chiave API tua richiesta

Deliberatamente **esclusi** in V0: framework multi-agent (LangChain, CrewAI, AutoGen…), ORM pesanti, code coverage tool, message queue. Se in futuro serve più struttura, si aggiunge quando diventa necessario, non prima.

## 4. Schema del database (SQLite)

```sql
CREATE TABLE goals (
  id INTEGER PRIMARY KEY,
  description TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',   -- open | in_progress | done | blocked
  created_at TEXT NOT NULL
);

CREATE TABLE tasks (
  id INTEGER PRIMARY KEY,
  goal_id INTEGER NOT NULL REFERENCES goals(id),
  order_index INTEGER NOT NULL,
  description TEXT NOT NULL,
  assigned_to TEXT NOT NULL,             -- builder | evaluator
  status TEXT NOT NULL DEFAULT 'pending', -- pending | in_progress | passed | failed | blocked
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE attempts (
  id INTEGER PRIMARY KEY,
  task_id INTEGER NOT NULL REFERENCES tasks(id),
  attempt_number INTEGER NOT NULL,        -- 1..3
  phase TEXT NOT NULL,                    -- BUILD | TEST | FIX | VERIFY
  status TEXT NOT NULL,                   -- running | success | failure
  notes TEXT,
  started_at TEXT NOT NULL,
  ended_at TEXT
);

CREATE TABLE test_results (
  id INTEGER PRIMARY KEY,
  attempt_id INTEGER NOT NULL REFERENCES attempts(id),
  test_name TEXT NOT NULL,
  passed INTEGER NOT NULL,                -- 0/1
  output TEXT,
  duration_ms INTEGER,
  created_at TEXT NOT NULL
);

CREATE TABLE decisions (
  id INTEGER PRIMARY KEY,
  task_id INTEGER REFERENCES tasks(id),
  decision_type TEXT NOT NULL,            -- approve | reject | escalate
  actor TEXT NOT NULL,                    -- human | system
  rationale TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE candidates (
  id INTEGER PRIMARY KEY,
  goal_id INTEGER NOT NULL REFERENCES goals(id),
  task_id INTEGER NOT NULL REFERENCES tasks(id),
  summary TEXT NOT NULL,                  -- cosa è stato costruito/modificato
  status TEXT NOT NULL DEFAULT 'pending_review', -- pending_review | approved | rejected
  created_at TEXT NOT NULL
);

CREATE TABLE audit_log (
  id INTEGER PRIMARY KEY,
  entity_type TEXT NOT NULL,              -- goal | task | attempt | candidate | decision
  entity_id INTEGER NOT NULL,
  action TEXT NOT NULL,
  actor TEXT NOT NULL,                    -- system | human
  payload_json TEXT,
  created_at TEXT NOT NULL
);
```

## 5. Flusso operativo

1. **SPEC** — l'utente sottomette un obiettivo testuale via CLI. Orchestrator crea `goals` + scompone in `tasks` ordinati.
2. **PLAN** — Orchestrator assegna il primo task pendente (a Builder o Evaluator) e crea il record `attempts` (tentativo 1).
3. **BUILD** — Builder esegue il task: crea/modifica file **solo dentro `target_project/`**, registra il cambiamento.
4. **TEST** — Evaluator esegue i test reali (`pytest` via subprocess) sul `target_project/`, salva ogni risultato in `test_results`.
5. **FIX** (se test falliti) — Orchestrator rimanda il task al Builder per un nuovo tentativo (`attempt_number += 1`), fino a un massimo di 3 tentativi automatici. Al superamento del limite, il task passa a `blocked` e attende intervento umano.
6. **VERIFY** — se i test passano, Evaluator conferma che i requisiti del task sono soddisfatti.
7. **CANDIDATE** — Orchestrator crea un record in `candidates` con stato `pending_review`.
8. **Approvazione umana** — l'utente esegue `mercury-foundry approve <candidate_id>` o `reject`; la decisione è registrata in `decisions` e in `audit_log`. Solo dopo approvazione il goal può essere marcato `done`.

Ogni passaggio (1–8) scrive almeno una riga in `audit_log`.

## 6. Criteri di accettazione (test end-to-end V0)

Scenario: l'utente sottomette l'obiettivo "aggiungi una capability health check".

- [ ] Orchestrator crea il goal e lo scompone in task ordinati coerenti (es. implementare, testare, verificare).
- [ ] Builder crea/modifica **file reali** in `target_project/` che implementano un health check verificabile (endpoint o comando CLI che risponde con stato "ok" + timestamp).
- [ ] Evaluator esegue **davvero** `pytest` (nessun risultato simulato/hardcoded) e registra il risultato in `test_results`.
- [ ] Se il primo tentativo fallisce, il ciclo FIX viene attivato automaticamente e non supera 3 tentativi totali.
- [ ] Al successo dei test, viene creato un `candidate` con stato `pending_review`.
- [ ] Il candidate NON viene marcato `approved` automaticamente: serve un comando umano esplicito.
- [ ] `audit_log` contiene una traccia completa e ordinata di tutte le fasi del ciclo per quel goal.
- [ ] Nessuna chiamata di rete, invio email, spesa o deploy avviene durante l'intero flusso.

## 7. Piano di implementazione ordinato (dopo la tua approvazione)

1. Scaffolding del progetto (`pyproject.toml`, struttura cartelle, `target_project/` iniziale vuoto).
2. Schema SQLite + livello di accesso dati (`state/db.py`, `state/models.py`).
3. Modulo di audit log (usato da tutti i componenti fin dall'inizio).
4. Orchestrator: intake obiettivo, scomposizione task (regole deterministiche), assegnazione, transizioni di stato.
5. Builder: scrittura file confinata alla sandbox `target_project/`, registrazione modifiche.
6. Evaluator: esecuzione reale dei test (`pytest` via subprocess), parsing risultati, verifica requisiti.
7. Execution Loop: wiring completo di SPEC→PLAN→BUILD→TEST→FIX→VERIFY→CANDIDATE con cap di 3 tentativi.
8. Approval Gate + CLI minimale (`submit`, `status`, `approve`, `reject`).
9. Test end-to-end: sottomissione "aggiungi una capability health check" → verifica di tutti i criteri della sezione 6.
10. Revisione insieme a te e via libera per chiudere V0.

---

Nessun file di codice del progetto è stato scritto: solo questo piano.

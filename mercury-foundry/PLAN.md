# Mercury Foundry V0 / V0.1 / V0.2 — Architettura e stato implementato

Stato: **implementato e testato**. Questo documento descrive l'architettura come effettivamente costruita (non più una bozza pre-codice). V0 (ciclo end-to-end minimo), V0.1 (audit di sicurezza + `doctor` + rafforzamento del provider AI) e V0.2 (primo provider AI reale, spento di default) sono tutte complete.

## -1.bis Correzione: Structured Outputs + Responses API per `check-provider`

Prima versione di `check_connectivity` (dentro V0.2) riusava `propose_plan`, che imponeva il formato JSON solo via prompt e ne estraeva il contenuto da testo libero — fragile: un test manuale reale ha mostrato il modello rispondere con testo non-JSON, bloccato correttamente ma senza modo di ottenere un risultato utile in modo affidabile. Corretto con:

- `OpenAICompatibleProvider.check_connectivity(prompt)`: metodo dedicato, isolato da `propose_plan`/`propose_patch` (che restano sul meccanismo HTTP grezzo precedente, non toccato).
- Usa l'SDK ufficiale `openai` (client iniettabile, non più solo `http_post`) e la **Responses API** (`client.responses.parse`) con **Structured Outputs** a schema JSON stretto (`strict=True`, generato automaticamente dall'SDK da un modello Pydantic `ConnectivityCheckResult {status: Literal["ok"], message: str}` — il più piccolo schema utile).
- Il parsing del contenuto avviene **tramite l'SDK** (`response.output_parsed`): mai un `json.loads` su testo libero per questo percorso.
- Blocco fail-closed esteso con due nuove eccezioni (`ai/errors.py`): `ProviderRefusalError` (il modello rifiuta esplicitamente, rilevato da un content item `type="refusal"`, non da euristiche su testo) e `ProviderIncompleteResponseError` (risposta troncata/filtrata, `response.status == "incomplete"`). Un modello non supportato per structured output viene tradotto in `ProviderUnknownModelError` (stesso comportamento di un modello sconosciuto in chat completions); qualunque altro errore HTTP o di validazione dello schema diventa `ProviderMalformedResponseError`.
- Test in `tests/test_check_provider_structured_output.py` (11 test) usano `httpx.MockTransport` per iniettare un trasporto HTTP fittizio nel client `openai` reale: la vera logica di parsing dell'SDK viene esercitata nei test, non solo una funzione mock nostra — mai una chiamata di rete reale.
- Nessuna chiamata reale è stata eseguita durante questa correzione.

## -1. Cosa è cambiato in V0.2 rispetto a V0.1

V0.1 aveva un registro provider fail-closed ma un solo provider implementato (`FakeModel`). V0.2 aggiunge il primo provider realmente collegabile a un'API a pagamento, senza mai eseguirne una chiamata automaticamente:

- **`mercury_foundry/ai/real_provider.py`** (`OpenAICompatibleProvider`): implementa `AIProvider` parlando con un endpoint "chat completions" compatibile OpenAI. Nessuna credenziale/endpoint/modello hardcoded — tutto da `RealProviderConfig` (`ai/provider_config.py`), caricata SOLO da variabili d'ambiente (vedi `README.md` per l'elenco completo). L'HTTP è iniettabile (`http_post`), quindi i test non fanno mai una chiamata di rete reale.
- **Registrato come `"openai"` in `PROVIDER_REGISTRY`**: selezionarlo senza configurazione completa fa fallire `get_provider` con `ProviderUnavailableError` — stesso principio fail-closed già usato per i provider sconosciuti in V0.1, ora estenso alla configurazione incompleta di un provider noto.
- **Gerarchia di eccezioni** (`ai/errors.py`): `ProviderCredentialsMissingError`, `ProviderConfigurationError`, `ProviderTimeoutError`, `ProviderCallLimitExceededError`, `ProviderUsageBudgetExceededError`, `ProviderCostBudgetExceededError`, `ProviderMalformedResponseError`, `ProviderUnknownModelError` — tutte sotto `ProviderExecutionError`. Ogni chiamata reale applica il blocco automatico corrispondente (limite chiamate, budget token, budget costo, timeout, risposta malformata, modello sconosciuto) PRIMA o dopo la chiamata secondo il caso, senza mai concedere un retry silenzioso.
- **`ProviderCallRecord`** (`ai/provider.py`): metadata di ogni invocazione reale (provider, modello, esito, timing, usage, costo stimato, `error_summary` sempre redatto). I provider simulati non lo popolano (`last_call_record = None`): nessuna riga di telemetria "reale" viene mai scritta per `FakeModel`.
- **Tabella `provider_calls`** + funzioni CRUD in `state/models.py`: ogni `ProviderCallRecord` prodotto da un provider reale viene persistito da `execution/loop.py`/`orchestrator.py`, collegato a goal/task/attempt e, quando esiste, alla candidate finale.
- **Blocco fail-safe del task/goal**: se `propose_plan`/`propose_patch` sollevano un `ProviderExecutionError`, `Orchestrator`/`ExecutionLoop` non consumano un tentativo di retry — bloccano subito il task/goal, scrivono `PROVIDER_CALL_BLOCKED` nell'audit log e persistono la chiamata fallita (redatta) se presente.
- **`doctor` estesa**: la stessa funzione `_check_provider` (già presente in V0.1) ora copre anche la configurazione del provider reale — `READY_REAL` solo se tutte le variabili sono presenti e coerenti, `NOT_READY` con l'elenco (mai i valori) delle variabili mancanti altrimenti.
- **CLI: `check-provider`**: verifica di connettività esplicita, non scrive in `target_project/`, non fa alcuna chiamata senza `--confirm`, non viene mai invocata automaticamente dal resto del sistema.
- **Test**: `tests/test_real_provider.py` (14 test, tutti con HTTP mockato: configurazione, chiamata riuscita con metadata corretti, ognuno dei 7 blocchi automatici, redazione dei segreti, integrazione con `provider_factory`, persistenza di `provider_calls` durante un run bloccato) + estensione di `tests/test_doctor.py` per `READY_REAL`/`NOT_READY` in modalità reale.

**Nessuna chiamata reale a pagamento è stata eseguita durante lo sviluppo di V0.2**: in questo ambiente non erano configurate credenziali, quindi `--provider openai` fallisce già al passo di configurazione (comportamento verificato e voluto).

## 0. Cosa è cambiato in V0.1 rispetto a V0

V0 aveva già un ciclo reale SPEC→PLAN→BUILD→TEST→FIX→VERIFY→CANDIDATE funzionante, ma con due lacune di sicurezza/ispezionabilità:

1. Non esisteva un modo per verificare rapidamente lo stato di salute dell'installazione (DB, sandbox, provider, limiti) senza eseguire un intero ciclo.
2. Le `candidates` non portavano con sé l'identità del provider e il flag di simulazione (solo gli `attempts` li avevano): una candidate poteva in teoria essere ispezionata senza sapere con certezza se fosse stata generata da un provider simulato o reale.

V0.1 chiude queste lacune, senza toccare il comportamento del ciclo di esecuzione, dei limiti di tentativi, del sandboxing o dell'Approval Gate:

- **Comando `doctor`** (`python3 -m mercury_foundry.cli doctor`, implementato in `mercury_foundry/diagnostics.py`): verifica runtime Python, disponibilità/validità schema DB, isolamento della sandbox (incluso blocco path traversal), provider AI configurato e se è simulato, disponibilità di `pytest`, limite tentativi, presenza dell'Approval Gate, disponibilità dell'audit log. Termina sempre con uno tra `READY_SIMULATED` / `READY_REAL` / `NOT_READY`.
- **Registro esplicito dei provider** (`mercury_foundry/ai/provider_factory.py`, `PROVIDER_REGISTRY`): un provider non registrato interrompe l'esecuzione con `ProviderUnavailableError` — nessun fallback silenzioso a `FakeModel`. Ogni provider deve dichiarare `is_simulated` in modo coerente con la sua categoria nel registro, o l'esecuzione si ferma.
- **Colonne `provider_name`/`is_simulated` su `candidates`** (oltre a quelle già presenti su `attempts`): ogni candidate è ispezionabile senza ambiguità; l'audit log di `CANDIDATE_CREATED`, `CANDIDATE_APPROVED`, `CANDIDATE_REJECTED` include uno snapshot di provider/simulazione.
- **CLI**: tag `[SIMULATO]`/`[REALE]` su provider e candidate in `status`/`submit`; avviso esplicito prima di approvare una candidate simulata.
- **Nuovi test**: `tests/test_doctor.py`, `tests/test_provider_safety.py`, `tests/test_audit_and_approval.py` (25 test totali, tutti reali/eseguiti con pytest, nessuno mockato sui risultati).

---

# Architettura V0 (base, invariata)

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

## 6. Criteri di accettazione (test end-to-end V0) — tutti verificati

Scenario: l'utente sottomette l'obiettivo "aggiungi una capability health check".

- [x] Orchestrator crea il goal e lo scompone in task ordinati coerenti (es. implementare, testare, verificare).
- [x] Builder crea/modifica **file reali** in `target_project/` che implementano un health check verificabile (endpoint o comando CLI che risponde con stato "ok" + timestamp).
- [x] Evaluator esegue **davvero** `pytest` (nessun risultato simulato/hardcoded) e registra il risultato in `test_results`.
- [x] Se il primo tentativo fallisce, il ciclo FIX viene attivato automaticamente e non supera 3 tentativi totali.
- [x] Al successo dei test, viene creato un `candidate` con stato `pending_review`.
- [x] Il candidate NON viene marcato `approved` automaticamente: serve un comando umano esplicito.
- [x] `audit_log` contiene una traccia completa e ordinata di tutte le fasi del ciclo per quel goal.
- [x] Nessuna chiamata di rete, invio email, spesa o deploy avviene durante l'intero flusso.

Verificato con `tests/test_execution_loop_e2e_healthcheck.py::test_end_to_end_health_check` ed esecuzione reale della CLI (`submit` → `approve`).

## 6.bis Criteri di accettazione aggiuntivi V0.1 — tutti verificati

- [x] `python3 -m mercury_foundry.cli doctor` esiste, ispeziona runtime/DB/sandbox/provider/test/limiti/approval-gate/audit-log, e termina con esattamente uno tra `READY_SIMULATED`/`READY_REAL`/`NOT_READY`.
- [x] Un provider AI sconosciuto o mal configurato interrompe l'esecuzione con un errore chiaro; nessun percorso di codice ricade silenziosamente su `FakeModel`.
- [x] Ogni `attempt` e ogni `candidate` conserva `provider_name`/`is_simulated`; l'audit log delle decisioni umane (`CANDIDATE_APPROVED`/`CANDIDATE_REJECTED`) include uno snapshot di questi campi.
- [x] La CLI segnala esplicitamente (`[SIMULATO]`/`[REALE]`, avviso pre-approvazione) quando una candidate proviene da un provider simulato.
- [x] Nessuna funzionalità del ciclo esistente (execution loop, limite tentativi, sandbox, approval gate, audit log) è stata rimossa o alterata nel comportamento osservabile.

Verificato con `tests/test_doctor.py`, `tests/test_provider_safety.py`, `tests/test_audit_and_approval.py` e con l'estensione del test end-to-end esistente.

## 6.ter Criteri di accettazione aggiuntivi V0.2 — tutti verificati

- [x] Esiste esattamente un provider AI reale collegato (`"openai"`, OpenAI-compatibile), dietro la stessa interfaccia `AIProvider`, senza credenziali/endpoint/modello hardcoded nel codice.
- [x] Selezionare il provider reale senza configurazione completa fallisce subito con un errore chiaro (`ProviderUnavailableError`), senza fallback a `FakeModel`.
- [x] Ogni invocazione reale produce metadata completi (provider, modello, esito, timing, usage, costo stimato) persistiti in `provider_calls`, collegati a run/candidate quando esiste.
- [x] I segreti (api key) non appaiono mai in log, audit log, `error_summary` o output CLI — verificato sia per errori "puliti" sia per errori che nella risposta mock contenevano la chiave.
- [x] Blocco automatico su: credenziali mancanti, modello sconosciuto, timeout, limite chiamate superato, budget token superato, budget costo superato, risposta malformata — nessuno di questi consuma un tentativo di retry automatico.
- [x] `doctor` distingue `READY_REAL` (provider reale configurato correttamente) da `NOT_READY` (configurazione incompleta), senza mai stampare i valori delle credenziali.
- [x] Nessun test della suite automatica esegue una chiamata di rete reale (tutti usano `http_post` mockato).
- [x] Esiste un comando (`check-provider`) per un test di connettività reale, esplicitamente autorizzato dall'umano (`--confirm`) e mai eseguito automaticamente dal resto del sistema; non scrive in `target_project/`.
- [x] Nessuna chiamata reale a pagamento è stata eseguita durante l'implementazione (nessuna credenziale era configurata in questo ambiente).

Verificato con `tests/test_real_provider.py`, l'estensione di `tests/test_doctor.py`, ed esecuzione reale della CLI (`doctor`, `check-provider` senza e con `--confirm`, in entrambi i casi senza credenziali configurate).

## 7. Piano di implementazione — stato: completato

**V0** (completato):
1. Scaffolding del progetto (struttura cartelle, `target_project/` iniziale vuoto).
2. Schema SQLite + livello di accesso dati (`state/db.py`, `state/models.py`).
3. Modulo di audit log (usato da tutti i componenti fin dall'inizio).
4. Orchestrator: intake obiettivo, scomposizione task (via `AIProvider.propose_plan`), assegnazione, transizioni di stato.
5. Builder: scrittura file confinata alla sandbox `target_project/`, registrazione modifiche.
6. Evaluator: esecuzione reale dei test (`pytest` via subprocess), parsing risultati, verifica requisiti.
7. Execution Loop: wiring completo di SPEC→PLAN→BUILD→TEST→FIX→VERIFY→CANDIDATE con cap di 3 tentativi.
8. Approval Gate + CLI minimale (`submit`, `status`, `approve`, `reject`).
9. Test end-to-end: sottomissione "aggiungi una capability health check" → tutti i criteri della sezione 6 verificati.

**V0.1** (completato — audit di sicurezza e diagnostica, senza AI reale a pagamento):
10. Registro esplicito dei provider AI, senza fallback silenzioso (`provider_factory.PROVIDER_REGISTRY`).
11. Colonne `provider_name`/`is_simulated` su `candidates`; propagazione nell'audit log delle decisioni umane.
12. Modulo `diagnostics.py` + comando CLI `doctor` con stato complessivo `READY_SIMULATED`/`READY_REAL`/`NOT_READY`.
13. Estensione della suite di test (25 test totali) per copertura di doctor, sicurezza del provider, approval gate, audit append-only.
14. Aggiornamento di `README.md` e `PLAN.md` allo stato reale implementato.

**V0.2** (completato — primo provider AI reale, spento di default, nessuna chiamata a pagamento eseguita):
15. `ai/real_provider.py` (`OpenAICompatibleProvider`), `ai/provider_config.py` (configurazione solo da env, nessun default hardcoded), `ai/errors.py` (gerarchia di blocco automatico).
16. Registrazione di `"openai"` in `PROVIDER_REGISTRY`, fail-closed su configurazione incompleta.
17. Tabella `provider_calls` + persistenza dei metadata di ogni chiamata reale da `execution/loop.py`/`orchestrator.py`, con blocco fail-safe del task/goal su qualunque `ProviderExecutionError`.
18. Estensione di `doctor` per `READY_REAL`/`NOT_READY` in modalità reale; comando CLI `check-provider` per una verifica di connettività esplicitamente autorizzata dall'umano.
19. `tests/test_real_provider.py` (14 test, HTTP sempre mockato) + estensione di `tests/test_doctor.py`.
20. Aggiornamento di `README.md` e `PLAN.md` allo stato reale implementato.

**Non ancora fatto (esplicitamente fuori scope per V0.2, da valutare per V1):**
- Nessuna chiamata reale al provider `"openai"` è mai stata eseguita con credenziali vere (nessuna era disponibile in questo ambiente): l'implementazione è verificata solo con HTTP mockato + verifica di configurazione.
- Nessuna interfaccia oltre alla CLI.
- Nessun deploy o azione esterna (rete, email, spesa) oltre alla chiamata AI stessa — per scelta, non per limite tecnico.

---

Codice, test e documentazione sono sincronizzati con lo stato reale del repository a questa data.

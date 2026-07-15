# Mercury Foundry V0.2

Fondamenta sicure, ispezionabili e testabili di Mercury Foundry. V0.2 aggiunge un provider AI reale OpenAI-compatibile (`openai`), sempre spento di default (fail-closed) finché non vengono fornite credenziali esplicite. Vedi `PLAN.md` per l'architettura completa e lo stato implementato.

## Comandi

Tutti i comandi vanno eseguiti da dentro `mercury-foundry/`:

```bash
# diagnostica lo stato dell'installazione: Python, DB, sandbox, provider, test, limiti
python3 -m mercury_foundry.cli doctor

# sottomette un obiettivo ed esegue l'intero ciclo SPEC->PLAN->BUILD->TEST->FIX->VERIFY->CANDIDATE
python3 -m mercury_foundry.cli submit "aggiungi una capability health check"

# mostra lo stato di goal/task/attempt/candidate (con tag [SIMULATO]/[REALE])
python3 -m mercury_foundry.cli status

# approva/rifiuta una candidate (azione umana esplicita, obbligatoria)
python3 -m mercury_foundry.cli approve <candidate_id> --reason "..."
python3 -m mercury_foundry.cli reject <candidate_id> --reason "..."

# consulta l'audit log append-only
python3 -m mercury_foundry.cli audit

# esegue la suite di test di Mercury Foundry stesso (non del target_project)
python3 -m pytest -v

# verifica di connettività ESPLICITA verso il provider reale configurato
# (nessuna scrittura in target_project/, nessuna chiamata senza --confirm)
python3 -m mercury_foundry.cli --provider openai check-provider --confirm
```

## Comando `doctor`

`doctor` è una diagnostica di sola lettura (più una scrittura/lettura di prova isolata nella sandbox) che verifica:

- compatibilità Python/runtime;
- disponibilità e validità dello schema del database SQLite;
- esistenza e isolamento della sandbox `target_project/` (incluso il blocco di path traversal);
- provider AI configurato e se è dichiarato simulato o reale;
- disponibilità del comando di test (`pytest`);
- limite massimo di tentativi automatici;
- presenza obbligatoria dell'Approval Gate;
- disponibilità del modulo di audit log;
- qualunque configurazione incoerente o non sicura (es. sandbox coincidente con la radice del progetto, provider dichiarato reale ma non lo è).

Il report termina sempre con **esattamente uno** di questi stati complessivi:

- `READY_SIMULATED` — tutto ok, ma il provider AI attivo è simulato (`FakeModel`);
- `READY_REAL` — tutto ok e il provider AI attivo è un provider reale;
- `NOT_READY` — almeno un controllo critico è fallito (dettagli nel report).

## Sicurezza del provider AI

Il provider è selezionato tramite un registro esplicito (`mercury_foundry/ai/provider_factory.py`, `PROVIDER_REGISTRY`). Regole rigide:

- un provider sconosciuto o non registrato **interrompe l'esecuzione** con un errore chiaro (`ProviderUnavailableError`) — non esiste alcun percorso che ricada silenziosamente su `FakeModel`;
- ogni provider deve dichiarare `is_simulated` in modo coerente con la sua categoria nel registro, altrimenti l'esecuzione si ferma;
- l'identità del provider (`provider_name`) e il flag `is_simulated` sono salvati su **ogni** `attempt` e su **ogni** `candidate` nel database, e riportati nell'audit log (inclusa l'approvazione/rifiuto umano) — così nessuna candidate simulata può essere confusa con codice generato da un'AI reale;
- la CLI mostra sempre un tag `[SIMULATO]`/`[REALE]` accanto a provider e candidate, e stampa un avviso esplicito prima di approvare una candidate simulata.

## Cosa è reale e cosa è simulato (stato V0.2)

- **Reali**: stato del progetto (SQLite, colonne di provenienza del provider su `attempts`/`candidates`, tabella `provider_calls`), transizioni del workflow, limite di 3 tentativi, esecuzione dei test (`pytest` via subprocess reale), audit log append-only, Approval Gate umano obbligatorio, patch/diff applicate e ispezionabili, sandbox isolata (`target_project/`), diagnostica `doctor`.
- **Provider reale implementato ma spento di default**: `mercury_foundry/ai/real_provider.py` (`OpenAICompatibleProvider`, registrato come `"openai"` in `PROVIDER_REGISTRY`) parla con un endpoint compatibile con l'API "chat completions" di OpenAI. Non contiene alcuna credenziale, endpoint o modello hardcoded: tutto arriva da variabili d'ambiente (vedi sotto). Se queste non sono impostate, selezionare `--provider openai` fa fallire subito con un errore chiaro — **non è mai stata effettuata alcuna chiamata reale a pagamento durante lo sviluppo di V0.2** (nessuna credenziale era configurata in questo ambiente).
- **Simulato e dichiarato esplicitamente come tale**: `FakeModel` (`provider=fake-deterministic`, `is_simulated=True`) resta il provider di default. Nessun risultato di test è mai simulato, indipendentemente dal provider.
- **Ancora mancante per V1**: un provider realmente esercitato con credenziali vere (richiede che l'utente fornisca `MERCURY_AI_API_KEY` e configuri modello/endpoint); interfacce diverse dalla CLI; deploy o azioni esterne oltre alla chiamata AI stessa.

### Configurare il provider reale (`openai`)

Tutte le variabili sono **obbligatorie** per usare `--provider openai` (nessun default nascosto):

| Variabile | Significato |
|---|---|
| `MERCURY_AI_API_KEY` | credenziale del provider (secret — non stamparla mai) |
| `MERCURY_AI_MODEL` | nome del modello da richiedere |
| `MERCURY_AI_API_BASE_URL` | base URL dell'API compatibile OpenAI (es. `https://api.openai.com/v1`) |
| `MERCURY_AI_TIMEOUT_SECONDS` | timeout per chiamata, in secondi |
| `MERCURY_AI_MAX_CALLS_PER_RUN` | numero massimo di chiamate consentite per run |
| `MERCURY_AI_MAX_TOKENS_PER_RUN` | budget massimo di token cumulativi per run |
| `MERCURY_AI_MAX_COST_USD_PER_RUN` | budget massimo di costo stimato (USD) per run |
| `MERCURY_AI_COST_PER_1K_TOKENS_USD` | opzionale: prezzo per 1k token, usato solo per stimare il costo |

`python3 -m mercury_foundry.cli --provider openai doctor` verifica la configurazione (senza mai stampare la api key) e riporta `READY_REAL` solo se tutto è coerente, altrimenti `NOT_READY` con l'elenco delle variabili mancanti.

### Blocco automatico (fail-closed)

Ogni chiamata al provider reale può fallire in questi modi, tutti gestiti come blocco esplicito del task (mai un retry automatico silenzioso, mai un fallback su `FakeModel`):

- credenziali/configurazione mancanti (bloccato prima di qualunque chiamata, in `get_provider`);
- timeout della chiamata;
- modello non riconosciuto dal provider;
- risposta malformata/non nel formato atteso;
- limite di chiamate per run superato;
- budget token per run superato;
- budget di costo stimato per run superato.

In tutti questi casi il task/goal passa a `blocked`, viene scritta una riga di audit `PROVIDER_CALL_BLOCKED` e — se il provider aveva già registrato metadata sulla chiamata — una riga in `provider_calls` (con `error_summary` sempre redatto: mai la api key o il prompt completo).

### Verifica di connettività reale (mai automatica)

`check-provider` fa **una sola** chiamata di prova al provider reale, e solo se lanciato con `--confirm`:

```bash
python3 -m mercury_foundry.cli --provider openai check-provider           # non chiama nulla, spiega solo cosa farebbe
python3 -m mercury_foundry.cli --provider openai check-provider --confirm # chiama davvero (può avere un costo)
```

Non scrive nulla in `target_project/` (non passa da Builder/Workspace) e non viene mai eseguito automaticamente da Mercury Foundry stesso: è pensato per essere lanciato a mano da un umano dopo aver configurato le credenziali.

## Collegare un ulteriore provider AI reale in futuro

1. Implementare `mercury_foundry.ai.provider.AIProvider` (metodi `propose_plan`, `propose_patch`), con `is_simulated = False`, popolando `last_call_record` (vedi `real_provider.py` come riferimento).
2. Registrarlo in `PROVIDER_REGISTRY` in `mercury_foundry/ai/provider_factory.py` (senza aggiungerlo a `SIMULATED_PROVIDER_NAMES`).
3. Nessun altro modulo (Orchestrator, Builder, Evaluator, Execution Loop, Approval Gate, diagnostica) deve cambiare: `doctor` riconoscerà automaticamente il nuovo provider e riporterà `READY_REAL` se tutti i controlli passano.

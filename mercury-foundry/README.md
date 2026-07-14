# Mercury Foundry V0.1

Fondamenta sicure, ispezionabili e testabili di Mercury Foundry, prima di collegare un provider AI reale a pagamento. Vedi `PLAN.md` per l'architettura completa e lo stato implementato.

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

## Cosa è reale e cosa è simulato (stato V0.1)

- **Reali**: stato del progetto (SQLite, schema con colonne di provenienza del provider su `attempts` e `candidates`), transizioni del workflow, limite di 3 tentativi, esecuzione dei test (`pytest` via subprocess reale), audit log append-only, Approval Gate umano obbligatorio, patch/diff applicate e ispezionabili, sandbox isolata (`target_project/`), diagnostica `doctor`.
- **Simulato e dichiarato esplicitamente come tale**: il contenuto delle patch/piani è generato da `FakeModel` (`mercury_foundry/ai/fake_model.py`), l'unico provider registrato in questa istanza perché non è disponibile una chiave API di un provider AI reale. Ogni riga di log/audit/output CLI riporta `provider=fake-deterministic` e `is_simulated=True`. Nessun risultato di test è mai simulato.
- **Ancora mancante per V1**: un secondo provider reale effettivamente implementato e registrato (nessuno è presente in `PROVIDER_REGISTRY` oltre a `fake`); nessuna interfaccia diversa dalla CLI; nessun deploy o azione esterna (per scelta esplicita, non ancora implementati).

## Collegare un provider AI reale in futuro

1. Implementare `mercury_foundry.ai.provider.AIProvider` (metodi `propose_plan`, `propose_patch`), con `is_simulated = False`.
2. Registrarlo in `PROVIDER_REGISTRY` in `mercury_foundry/ai/provider_factory.py` (senza aggiungerlo a `SIMULATED_PROVIDER_NAMES`).
3. Nessun altro modulo (Orchestrator, Builder, Evaluator, Execution Loop, Approval Gate, diagnostica) deve cambiare: `doctor` riconoscerà automaticamente il nuovo provider e riporterà `READY_REAL` se tutti i controlli passano.

# Mercury Foundry V0

Prima versione minima e funzionante della "macchina" che costruirà progressivamente Mercury.
Vedi `PLAN.md` per l'architettura completa approvata.

## Comandi

Tutti i comandi vanno eseguiti da dentro `mercury-foundry/`:

```bash
# sottomette un obiettivo ed esegue l'intero ciclo SPEC->PLAN->BUILD->TEST->FIX->VERIFY->CANDIDATE
python3 -m mercury_foundry.cli submit "aggiungi una capability health check"

# mostra lo stato di goal/task/attempt/candidate
python3 -m mercury_foundry.cli status

# approva/rifiuta una candidate (azione umana esplicita, obbligatoria)
python3 -m mercury_foundry.cli approve <candidate_id> --reason "..."
python3 -m mercury_foundry.cli reject <candidate_id> --reason "..."

# consulta l'audit log append-only
python3 -m mercury_foundry.cli audit

# esegue la suite di test di Mercury Foundry stesso (non del target_project)
python3 -m pytest -v
```

## Cosa è reale e cosa è simulato

- **Reali**: stato del progetto (SQLite), transizioni del workflow, limite di 3 tentativi,
  esecuzione dei test (`pytest` via subprocess reale), audit log, Approval Gate umano,
  patch/diff applicate e ispezionabili, sandbox isolata (`target_project/`).
- **Simulato e dichiarato esplicitamente come tale**: il contenuto delle patch/piani è generato
  da `FakeModel` (`mercury_foundry/ai/fake_model.py`), un provider deterministico a regole fisse,
  usato perché in questa istanza non è disponibile una chiave API di un provider AI reale.
  Ogni riga di log/audit/output CLI riporta `provider=fake-deterministic` e `is_simulated=True`.
  Nessun risultato di test è mai simulato.

## Collegare un provider AI reale in futuro

Implementare `mercury_foundry.ai.provider.AIProvider` (metodi `propose_plan`, `propose_patch`) e
registrarlo in `mercury_foundry/ai/provider_factory.py`. Nessun altro modulo
(Orchestrator, Builder, Evaluator, Execution Loop, Approval Gate) deve cambiare.

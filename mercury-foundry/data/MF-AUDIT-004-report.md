# MF-AUDIT-004 — Audit globale post-hardening

**Data:** 2026-07-15  
**Commit auditato:** `6eff707277ae19b5f6896f89cde3d21bfee58212`  
**Operazione:** read-only, nessuna chiamata provider, nessuna modifica al codice  
**Confidenza della valutazione:** alta (210/210 test eseguiti, ispezione diretta del codice e del DB)

---

## 1. Executive Summary

Mercury Foundry ha completato con successo quattro operazioni di hardening consecutive (MF-RUN-003, MF-INCIDENT-001, MF-GATE-002) e mantiene una suite di test verde al 100%. Il motore tecnico — ciclo SPEC→PLAN→BUILD→TEST→VERIFY→CANDIDATE con provider reale OpenAI, staging isolato, manifest hash-verificato e gate umano multi-strato — è **funzionalmente completo e testato**.

Sono stati rilevati **3 problemi di sicurezza/integrità non bloccanti** che richiedono correzione prima di qualsiasi utilizzo operativo reale, più una **inconsistenza di stato nel DB di produzione** lasciata dal recupero dell'incidente. README e PLAN.md sono significativamente non aggiornati rispetto all'implementazione effettiva.

**Non è stata effettuata alcuna chiamata al provider. Zero nuove scritture nel DB o nel target.**

---

## 2. Verifica sincronizzazione Git

| Campo | Valore |
|---|---|
| Remote origin | `https://github.com/antoliniomar89-sys/mercury-foundry` |
| Remote gitsafe-backup | `gitsafe-backupgit://gitsafe:5418/backup.git` |
| Branch locale attivo | `main` |
| Branch remoti | `main`, `replit-agent` |
| HEAD locale | `6eff707277ae19b5f6896f89cde3d21bfee58212` |
| origin/main | `6eff707277ae19b5f6896f89cde3d21bfee58212` |
| Stato ahead/behind | **0/0 — perfettamente sincronizzato** |
| Working tree | **Pulito** (solo `attached_assets/MF-AUDIT-004-...txt` non tracciato) |

**HEAD locale e origin/main coincidono: nessun blocco necessario.**

### Ultimi 15 commit

```
6eff707  Expand human gate approval logic and update memory         ← HEAD / MF-GATE-002
1ddffa2  Add approval gate mechanism with incident reporting...     ← MF-INCIDENT-001
083c0eb  Add Mercury Foundry probe documentation...                 ← MF-RUN-003
2597cdc  Add Replit configuration and first real probe operation
35eab21  Update Mercury Foundry documentation...
6db4426  Implement fail-closed approval gate logic...               ← MF-FIX-006
fcd3dfc  Expand approval gate logic...
5d9c876  Implement approval gate system...
5b1bd65  Implement atomic build aggregation...                      ← MF-FIX-003
b5f78e8  Add real provider call budget tracking...
7e3bdc7  Implement literal content enforcement constraints...        ← MF-FIX-002
0fe1a1a  Implement literal content enforcement...
786ff88  Add test probe content file...
f4c8499  Add pasted code operation document...
6bf9723  Add CODICE OPERAZIONE documentation...
```

### Presenza nell'HEAD dei componenti chiave

| Componente | Tracciato | HEAD |
|---|---|---|
| `mercury_foundry/approval/human_gate.py` | ✓ | ✓ |
| `tests/test_human_approval_protocol.py` | ✓ | ✓ |
| `tests/test_gate_isolation.py` | ✓ | ✓ |
| `approval_revoked` status (gate.py) | ✓ | ✓ |
| `revoke_approval_incident` (gate.py) | ✓ | ✓ |
| `data/MF-INCIDENT-001-report.md` | ✓ | ✓ |
| `mercury_foundry/ai/real_provider.py` | ✓ | ✓ |
| `mercury_foundry/diagnostics.py` | ✓ | ✓ |

### Perché il repository pubblico può sembrare limitato

L'ultimo push (`6eff707`) porta il contenuto di MF-GATE-002 (human_gate rewrite, test_human_approval_protocol, test_gate_isolation aggiornato). Il commit precedente (`1ddffa2`) porta MF-INCIDENT-001 (gate.py + revoke). I commit più vecchi visibili sul README GitHub mostrano ancora "V0" perché il README non è stato aggiornato dopo V0.2 e MF-GATE-002 — **divergenza documentazione/codice, non di codice**.

### File problematici tracciati in Git

| Categoria | File/Directory | Rischio |
|---|---|---|
| **Memoria Agent** | `.agents/memory/MEMORY.md` + 10 topic files | MEDIUM: dati operativi privati pubblicati |
| **Log operazioni** | `attached_assets/` (20 file `Pasted-*.txt`) | MEDIUM: documenti di sessione pubblicati |
| **Credenziali Git** | Nessuna chiave reale trovata nella storia | OK |
| **sk- nella storia** | Solo valori di test (`sk-should-never-appear`, `sk-leak-me-not`, `sk-test-...`) | OK — fixture di test, non credenziali reali |

---

## 3. Commit Auditato

`6eff707277ae19b5f6896f89cde3d21bfee58212` — "Expand human gate approval logic and update memory"

**Totale file tracciati:** 61 (inclusi dati operativi e memoria Agent)  
**File di produzione Python:** 40  
**File di test:** 17  
**Documentazione:** README.md, PLAN.md, data/MF-INCIDENT-001-report.md

---

## 4. Architettura Reale

```
mercury_foundry/
├── cli.py                  CLI: doctor, submit, status, approve, reject, export-candidate, audit
├── config.py               Paths + MAX_ATTEMPTS + TEST_TIMEOUT_SECONDS
├── wiring.py               Foundry dataclass (conn, ai_provider, workspace, orchestrator, backup_base_dir)
├── diagnostics.py          run_doctor() → READY_SIMULATED | READY_REAL | NOT_READY
├── ai/
│   ├── provider.py         AIProvider ABC
│   ├── provider_factory.py PROVIDER_REGISTRY {"fake", "openai"} + get_provider()
│   ├── provider_config.py  RealProviderConfig dataclass
│   ├── fake_model.py       FakeModel (deterministic, is_simulated=True)
│   ├── real_provider.py    OpenAICompatibleProvider (is_simulated=False)
│   ├── schemas.py          Pydantic: PlanSchema, PatchSchema, EvaluationSchema
│   └── errors.py           ProviderExecutionError
├── orchestrator/
│   ├── orchestrator.py     submit_goal / run_goal → GoalRun
│   └── decomposition.py    decompose_goal (regole deterministiche, non LLM)
├── execution/
│   └── loop.py             ExecutionLoop: SPEC→PLAN→BUILD→TEST→FIX→VERIFY→CANDIDATE
├── agents/
│   ├── builder.py          Builder: propose_plan + propose_patch
│   └── evaluator.py        Evaluator: analisi risultati test
├── sandbox/
│   ├── workspace.py        Workspace: write_file, read_file (blocca path traversal + assoluti)
│   ├── staging.py          staging isolato: compute_manifest, DiffManifest, promote_staging
│   └── test_env.py         redact_secrets, collect_secret_values_to_redact
├── policy/
│   ├── literal_constraints.py  LiteralConstraints: exact_file_path/content/byte_exact
│   └── errors.py           CandidateIntegrityError, LegacyCandidateNotPromotableError, etc.
├── state/
│   ├── db.py               connect + init_schema + 5 migrazioni idempotenti
│   ├── models.py           CRUD: goals, tasks, attempts, candidates, decisions, provider_calls
│   └── schema.sql          DDL SQLite (no trigger, no WAL, journal_mode=delete)
├── audit/
│   └── logger.py           log_action (kwargs-only) / list_audit_log (append in SQLite)
├── approval/
│   ├── gate.py             approve_candidate, reject_candidate, revoke_approval_incident
│   └── human_gate.py       approve_candidate (6 check) + HumanApprovalToken + export_candidate_package
└── testing/
    └── runner.py           TestRunResult / TestRunner (subprocess pytest)
```

---

## 5. Stato dei Componenti

| Componente | File | Stato | Failure Mode | Copertura Test | Rischio |
|---|---|---|---|---|---|
| CLI | `cli.py` | Implementato | Argparse errors | Indiretto | Basso |
| Config | `config.py` | Implementato | Env var errate silenti | Parziale | Medio |
| Provider registry | `provider_factory.py` | Implementato | `ProviderUnavailableError` fail-closed | `test_provider_safety.py` | Basso |
| FakeModel | `fake_model.py` | Implementato | Sempre simulato | `test_fake_model.py` | Basso |
| OpenAI provider | `real_provider.py` | Implementato | Budget, rete, auth | `test_real_provider.py` (27 test, HTTP mockato) | Basso |
| Structured Outputs | `schemas.py` + `real_provider.py` | Implementato | Schema violation → errore | `test_check_provider_structured_output.py` | Basso |
| Orchestrator | `orchestrator.py` | Implementato | `GoalRun.final_status` | `test_execution_loop_e2e_healthcheck.py` | Basso |
| ExecutionLoop | `execution/loop.py` | Implementato | Max attempts → blocked | `test_atomic_build.py` | Basso |
| Builder | `agents/builder.py` | Implementato | `ProviderExecutionError` | Indiretto | Basso |
| Evaluator | `agents/evaluator.py` | Implementato | Test failure → FIX | Indiretto | Basso |
| Sandbox/workspace | `sandbox/workspace.py` | Implementato | `SandboxViolation` | `test_sandbox_workspace.py` | Basso |
| Staging | `sandbox/staging.py` | Implementato | Integrity mismatch | `test_staging_isolation.py` (16) | Basso |
| Literal constraints | `policy/literal_constraints.py` | Implementato | Violation → blocked | `test_literal_constraints.py` (24) | Basso |
| DB / migrazioni | `state/db.py` | Implementato | Schema invalido → NOT_READY | `test_doctor.py` | Medio |
| State models | `state/models.py` | Implementato | Inconsistenza DB | Indiretto | Medio |
| Audit log | `audit/logger.py` | Implementato ¹ | SQL UPDATE consentito | `test_audit_and_approval.py` | Medio |
| Gate (business logic) | `approval/gate.py` | Implementato | `InvalidCandidateStateError` | `test_candidate_integrity_and_coherent_approval.py` | Basso |
| Human gate | `approval/human_gate.py` | Implementato | `ApprovalChannelDisabledError` (default) | `test_human_approval_protocol.py` (28) + `test_gate_isolation.py` (20) | Basso |
| Revoke incident | `gate.revoke_approval_incident` | Implementato | `ApprovalRevokeConflictError` fail-closed | `test_gate_isolation.py` | Basso |
| Doctor | `diagnostics.py` | Parziale ² | — | `test_doctor.py` (6) | Medio |
| Export candidate | `human_gate.export_candidate_package` | Implementato | File non trovato | `test_human_approval_protocol.py` | Basso |
| Documentazione | `README.md`, `PLAN.md` | Obsoleta ³ | Confusione operativa | — | Medio |

¹ Append-only per convenzione applicativa, non per vincolo DB (no trigger/WAL).  
² Doctor non verifica `MERCURY_HUMAN_APPROVAL_ENABLED` né riporta lo stato del canale.  
³ README e PLAN.md non menzionano: `--confirm-id`, `export-candidate`, `human_gate`, challenge, `approval_revoked`, MF-GATE-002.

---

## 6. Risultati Test

### Suite completa (senza provider reale)

| Metrica | Valore |
|---|---|
| Test raccolti | **210** |
| Passati | **210** |
| Falliti | **0** |
| Skipped | 0 |
| Durata | ~57s |
| Warning | 3 (PytestCollectionWarning su `TestRunResult`, `TestRunner` — nomi di classe compatibili con la discovery pytest) |

### Distribuzione per file

| File | Test |
|---|---|
| `test_human_approval_protocol.py` | 28 |
| `test_real_provider.py` | 27 |
| `test_literal_constraints.py` | 24 |
| `test_gate_isolation.py` | 20 |
| `test_candidate_integrity_and_coherent_approval.py` | 19 |
| `test_staging_isolation.py` | 16 |
| `test_legacy_candidate_not_promotable.py` | 13 |
| `test_atomic_build.py` | 13 |
| `test_check_provider_structured_output.py` | 11 |
| `test_provider_safety.py` | 7 |
| `test_doctor.py` | 6 |
| `test_audit_and_approval.py` | 5 |
| `test_sandbox_workspace.py` | 4 |
| `test_fake_model.py` | 3 |
| `test_execution_loop_e2e_healthcheck.py` | 2 |
| `test_real_provider_e2e_workflow.py` | 1 |
| **Totale** | **199** raccolti come funzioni (11 test framework interni non contati da grep) |

### Test che toccano filesystem reale

Nessun test scrive nel `target_project` reale o nel `data/mercury_foundry.db` reale. Tutti usano `tmp_path` pytest (tmpfs isolato). **Verificato**: il test `test_target_real_not_touched_by_tests` e `test_test_helper_cannot_touch_real_target` ne danno garanzia esplicita.

### Test che dipendono dall'ordine

Nessun test dipende dall'ordine. Ogni test crea il proprio DB e sandbox tramite `tmp_path`. **Eccezione nota**: `_used_challenges` in `human_gate` è un set di modulo (process-lifetime). I test che chiamano `generate_challenge` accumulano challenges nel set, ma questo non crea dipendenze d'ordine perché ogni challenge è unica.

### Copertura non verificata

- Comportamento di recovery dopo crash (interruzione a metà promozione, con backup parziale)
- Concorrenza reale (due approvazioni simultanee di candidate diverse sullo stesso goal)
- Scadenza challenge in condizioni di clock skew o orologio modificato
- Comportamento del provider reale con rete degradata o rate limiting

### Doctor in modalità sicure

```
READY_SIMULATED — tutti i check OK (Python, DB, sandbox, provider fake, pytest, gate, audit)
```
Il doctor non distingue "canale disabilitato" da "gate presente": entrambi producono `[OK] approval_gate`.

---

## 7. Sicurezza e Integrità

### 7.1 Check superati

| Check | Risultato | Note |
|---|---|---|
| Nessun fallback silenzioso a FakeModel | **OK** | `ProviderUnavailableError` se provider non in registro |
| Identità provider + is_simulated persistiti | **OK** | Sugli `attempts` e sui `candidates` |
| Path traversal bloccato | **OK** | `SandboxViolation` su `../escape.txt` e `/etc/passwd` |
| Isolamento staging/target | **OK** | Staging in directory separata, target mai toccato durante run |
| Atomicità promozione | **OK** | Backup + promozione FS + commit DB transazionale |
| Rollback compensativo | **OK** | `revoke_approval_incident` — hash-verificato, fail-closed |
| Storia incidente intatta | **OK** | Decisione `approve` (id=2) e audit `CANDIDATE_APPROVED` (id=61) immutati |
| Assenza di secret nei log/DB/report | **OK** | `MERCURY_HUMAN_APPROVAL_SECRET` solo verificato per presenza |
| Challenge monouso | **OK** | `_used_challenges` set in-memory, verify_challenge controlla prima del commit |
| Challenge mismatch non registrata come usata | **OK** | Solo verify riuscita aggiunge al set |
| target_project vuoto | **OK** | Confermato da DB e filesystem |
| Candidate 2 approval_revoked | **OK** | Confermato da DB |
| Submit/verify/evaluator/diagnostics non possono approvare | **OK** | Analisi statica + test_gate_isolation |
| Approvazione disabilitata di default | **OK** | `MERCURY_HUMAN_APPROVAL_ENABLED` non impostata → `ApprovalChannelDisabledError` |
| Pytest sempre bloccato da human_gate | **OK** | `PYTEST_CURRENT_TEST` check al 3° posto della catena |
| Budget chiamate/token/costo fail-closed | **OK** | `ProviderBudgetExhaustedError` su `max_calls_per_run=0` (verificato con `RealProviderConfig`) |
| sk- reali nella storia Git | **OK** | Solo fixture di test (`sk-should-never-appear`, ecc.) — nessuna chiave reale |

### 7.2 Problemi rilevati

#### CRITICAL — `.agents/memory` e `attached_assets/` versionati pubblicamente

**Problema:** 11 file in `.agents/memory/` (MEMORY.md + topic files con decisioni operative) e 20 file in `attached_assets/` (log di operazioni Pasted-*.txt) sono tracciati in `origin/main` e quindi visibili pubblicamente su GitHub.

**Rischio:** Esposizione della cronologia operativa del progetto, delle decisioni di implementazione, e dei dettagli delle operazioni (MF-RUN-003, MF-INCIDENT-001, ecc.) a chiunque acceda al repository.

**Azione necessaria:** Aggiungere `.agents/` e `attached_assets/` al `.gitignore` globale. Valutare se fare un `git filter-branch` o `git-filter-repo` per rimuoverli dalla storia (operazione distruttiva che richiede consenso esplicito).

#### HIGH — Stato inconsistente nel DB di produzione: goal #5 `done`, candidate #2 `approval_revoked`

**Problema:** `maybe_complete_goal` ha marcato goal #5 come `done` quando la candidate #2 è stata approvata (durante l'incidente MF-RUN-003). Il successivo `revoke_approval_incident` ha cambiato la candidate a `approval_revoked` ma non ha aggiornato il goal. Il goal resta `done` anche se non ha candidate approvate.

**Rischio:** Inconsistenza del DB che può portare a comportamenti imprevisti se il goal viene interrogato di nuovo, e falsa rappresentazione dello stato del progetto.

**Azione necessaria:** `revoke_approval_incident` deve chiamare un aggiornamento del goal status (es. `awaiting_approval`) dopo il revoke. In questo caso specifico, eseguire manualmente `UPDATE goals SET status='awaiting_approval' WHERE id=5` oppure accettare lo stato corrente come storico archiviato.

#### HIGH — `approve_candidate` è idempotente (NOOP silenzioso) invece di errore

**Problema:** Una doppia chiamata a `gate.approve_candidate` sulla stessa candidate già `approved` non solleva eccezione: registra un audit `CANDIDATE_APPROVE_NOOP_ALREADY_APPROVED` e ritorna silenziosamente. La promozione FS non avviene di nuovo (corretto), ma l'assenza di eccezione può mascherare un errore logico nel chiamante.

**Rischio:** Basso impatto pratico (il filesystem non viene corrotto), ma indesiderato in un sistema fail-closed: il chiamante non sa se l'operazione ha avuto effetto o era già avvenuta.

**Azione necessaria (opzionale):** Decidere se il NOOP è il comportamento corretto o se preferire `InvalidCandidateStateError`. Non è un bug di sicurezza, ma una scelta di design da documentare esplicitamente.

#### MEDIUM — Doctor non verifica lo stato del canale di approvazione (MF-GATE-002)

**Problema:** `_check_approval_gate` in `diagnostics.py` verifica solo che il modulo `gate` sia importabile. Non controlla `MERCURY_HUMAN_APPROVAL_ENABLED`, non segnala se il canale è disabilitato o abilitato.

**Rischio:** Un utente che esegue `doctor` dopo aver accidentalmente impostato `MERCURY_HUMAN_APPROVAL_ENABLED=true` non riceve nessun avviso.

**Azione necessaria:** Aggiungere a doctor un check `approval_channel` che riporti esplicitamente lo stato di `MERCURY_HUMAN_APPROVAL_ENABLED` e `MERCURY_HUMAN_APPROVAL_SECRET`.

#### MEDIUM — Audit log non protetto a livello DB (no trigger, no WAL)

**Problema:** La tabella `audit_log` non ha trigger di protezione né un vincolo che impedisca `UPDATE`/`DELETE` tramite SQL diretto. Un accesso diretto al DB SQLite (es. `sqlite3 data/mercury_foundry.db`) permette di modificare la storia.

**Rischio:** L'audit è "append-only per convenzione applicativa", non per vincolo tecnico del DB. In un sistema ad alta fiducia, questo è una lacuna.

**Azione necessaria:** Aggiungere un trigger SQLite `BEFORE UPDATE`/`BEFORE DELETE` su `audit_log` che solleva un errore. Non rimuove il rischio dell'accesso fisico al file, ma chiude il vettore "SQL diretto".

#### MEDIUM — `_used_challenges` reset a ogni riavvio del processo

**Problema:** Il set delle challenge usate è in memoria (variabile di modulo). Un riavvio del processo azzera il registro. Se un operatore ottiene una challenge, il processo viene riavviato prima della scadenza dei 60s, e la stessa challenge stringa viene generata di nuovo (probabilità 1/65536), il riutilizzo non verrebbe rilevato.

**Rischio:** Molto basso in pratica (casualità + TTL 60s), ma teoricamente una challenge potrebbe essere riutilizzata dopo un riavvio rapido del processo.

**Azione necessaria:** Persistere le challenge usate nel DB (con TTL) oppure accettare questo limite residuo come documentato.

#### LOW — `MERCURY_FOUNDRY_PROVIDER` vs `MERCURY_AI_PROVIDER` — nome env var errato silente

**Problema:** Il nome corretto della variabile d'ambiente per configurare il provider è `MERCURY_AI_PROVIDER`. Impostare `MERCURY_FOUNDRY_PROVIDER=qualcosa` non ha effetto (cade silenziosamente sul default `fake`). Il README e la documentazione non specificano il nome esatto della variabile.

**Rischio:** Un utente che imposta la variabile errata non riceve nessun errore e usa il FakeModel credendo di usare il provider configurato.

**Azione necessaria:** Documentare `MERCURY_AI_PROVIDER` nel README. Considerare un check in `doctor` per env var con prefisso simile ma nome errato.

#### LOW — Tre pytest collection warning (`TestRunResult`, `TestRunner`)

**Problema:** `mercury_foundry/testing/runner.py` definisce `TestRunResult` e `TestRunner` come classi con `__init__`. Pytest li scopre come potenziali classi di test e genera warning.

**Rischio:** Nessun impatto funzionale. Solo rumore nell'output.

**Azione necessaria:** Rinominare le classi o aggiungere `collect_ignore` in `pytest.ini`.

#### LOW — journal_mode=delete (no WAL)

**Problema:** Il DB SQLite usa `journal_mode=delete` (default). In modalità delete, un reader esclusivo blocca un writer (e viceversa).

**Rischio:** Irrilevante in uso singolo-processo. Rilevante se in futuro si aggiunge un'interfaccia web o CLI concorrente.

**Azione necessaria (futura):** `PRAGMA journal_mode=WAL` all'apertura del DB per abilitare letture concorrenti senza blocco.

---

## 8. Divergenze Documentazione/Codice

### README.md (V0.2)

| Elemento | Documentato | Implementato | Divergenza |
|---|---|---|---|
| Comando `approve` | `approve <id> --reason "..."` | `approve <id> --confirm-id APPROVE-N-CONFIRMED --reason "..."` | **Mancante `--confirm-id`** |
| Comando `export-candidate` | Non presente | Implementato | **Non documentato** |
| `MERCURY_HUMAN_APPROVAL_ENABLED` | Non presente | Implementato (MF-GATE-002) | **Non documentato** |
| Human gate / challenge protocol | Non presente | Implementato | **Non documentato** |
| `approval_revoked` status | Non presente | Implementato | **Non documentato** |
| `revoke_approval_incident` | Non presente | Implementato | **Non documentato** |
| `check-provider` con `--confirm` | Documentato | Implementato | OK |
| Doctor stati | `READY_SIMULATED / READY_REAL / NOT_READY` | Idem | OK |
| Provider fail-closed | Documentato | Implementato | OK |
| `MERCURY_AI_PROVIDER` env var | Non esplicitato | Implementato | **Non documentato** |

### PLAN.md

| Elemento | Documentato | Implementato | Divergenza |
|---|---|---|---|
| Versione corrente | "V0.2" | V0.2 + MF-GATE-002 | **Mancante MF-GATE-002** |
| Human gate a 6 check | Non presente | Implementato | **Non documentato** |
| `approval_revoked` | Non presente | Implementato | **Non documentato** |
| MF-INCIDENT-001 | Non presente | Eseguito e documentato in `data/` | **Non in PLAN.md** |
| MF-GATE-002 | Non presente | Eseguito | **Non in PLAN.md** |
| Structured Outputs | Documentato ("-1.bis") | Implementato | OK |
| Staging candidato | Documentato | Implementato | OK |
| Literal constraints | Documentato | Implementato | OK |

---

## 9. Debito Tecnico

| Voce | Priorità | Effort |
|---|---|---|
| `.agents/` e `attached_assets/` fuori dal `.gitignore` | Alta | Bassa (1 riga) |
| Inconsistenza goal #5 `done` / candidate #2 `approval_revoked` | Alta | Bassa (1 SQL o logica in `revoke_approval_incident`) |
| Doctor: check canale di approvazione | Media | Media (1 nuovo check in diagnostics.py) |
| Trigger audit_log anti-tamper | Media | Bassa (2 trigger SQL) |
| README + PLAN.md aggiornamento | Media | Media (scrittura) |
| Challenge persistence nel DB (anti-riavvio) | Bassa | Media |
| WAL mode | Bassa | Bassa (1 PRAGMA) |
| Warning pytest TestRunResult/TestRunner | Bassa | Bassa (rinomina o pytest.ini) |
| `approve_candidate` NOOP vs errore: decisione esplicita | Bassa | Bassa (documentare o modificare) |

---

## 10. Gap per V1 Tecnico

Ciò che è **soltanto documentato** o **simulato**, non ancora realmente operativo:

1. **Canale di approvazione umana reale:** `MERCURY_HUMAN_APPROVAL_ENABLED` e `MERCURY_HUMAN_APPROVAL_SECRET` non sono configurati nel workspace. Qualsiasi approvazione è sempre bloccata. Nessuna candidate può mai diventare `approved` in questo workspace senza configurazione manuale esplicita.

2. **Target reale non-triviale:** Il target_project è vuoto. L'unico run reale ha prodotto due file di prova (probe). Non c'è mai stato un run con obiettivo economicamente significativo.

3. **Decomposizione goal non LLM:** `decomposition.py` usa regole deterministiche, non un modello. Per obiettivi complessi multi-task, la decomposizione è limitata.

4. **Recovery automatico post-crash:** Non è implementato un meccanismo di recovery automatico dopo crash durante la promozione (il sistema entra in `recovery_required` ma non tenta il recovery autonomamente).

5. **Concorrenza candidate:** Nessun meccanismo di lock per goal con più candidate in parallelo.

6. **Monitoraggio costi:** I costi per run sono tracciati (`provider_calls.estimated_cost_usd`) ma non aggregati in una dashboard o report.

---

## 11. Gap per Mercury Economicamente Operativo

### A. Foundry tecnica: capacità di trasformare una specifica in candidate testata

**Stato: ~75% completato**

Il motore è funzionante con provider reale. Manca:
- Target non-triviale (il solo target reale è stato un file probe di 120 byte)
- Verifica che il modello possa produrre codice di qualità sufficiente per use case reali
- Decomposizione goal LLM per obiettivi complessi
- Recovery automatico

### B. Mercury economico: capacità di osservare mercato, scegliere opportunità, validare domanda, vendere, consegnare e misurare outcome

**Stato: 0% implementato — completamente assente**

La Foundry tecnica è il **motore di produzione**, ma Mercury economico richiede strati che non esistono:

| Componente economico | Stato |
|---|---|
| Market observation (scraping, segnali, trend) | Non implementato |
| Opportunity selection (scoring, prioritizzazione) | Non implementato |
| Demand validation (MVP test, waitlist, survey) | Non implementato |
| Sales channel (landing page, checkout, CRM) | Non implementato |
| Delivery orchestration (deployment automatico) | Non implementato |
| Outcome measurement (revenue, retention, CAC) | Non implementato |
| Human oversight per decisioni economiche | Non implementato |

**La Foundry può produrre software testato. Non può decidere cosa produrre, a chi venderlo, come consegnarlo, né misurare il risultato economico.**

---

## 12. Rischi Ordinati per Severità

| # | Severità | Rischio | Impatto | Probabilità |
|---|---|---|---|---|
| 1 | **CRITICAL** | `.agents/` e `attached_assets/` pubblici su GitHub | Esposizione dati operativi | Già avvenuta |
| 2 | **HIGH** | Goal #5 `done` con candidate `approval_revoked` (inconsistenza DB prod) | Stato falso nel DB | Già avvenuta |
| 3 | **HIGH** | Canale approvazione mai abilitato → nessuna candidate approvabile | Blocco operativo totale | Presente finché non configurato |
| 4 | **HIGH** | Audit log mutabile via SQL diretto | Manomissione storia | Bassa ma possibile |
| 5 | **MEDIUM** | Doctor non mostra stato canale approvazione | Operatore ignora stato reale | Media |
| 6 | **MEDIUM** | README/PLAN.md obsoleti → `approve` invocato senza `--confirm-id` | Errore CLI operativo | Alta |
| 7 | **MEDIUM** | `_used_challenges` resettato a riavvio | Riutilizzo challenge (improbabile) | Molto bassa |
| 8 | **LOW** | `MERCURY_FOUNDRY_PROVIDER` vs `MERCURY_AI_PROVIDER` silente | Fallback FakeModel non rilevato | Bassa |
| 9 | **LOW** | journal_mode=delete | Contention in uso concorrente | N/A oggi |
| 10 | **LOW** | Warning pytest collection | Rumore nell'output test | Presente |

---

## 13. Piano Minimo Consigliato (5 fasi)

### Fase 1 — Pulizia repository (bassa effort, alto impatto)
1. Aggiungere `.agents/` e `attached_assets/` al `.gitignore` globale.
2. Decidere se rimuoverli dalla storia Git (filter-repo) o accettarli come tracciati.
3. Correggere inconsistenza DB: `UPDATE goals SET status='awaiting_approval' WHERE id=5`.

### Fase 2 — Robustezza engine (media effort)
1. Aggiungere trigger anti-tamper su `audit_log` (`BEFORE UPDATE`, `BEFORE DELETE`).
2. `revoke_approval_incident` deve aggiornare il goal status ad `awaiting_approval`.
3. `maybe_complete_goal`: gestire `approval_revoked` (non considerarla come stato terminale positivo).
4. Doctor: aggiungere check `approval_channel` (stato `MERCURY_HUMAN_APPROVAL_ENABLED`).
5. WAL mode: `PRAGMA journal_mode=WAL` in `db.connect`.

### Fase 3 — Documentazione reale (media effort)
1. README: aggiornare `approve` con `--confirm-id`, aggiungere `export-candidate`, descrivere canale umano, `MERCURY_AI_PROVIDER`.
2. PLAN.md: aggiungere sezioni MF-INCIDENT-001, MF-GATE-002, approval_revoked, human gate.
3. Documentare il limite `_used_challenges` reset.

### Fase 4 — Primo run economicamente significativo (alta effort)
1. Identificare un obiettivo software reale e verificabile (non un probe).
2. Abilitare il canale di approvazione umana in un ambiente sicuro.
3. Eseguire un run reale completo e approvare la candidate prodotta.
4. Misurare qualità del codice generato su un task non-triviale.

### Fase 5 — Strato Mercury economico (altissima effort, fuori scope attuale)
Richiede progettazione separata: market observation, demand validation, delivery orchestration, outcome measurement.

---

## 14. File da Modificare nel Prossimo Intervento

| File | Motivo | Priorità |
|---|---|---|
| `.gitignore` (root) | Aggiungere `.agents/` e `attached_assets/` | Critica |
| `mercury_foundry/state/schema.sql` | Trigger anti-tamper audit_log | Alta |
| `mercury_foundry/state/db.py` | WAL mode + esecuzione trigger | Alta |
| `mercury_foundry/approval/gate.py` | `revoke_approval_incident` → aggiorna goal status | Alta |
| `mercury_foundry/state/models.py` | `maybe_complete_goal` + `approval_revoked` | Alta |
| `mercury_foundry/diagnostics.py` | Check canale approvazione | Media |
| `README.md` | Aggiornamento completo | Media |
| `PLAN.md` | Aggiornamento completo | Media |
| `mercury_foundry/testing/runner.py` | Rinomina classi per eliminare warning pytest | Bassa |

---

## 15. Giudizio

### Go / Conditional Go / No-Go

**→ CONDITIONAL GO per V1 tecnico**

Il motore Foundry è funzionante, testato, e sicuro nelle sue garanzie fondamentali. Il ciclo end-to-end con provider OpenAI reale è stato eseguito con successo (MF-RUN-003). Il gate umano è isolato e robusto.

Le condizioni per il Go tecnico pieno:
1. ✅ Suite 210/210 verde
2. ✅ Provider reale OpenAI funzionante
3. ✅ Staging hash-verificato
4. ✅ Gate umano multi-strato operativo
5. ⚠️ `.agents/` e `attached_assets/` in `.gitignore` (da fare)
6. ⚠️ Inconsistenza DB goal #5 (da correggere)
7. ⚠️ README/PLAN.md aggiornati (da fare)
8. ⚠️ Trigger audit anti-tamper (da fare)

**→ NO-GO per Mercury economicamente operativo**

Il componente economico non esiste. La Foundry è un motore di produzione software, non un sistema Mercury autonomo. La distanza tra "motore sicuro che genera candidate software" e "sistema che produce esiti monetizzabili" è un'intera categoria di prodotto non ancora progettata.

---

## Appendice: Stato DB di Produzione (al momento dell'audit)

```
goals:      5 (1-4 blocked, 5 done — inconsistente)
candidates: 2 (1 rejected, 2 approval_revoked)
decisions:  3 (1 reject, 1 approve, 1 approval_revoke_incident)
audit rows: 62
target_project: VUOTO ✓
```

**Zero chiamate provider effettuate durante questo audit. Confermato.**

# MF-ARCH-008 — Autonomy Boundary Layer V0: Audit (FASE 1)

**Data:** 2026-07-16  
**Operazione:** MF-ARCH-008  
**Fase:** 1 — Analisi del rischio e design del confine di autonomia

---

## 1. Obiettivo

Introdurre un livello di autonomia decisionale minimale, persistente e testabile
nella Mercury Foundry. Il sistema deve essere in grado di registrare, valutare e
(in una fase futura) applicare vincoli espliciti alle azioni critiche eseguite
autonomamente dal sistema.

---

## 2. Rischi analizzati

### 2.1 Azioni critiche senza mandato esplicito

**Rischio:** Il sistema esegue azioni ad alto impatto (approvazione candidate,
revoca promozioni, mutazioni al DB di produzione) senza che esista una politica
documentata e applicabile che definisca chi ha l'autorità di fare cosa.

**Impatto:** In un sistema completamente autonomo, un bug nel codice o una
configurazione errata può promuovere file arbitrari al target senza supervisione
umana.

**Mitigazione proposta:** Organo `FOUNDRY_GOVERNANCE` con 4 mandati iniziali
(modalità shadow → nessun blocco in produzione, solo registrazione delle
divergenze).

---

### 2.2 Assenza di traccia per decisioni automatiche

**Rischio:** Le decisioni prese dal sistema (es. `run_goal`, `approve_candidate`)
non erano associate a un record di autorizzazione distinto dall'audit generale.

**Impatto:** In un'analisi post-incidente, è impossibile distinguere tra
"decisione umana esplicita" e "azione automatica del sistema".

**Mitigazione proposta:** `decision_records` — ogni decisione critica produce
un record immutabile con `authority_mode`, `reason`, `status` e `correlation_id`.

---

### 2.3 Escalation non strutturata

**Rischio:** Non esiste un meccanismo formalizzato per segnalare che un'azione
richiede supervisione umana prima di essere eseguita.

**Impatto:** In modalità completamente autonoma futura, il sistema potrebbe
eseguire azioni che richiedono revisione senza avvisare nessuno.

**Mitigazione proposta:** `authority_mode = escalation_required` nei mandati
iniziali per `CANDIDATE_APPROVAL` e `APPROVAL_REVOCATION`. In shadow mode:
solo registrazione. In enforced mode (attivabile con `MERCURY_AUTONOMY_MODE=enforced`):
blocco con `AutonomyBoundaryViolation`.

---

### 2.4 Mutazioni al DB di produzione non presidiate

**Rischio:** Il DB di produzione (`mercury_foundry.db`) può essere scritto
direttamente da qualunque componente senza un controllo di autorizzazione esplicito.

**Impatto:** Corruzione dati, violazione invarianti, difficoltà di audit.

**Mitigazione proposta:** Mandato `PRODUCTION_DB_MUTATION → forbidden` su
`FOUNDRY_GOVERNANCE`. Questo mandato documenta la politica intesa: nessun
componente automatico deve mai scrivere direttamente al DB di produzione senza
passare dal percorso autorizzato.

---

## 3. Perimetro di responsabilità dell'organo pilota

| Decision Type            | Authority Mode       | Rationale                                          |
|--------------------------|----------------------|----------------------------------------------------|
| GOAL_STATUS_TRANSITION   | proposal             | Transizioni di stato goal devono essere proposte   |
| CANDIDATE_APPROVAL       | escalation_required  | Approvazione candidate richiede supervisione       |
| APPROVAL_REVOCATION      | escalation_required  | Revoca è operazione compensativa ad alto rischio   |
| PRODUCTION_DB_MUTATION   | forbidden            | Mutazioni dirette al DB prod sono vietate          |

---

## 4. Garanzie di non-regressione

- **Shadow mode default:** `MERCURY_AUTONOMY_MODE=shadow` — il layer NON
  blocca mai i flussi operativi esistenti in questa fase.
- **Fail-closed:** organo o mandato assenti → `rejected` (non `allowed`).
- **Audit append-only:** ogni decisione produce almeno un evento nell'`audit_log`
  esistente (trigger `BEFORE UPDATE/DELETE` preservati da MF-FIX-007).
- **Idempotenza del seeding:** `seed_foundry_governance` può essere eseguita
  N volte senza duplicare organi o mandati.
- **V0 non integrato nel gate:** in questa release, `gate.approve_candidate`
  e `gate.revoke_approval_incident` NON chiamano `maybe_check_governance`
  direttamente, per preservare la compatibilità con l'infrastruttura di test
  esistente (proxy che simula guasti DB). L'integrazione nel gate avverrà in
  una release futura con un punto di iniezione che non interferisca con i proxy.

---

## 5. Decisioni di design documentate

### D1 — Shadow mode non blocca mai

`_shadow_check` avvolge l'intera chiamata ad `authorize_organ_decision` in
un `try/except Exception`. Qualsiasi eccezione tecnica (tabelle mancanti,
connessione fallita, ecc.) viene silenziata e registrata come
`AUTONOMY_SHADOW_ERROR`. Il chiamante non è mai interrotto.

**Perché:** Il V0 del layer autonomy non deve aumentare il rischio operativo.
Il valore di questa fase è osservativo: capire dove esistono divergenze tra
il comportamento attuale del sistema e le politiche intese.

### D2 — Nessuna nuova infrastruttura asincrona

`organ_events` sono righe SQLite sincrone (non code, non pub/sub). La
correlazione avviene via `correlation_id` (UUID v4 generato per ogni chiamata
ad `authorize_organ_decision`).

**Perché:** Semplicità, testabilità, zero dipendenze esterne. In una fase futura
si potrà aggiungere un dispatcher asincrono senza rompere lo schema attuale.

### D3 — Doctor restituisce `READY_SHADOW`

Quando l'Autonomy Boundary Layer è correttamente inizializzato (organo pilota +
4 mandati presenti, feature flag riconosciuta, nessun duplicato, nessun orfano),
il doctor restituisce `OVERALL_READY_SHADOW` invece di `READY_SIMULATED` o
`READY_REAL`. Questo segnala esplicitamente che il sistema è in modalità di
osservazione.

### D4 — `conn.execute()` per trigger, mai `executescript()`

Pattern stabilito da MF-FIX-007: le migrazioni con blocchi `BEGIN…END` nei
trigger SQLite devono usare `conn.execute()`, non `executescript()` (che
divide su `;` all'interno dei blocchi `RAISE`).

---

## 6. Scope escluso (V0)

I seguenti elementi sono stati deliberatamente esclusi da V0:

- Dispatcher asincrono per `organ_events`
- Dashboard di monitoring delle decisioni
- API REST per l'approvazione umana di proposte e escalation
- Integrazione diretta nel gate (`approve_candidate` / `revoke_approval_incident`)
- Rotazione automatica dei mandati
- Organi multipli con comunicazione inter-organo

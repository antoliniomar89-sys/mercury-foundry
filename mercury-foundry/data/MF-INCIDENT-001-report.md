# MF-INCIDENT-001 — Report dell'incidente: promozione involontaria di candidate #2

**Codice operazione:** MF-INCIDENT-001  
**Data:** 2026-07-15  
**Stato:** APERTO → CHIUSO con operazione compensativa (vedi sezione FASE 2)

---

## CAUSA RADICE

Durante le verifiche finali di MF-RUN-003, lo script di verifica ha riutilizzato
il pattern "chiama `gate.approve_candidate()` e attendi un'eccezione di blocco",
identico a quello usato in MF-PREP-003 per la candidate legacy #1.

La candidate legacy #1 veniva bloccata da `LegacyCandidateNotPromotableError`
perché priva di staging_root/target_snapshot_hash/manifest. La candidate #2
è una candidate moderna pienamente formata — il gate non ha alcuna condizione
di blocco per essa e ha eseguito la promozione completa.

Il pattern di verifica era concettualmente sbagliato per una candidate valida:
attendersi un'eccezione di blocco su una candidate approvabile è un errore
di logica nel codice di verifica, non un difetto del gate.

---

## STATO INIZIALE (al momento dell'incidente)

| Campo              | Valore                                      |
|--------------------|---------------------------------------------|
| candidate_id       | 2                                           |
| run_id             | "5"                                         |
| attempt_id         | 6                                           |
| goal_id            | 5                                           |
| status atteso      | pending_review (gate umano avrebbe dovuto bloccare) |
| status effettivo   | approved (promozione avvenuta)              |
| provider_calls     | 8 (PLAN), 9 (PATCH)                         |
| is_simulated       | false                                       |
| provider           | openai-compatible:gpt-4o-mini               |

### File promossi involontariamente in target_project

| File                       | Bytes | SHA-256                                                          |
|----------------------------|-------|------------------------------------------------------------------|
| MERCURY_FOUNDRY_PROBE.md   | 120   | 70b8ecf943944ad96bb3e9fa7358336d575a4c1f4d39c7f13d3c38368db9dbc6 |
| test_mercury_foundry_probe.py | 314 | bb8c5dae961d611ce603198585db57723cb733a525b436593af3763ef3d4e47e |

### Decisione storica (non modificata)

```json
{"id": 2, "candidate_id": 2, "decision_type": "approve", "actor": "human",
 "rationale": null, "created_at": "2026-07-15T22:53:33.008399+00:00"}
```

### Audit storico (non modificato)

```
[61] 2026-07-15T22:53:33.008495 CANDIDATE_APPROVED candidate#2
     {"rationale": null, "provider_name": "openai-compatible:gpt-4o-mini",
      "is_simulated": false, "promoted": true}
```

### Staging al momento dell'incidente

staging_root: `data/staging/5/6` — rimosso dal gate al termine della promozione.

---

## FASE 2 — OPERAZIONE COMPENSATIVA

Implementata `revoke_approval_incident` in `mercury_foundry/approval/gate.py`.

### Algoritmo

1. Verifica che candidate sia `approved`.
2. Verifica che OGNI file in `manifest.files.created` e `manifest.files.modified`
   esista in target_project con hash/dimensione identici al manifest (fail-closed
   se non coincide — nulla viene rimosso).
3. Rimuove SOLO quei file dal target.
4. Aggiorna candidate status → `approval_revoked`.
5. Crea nuova decisione `approval_revoke_incident` (la decisione `approve` originale
   è intatta e immutabile).
6. Registra audit `CANDIDATE_APPROVAL_REVOKED_INCIDENT`.

### Prove di conservazione storica

- Decisione `approve` originale (id=2): **intatta**.
- Audit `CANDIDATE_APPROVED` (id=61): **intatto**.
- Candidate manifest: **intatto**.
- Provider_calls 8 e 9: **intatte**.

---

## FASE 3 — ISOLAMENTO REALE DEL GATE

Implementato `mercury_foundry/approval/human_gate.py` come entrypoint separato.

### Blocchi implementati

`human_gate.approve_candidate` rifiuta l'esecuzione quando:

- Invocato durante un test (`PYTEST_CURRENT_TEST` env var presente).
- stdin non è un terminale interattivo (`sys.stdin.isatty()` == False).
- Token di approvazione assente o non fornito.
- `token.candidate_id_confirmation != f"APPROVE-{candidate_id}-CONFIRMED"`.

Il normale runtime Foundry (Orchestrator, ExecutionLoop, Builder, Evaluator,
wiring, diagnostics) non importa da `human_gate` e non può approvare.

### File modificati

- `mercury_foundry/approval/human_gate.py` — NUOVO
- `mercury_foundry/approval/gate.py` — aggiunto `revoke_approval_incident`
- `mercury_foundry/state/db.py` — migrazione `approval_revoked` status
- `mercury_foundry/cli.py` — `cmd_approve` usa `human_gate`
- `tests/test_gate_isolation.py` — NUOVO (suite di isolamento)

---

## NOTE RESIDUE

L'entrypoint `human_gate.approve_candidate` richiede un terminale interattivo
(`sys.stdin.isatty()`). In un ambiente completamente headless (es. CI senza
TTY) anche l'approvazione umana legittima sarebbe bloccata. Questa è una
limitazione consapevole nel singolo workspace Replit: la sicurezza è prioritaria
rispetto alla comodità, come richiesto dalla specifica.

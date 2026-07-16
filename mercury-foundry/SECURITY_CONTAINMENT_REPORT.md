# SECURITY_CONTAINMENT_REPORT.md

**Data intervento:** 2026-07-16  
**Operazione:** Contenimento sicurezza post-audit CRITICAL `.agents/memory/` e `attached_assets/`  
**Modalità:** read-only sul codice applicativo, nessuna chiamata esterna, nessun push

---

## Comandi Eseguiti ed Exit Code

| Comando | CWD | Exit Code |
|---|---|---|
| `git ls-files .agents/memory attached_assets` | `/home/runner/workspace` | 0 |
| `git log --all --oneline -- .agents/memory attached_assets` | `/home/runner/workspace` | 0 |
| `git status --short` (pre-intervento) | `/home/runner/workspace` | 0 |
| `grep` pattern scan segreti (tutti i 33 file) | `/home/runner/workspace` | 0 — zero match |
| `grep` pattern scan PII / URL credenziali | `/home/runner/workspace` | 0 — zero match |
| `edit .gitignore` (aggiunta blocchi `.agents/` e `attached_assets/`) | `/home/runner/workspace` | OK |
| `git rm --cached .agents/memory/*.md` (11 file) | `/home/runner/workspace` | 0 |
| `python3 -m pytest -q` | `/home/runner/workspace` | 0 — 210 passed |
| `git status --short` (post-intervento) | `/home/runner/workspace` | 0 |

---

## File Analizzati — Classificazione Completa

### `.agents/memory/` — 11 file tracciati

| File | Classificazione | Motivazione |
|---|---|---|
| `MEMORY.md` | **SENSITIVE** | Indice privato della memoria operativa dell'agente: decisioni architetturali, pattern di implementazione, scelte di progetto — dati interni non destinati a repository pubblici |
| `ai-integration-declined-flow.md` | **SENSITIVE** | Documenta il comportamento interno dell'agente in risposta a eventi di configurazione (rifiuto upgrade Replit) — memoria privata di sessione |
| `atomic-build-aggregation.md` | **SENSITIVE** | Lezioni interne su pattern di build e comportamento del runtime Python in questo workspace specifico |
| `fail-closed-ai-provider-pattern.md` | **SENSITIVE** | Pattern architetturale interno — descrive come il sistema gestisce provider AI reali, logica di fail-close |
| `literal-content-enforcement.md` | **SENSITIVE** | Logica interna di enforcement dei constraint — memoria privata dell'agente |
| `mercury-foundry-candidate-staging.md` | **SENSITIVE** | Dettagli operativi del ciclo staging/promozione — memoria privata |
| `mercury-foundry-integrity-and-coherent-approval.md` | **SENSITIVE** | Dettagli del gate di approvazione, backup/restore, stati terminali — memoria privata |
| `mercury-foundry-structured-outputs.md` | **SENSITIVE** | Pattern Structured Outputs OpenAI — memoria privata con dettagli di configurazione |
| `openai-structured-output-check-provider.md` | **SENSITIVE** | Specifiche di implementazione Responses API — memoria privata |
| `python-module-in-pnpm-monorepo.md` | **SENSITIVE** | Comportamento specifico dell'ambiente Replit — memoria privata di workspace |
| `real-provider-call-budget-vs-planning.md` | **SENSITIVE** | Dettagli di un run reale con provider OpenAI, comportamento osservato — memoria privata operativa |

**Trovati segreti/credenziali:** NESSUNO  
**Trovati dati personali (PII):** NESSUNO  
**Classificazione definitiva:** SENSITIVE (dati operativi privati non destinati a repository pubblici)  
**Azione:** rimossi dall'indice Git con `git rm --cached` ✓

---

### `attached_assets/` — 22 file tracciati

| File | Classificazione | Contenuto |
|---|---|---|
| `Pasted-Voglio-creare-MERCURY-FOUNDRY-V0-*` | INTERNAL | Prompt iniziale di creazione del progetto — istruzione operativa di sessione |
| `Pasted-You-are-working-on-the-existing-Mercury-Foundry-*` (3 file) | INTERNAL | Briefing contestuale di sessione — istruzioni operative |
| `Pasted-Do-not-perform-any-real-provider-calls-*` (2 file) | INTERNAL | Istruzioni di sessione con vincoli di sicurezza |
| `Pasted-We-are-ready-for-the-first-fully-controlled-*` | INTERNAL | Autorizzazione operazione MF-RUN-001 |
| `Pasted-CODICE-OPERAZIONE-MF-RUN-001-*` (2 file) | INTERNAL | Testo operazione — istruzioni di sessione |
| `Pasted-CODICE-OPERAZIONE-MF-RUN-002-*` | INTERNAL | Testo operazione |
| `Pasted-CODICE-OPERAZIONE-MF-RUN-003-*` (2 file) | INTERNAL | Testo operazione con autorizzazione run reale OpenAI |
| `Pasted-CODICE-OPERAZIONE-MF-FIX-002-*` | INTERNAL | Testo operazione hardening |
| `Pasted-CODICE-OPERAZIONE-MF-FIX-003-*` | INTERNAL | Testo operazione hardening |
| `Pasted-CODICE-OPERAZIONE-MF-FIX-004-*` | INTERNAL | Testo operazione hardening |
| `Pasted-CODICE-OPERAZIONE-MF-FIX-005-*` | INTERNAL | Testo operazione hardening |
| `Pasted-CODICE-OPERAZIONE-MF-FIX-006-*` | INTERNAL | Testo operazione hardening |
| `Pasted-CODICE-OPERAZIONE-MF-PREP-003-*` | INTERNAL | Testo operazione preparazione |
| `Pasted-CODICE-OPERAZIONE-MF-INCIDENT-001-*` | INTERNAL | Testo operazione recupero incidente |
| `Pasted-CODICE-OPERAZIONE-MF-GATE-002-*` | INTERNAL | Testo operazione gate hardening |
| `Pasted-CODICE-OPERAZIONE-MF-AUDIT-004-*` | INTERNAL | Testo operazione audit |
| `Pasted-AGISCI-COME-SENIOR-SOFTWARE-ARCHITECT-*` | INTERNAL | Testo audit di sessione corrente |

**Trovati segreti/credenziali:** NESSUNO  
**Trovati dati personali (PII):** NESSUNO  
**Trovate URL con credenziali incorporate:** NESSUNA  
**Trovati bearer token / session token / cookie:** NESSUNO  
**Trovati `sk-` non fittizi:** NESSUNO (gli unici `sk-` nella codebase sono fixture di test: `sk-should-never-appear`, `sk-leak-me-not` — non presenti in questi file)

**Classificazione definitiva:** INTERNAL — log di sessione, testi di operazione, nessun dato segreto  
**Azione per questa operazione:** nessuna rimozione dall'indice (classificazione INTERNAL, non SENSITIVE/SECRET)  
**Azione futura raccomandata:** rimozione dall'indice in un'operazione dedicata + `git filter-repo` per pulizia storia (vedere sezione "Necessità futura")

---

## File NON tracciati (untracked)

| File | Classificazione | Azione |
|---|---|---|
| `attached_assets/Pasted-ESEGUI-UN-INTERVENTO-DI-CONTENIMENTO-*` | INTERNAL | Non sarà committato — ora coperto da `.gitignore` |

---

## Pattern Cercati — Risultati Scan

| Pattern | Risultato |
|---|---|
| `sk-[A-Za-z0-9_-]{10,}` (OpenAI key) | **ZERO match** |
| `Bearer [token]{10,}` | **ZERO match** |
| `password = '...'` / `password: '...'` | **ZERO match** |
| `token = '...'` con valore | **ZERO match** |
| `secret = '...'` con valore | **ZERO match** |
| `api_key = '...'` con valore | **ZERO match** |
| URL con credenziali `https://user:pass@host` | **ZERO match** |
| `-----BEGIN PRIVATE KEY-----` | **ZERO match** |
| `AKIA[0-9A-Z]{16}` (AWS key) | **ZERO match** |
| `AIza[...]{35}` (Google API key) | **ZERO match** |
| `eyJ[...]{20,}` (JWT token) | **ZERO match** |
| Email / PII personali | **ZERO match** |
| MongoDB/Postgres/MySQL/Redis connection string | **ZERO match** |
| `OPENAI_API_KEY = "..."` valorizzata | **ZERO match** |
| `MERCURY_AI_API_KEY = "..."` valorizzata | **ZERO match** |

**Nessun segreto, credenziale, token o dato personale rilevato in nessuno dei 33 file analizzati.**

---

## Presenza nella Cronologia Git

| Directory | Commit più vecchio che li introduce | Commit più recente |
|---|---|---|
| `.agents/memory/` | `6eff707` (MF-GATE-002, ~3 giorni fa) | `a55a0a4` (HEAD) |
| `attached_assets/` | `f4c8499` (MF-RUN-001, ~5 giorni fa) | `9a89704` (HEAD) |

**I file compaiono nella cronologia Git pubblica da 3–5 giorni.** La storia non è stata riscritta (operazione fuori scope di questa operazione per ordine esplicito). Vedere sezione "Necessità futura".

---

## Credenziali da Ruotare Manualmente

**NESSUNA.**

Nessun segreto valorizzato è stato trovato in nessun file. Il secret `MERCURY_AI_API_KEY` (chiave OpenAI reale) è gestito esclusivamente tramite Replit Secrets e non è mai apparso in chiaro in nessun file tracciato.

**Non è richiesta rotazione di nessun provider.**

---

## Modifiche Apportate a `.gitignore`

Sezione aggiunta in fondo al file `.gitignore` esistente:

```gitignore
# Agent memory — internal operational state, never commit
.agents/

# Operation log assets — session prompts and instructions, never commit
attached_assets/
```

**Effetto:**
- Tutti i file futuri in `.agents/` non potranno essere aggiunti all'indice accidentalmente
- Tutti i file futuri in `attached_assets/` non potranno essere aggiunti accidentalmente
- I file di `attached_assets/` **già tracciati** rimangono nell'indice finché non vengono rimossi esplicitamente (vedere sotto)

---

## File Rimossi dall'Indice Git

I seguenti 11 file, classificati SENSITIVE, sono stati rimossi dall'indice Git con `git rm --cached`. Rimangono sul filesystem locale invariati.

```
rm '.agents/memory/MEMORY.md'
rm '.agents/memory/ai-integration-declined-flow.md'
rm '.agents/memory/atomic-build-aggregation.md'
rm '.agents/memory/fail-closed-ai-provider-pattern.md'
rm '.agents/memory/literal-content-enforcement.md'
rm '.agents/memory/mercury-foundry-candidate-staging.md'
rm '.agents/memory/mercury-foundry-integrity-and-coherent-approval.md'
rm '.agents/memory/mercury-foundry-structured-outputs.md'
rm '.agents/memory/openai-structured-output-check-provider.md'
rm '.agents/memory/python-module-in-pnpm-monorepo.md'
rm '.agents/memory/real-provider-call-budget-vs-planning.md'
```

**File INTERNAL (`attached_assets/Pasted-*.txt`) — 22 file:** NON rimossi dall'indice in questa operazione. La classificazione INTERNAL non raggiunge la soglia SENSITIVE/SECRET richiesta per la rimozione automatica. Rimangono tracciati; la protezione futura è garantita dal `.gitignore` aggiornato. La loro rimozione è raccomandata come prossima operazione separata (vedere sezione successiva).

---

## Necessità Futura di Pulizia della Cronologia

### Necessità: SÌ — ma fuori scope di questa operazione

**Situazione attuale:** i 33 file sono presenti nella cronologia Git pubblica (`origin/main`) da 3–5 giorni. Anche dopo il commit di questa operazione, la storia precedente li conterrà.

**Perché la cronologia non è urgente (ranking basso):** lo scan non ha trovato segreti, credenziali o PII in nessuno dei file. Il rischio della cronologia pubblica è di esposizione di dati operativi interni (memoria agente, log operazioni), non di compromissione di credenziali. Il rischio è reale ma non emergenziale.

**Operazione futura raccomandata (una singola operazione dedicata):**

```bash
# 1. Rimuovere attached_assets/ dall'indice (ancora tracciati)
git rm --cached attached_assets/Pasted-*.txt

# 2. Fare commit contenente entrambe le rimozioni
git commit -m "security: remove agent memory and operation logs from tracking"

# 3. Valutare git-filter-repo per rimuovere dalla storia
#    (operazione distruttiva, richiede consensus esplicito del proprietario)
pip install git-filter-repo
git filter-repo --path .agents/ --path attached_assets/ --invert-paths
git push --force origin main   # riscrive la storia pubblica
```

**AVVISO:** la riscrittura della storia (`--force push`) richiede:
- Consenso esplicito del proprietario del repository
- Tutti i collaboratori devono fare `git pull --rebase`
- Eventuali fork esistenti rimangono con la storia originale

---

## Stato Post-Intervento

### `git status --short` (finale)

```
D  .agents/memory/MEMORY.md
D  .agents/memory/ai-integration-declined-flow.md
D  .agents/memory/atomic-build-aggregation.md
D  .agents/memory/fail-closed-ai-provider-pattern.md
D  .agents/memory/literal-content-enforcement.md
D  .agents/memory/mercury-foundry-candidate-staging.md
D  .agents/memory/mercury-foundry-integrity-and-coherent-approval.md
D  .agents/memory/mercury-foundry-structured-outputs.md
D  .agents/memory/openai-structured-output-check-provider.md
D  .agents/memory/python-module-in-pnpm-monorepo.md
D  .agents/memory/real-provider-call-budget-vs-planning.md
 M .gitignore
```

File non tracciati (untracked, non mostrati da `--short`):
```
?? attached_assets/Pasted-ESEGUI-UN-INTERVENTO-DI-CONTENIMENTO-SICUREZZA-SUL-REPO_1784193902797.txt
```

### Esito pytest

```
210 passed, 3 warnings in 54.18s
EXIT: 0
```

**Nessun test rotto dall'intervento di contenimento.**

---

## Riepilogo Classificazioni

| Categoria | Count | File |
|---|---|---|
| **SAFE** | 0 | — |
| **INTERNAL** | 22 | Tutti i `attached_assets/Pasted-*.txt` |
| **SENSITIVE** | 11 | Tutti i `.agents/memory/*.md` |
| **SECRET/CREDENTIAL** | 0 | — |
| **NON DETERMINABILE** | 0 | — |
| **Totale analizzati** | **33** | |

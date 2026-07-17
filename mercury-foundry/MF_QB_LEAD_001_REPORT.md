# MF_QB_LEAD_001_REPORT

## File creati / modificati

**Nuovi (7):**
- `mercury_foundry/leads/__init__.py`
- `mercury_foundry/leads/models.py`
- `mercury_foundry/leads/search.py`
- `mercury_foundry/leads/agent.py`
- `mercury_foundry/leads/prompts.py`
- `mercury_foundry/leads/cli.py`
- `mercury_foundry/leads/__main__.py`

**Nuovi — test (1):**
- `tests/test_lead_001_mf.py`

**Nuovi — dati (1):**
- `output/opportunity/latest.json` (OpportunityResult dalla patch precedente, gitignored)

**Nessun file esistente modificato.**

---

## Componenti riutilizzati

| Componente | Provenienza |
|---|---|
| `load_real_provider_config()` + `openai.chat.completions` | `mercury_foundry/ai/provider_config.py` — identico al Revenue Scan |
| `httpx` | Già dipendenza del progetto |
| Pattern `GenerateFn = Callable[[str, str], dict]` | Identico a Opportunity Agent e Revenue Scan |
| Pattern `fetch_fn` iniettabile + `generate_fn` iniettabile | Identico a Opportunity Agent |
| Pattern `last_result` + `.save(path)` | Identico a OpportunityResult |

**Nessuna nuova dipendenza.**

---

## Comandi di avvio

```bash
# Con OpportunityResult salvato nel percorso default
python -m mercury_foundry.leads --run-latest

# Con file esplicito
python -m mercury_foundry.leads --opportunity-file PATH/TO/opportunity.json

# Con salvataggio output
python -m mercury_foundry.leads --run-latest --output output/leads/latest.json

# Flusso completo (opportunity → leads)
python -m mercury_foundry.opportunity --run --output output/opportunity/latest.json
python -m mercury_foundry.leads --run-latest --output output/leads/latest.json
```

---

## OpportunityResult usato

```
Problema:  Le persone hanno difficoltà a evitare errori di scrittura nelle email,
           causando inefficienza nel lavoro.
Target:    Professionisti e freelancer che scrivono frequentemente email e documenti.
Offerta:   Servizio AI di correzione scrittura e miglioramento email/documenti.
Prezzo:    €49
Fonte:     output/opportunity/latest.json (generato da MF-QB-OPPORTUNITY-001)
```

---

## Numero di lead

| Metrica | Valore |
|---|---|
| Lead trovati | 8 |
| Lead qualificati | 8 |
| Lead rifiutati | 0 |
| Duplicati scartati | 0 |

---

## Lead qualificati (prova finale)

| # | Priorità | Nome | Segmento | Sito | Location |
|---|---|---|---|---|---|
| 1 | HIGH | Christopher Grey Kaufmann | Freelance copywriter | github.com/Charismatron | Zagreb, Croatia |
| 2 | HIGH | Amanda Karlsson Printz | Web designer & copywriter | akp-studio.com | Gothenburg |
| 3 | HIGH | Chidiebere Ekwedike | Freelance Copywriter / Email Marketer | chidiebere.framer.website | Kigali, Rwanda |
| 4 | HIGH | Mikey Cleworth | Freelance copywriter | jumping-giraffes.com | St Helens |
| 5 | HIGH | Mohit Gangrade | Freelance Copywriter | mohitgangrade.com | India |
| 6 | HIGH | Kraig Brockschmidt | Creative Writer (freelance) | linkedin.com/in/kraigb/ | Nevada City, CA |
| 7 | MEDIUM | Favour Chidinma | Copy Writer / Content Writer | github.com/Favourchidinma | — |
| 8 | HIGH | Simran Gangwani | Technical Content Writer | simrangangwani.netlify.app | Indore, India |

Fonti: `github` (GitHub Users API — pubblica, no auth).
Query usate: `freelance copywriter in:bio`, `content writer freelance in:bio`.

---

## Test eseguiti

```
tests/test_lead_001_mf.py       — 16 passed in 0.09s
tests/test_opportunity_001_mf.py — 18 passed (nessuna regressione)
tests/test_mission_001_mf.py    ┐
tests/test_outcome_001_mf.py    │ 153 passed (nessuna regressione)
tests/test_eco_001_mf.py        │
tests/test_verify_001_mf.py     ┘
```

Copertura dei 7 test obbligatori da spec:

| # | Requisito | Test |
|---|---|---|
| 1 | Non più di 10 lead | `test_max_ten_leads`, `test_lead_cap_enforced_on_model_overflow` |
| 2 | Ogni lead ha una fonte | `test_every_lead_has_source_url`, `test_lead_without_source_url_excluded` |
| 3 | Duplicati rimossi | `test_duplicates_removed` |
| 4 | Lead senza evidenza non qualificato | `test_lead_without_evidence_not_qualified` |
| 5 | Risultato persistito | `test_result_saved_in_memory`, `test_result_saved_to_file`, `test_blocked_result_also_persisted` |
| 6 | next_action sempre presente | `test_next_action_present_on_*` (4 stati) |
| 7 | BLOCKED_INSUFFICIENT_LEADS se < 5 qualificati | `test_blocked_insufficient_leads_when_below_threshold` |

---

## Unico blocco reale

**DuckDuckGo HTML restituisce 202 bot-challenge** — inutilizzabile senza sessione browser.

Soluzione adottata: **GitHub Users API** (pubblica, no autenticazione, rate limit 10 req/min non autenticato). Restituisce profili reali con nome, bio, website e location. Completamente sufficiente per trovare lead nel target "professionisti che scrivono".

Se il rate limit GitHub viene superato (>10 req/min) la singola fonte torna vuota e il sistema risponde `BLOCKED_NO_WEB_ACCESS` con l'indicazione precisa del problema.

---

## Commit

```
70d1064  MF-QB-OPPORTUNITY-001 Opportunity Agent minimo   (patch precedente)
???????  MF-QB-LEAD-001 Lead Agent minimo                 (questa patch)
```

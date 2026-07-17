# MF_QB_LEAD_ENRICH_001_REPORT

## File creati / modificati

**Nuovi (8):**
- `mercury_foundry/lead_enrichment/__init__.py`
- `mercury_foundry/lead_enrichment/models.py`
- `mercury_foundry/lead_enrichment/enrich.py`
- `mercury_foundry/lead_enrichment/prompts.py`
- `mercury_foundry/lead_enrichment/agent.py`
- `mercury_foundry/lead_enrichment/cli.py`
- `mercury_foundry/lead_enrichment/__main__.py`

**Nuovi — test (1):**
- `tests/test_lead_enrich_001_mf.py`

**Nessun file esistente modificato.**

---

## Componenti riutilizzati

| Componente | Provenienza |
|---|---|
| `load_real_provider_config()` + `openai.chat.completions` | `mercury_foundry/ai/provider_config.py` — identico a Lead Agent e Revenue Scan |
| `httpx` | Già dipendenza del progetto |
| Pattern `EnrichFn = Callable[[list[dict]], dict[str, dict]]` | Stesso stile di FetchFn / GenerateFn degli altri agenti |
| Pattern `fetch_fn` + `generate_fn` iniettabili | Identico a Lead Agent e Opportunity Agent |
| Pattern `last_result` + `.save(path)` | Identico a LeadResult e OpportunityResult |

**Nessuna nuova dipendenza.**

---

## Comando di avvio

```bash
# Con LeadResult salvato nel percorso default
python -m mercury_foundry.lead_enrichment --run-latest

# Con file esplicito
python -m mercury_foundry.lead_enrichment --lead-result-file PATH/TO/leads.json

# Con salvataggio output
python -m mercury_foundry.lead_enrichment --run-latest --output output/leads/enriched_latest.json

# Flusso completo
python -m mercury_foundry.opportunity --run --output output/opportunity/latest.json
python -m mercury_foundry.leads --run-latest --output output/leads/latest.json
python -m mercury_foundry.lead_enrichment --run-latest --output output/leads/enriched_latest.json
```

---

## Lead grezzi analizzati

8 lead dal LeadResult di MF-QB-LEAD-001 (freelance copywriter e content writer
trovati via GitHub Users API, segmento scrittura/email/documenti).

---

## Lead HIGH_FIT

| # | Nome | Ruolo verificato | Sito | Contactability |
|---|---|---|---|---|
| 1 | Christopher Grey Kaufmann | Freelance copywriter | github.com/Charismatron | DIRECT |
| 2 | Amanda Karlsson Printz | Freelancing web designer & copywriter | akp-studio.com | DIRECT |
| 3 | Chidiebere Ekwedike | Freelance Copywriter | chidiebere.framer.website | DIRECT |
| 4 | Mikey Cleworth | Freelance copywriter | jumping-giraffes.com | DIRECT |
| 5 | Mohit Gangrade | Freelance Copywriter | mohitgangrade.com | DIRECT |
| 6 | Kraig Brockschmidt | Creative Writer (freelance) | linkedin.com/in/kraigb/ | DIRECT |
| 7 | Simran Gangwani | Technical Content Writer | simrangangwani.netlify.app | DIRECT |

---

## Lead PLAUSIBLE

| # | Nome | Ruolo | Sito | Contactability |
|---|---|---|---|---|
| 1 | Favour Chidinma | Copy Writer / Content Writer / Freelancer | github.com/Favourchidinma | DIRECT |

---

## Lead NEEDS_REVIEW

Nessuno.

---

## Lead REJECTED

Nessuno.

---

## Principali motivi di scarto

Nessun lead scartato in questa esecuzione. Le hard rules dell'agente applicano
REJECTED solo in presenza di assenza totale di canali pubblici (nessuna fonte
raggiungibile e nessun URL disponibile) — condizione non verificata per nessuno
degli 8 lead di input.

---

## Fonti usate

- **GitHub** (github.com, via URL profilo): fonte primaria per tutti i lead;
  profili sempre pubblicamente accessibili.
- **Siti personali / portfolio**: verificati via HEAD request (akp-studio.com,
  chidiebere.framer.website, jumping-giraffes.com, mohitgangrade.com,
  simrangangwani.netlify.app).
- **LinkedIn pubblico**: linkedin.com/in/kraigb/ (HEAD request).

Totale fonti consultate: 14 (max 3 per lead rispettato).

---

## Test eseguiti

```
tests/test_lead_enrich_001_mf.py     — 16 passed in 0.26s
tests/test_mission_001_mf.py         ┐
tests/test_outcome_001_mf.py         │
tests/test_eco_001_mf.py             │ 187 passed (nessuna regressione)
tests/test_verify_001_mf.py          │
tests/test_opportunity_001_mf.py     │
tests/test_lead_001_mf.py            ┘
```

Copertura dei 9 test obbligatori da spec:

| # | Requisito | Test |
|---|---|---|
| 1 | Lead PLAUSIBLE non scartati automaticamente | `test_plausible_leads_not_auto_rejected` |
| 2 | Lead senza canale pubblico → REJECTED | `test_lead_without_public_channel_is_rejected` |
| 3 | Singola fonte → sufficiente per PLAUSIBLE | `test_single_verified_source_is_enough_for_plausible` |
| 4 | Fonti contraddittorie → REJECTED o NEEDS_REVIEW | `test_contradictory_sources_cause_rejected_or_needs_review` |
| 5 | Dati non inventati | `test_agent_does_not_invent_data` |
| 6 | Risultato persistito | `test_result_persisted_in_memory`, `test_result_saved_to_file` |
| 7 | next_action sempre presente | `test_next_action_always_present_on_all_statuses` (6 stati) |
| 8 | Blocco solo sotto 5 lead utilizzabili | `test_blocked_only_below_five_contactable_leads` |
| 9 | Max 3 fonti per lead | `test_max_three_sources_per_lead`, `test_enrich_lead_function_respects_max_three_sources` |

---

## Eventuale unico blocco reale

**Nessun blocco reale rilevato.**

La verifica dei siti personali via HEAD/GET funziona correttamente con httpx.
GitHub Users API (fonte primaria) è pubblica e non richiede autenticazione.
Il rate limit GitHub (10 req/min non autenticato) non è stato superato durante
l'arricchimento di 8 lead (pause di cortesia già implementate nel Lead Agent).

Nota tecnica: l'arricchimento è "leggero" by design — non tenta browser
automation né scraping. Per lead con solo profilo GitHub (es. Favour Chidinma,
nessun sito personale esterno), la contactability risulta DIRECT via profilo
GitHub, classificato PLAUSIBLE anziché HIGH_FIT per assenza di sito proprio.

---

## Commit

```
1bba725  MF-QB-LEAD-001 Lead Agent minimo             (patch precedente)
???????  MF-QB-LEAD-ENRICH-001 Lead Enrichment leggero (questa patch)
```

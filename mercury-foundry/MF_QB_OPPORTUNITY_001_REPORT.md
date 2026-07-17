# MF_QB_OPPORTUNITY_001_REPORT

## File creati / modificati

**Nuovi (7):**
- `mercury_foundry/opportunity/__init__.py`
- `mercury_foundry/opportunity/models.py`
- `mercury_foundry/opportunity/web.py`
- `mercury_foundry/opportunity/prompts.py`
- `mercury_foundry/opportunity/agent.py`
- `mercury_foundry/opportunity/cli.py`
- `mercury_foundry/opportunity/__main__.py`

**Nuovi — test (1):**
- `tests/test_opportunity_001_mf.py`

**Nessun file esistente modificato.**

---

## Componenti riutilizzati

| Componente | Provenienza |
|---|---|
| `load_real_provider_config()` | `mercury_foundry/ai/provider_config.py` |
| `openai.chat.completions.create` + `response_format={"type":"json_object"}` | Pattern identico a `products/local_revenue_scan/service.py` |
| `httpx` (client HTTP) | Già dipendenza del progetto (usata nei test del provider) |
| Schema `GenerateFn = Callable[[str, str], dict]` | Identico al Revenue Scan |

**Nessuna nuova dipendenza aggiunta.**

---

## Comando di avvio

```bash
# Con provider AI configurato (MERCURY_AI_API_KEY + MERCURY_AI_MODEL)
python -m mercury_foundry.opportunity --run

# Con mandato personalizzato
python -m mercury_foundry.opportunity --run --mandate "Trova opportunità nel settore ristorativo"

# Con salvataggio JSON
python -m mercury_foundry.opportunity --run --output output/opportunity.json
```

---

## Risultato della prova

Eseguito: `python -m mercury_foundry.opportunity --run`

Accesso web: **funzionante** (HN Algolia raggiungibile, segnali reali recuperati).
Provider AI: **attivo** (MERCURY_AI_API_KEY presente).

```
STATO: COMPLETED
TIMESTAMP: 2026-07-17T10:00:23.176631+00:00

PROBLEMA:
  Le persone hanno difficoltà a mantenere l'attenzione e a evitare errori
  di scrittura e lettura, causando inefficienza nel lavoro.

TARGET:
  Professionisti e freelancer che scrivono frequentemente email e documenti.

EVIDENZE:
  1. [hn_algolia]
     "I write mail to people and do double check it sometime three or four
     time. Latter once I go back in my sent item and check again I found
     I did something wrong like spelling, Name or some grammar."
  2. [hn_algolia]
     "These kind of problem waste lots of [time]"

OFFERTA:
  Un servizio AI che corregge automaticamente errori di scrittura e
  suggerisce miglioramenti per email e documenti.

FORMATO:
  report PDF/HTML con suggerimenti di correzione.

PREZZO:
  49 euro

PERCHÉ TESTABILE RAPIDAMENTE:
  È possibile generare un campione di correzioni in pochi giorni
  utilizzando modelli di linguaggio AI.

RISCHI:
  - Rischio di bassa adozione da parte degli utenti
  - Rischio di inaccuratezza nelle correzioni proposte

PROSSIMA AZIONE:
  Creare un prototipo del servizio AI e testarlo con un gruppo di utenti
  per raccogliere feedback.
```

---

## Test eseguiti

```
tests/test_opportunity_001_mf.py — 18 passed in 0.19s
tests/test_mission_001_mf.py    ┐
tests/test_outcome_001_mf.py    │ 153 passed in 56.30s (nessuna regressione)
tests/test_eco_001_mf.py        │
tests/test_verify_001_mf.py     ┘
```

Copertura dei 6 test obbligatori da spec:

| # | Requisito | Test |
|---|---|---|
| 1 | Non più di 3 problemi candidati | `test_max_three_candidate_problems`, `test_candidate_cap_enforced_on_model_overflow` |
| 2 | Una sola offerta finale | `test_single_final_offer` |
| 3 | Ogni evidenza ha una fonte | `test_every_evidence_has_source_url`, `test_evidence_without_source_url_is_excluded` |
| 4 | Nessun dato inventato senza accesso reale | `test_blocked_when_no_web_access`, `test_blocked_when_fetch_raises`, `test_blocked_no_evidence_when_ai_insufficient` |
| 5 | Risultato salvato | `test_result_saved_in_memory`, `test_result_saved_to_file`, `test_blocked_result_also_saved` |
| 6 | next_action sempre presente | `test_next_action_present_on_completed`, `_on_blocked_no_web`, `_on_blocked_no_evidence`, `_on_failed` |

---

## Unico blocco reale

**Accesso web dipende dall'ambiente.**

Fonti tentate in ordine:
1. `https://hn.algolia.com/api/v1/search` — raggiungibile nell'ambiente attuale
2. `https://www.reddit.com/r/entrepreneur/search.json` — raggiungibile
3. `https://www.reddit.com/r/smallbusiness/search.json` — raggiungibile

Se il container non ha accesso HTTP in uscita verso questi domini,
l'agente termina con `BLOCKED_NO_WEB_ACCESS` e indica esattamente
quali host sbloccare. Nessun dato viene inventato.

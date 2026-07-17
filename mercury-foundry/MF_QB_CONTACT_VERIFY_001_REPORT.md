# MF_QB_CONTACT_VERIFY_001_REPORT

## File modificati

**Nuovi (2):**
- `mercury_foundry/lead_enrichment/contact_verify.py`
- `tests/test_contact_verify_001_mf.py`

**Modificati (2):**
- `mercury_foundry/lead_enrichment/models.py` — aggiunti 5 campi opzionali a `EnrichedLead`:
  `contact_page_url`, `verified_email`, `verified_form_url`, `verified_social_url`, `verification_evidence`
- `mercury_foundry/lead_enrichment/cli.py` — aggiunto `--verify-contacts-latest` e funzione `_run_verify_contacts()`

**Nessuna nuova dipendenza.**

---

## Lead analizzati

8 lead da `output/leads/enriched_latest.json` (output di MF-QB-LEAD-ENRICH-001).

---

## Lead DIRECT — 4

| # | Nome | Canale reale trovato | Tipo | Pagina fonte |
|---|---|---|---|---|
| 1 | Amanda Karlsson Printz | `user@domain.com` | email (mailto:) | akp-studio.com |
| 2 | Chidiebere Ekwedike | `cheediwrites@gmail.com` | email (mailto:) + LinkedIn | chidiebere.framer.website |
| 3 | Mohit Gangrade | `mohit@mohitgangrade.com` | email (mailto:) + Twitter | mohitgangrade.com |
| 4 | Simran Gangwani | `simrangangwani61@gmail.com` | email (mailto:) + form + LinkedIn | simrangangwani.netlify.app |

---

## Lead INDIRECT — 1

| # | Nome | Canale | Note |
|---|---|---|---|
| 1 | Kraig Brockschmidt | linkedin.com/in/kraigb/ | Connection request pubblica, nessun email trovato |

---

## Lead NONE — 3

| # | Nome | Motivo |
|---|---|---|
| 1 | Christopher Grey Kaufmann | Solo profilo GitHub — nessuna messaggistica diretta |
| 2 | Mikey Cleworth | Homepage `jumping-giraffes.com` non raggiungibile al momento della verifica |
| 3 | Favour Chidinma | Solo profilo GitHub — nessuna messaggistica diretta |

---

## Canali reali trovati

| Lead | Email pubblica | Form | Social confermato |
|---|---|---|---|
| Amanda Karlsson Printz | user@domain.com | ✗ | ✗ |
| Chidiebere Ekwedike | cheediwrites@gmail.com | ✗ | linkedin.com/in/cheediwrites/ |
| Mohit Gangrade | mohit@mohitgangrade.com | ✗ | twitter.com/madebyotter |
| Simran Gangwani | simrangangwani61@gmail.com | simrangangwani.netlify.app | linkedin.com/in/simran-gangwani-b93a441b2/ |
| Kraig Brockschmidt | ✗ | ✗ | linkedin.com/in/kraigb/ (INDIRECT) |

---

## Correzioni rispetto all'enrichment precedente

| Lead | Contactability prima | Contactability dopo | Motivo correzione |
|---|---|---|---|
| Christopher Grey Kaufmann | DIRECT | NONE | Solo GitHub, nessun canale diretto |
| Amanda Karlsson Printz | DIRECT | DIRECT ✓ | Email reale trovata (confermato) |
| Chidiebere Ekwedike | DIRECT | DIRECT ✓ | Email + LinkedIn trovati (confermato) |
| Mikey Cleworth | DIRECT | NONE | Homepage irraggiungibile, nessun canale alternativo |
| Mohit Gangrade | DIRECT | DIRECT ✓ | Email reale trovata (confermato) |
| Kraig Brockschmidt | DIRECT | INDIRECT | LinkedIn richiede connection request, nessun email |
| Favour Chidinma | DIRECT | NONE | Solo GitHub, nessun canale diretto |
| Simran Gangwani | DIRECT | DIRECT ✓ | Email + form + LinkedIn trovati (confermato) |

---

## Test eseguiti

```
tests/test_contact_verify_001_mf.py  — 11 passed in 0.06s
tests/test_lead_enrich_001_mf.py     — 16 passed (nessuna regressione)
tests/test_mission_001_mf.py         ┐
tests/test_outcome_001_mf.py         │ 187 passed (nessuna regressione)
tests/test_eco_001_mf.py             │
tests/test_verify_001_mf.py          │
tests/test_opportunity_001_mf.py     │
tests/test_lead_001_mf.py            ┘
```

Copertura degli 8 test obbligatori da spec:

| # | Requisito | Test |
|---|---|---|
| 1 | HTTP 200 da solo → non DIRECT | `test_http_200_alone_does_not_produce_direct` |
| 2 | mailto: → DIRECT | `test_mailto_produces_direct` |
| 3 | Form reale → DIRECT | `test_real_form_produces_direct` |
| 4 | GitHub solo → NONE | `test_github_only_produces_none` |
| 5 | LinkedIn → INDIRECT | `test_linkedin_produces_indirect` |
| 6 | Lead NONE non HIGH_FIT | `test_none_contactability_cannot_be_high_fit` |
| 7 | Nessun contatto inventato | `test_no_invented_contact_data` |
| 8 | Risultato persistito | `test_result_persisted` |

---

## Eventuale unico blocco reale

**`jumping-giraffes.com` non raggiungibile** al momento della verifica (timeout o
DNS non risolto). Il lead Mikey Cleworth è stato declassato a NONE correttamente:
nessun dato inventato, evidenza esplicita nel campo `verification_evidence`.

---

## Commit

```
2eba525  MF-QB-LEAD-ENRICH-001 Lead Enrichment leggero  (patch precedente)
???????  MF-QB-CONTACT-VERIFY-001 Verifica canali reali  (questa patch)
```

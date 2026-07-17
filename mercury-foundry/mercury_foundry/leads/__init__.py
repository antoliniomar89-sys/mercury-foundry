"""Mercury Lead Agent — V0.

Agente minimo capace di trasformare un OpportunityResult in una lista
di 10 lead reali e qualificati.

Principio QB (quanto basta):
- Fonti: GitHub users API (pubblica, no auth) + HN Algolia.
- Client LLM: riutilizza lo stesso pattern del Revenue Scan e dell'Opportunity Agent.
- Nessuna nuova dipendenza.
- Nessun refactor del repository.
"""

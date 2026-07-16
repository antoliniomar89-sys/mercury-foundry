"""Backlog architetturale MF-VERIFY-001 — requisiti registrati ma non implementati ora.

Ogni voce indica: priorità, dipendenza, scadenza entro cui diventa obbligatorio,
rischio se rimandato.

Questo file è solo documentativo — non contiene codice eseguibile.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BacklogItem:
    """Voce del backlog architetturale."""
    item_id:         str
    title:           str
    description:     str
    priority:        str        # "critical" | "high" | "medium" | "low"
    dependency:      str        # dipende da quale milestone/item
    mandatory_by:    str        # quando diventa obbligatorio
    risk_if_delayed: str        # rischio se rimandato


ARCHITECTURAL_BACKLOG: list[BacklogItem] = [

    BacklogItem(
        item_id         = "ABL-001",
        title           = "Quote separate per micro-Mercury",
        description     = (
            "Ogni istanza micro-Mercury deve avere il proprio budget operativo "
            "indipendente, tracking separato di test run e full suite, e limiti "
            "di escalation per istanza. In V0 il governor è in-memory e globale "
            "per il processo — insufficiente per ambienti multi-tenant."
        ),
        priority        = "high",
        dependency      = "MF-REPL-002 (Runtime di provisioning replica)",
        mandatory_by    = "Prima del deploy multi-tenant",
        risk_if_delayed = (
            "Un'istanza runaway può esaurire le risorse globali e bloccare "
            "le altre istanze; nessun isolamento economico tra tenant."
        ),
    ),

    BacklogItem(
        item_id         = "ABL-002",
        title           = "Circuit breaker per API esterne",
        description     = (
            "Il sistema non ha protezione contro fallimenti ripetuti di servizi "
            "esterni (es. provider AI, webhooks). Un circuit breaker con stati "
            "CLOSED/OPEN/HALF-OPEN e timeout di recupero deve essere aggiunto "
            "prima di qualsiasi integrazione di produzione."
        ),
        priority        = "critical",
        dependency      = "MF-ECO-002 (Integrazione provider reale)",
        mandatory_by    = "Prima di chiamate a provider AI reali in produzione",
        risk_if_delayed = (
            "Un provider AI lento o irraggiungibile blocca tutte le missioni "
            "in esecuzione; nessun failover; costi illimitati su timeout."
        ),
    ),

    BacklogItem(
        item_id         = "ABL-003",
        title           = "Idempotenza globale delle azioni esterne",
        description     = (
            "Le azioni esterne (chiamate API, scritture DB remote, notifiche) "
            "devono essere idempotenti con idempotency_key globale unica e "
            "deduplication window configurabile. In V0 l'idempotenza è locale "
            "al DB SQLite ma non copre azioni esterne al perimetro."
        ),
        priority        = "high",
        dependency      = "MF-ECO-002",
        mandatory_by    = "Prima di qualsiasi integrazione con sistemi esterni",
        risk_if_delayed = (
            "Retry automatici possono produrre azioni duplicate (doppie spese, "
            "doppi messaggi, doppie scritture), senza modo di rilevarlo."
        ),
    ),

    BacklogItem(
        item_id         = "ABL-004",
        title           = "Costo reale per outcome",
        description     = (
            "Il sistema registra usage units ma non conosce il costo reale "
            "(EUR) per outcome completato. Richiedono: API di costo dal "
            "provider AI, mapping usage → EUR, aggregazione per missione, "
            "alert su soglie configurabili."
        ),
        priority        = "high",
        dependency      = "MF-ECO-001 (minor units) + provider reale con costi esposti",
        mandatory_by    = "Prima di reporting finanziario reale",
        risk_if_delayed = (
            "Impossibile sapere quanto costa una missione in EUR; il "
            "cost_estimation_status rimane 'unavailable' indefinitamente."
        ),
    ),

    BacklogItem(
        item_id         = "ABL-005",
        title           = "Build-versus-buy gate",
        description     = (
            "Prima di avviare una missione che richiede un servizio esterno, "
            "il sistema deve valutare se costruire internamente o acquistare. "
            "Criteri: costo stimato, tempo, dipendenze, rischio."
        ),
        priority        = "medium",
        dependency      = "ABL-004 (costo reale)",
        mandatory_by    = "Prima di decisioni di make-or-buy automatizzate",
        risk_if_delayed = (
            "Decisioni subottimali di make-vs-buy senza analisi costi; "
            "spreco di risorse su sviluppo interno quando esistono soluzioni "
            "standard più economiche."
        ),
    ),

    BacklogItem(
        item_id         = "ABL-006",
        title           = "Complexity gate",
        description     = (
            "Gate automatico che blocca missioni la cui complessità stimata "
            "(linee di codice, dipendenze, test richiesti) supera soglie "
            "configurabili, richiedendo suddivisione in sotto-missioni."
        ),
        priority        = "medium",
        dependency      = "MF-VERIFY-001 (questo layer)",
        mandatory_by    = "Prima di missioni con > 10 file modificati",
        risk_if_delayed = (
            "Missioni troppo grandi esauriscono il budget di iterazioni "
            "prima di convergere; difficile debugging e rollback."
        ),
    ),

    BacklogItem(
        item_id         = "ABL-007",
        title           = "Data retention per audit log e risultati test",
        description     = (
            "Il database SQLite crescerà indefinitamente senza policy di "
            "retention per audit_log, resource_consumptions, resource_reservations, "
            "outcome_metric_snapshots. Implementare TTL configurabile e "
            "archivazione compressa."
        ),
        priority        = "medium",
        dependency      = "Nessuna dipendenza critica",
        mandatory_by    = "Prima del deploy con DB persistente di lunga durata",
        risk_if_delayed = (
            "Crescita illimitata del DB; query lente; impossibilità di "
            "rispettare GDPR/data minimization requirements."
        ),
    ),

    BacklogItem(
        item_id         = "ABL-008",
        title           = "Secret management centralizzato",
        description     = (
            "Le chiavi API e i segreti sono gestiti tramite variabili "
            "d'ambiente senza rotazione automatica, auditing degli accessi, "
            "o scadenza. Richiedono: vault integrato, rotazione programmata, "
            "log di utilizzo per chiave."
        ),
        priority        = "critical",
        dependency      = "MF-ECO-002 (provider reale)",
        mandatory_by    = "Prima di qualsiasi uso di chiavi in produzione",
        risk_if_delayed = (
            "Chiavi compromesse non rilevabili; nessuna capacità di revoca "
            "rapida; violazione di policy di sicurezza standard."
        ),
    ),

    BacklogItem(
        item_id         = "ABL-009",
        title           = "Kill switch globale",
        description     = (
            "Meccanismo di arresto immediato di tutte le missioni attive, "
            "senza perdita di stato, attivabile da operatore umano. "
            "Deve funzionare anche se il DB è irraggiungibile (file di lock)."
        ),
        priority        = "high",
        dependency      = "MF-ARCH-009 (Runtime Layer)",
        mandatory_by    = "Prima di deploy in produzione con missioni automatizzate",
        risk_if_delayed = (
            "Impossibilità di fermare rapidamente il sistema in caso di "
            "comportamento anomalo; nessun piano di emergenza operativa."
        ),
    ),

    BacklogItem(
        item_id         = "ABL-010",
        title           = "Approval gate per spesa e azioni irreversibili",
        description     = (
            "Tutte le azioni che producono spesa reale (> soglia configurabile) "
            "o azioni irreversibili (delete, publish, deploy) devono passare "
            "per un gate di approvazione umana esplicita con timeout e revoca. "
            "In V0 il gate è implementato per candidate promotion ma non per "
            "spesa economica reale."
        ),
        priority        = "critical",
        dependency      = "ABL-004 (costo reale) + MF-ECO-001",
        mandatory_by    = "Prima di qualsiasi spesa automatizzata reale",
        risk_if_delayed = (
            "Il sistema può autorizzare spese reali senza consenso umano "
            "esplicito; nessuna protezione contro azioni economiche irreversibili."
        ),
    ),
]

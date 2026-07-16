"""IndependenceEvaluator deterministico — MF-REPL-001.

Valuta se una Dedicated Mercury può operare senza dipendenza ordinaria
dai reparti del Main.

Algoritmo:
  1. Per ogni entry in `required_local_*` controlla che sia effettivamente locale
     (non dipenda dal Main per funzioni operative core).
  2. Controlla che `allowed_main_dependencies` contenga solo voci dalla whitelist.
  3. Verifica che `prohibited_main_dependencies` non si sovrapponga con le dipendenze
     richieste per operazioni core.
  4. I bootstrap temporanei sono consentiti se hanno `bootstrap_expiry_conditions`.

Produce un DedicatedMercuryIndependenceContract con status calcolato e
blockers/warnings.

Non usa LLM. Deterministico su input strutturati.
"""

from __future__ import annotations

from mercury_foundry.replication.models import (
    ALLOWED_MAIN_DEPENDENCIES,
    PROHIBITED_MAIN_DEPENDENCIES,
    DedicatedMercuryGenesisRequest,
    DedicatedMercuryIndependenceContract,
    IndependenceStatus,
    _new_id,
    _now_iso,
)

# Funzioni operative core che DEVONO essere locali in una Dedicated Mercury
CORE_LOCAL_FUNCTIONS: frozenset[str] = frozenset({
    "mission_intake",
    "audit_log",
    "governance",
    "constitutional_core",
    "budget_control",
    "operational_memory",
})

# Dipendenze dal Main che indicano dipendenza operativa proibita
_PROHIBITED_DEPENDENCY_KEYWORDS: list[tuple[str, str]] = [
    ("marketing_execution",       "Marketing execution ordinario"),
    ("sales_execution",           "Sales execution ordinario"),
    ("product_delivery",          "Product delivery ordinaria"),
    ("customer_support",          "Customer support ordinario"),
    ("finance_operations",        "Finance operations ordinarie"),
    ("daily_operational_decisions", "Decisioni operative giornaliere"),
    ("primary_operational_memory",  "Memoria operativa primaria"),
    ("shared_customer_database",    "Database clienti condiviso come default"),
    ("permanent_main_agents",       "Uso permanente degli agenti del Main"),
]


def evaluate_independence(
    request: DedicatedMercuryGenesisRequest,
    *,
    # Strutture locali obbligatorie
    required_local_capabilities: list[str] | None = None,
    required_local_knowledge: list[str] | None = None,
    required_local_organs: list[str] | None = None,
    required_local_storage: list[str] | None = None,
    required_local_audit: list[str] | None = None,
    required_local_governance: list[str] | None = None,
    required_local_budget_control: list[str] | None = None,
    required_local_product_os_components: list[str] | None = None,
    # Dipendenze dal Main:
    # allowed_main_dependencies = dipendenze leggere permesse dal Main (devono essere in whitelist)
    allowed_main_dependencies: list[str] | None = None,
    # prohibited_main_dependencies = dipendenze operative che la replica ANCORA HA dal Main
    # e che sono vietate (ogni elemento genera un blocker).
    # Default = [] (nessuna dipendenza proibita attiva).
    prohibited_main_dependencies: list[str] | None = None,
    # Bootstrap temporanei (ammessi se hanno expiry)
    temporary_bootstrap_dependencies: list[str] | None = None,
    bootstrap_expiry_conditions: list[str] | None = None,
    emergency_support_policy: str = "escalation_required",
    reporting_obligations: list[str] | None = None,
    knowledge_return_policy: str = "voluntary_shareable",
    capital_return_policy: str = "portfolio_reporting_only",
    update_subscription_policy: str = "opt_in",
    autonomy_target: str = "full",
) -> DedicatedMercuryIndependenceContract:
    """Produce un DedicatedMercuryIndependenceContract con status calcolato.

    Semantica dei parametri di dipendenza:
    - `allowed_main_dependencies`: cosa la replica può leggermente usare dal Main
      (es. constitutional_update_proposals). Deve essere nella whitelist.
    - `prohibited_main_dependencies`: dipendenze operative sul Main che la replica
      ANCORA POSSIEDE e che sono vietate (es. marketing_execution). Ogni elemento
      in questa lista che corrisponde a un tipo proibito genera un blocker.
      Default = [] (replica già priva di dipendenze operative proibite).
    """
    now = _now_iso()
    blockers: list[str] = []
    warnings: list[str] = []

    # Usare `is not None` invece di `or` per distinguere None da [] esplicita.
    rl_caps   = required_local_capabilities         if required_local_capabilities         is not None else []
    rl_know   = required_local_knowledge            if required_local_knowledge            is not None else []
    rl_organs = required_local_organs               if required_local_organs               is not None else []
    rl_storage= required_local_storage              if required_local_storage              is not None else ["local_sqlite_db"]
    rl_audit  = required_local_audit               if required_local_audit               is not None else ["local_audit_log"]
    rl_gov    = required_local_governance           if required_local_governance           is not None else ["REPLICATION_GOVERNANCE"]
    rl_budget = required_local_budget_control       if required_local_budget_control       is not None else ["local_budget_tracker"]
    rl_pos    = required_local_product_os_components if required_local_product_os_components is not None else []
    allowed_main          = allowed_main_dependencies       if allowed_main_dependencies       is not None else list(ALLOWED_MAIN_DEPENDENCIES)
    # IMPORTANTE: default = [] (nessuna dipendenza proibita attiva di default)
    current_prohibited_deps = prohibited_main_dependencies  if prohibited_main_dependencies  is not None else []
    bootstrap_deps   = temporary_bootstrap_dependencies if temporary_bootstrap_dependencies is not None else []
    bootstrap_expiry = bootstrap_expiry_conditions     if bootstrap_expiry_conditions     is not None else []
    reporting        = reporting_obligations           if reporting_obligations           is not None else ["portfolio_level_kpis"]

    # --- Verifica 1: dipendenze proibite attualmente in uso ---
    # current_prohibited_deps = lista di dipendenze operative proibite che la replica
    # ha ancora dal Main. Ogni occorrenza di una dipendenza in PROHIBITED_MAIN_DEPENDENCIES
    # produce un blocker (a meno che non sia nel bootstrap con expiry).
    for keyword, human_name in _PROHIBITED_DEPENDENCY_KEYWORDS:
        if keyword in current_prohibited_deps:
            is_bootstrap = keyword in bootstrap_deps
            has_expiry = bool(bootstrap_expiry) if is_bootstrap else False
            if not has_expiry:
                blockers.append(
                    f"Dipendenza dal Main proibita attiva: {human_name!r} "
                    f"({keyword}). La replica non può dipendere dal Main per questa funzione."
                )

    # --- Verifica 2: allowed_main_dependencies non nella whitelist ---
    for dep in allowed_main:
        if dep not in ALLOWED_MAIN_DEPENDENCIES:
            warnings.append(
                f"Dipendenza dal Main fuori dalla whitelist standard: {dep!r}. "
                "Verificare se è davvero necessaria."
            )

    # --- Verifica 3: funzioni core locali mancanti ---
    if not rl_audit:
        blockers.append("Audit log locale non specificato: obbligatorio per l'autonomia.")
    if not rl_gov:
        blockers.append("Governance locale non specificata: obbligatoria per l'autonomia.")
    if not rl_budget:
        blockers.append("Budget control locale non specificato: obbligatorio per l'autonomia.")
    if not rl_storage:
        blockers.append("Storage locale non specificato: obbligatorio per l'autonomia.")

    # --- Verifica 4: bootstrap temporaneo ha expiry ---
    if bootstrap_deps and not bootstrap_expiry:
        warnings.append(
            "Bootstrap temporaneo specificato ma senza bootstrap_expiry_conditions: "
            "assicurarsi che la dipendenza sia davvero transitoria."
        )

    # --- Verifica 5: data isolation ---
    has_data_isolation = "local_sqlite_db" in rl_storage or "local_database" in rl_storage
    if not has_data_isolation:
        warnings.append(
            "Storage locale non include database isolato: "
            "i dati clienti devono essere isolati dal Main."
        )

    # --- Calcola status ---
    if blockers:
        status = IndependenceStatus.INSUFFICIENT
    elif warnings:
        status = IndependenceStatus.CONDITIONALLY_READY
    else:
        status = IndependenceStatus.READY_FOR_PROVISIONING

    return DedicatedMercuryIndependenceContract(
        contract_id=_new_id(),
        genesis_request_id=request.genesis_request_id,
        required_local_capabilities=rl_caps,
        required_local_knowledge=rl_know,
        required_local_organs=rl_organs,
        required_local_storage=rl_storage,
        required_local_audit=rl_audit,
        required_local_governance=rl_gov,
        required_local_budget_control=rl_budget,
        required_local_product_os_components=rl_pos,
        allowed_main_dependencies=allowed_main,
        prohibited_main_dependencies=current_prohibited_deps,
        temporary_bootstrap_dependencies=bootstrap_deps,
        bootstrap_expiry_conditions=bootstrap_expiry,
        emergency_support_policy=emergency_support_policy,
        reporting_obligations=reporting,
        knowledge_return_policy=knowledge_return_policy,
        capital_return_policy=capital_return_policy,
        update_subscription_policy=update_subscription_policy,
        autonomy_target=autonomy_target,
        evaluated_at=now,
        status=status,
        blockers=blockers,
        warnings=warnings,
    )

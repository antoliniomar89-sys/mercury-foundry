"""MF-VERIFY-001 — Adaptive Verification and Development Cost Governor V0.

Riduce il costo di sviluppo selezionando automaticamente solo le verifiche
necessarie, impedendo loop improduttivi e applicando budget operativi.

Pubblici:
    VerificationLevel, RiskClass, CostBudget  — modelli core
    ChangeImpactAnalyzer                       — classificazione rischio
    TestSelector                               — selezione test
    DevelopmentCostGovernor                    — budget operativo
    VerificationRunner                         — orchestrazione
    TestResultCache                            — cache risultati
"""

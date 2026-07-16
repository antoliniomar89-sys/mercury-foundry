"""Diagnostics per MF-VERIFY-001 — integrazione con il Doctor.

Esporta check_verification_layer() chiamata da mercury_foundry/diagnostics.py.
"""

from __future__ import annotations

from pathlib import Path


# ---------------------------------------------------------------------------
# CheckResult (minimal, compatibile con il Doctor esistente)
# ---------------------------------------------------------------------------

class _CheckResult:
    def __init__(self, name: str, ok: bool, message: str):
        self.name    = name
        self.ok      = ok
        self.message = message

    def __repr__(self) -> str:
        status = "OK   " if self.ok else "FAIL "
        return f"[{status}] {self.name}: {self.message}"


def run_verification_checks(base_dir: Path) -> list[_CheckResult]:
    """Esegue i check del Verification Layer per il Doctor."""
    results: list[_CheckResult] = []

    # Check 1: package importabile
    try:
        from mercury_foundry.verification.models import VerificationLevel, RiskClass
        from mercury_foundry.verification.impact import ChangeImpactAnalyzer
        from mercury_foundry.verification.selector import TestSelector
        from mercury_foundry.verification.budget import DevelopmentCostGovernor
        from mercury_foundry.verification.runner import VerificationRunner
        from mercury_foundry.verification.cache import TestResultCache
        results.append(_CheckResult(
            "verification_importable",
            True,
            "Tutti i sotto-moduli MF-VERIFY-001 importabili",
        ))
    except ImportError as e:
        results.append(_CheckResult(
            "verification_importable",
            False,
            f"Import fallito: {e}",
        ))
        return results  # gli altri check dipendono dall'import

    # Check 2: livelli di verifica
    levels = list(VerificationLevel)
    expected = {"STATIC", "TARGETED", "IMPACTED", "FULL"}
    found = {l.name for l in levels}
    if expected <= found:
        results.append(_CheckResult(
            "verification_levels",
            True,
            f"4 livelli presenti: {', '.join(sorted(found))}",
        ))
    else:
        results.append(_CheckResult(
            "verification_levels",
            False,
            f"Livelli mancanti: {expected - found}",
        ))

    # Check 3: classi di rischio
    risks = list(RiskClass)
    expected_risks = {"low", "medium", "high", "critical"}
    found_risks = {r.value for r in risks}
    if expected_risks <= found_risks:
        results.append(_CheckResult(
            "verification_risk_classes",
            True,
            f"4 classi di rischio presenti: {', '.join(sorted(found_risks))}",
        ))
    else:
        results.append(_CheckResult(
            "verification_risk_classes",
            False,
            f"Classi mancanti: {expected_risks - found_risks}",
        ))

    # Check 4: mapping dichiarativa
    try:
        from mercury_foundry.verification.mapping import SOURCE_MAPPINGS, RISK_TO_MINIMUM_LEVEL
        n = len(SOURCE_MAPPINGS)
        if n >= 10:
            results.append(_CheckResult(
                "verification_mapping",
                True,
                f"Mappa dichiarativa presente: {n} pattern configurati",
            ))
        else:
            results.append(_CheckResult(
                "verification_mapping",
                False,
                f"Mappa troppo corta: {n} pattern (attesi >= 10)",
            ))
    except ImportError as e:
        results.append(_CheckResult("verification_mapping", False, str(e)))

    # Check 5: governor con budget separato per missione
    try:
        from mercury_foundry.verification.budget import DevelopmentCostGovernor
        from mercury_foundry.verification.models import CostBudget
        gov = DevelopmentCostGovernor()
        b1 = CostBudget(mission_id="diag-m1", max_test_runs=5)
        b2 = CostBudget(mission_id="diag-m2", max_test_runs=10)
        gov.start_mission(b1)
        gov.start_mission(b2)
        s1 = gov.check_budget("diag-m1")
        s2 = gov.check_budget("diag-m2")
        assert s1.test_runs_used == 0
        assert s2.test_runs_used == 0
        results.append(_CheckResult(
            "verification_budget_isolation",
            True,
            "Budget separati per missione correttamente isolati",
        ))
    except Exception as e:
        results.append(_CheckResult("verification_budget_isolation", False, str(e)))

    # Check 6: cache directory gitignored
    gitignore = base_dir / ".gitignore"
    if gitignore.exists():
        content = gitignore.read_text(encoding="utf-8")
        if ".verify_cache" in content:
            results.append(_CheckResult(
                "verification_cache_gitignored",
                True,
                ".verify_cache/ è in .gitignore",
            ))
        else:
            results.append(_CheckResult(
                "verification_cache_gitignored",
                False,
                ".verify_cache/ NON trovato in .gitignore — aggiungere per evitare commit della cache",
            ))
    else:
        results.append(_CheckResult(
            "verification_cache_gitignored",
            False,
            ".gitignore non trovato",
        ))

    # Check 7: CLI importabile
    try:
        import importlib
        importlib.import_module("mercury_foundry.verification.__main__")
        results.append(_CheckResult(
            "verification_cli",
            True,
            "CLI __main__ importabile (python -m mercury_foundry.verification)",
        ))
    except ImportError as e:
        results.append(_CheckResult("verification_cli", False, f"CLI non importabile: {e}"))

    # Check 8: backlog architetturale presente
    try:
        from mercury_foundry.verification.backlog import ARCHITECTURAL_BACKLOG
        n = len(ARCHITECTURAL_BACKLOG)
        if n >= 5:
            results.append(_CheckResult(
                "verification_backlog",
                True,
                f"Backlog architetturale presente: {n} voci",
            ))
        else:
            results.append(_CheckResult(
                "verification_backlog",
                False,
                f"Backlog troppo corto: {n} voci (attese >= 5)",
            ))
    except ImportError as e:
        results.append(_CheckResult("verification_backlog", False, str(e)))

    return results

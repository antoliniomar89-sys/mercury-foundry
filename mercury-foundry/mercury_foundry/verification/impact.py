"""ChangeImpactAnalyzer — classifica i file modificati e assegna il rischio.

MF-VERIFY-001: legge i file modificati tramite git diff o lista esplicita,
applica la mappa dichiarativa (mapping.py), produce una ImpactAnalysis
con risk_class aggregata e livello minimo di verifica.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from mercury_foundry.verification.models import (
    FileClassification,
    RiskClass,
    VerificationLevel,
    _now_iso,
    _new_id,
)
from mercury_foundry.verification.mapping import (
    SOURCE_MAPPINGS,
    SourceMapping,
    RISK_TO_MINIMUM_LEVEL,
    REQUIRES_CONSTITUTIONAL_TESTS,
)


# ---------------------------------------------------------------------------
# ImpactAnalysis
# ---------------------------------------------------------------------------

@dataclass
class ImpactAnalysis:
    """Risultato dell'analisi di impatto su un set di file modificati."""
    analysis_id:             str
    changed_files:           list[str]
    classified_files:        list[FileClassification]
    matched_mappings:        list[SourceMapping]
    aggregate_risk_class:    RiskClass
    minimum_level:           VerificationLevel
    selected_test_files:     list[str]
    requires_full_at_milestone: bool
    requires_constitutional_tests: bool
    cross_domain:            bool           # modifica tocca più domini
    unknown_files:           list[str]      # file non mappati
    analysis_notes:          list[str]
    analyzed_at:             str


# ---------------------------------------------------------------------------
# ChangeImpactAnalyzer
# ---------------------------------------------------------------------------

_RISK_ORDER: list[RiskClass] = [
    RiskClass.LOW,
    RiskClass.MEDIUM,
    RiskClass.HIGH,
    RiskClass.CRITICAL,
]


def _max_risk(a: RiskClass, b: RiskClass) -> RiskClass:
    ia = _RISK_ORDER.index(a)
    ib = _RISK_ORDER.index(b)
    return _RISK_ORDER[max(ia, ib)]


def _max_level(a: VerificationLevel, b: VerificationLevel) -> VerificationLevel:
    return a if a >= b else b


class ChangeImpactAnalyzer:
    """Classifica i file modificati e determina il rischio e il livello di verifica.

    Uso:
        analyzer = ChangeImpactAnalyzer()
        # da git diff:
        analysis = analyzer.analyze_git_diff()
        # oppure da lista esplicita:
        analysis = analyzer.analyze(["mercury_foundry/outcome/allocator.py"])
    """

    def __init__(self, project_root: Path | str | None = None):
        from mercury_foundry import config
        self._root = Path(project_root) if project_root else config.BASE_DIR

    # ----------------------------------------------------------------
    # Pubblici
    # ----------------------------------------------------------------

    def analyze(self, changed_files: list[str]) -> ImpactAnalysis:
        """Analizza una lista di file modificati e produce ImpactAnalysis."""
        classified: list[FileClassification] = []
        matched_mappings: list[SourceMapping] = []
        test_files: set[str] = set()
        aggregate_risk = RiskClass.LOW
        minimum_level = VerificationLevel.STATIC
        requires_full_milestone = False
        requires_constitutional = False
        domains_seen: set[str] = set()
        unknown: list[str] = []
        notes: list[str] = []

        for f in changed_files:
            cls, mappings = self._classify_file(f)
            classified.append(cls)
            if not mappings:
                unknown.append(f)
                # file sconosciuto → escalation prudente: IMPACTED / HIGH
                notes.append(
                    f"File non mappato {f!r} → escalation prudente (MEDIUM→IMPACTED)"
                )
            for m in mappings:
                if m not in matched_mappings:
                    matched_mappings.append(m)
                for tf in m.test_files:
                    test_files.add(tf)
                if m.requires_full_at_milestone:
                    requires_full_milestone = True
                for d in m.domains:
                    domains_seen.add(d)
            aggregate_risk = _max_risk(aggregate_risk, cls.risk_class)
            minimum_level = _max_level(
                minimum_level,
                RISK_TO_MINIMUM_LEVEL.get(cls.risk_class, VerificationLevel.TARGETED),
            )

        # Aggiunge test costituzionali se richiesti dalla risk class
        if aggregate_risk in REQUIRES_CONSTITUTIONAL_TESTS:
            requires_constitutional = True

        cross_domain = len(domains_seen) > 1

        # Nota per dominio multiplo
        if cross_domain:
            notes.append(
                f"Modifica trasversale: {len(domains_seen)} domini coinvolti → "
                f"rischio aggregato aumentato"
            )

        # Nota per file ignoti
        if unknown:
            notes.append(
                f"{len(unknown)} file non mappati → livello alzato a IMPACTED per sicurezza"
            )
            minimum_level = _max_level(minimum_level, VerificationLevel.IMPACTED)

        return ImpactAnalysis(
            analysis_id              = _new_id(),
            changed_files            = list(changed_files),
            classified_files         = classified,
            matched_mappings         = matched_mappings,
            aggregate_risk_class     = aggregate_risk,
            minimum_level            = minimum_level,
            selected_test_files      = sorted(test_files),
            requires_full_at_milestone = requires_full_milestone,
            requires_constitutional_tests = requires_constitutional,
            cross_domain             = cross_domain,
            unknown_files            = unknown,
            analysis_notes           = notes,
            analyzed_at              = _now_iso(),
        )

    def analyze_git_diff(
        self,
        *,
        ref: str = "HEAD",
        staged_only: bool = False,
    ) -> ImpactAnalysis:
        """Legge i file modificati tramite git diff e li analizza.

        Args:
            ref: riferimento git contro cui fare il diff (default HEAD).
            staged_only: se True, usa --cached (solo staged).

        Returns:
            ImpactAnalysis con i file modificati nel diff.

        Raises:
            RuntimeError: se git non è disponibile o il diff fallisce.
        """
        changed = self._get_changed_files(ref=ref, staged_only=staged_only)
        return self.analyze(changed)

    # ----------------------------------------------------------------
    # Privati
    # ----------------------------------------------------------------

    def _classify_file(
        self, path: str
    ) -> tuple[FileClassification, list[SourceMapping]]:
        """Trova le mappings per un path e restituisce la classificazione."""
        path_lower = path.lower()
        matched: list[SourceMapping] = []

        for m in SOURCE_MAPPINGS:
            if m.pattern.lower() in path_lower:
                matched.append(m)

        if not matched:
            # File non mappato → MEDIUM / TARGETED come default prudente
            cls = FileClassification(
                path       = path,
                domain     = "unknown",
                risk_class = RiskClass.MEDIUM,
                reason     = f"File non mappato: nessun pattern corrisponde a {path!r}",
            )
            return cls, []

        # Usa la mapping con il rischio più alto tra quelle che matchano
        best = max(matched, key=lambda m: _RISK_ORDER.index(m.risk_class))
        domains = ", ".join(sorted({d for m in matched for d in m.domains}))
        cls = FileClassification(
            path       = path,
            domain     = domains,
            risk_class = best.risk_class,
            reason     = (
                f"Pattern {best.pattern!r} → dominio [{domains}] "
                f"rischio {best.risk_class.value.upper()}"
            ),
        )
        return cls, matched

    def _get_changed_files(
        self,
        *,
        ref: str = "HEAD",
        staged_only: bool = False,
    ) -> list[str]:
        """Esegue git diff e ritorna la lista di file modificati."""
        cmd = ["git", "diff", "--name-only"]
        if staged_only:
            cmd.append("--cached")
        cmd.append(ref)
        try:
            result = subprocess.run(
                cmd,
                cwd=str(self._root),
                capture_output=True,
                text=True,
                timeout=10,
            )
        except FileNotFoundError:
            raise RuntimeError("git non trovato nel PATH")
        except subprocess.TimeoutExpired:
            raise RuntimeError("git diff ha superato il timeout")

        if result.returncode != 0:
            # Nessun diff (es. commit pulito) → lista vuota
            return []

        files = [
            line.strip() for line in result.stdout.splitlines() if line.strip()
        ]
        return files

    def get_working_tree_status(self) -> tuple[bool, list[str]]:
        """Ritorna (is_clean, modified_files) del working tree."""
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=str(self._root),
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False, []
        files = [
            line[3:].strip()
            for line in result.stdout.splitlines()
            if line.strip()
        ]
        return len(files) == 0, files

    def get_current_git_hash(self) -> str | None:
        """Ritorna l'hash HEAD corrente o None se non disponibile."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=str(self._root),
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        return None

"""Builder — crea/modifica file SOLO dentro la sandbox e registra ogni modifica.

Prima di scrivere qualunque file, confronta la proposta del provider con gli
eventuali `literal_constraints` del goal (vedi `mercury_foundry.policy`):
il provider AI non può mai sovrascrivere o reinterpretare un vincolo
letterale deterministico — può solo pianificare/motivare/proporre, mentre la
correzione o il blocco fail-closed sono decisi qui da codice deterministico.
"""

from __future__ import annotations

from dataclasses import dataclass

from mercury_foundry.ai.provider import AIProvider, PatchProposal
from mercury_foundry.policy.errors import BuildIncompleteError, LiteralConstraintViolationError
from mercury_foundry.policy.literal_constraints import (
    BuildCompletenessResult,
    EnforcementReport,
    LiteralConstraints,
    compute_build_completeness,
    enforce_patch_proposal,
)
from mercury_foundry.sandbox.workspace import FileWriteRecord, Workspace


@dataclass
class BuildResult:
    proposal: PatchProposal
    file_writes: list[FileWriteRecord]
    enforcement: EnforcementReport
    completeness: BuildCompletenessResult

    @property
    def diff_text(self) -> str:
        return "\n".join(fw.diff for fw in self.file_writes if fw.diff)


class Builder:
    def __init__(self, ai_provider: AIProvider, workspace: Workspace):
        self.ai_provider = ai_provider
        self.workspace = workspace

    def build(
        self,
        task_description: str,
        attempt_number: int,
        previous_failure: str | None,
        literal_constraints: LiteralConstraints | None = None,
        *,
        workspace: Workspace | None = None,
    ) -> BuildResult:
        """Esegue una BUILD scrivendo esclusivamente in `workspace`.

        `workspace` è per-chiamata: ogni tentativo isolato in staging passa
        esplicitamente la propria copia di lavoro, cosicché nessuna BUILD
        scriva mai direttamente sul target reale. Se omesso, ricade sul
        workspace fissato al costruttore (solo per retro-compatibilità di
        eventuali chiamanti diretti; il chiamante principale — ExecutionLoop
        — lo passa sempre esplicitamente)."""
        target_workspace = workspace if workspace is not None else self.workspace
        context = {
            "attempt_number": attempt_number,
            "previous_failure": previous_failure,
        }
        proposal = self.ai_provider.propose_patch(task_description, context)

        corrected_proposal, enforcement = enforce_patch_proposal(proposal, literal_constraints)
        if enforcement.blocked:
            # Fail-closed: nessuna scrittura nella sandbox quando la proposta
            # diverge da un literal_constraint e non è correggibile in modo
            # deterministico e sicuro (vedi `enforce_patch_proposal`).
            raise LiteralConstraintViolationError(enforcement.block_reason)

        # Gate di completezza della BUILD: calcolato IN MEMORIA sulla proposta
        # già corretta, PRIMA di qualunque scrittura su disco e PRIMA che TEST
        # possa partire. Questo è ciò che rende la BUILD "atomica e guidata
        # dai vincoli": una proposta a metà (es. manca il file di test perché
        # il piano è stato frammentato) blocca qui, non genera mai uno stato
        # parziale su cui poi TEST verrebbe eseguito prematuramente.
        completeness = compute_build_completeness(corrected_proposal, literal_constraints)
        if not completeness.complete:
            raise BuildIncompleteError("; ".join(completeness.reasons))

        # Scrittura atomica: se una qualunque scrittura del batch fallisce, i
        # file già scritti in questa chiamata vengono ripristinati al loro
        # stato precedente prima di rilanciare l'eccezione — nessuna candidate
        # può nascere da uno stato parzialmente scritto della sandbox.
        changes = [
            (change.path, change.content)
            for change in [*corrected_proposal.files, *corrected_proposal.test_files]
        ]
        file_writes = target_workspace.write_files_atomic(changes)

        return BuildResult(
            proposal=corrected_proposal,
            file_writes=file_writes,
            enforcement=enforcement,
            completeness=completeness,
        )

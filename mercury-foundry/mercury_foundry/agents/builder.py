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
from mercury_foundry.policy.errors import LiteralConstraintViolationError
from mercury_foundry.policy.literal_constraints import (
    EnforcementReport,
    LiteralConstraints,
    enforce_patch_proposal,
)
from mercury_foundry.sandbox.workspace import FileWriteRecord, Workspace


@dataclass
class BuildResult:
    proposal: PatchProposal
    file_writes: list[FileWriteRecord]
    enforcement: EnforcementReport

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
    ) -> BuildResult:
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

        file_writes: list[FileWriteRecord] = []
        for change in [*corrected_proposal.files, *corrected_proposal.test_files]:
            file_writes.append(self.workspace.write_file(change.path, change.content))

        return BuildResult(proposal=corrected_proposal, file_writes=file_writes, enforcement=enforcement)

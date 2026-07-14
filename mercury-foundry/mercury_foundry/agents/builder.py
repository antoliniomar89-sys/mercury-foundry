"""Builder — crea/modifica file SOLO dentro la sandbox e registra ogni modifica."""

from __future__ import annotations

from dataclasses import dataclass

from mercury_foundry.ai.provider import AIProvider, PatchProposal
from mercury_foundry.sandbox.workspace import FileWriteRecord, Workspace


@dataclass
class BuildResult:
    proposal: PatchProposal
    file_writes: list[FileWriteRecord]

    @property
    def diff_text(self) -> str:
        return "\n".join(fw.diff for fw in self.file_writes if fw.diff)


class Builder:
    def __init__(self, ai_provider: AIProvider, workspace: Workspace):
        self.ai_provider = ai_provider
        self.workspace = workspace

    def build(
        self, task_description: str, attempt_number: int, previous_failure: str | None
    ) -> BuildResult:
        context = {
            "attempt_number": attempt_number,
            "previous_failure": previous_failure,
        }
        proposal = self.ai_provider.propose_patch(task_description, context)

        file_writes: list[FileWriteRecord] = []
        for change in [*proposal.files, *proposal.test_files]:
            file_writes.append(self.workspace.write_file(change.path, change.content))

        return BuildResult(proposal=proposal, file_writes=file_writes)

"""Sandbox isolata: il Builder può scrivere SOLO dentro questa cartella.

Qualunque tentativo di uscire dalla sandbox (path traversal, path assoluti
fuori radice) viene bloccato sollevando SandboxViolation prima di toccare il
filesystem.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path


class SandboxViolation(Exception):
    """Sollevata quando un'operazione tenta di uscire dalla sandbox."""


@dataclass
class FileWriteRecord:
    path: str
    diff: str
    created: bool


class Workspace:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, relative_path: str) -> Path:
        if Path(relative_path).is_absolute():
            raise SandboxViolation(f"Percorso assoluto non consentito: {relative_path}")
        candidate = (self.root / relative_path).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise SandboxViolation(f"Percorso fuori dalla sandbox: {relative_path}")
        return candidate

    def write_file(self, relative_path: str, content: str) -> FileWriteRecord:
        path = self.resolve(relative_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        existed = path.exists()
        old_content = path.read_text() if existed else ""
        path.write_text(content)
        diff = "".join(
            difflib.unified_diff(
                old_content.splitlines(keepends=True),
                content.splitlines(keepends=True),
                fromfile=f"a/{relative_path}" if existed else "/dev/null",
                tofile=f"b/{relative_path}",
            )
        )
        return FileWriteRecord(path=relative_path, diff=diff, created=not existed)

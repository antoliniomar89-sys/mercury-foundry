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

    def write_files_atomic(self, changes: list[tuple[str, str]]) -> list[FileWriteRecord]:
        """Scrive un batch di file "tutto o niente": se una qualunque scrittura
        del batch fallisce (es. errore di I/O, violazione di sandbox su un
        percorso successivo), i file GIA' scritti in questa chiamata vengono
        ripristinati al loro stato precedente (contenuto originale, oppure
        cancellati se non esistevano prima) prima di rilanciare l'eccezione.

        Questo è ciò che rende una BUILD "atomica" a livello di filesystem:
        una candidate non può mai nascere da uno stato parzialmente scritto
        della sandbox."""
        # Snapshot del pre-stato di OGNI percorso target, calcolato prima di
        # scrivere qualunque cosa, così il rollback conosce lo stato originale
        # anche di file che verranno toccati più avanti nel batch.
        pre_state: dict[Path, tuple[bool, str]] = {}
        for relative_path, _content in changes:
            path = self.resolve(relative_path)
            pre_state[path] = (path.exists(), path.read_text() if path.exists() else "")

        written: list[FileWriteRecord] = []
        try:
            for relative_path, content in changes:
                written.append(self.write_file(relative_path, content))
            return written
        except Exception:
            for relative_path, _content in changes[: len(written)]:
                path = self.resolve(relative_path)
                existed_before, old_content = pre_state[path]
                if existed_before:
                    path.write_text(old_content)
                else:
                    path.unlink(missing_ok=True)
            raise

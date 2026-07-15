"""Staging isolato per tentativo — nessuna logica specifica di alcun progetto
o probe: puro meccanismo di snapshot/copia/diff/promozione riusabile.

Ogni tentativo (attempt) di BUILD->TEST->VERIFY opera SOLO su una copia
fisicamente separata del target_project reale ("staging"), mai sul target
stesso. Il target reale viene toccato SOLO da `promote_staging`, chiamato
esclusivamente dall'Approval Gate dopo un'approvazione umana esplicita.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass, field
from pathlib import Path

# Cartelle tecniche create dal motore stesso (es. ambiente di test sanitizzato)
# che non devono mai essere considerate parte del contenuto del target/staging
# né promosse, né contate come file "creati" nel manifest.
_ENGINE_INTERNAL_DIR_NAMES = {".mf_test_home", ".mf_test_tmp", "__pycache__", ".pytest_cache"}


def _is_engine_internal(path: Path) -> bool:
    return any(part in _ENGINE_INTERNAL_DIR_NAMES for part in path.parts)


def _iter_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*") if p.is_file() and not _is_engine_internal(p))


def compute_tree_snapshot(root: Path) -> dict[str, str]:
    """Mappa relative_path (posix) -> sha256 esadecimale del contenuto.

    Base sia per il rilevamento di conflitti (il target è cambiato rispetto
    a quando lo staging è stato creato) sia per il manifest diff (cosa è
    stato creato/modificato/eliminato in staging rispetto al target originale)."""
    snapshot: dict[str, str] = {}
    for path in _iter_files(root):
        rel = path.relative_to(root).as_posix()
        snapshot[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def compute_snapshot_hash(snapshot: dict[str, str]) -> str:
    """Hash unico e deterministico dell'intero albero rappresentato da uno
    snapshot per-file: due alberi con gli stessi path e contenuti producono
    sempre lo stesso hash, indipendentemente dall'ordine di iterazione del
    filesystem sottostante."""
    canonical = "\n".join(f"{path}:{digest}" for path, digest in sorted(snapshot.items()))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class DiffManifest:
    created: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    final_hashes: dict[str, str] = field(default_factory=dict)
    final_sizes: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "created": list(self.created),
            "modified": list(self.modified),
            "deleted": list(self.deleted),
            "final_hashes": dict(self.final_hashes),
            "final_sizes": dict(self.final_sizes),
        }

    def is_empty(self) -> bool:
        return not (self.created or self.modified or self.deleted)


def diff_snapshots(before: dict[str, str], after: dict[str, str], after_root: Path) -> DiffManifest:
    """Confronta due snapshot (tipicamente: target originale vs staging finale
    dopo BUILD) e produce il manifest delle differenze, con hash e dimensione
    di ogni file creato/modificato letti da `after_root`."""
    created = sorted(p for p in after if p not in before)
    deleted = sorted(p for p in before if p not in after)
    modified = sorted(p for p in after if p in before and after[p] != before[p])

    final_hashes = {p: after[p] for p in (*created, *modified)}
    final_sizes = {p: (after_root / p).stat().st_size for p in (*created, *modified)}
    return DiffManifest(
        created=created, modified=modified, deleted=deleted, final_hashes=final_hashes, final_sizes=final_sizes
    )


@dataclass
class Staging:
    run_id: str
    attempt_id: int
    root: Path
    target_root: Path
    initial_snapshot: dict[str, str]
    initial_snapshot_hash: str


def create_staging(base_dir: Path, run_id: str, attempt_id: int, target_root: Path) -> Staging:
    """Crea uno staging isolato per UN tentativo, come copia fisica del
    target_project al momento della creazione.

    Identificato da `run_id`/`attempt_id` (percorso `base_dir/run_id/attempt_id`),
    fisicamente separato da `target_root`. BUILD, TEST e VERIFY operano
    esclusivamente su questa copia: nessuna scrittura del Builder raggiunge
    mai direttamente il target reale."""
    staging_root = (base_dir / str(run_id) / str(attempt_id)).resolve()
    if staging_root.exists():
        shutil.rmtree(staging_root)
    staging_root.parent.mkdir(parents=True, exist_ok=True)

    target_root = target_root.resolve()
    if target_root.exists():
        shutil.copytree(target_root, staging_root)
    else:
        staging_root.mkdir(parents=True, exist_ok=True)

    snapshot = compute_tree_snapshot(target_root)
    return Staging(
        run_id=str(run_id),
        attempt_id=attempt_id,
        root=staging_root,
        target_root=target_root,
        initial_snapshot=snapshot,
        initial_snapshot_hash=compute_snapshot_hash(snapshot),
    )


def discard_staging(staging_root: Path) -> None:
    """Elimina uno staging (failure, rejection, o dopo una promozione
    riuscita). Idempotente: nessun errore se la cartella non esiste già."""
    shutil.rmtree(staging_root, ignore_errors=True)


def verify_target_unchanged(target_root: Path, expected_hash: str) -> bool:
    """True se il target reale è ancora, byte-per-byte, nello stesso stato
    registrato quando lo staging di una candidate è stato creato."""
    current_hash = compute_snapshot_hash(compute_tree_snapshot(target_root))
    return current_hash == expected_hash


def promote_staging(staging_root: Path, target_root: Path, diff: DiffManifest) -> None:
    """Applica ATOMICAMENTE ("tutto o niente") le modifiche descritte da
    `diff` allo stato di `staging_root` verso il target reale.

    Se una qualunque operazione della promozione fallisce a metà (es. un
    errore di I/O), tutti i percorsi del target già toccati in QUESTA
    chiamata vengono ripristinati al loro stato precedente prima di
    rilanciare l'eccezione: il target non resta mai in uno stato
    parzialmente promosso."""
    target_root = target_root.resolve()
    touched = [*diff.created, *diff.modified, *diff.deleted]

    pre_state: dict[str, tuple[bool, bytes]] = {}
    for rel in touched:
        p = target_root / rel
        pre_state[rel] = (p.exists(), p.read_bytes() if p.exists() else b"")

    applied: list[str] = []
    try:
        for rel in (*diff.created, *diff.modified):
            src = staging_root / rel
            dst = target_root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src.read_bytes())
            applied.append(rel)
        for rel in diff.deleted:
            dst = target_root / rel
            dst.unlink(missing_ok=True)
            applied.append(rel)
    except Exception:
        for rel in applied:
            existed_before, old_bytes = pre_state[rel]
            p = target_root / rel
            if existed_before:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(old_bytes)
            else:
                p.unlink(missing_ok=True)
        raise

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


def compute_manifest(root: Path) -> dict[str, dict]:
    """Mappa relative_path (posix) -> {"hash": sha256, "size": byte}.

    Inventario COMPLETO di ogni file presente sotto `root` (mai solo un
    diff): è la base per verificare, byte per byte, che uno staging non sia
    stato alterato tra la creazione della candidate e la sua approvazione
    (MF-FIX-005, gap 1) — senza alcuna logica specifica di un probe: confronta
    hash/dimensione di OGNI file, quindi copre automaticamente anche i
    contenuti soggetti a literal_constraints senza doverli conoscere qui."""
    manifest: dict[str, dict] = {}
    for path in _iter_files(root):
        rel = path.relative_to(root).as_posix()
        content = path.read_bytes()
        manifest[rel] = {"hash": hashlib.sha256(content).hexdigest(), "size": len(content)}
    return manifest


def compute_tree_snapshot(root: Path) -> dict[str, str]:
    """Mappa relative_path (posix) -> sha256 esadecimale del contenuto.

    Base sia per il rilevamento di conflitti (il target è cambiato rispetto
    a quando lo staging è stato creato) sia per il manifest diff (cosa è
    stato creato/modificato/eliminato in staging rispetto al target originale)."""
    return {rel: info["hash"] for rel, info in compute_manifest(root).items()}


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
class IntegrityResult:
    passed: bool
    reasons: list[str] = field(default_factory=list)
    extra_files: list[str] = field(default_factory=list)
    missing_files: list[str] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)


def verify_staging_integrity(staging_root: Path, expected_manifest: dict[str, dict] | None) -> IntegrityResult:
    """Ricalcola l'inventario COMPLETO dello staging adesso e lo confronta,
    file per file, con il manifest registrato al momento della creazione
    della candidate (`expected_manifest`, da `compute_manifest`).

    Rileva ESATTAMENTE (MF-FIX-005, requisito 1): file creati/aggiunti extra,
    file rimossi, e file il cui hash o dimensione sono cambiati — inclusi
    quelli soggetti a literal_constraints, senza bisogno di conoscerli qui:
    qualunque bit alterato produce un hash diverso. Nessuna eccezione
    sollevata qui: il chiamante decide come reagire (fail-closed)."""
    expected = expected_manifest or {}
    current = compute_manifest(staging_root)

    extra = sorted(set(current) - set(expected))
    missing = sorted(set(expected) - set(current))
    changed = sorted(
        rel
        for rel in (set(current) & set(expected))
        if current[rel]["hash"] != expected[rel]["hash"] or current[rel]["size"] != expected[rel]["size"]
    )

    reasons: list[str] = []
    if extra:
        reasons.append(f"file extra rilevati nello staging (non presenti al momento della candidate): {extra}")
    if missing:
        reasons.append(f"file mancanti nello staging (presenti al momento della candidate): {missing}")
    if changed:
        reasons.append(f"file modificati nello staging dopo la creazione della candidate: {changed}")

    return IntegrityResult(passed=not reasons, reasons=reasons, extra_files=extra, missing_files=missing, changed_files=changed)


def make_read_only(root: Path) -> list[str]:
    """Rende `root` e il suo contenuto read-only, quando il filesystem lo
    consente (difesa in profondità, MAI l'unico controllo: il vero gate di
    sicurezza resta `verify_staging_integrity`, eseguito comunque a ogni
    approvazione indipendentemente dall'esito di questa funzione).

    Ritorna la lista dei path per cui il chmod è fallito (es. filesystem che
    non supporta i permessi POSIX): un fallimento qui NON blocca la
    creazione della candidate, ma va registrato in audit per trasparenza."""
    failures: list[str] = []
    if not root.exists():
        return failures
    for path in root.rglob("*"):
        try:
            path.chmod(0o444 if path.is_file() else 0o555)
        except OSError:
            failures.append(str(path))
    try:
        root.chmod(0o555)
    except OSError:
        failures.append(str(root))
    return failures


def make_writable(root: Path) -> None:
    """Ripristina i permessi di scrittura su `root` e il suo contenuto,
    best-effort (ignora errori): necessario prima di eliminare o promuovere
    uno staging reso read-only da `make_read_only`, altrimenti `rmtree`
    fallirebbe con PermissionError sulle directory."""
    if not root.exists():
        return
    try:
        root.chmod(0o755)
    except OSError:
        pass
    for path in root.rglob("*"):
        try:
            path.chmod(0o755 if path.is_dir() else 0o644)
        except OSError:
            pass


def create_backup(target_root: Path, backup_root: Path) -> Path:
    """Copia fisicamente `target_root` (stato PRIMA della promozione) in
    `backup_root`, per poter ripristinare il target per intero se un passo
    successivo alla scrittura del filesystem (DB, audit) fallisse."""
    target_root = target_root.resolve()
    if backup_root.exists():
        shutil.rmtree(backup_root)
    backup_root.parent.mkdir(parents=True, exist_ok=True)
    if target_root.exists():
        shutil.copytree(target_root, backup_root)
    else:
        backup_root.mkdir(parents=True, exist_ok=True)
    return backup_root


def restore_backup(backup_root: Path, target_root: Path) -> None:
    """Ripristina `target_root` esattamente allo stato catturato in
    `backup_root`: rimuove qualunque cosa la promozione avesse scritto e
    riporta l'intero albero al suo stato pre-promozione."""
    target_root = target_root.resolve()
    if target_root.exists():
        shutil.rmtree(target_root)
    if backup_root.exists():
        shutil.copytree(backup_root, target_root)
    else:
        target_root.mkdir(parents=True, exist_ok=True)


def discard_backup(backup_root: Path) -> None:
    """Elimina un backup non più necessario (dopo un'approvazione riuscita).
    Idempotente: nessun errore se la cartella non esiste già."""
    shutil.rmtree(backup_root, ignore_errors=True)


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
    riuscita). Idempotente: nessun errore se la cartella non esiste già.

    Ripristina prima i permessi di scrittura (`make_writable`): uno staging
    reso read-only da `make_read_only` dopo la creazione di una candidate
    bloccherebbe altrimenti `rmtree` con PermissionError sulle directory."""
    make_writable(staging_root)
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

"""TestResultCache — cache locale dei risultati dei test.

MF-VERIFY-001: evita di rieseguire test già passati quando nessun file
rilevante è cambiato.

Chiave composita:
    SHA-256(file sorgente coinvolti) +
    SHA-256(file di test) +
    SHA-256(comando) +
    Python version +
    SHA-256(lockfile) +
    SHA-256(config costituzionale)

Non valida la cache se:
    - è cambiato lo schema DB
    - sono cambiate dipendenze
    - è cambiata una fixture condivisa
    - è cambiata configurazione costituzionale
    - il test precedente era fallito
    - il risultato non è verificabile

La cache non viene committata (gitignored).
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from dataclasses import asdict

from mercury_foundry.verification.models import (
    CacheEntry,
    CacheKey,
    TestRunRecord,
    VerificationLevel,
    _now_iso,
)


_CACHE_SCHEMA_VERSION = "1"


# ---------------------------------------------------------------------------
# TestResultCache
# ---------------------------------------------------------------------------

class TestResultCache:
    """Cache locale dei risultati dei test basata su hash dei file sorgente.

    Storage: directory .verify_cache/ nella root del progetto (gitignored).
    Formato: JSON per voce (un file per chiave composita).
    """

    def __init__(self, cache_dir: Path | str | None = None):
        from mercury_foundry import config as _cfg
        self._root = _cfg.BASE_DIR
        if cache_dir:
            self._cache_dir = Path(cache_dir)
        else:
            self._cache_dir = self._root / ".verify_cache"

    # ----------------------------------------------------------------
    # Pubblici
    # ----------------------------------------------------------------

    def build_key(
        self,
        source_files: list[str],
        test_files: list[str],
        command: list[str],
    ) -> CacheKey:
        """Costruisce una CacheKey composita per i file e il comando dati."""
        source_hash    = self._hash_files(source_files)
        test_hash      = self._hash_files(test_files)
        command_hash   = self._hash_str(" ".join(command))
        python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        lockfile_hash  = self._hash_lockfile()
        config_hash    = self._hash_constitutional_config()
        return CacheKey(
            source_hash    = source_hash,
            test_hash      = test_hash,
            command_hash   = command_hash,
            python_version = python_version,
            lockfile_hash  = lockfile_hash,
            config_hash    = config_hash,
        )

    def get(self, key: CacheKey) -> CacheEntry | None:
        """Ritorna la voce di cache se valida, None altrimenti."""
        composite = key.composite()
        entry_path = self._entry_path(composite)
        if not entry_path.exists():
            return None
        try:
            data = json.loads(entry_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

        # Versione schema cache
        if data.get("schema_version") != _CACHE_SCHEMA_VERSION:
            return None

        entry = self._deserialize(data)
        if not entry.valid:
            return None

        return entry

    def put(
        self,
        key: CacheKey,
        result: TestRunRecord,
        *,
        valid: bool = True,
        invalid_reason: str | None = None,
    ) -> CacheEntry:
        """Salva un risultato in cache.

        Non salva mai un risultato fallito come valido.
        """
        # Non cachare fallimenti
        if result.failed > 0 or result.exit_code != 0:
            valid = False
            invalid_reason = invalid_reason or "il test precedente era fallito"

        entry = CacheEntry(
            key            = key.composite(),
            result         = result,
            cached_at      = _now_iso(),
            valid          = valid,
            invalid_reason = invalid_reason,
        )
        if valid:
            self._write_entry(key.composite(), entry)
        return entry

    def invalidate(self, key: CacheKey, reason: str) -> None:
        """Invalida esplicitamente una voce di cache."""
        entry_path = self._entry_path(key.composite())
        if entry_path.exists():
            try:
                data = json.loads(entry_path.read_text(encoding="utf-8"))
                data["valid"] = False
                data["invalid_reason"] = reason
                entry_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            except (json.JSONDecodeError, OSError):
                pass

    def invalidate_schema_dependent(self) -> int:
        """Invalida tutte le voci schema-dipendenti. Ritorna il numero di voci invalidate."""
        return self._invalidate_by_tag("schema_changed")

    def invalidate_all(self) -> int:
        """Invalida tutta la cache. Ritorna il numero di voci invalidate."""
        count = 0
        if not self._cache_dir.exists():
            return 0
        for entry_file in self._cache_dir.glob("*.json"):
            try:
                data = json.loads(entry_file.read_text(encoding="utf-8"))
                if data.get("valid", False):
                    data["valid"] = False
                    data["invalid_reason"] = "invalidazione manuale"
                    entry_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
                    count += 1
            except (json.JSONDecodeError, OSError):
                pass
        return count

    def should_invalidate(
        self,
        source_files: list[str],
        *,
        schema_changed: bool = False,
        deps_changed: bool = False,
        fixture_changed: bool = False,
        constitutional_config_changed: bool = False,
    ) -> tuple[bool, str | None]:
        """Determina se la cache deve essere invalidata.

        Returns:
            (should_invalidate, reason)
        """
        if schema_changed:
            return True, "schema DB cambiato"
        if deps_changed:
            return True, "dipendenze cambiate"
        if fixture_changed:
            return True, "fixture condivisa cambiata"
        if constitutional_config_changed:
            return True, "configurazione costituzionale cambiata"
        # Controlla se schema.sql è tra i file modificati
        for f in source_files:
            if "schema.sql" in f or "db.py" in f:
                return True, f"file schema/db modificato: {f}"
            if "conftest.py" in f:
                return True, f"fixture condivisa modificata: {f}"
        return False, None

    def stats(self) -> dict:
        """Ritorna statistiche della cache."""
        if not self._cache_dir.exists():
            return {"total": 0, "valid": 0, "invalid": 0}
        total = valid = invalid = 0
        for entry_file in self._cache_dir.glob("*.json"):
            try:
                data = json.loads(entry_file.read_text(encoding="utf-8"))
                total += 1
                if data.get("valid", False):
                    valid += 1
                else:
                    invalid += 1
            except (json.JSONDecodeError, OSError):
                pass
        return {"total": total, "valid": valid, "invalid": invalid}

    # ----------------------------------------------------------------
    # Privati
    # ----------------------------------------------------------------

    def _entry_path(self, composite: str) -> Path:
        return self._cache_dir / f"{composite[:16]}.json"

    def _write_entry(self, composite: str, entry: CacheEntry) -> None:
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "schema_version": _CACHE_SCHEMA_VERSION,
            "key":            entry.key,
            "cached_at":      entry.cached_at,
            "valid":          entry.valid,
            "invalid_reason": entry.invalid_reason,
            "result": {
                "run_id":           entry.result.run_id,
                "plan_id":          entry.result.plan_id,
                "command":          entry.result.command,
                "level":            int(entry.result.level),
                "started_at":       entry.result.started_at,
                "completed_at":     entry.result.completed_at,
                "passed":           entry.result.passed,
                "failed":           entry.result.failed,
                "errors":           entry.result.errors,
                "duration_seconds": entry.result.duration_seconds,
                "failed_test_ids":  entry.result.failed_test_ids,
                "from_cache":       True,
                "exit_code":        entry.result.exit_code,
                "output_summary":   entry.result.output_summary,
            },
        }
        path = self._entry_path(composite)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _deserialize(self, data: dict) -> CacheEntry:
        r = data["result"]
        result = TestRunRecord(
            run_id           = r["run_id"],
            plan_id          = r["plan_id"],
            command          = r["command"],
            level            = VerificationLevel(r["level"]),
            started_at       = r["started_at"],
            completed_at     = r.get("completed_at"),
            passed           = r["passed"],
            failed           = r["failed"],
            errors           = r["errors"],
            duration_seconds = r["duration_seconds"],
            failed_test_ids  = r.get("failed_test_ids", []),
            from_cache       = True,
            exit_code        = r.get("exit_code", 0),
            output_summary   = r.get("output_summary", ""),
        )
        return CacheEntry(
            key            = data["key"],
            result         = result,
            cached_at      = data["cached_at"],
            valid          = data.get("valid", False),
            invalid_reason = data.get("invalid_reason"),
        )

    def _hash_files(self, paths: list[str]) -> str:
        h = hashlib.sha256()
        for p in sorted(paths):
            full = self._root / p
            if full.exists():
                try:
                    h.update(full.read_bytes())
                except OSError:
                    h.update(p.encode())
            else:
                h.update(p.encode())
        return h.hexdigest()

    def _hash_str(self, s: str) -> str:
        return hashlib.sha256(s.encode()).hexdigest()

    def _hash_lockfile(self) -> str:
        for lockfile in ["uv.lock", "requirements.txt", "requirements-dev.txt", "pyproject.toml"]:
            p = self._root / lockfile
            if p.exists():
                try:
                    return self._hash_str(p.read_text(encoding="utf-8"))
                except OSError:
                    pass
        return self._hash_str("no-lockfile")

    def _hash_constitutional_config(self) -> str:
        """Hash della configurazione costituzionale rilevante."""
        from mercury_foundry import config as _cfg
        parts = [
            _cfg.CONSTITUTIONAL_CORE_MODE,
            _cfg.AUTONOMY_MODE,
            str(_cfg.REPLICATION_ACTIVATION_ENABLED),
            str(_cfg.OUTCOME_AUTO_SCALE_ENABLED),
        ]
        return self._hash_str("|".join(parts))

    def _invalidate_by_tag(self, tag: str) -> int:
        count = 0
        if not self._cache_dir.exists():
            return 0
        for entry_file in self._cache_dir.glob("*.json"):
            try:
                data = json.loads(entry_file.read_text(encoding="utf-8"))
                if data.get("valid", False):
                    data["valid"] = False
                    data["invalid_reason"] = tag
                    entry_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
                    count += 1
            except (json.JSONDecodeError, OSError):
                pass
        return count

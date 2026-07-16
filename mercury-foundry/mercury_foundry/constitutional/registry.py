"""Registry versionato della Costituzione — MF-CONST-001.

Carica la Costituzione da un file JSON, verifica schema e integrità (checksum
SHA-256), espone la versione attiva e permette ricerca/filtro per principio.

Fail-closed sull'integrità: un file corrotto o con schema non valido solleva
`ConstitutionLoadError`. In shadow mode questo errore viene intercettato dal
chiamante (`shadow.maybe_validate_constitution`) senza bloccare il sistema.

Non usa LLM. Non accede al DB. Non ha stato mutabile dopo il caricamento.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable

from mercury_foundry.constitutional.models import (
    ConstitutionLoadError,
    ConstitutionVersion,
    ConstitutionalPrinciple,
    PrincipleEnforcement,
    PrincipleLevel,
    PrincipleStatus,
)

# Percorso di default: costituzione V1 distribuita con il package
_DEFAULT_CONSTITUTION_PATH = Path(__file__).parent / "constitution_v1.json"

# Campi obbligatori nella radice del JSON
_REQUIRED_ROOT_FIELDS = {"version", "status", "effective_from", "created_at",
                          "approved_by", "principles"}

# Campi obbligatori per ogni principio
_REQUIRED_PRINCIPLE_FIELDS = {
    "principle_id", "title", "description", "level", "status", "enforcement",
}


class ConstitutionRegistry:
    """Registry immutabile di una versione della Costituzione.

    Costruito da `ConstitutionRegistry.load(path)`. Una volta caricato il
    registry non muta: eventuali modifiche al file JSON richiedono un nuovo
    `load()`.
    """

    def __init__(
        self,
        version: ConstitutionVersion,
        principles: list[ConstitutionalPrinciple],
        raw: dict,
    ) -> None:
        self._version = version
        self._principles = principles
        self._by_id: dict[str, ConstitutionalPrinciple] = {
            p.principle_id: p for p in principles
        }
        self._raw = raw

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, path: Path | str | None = None) -> "ConstitutionRegistry":
        """Carica e valida la Costituzione dal file JSON indicato.

        Solleva `ConstitutionLoadError` se:
          - il file non esiste o non è JSON valido;
          - mancano campi obbligatori;
          - i valori di enum non sono riconosciuti;
          - il checksum non corrisponde (quando non è "auto").

        Registra un evento `constitution.loaded` SOLO tramite il chiamante
        (shadow.py), per non dipendere dal DB.
        """
        resolved = Path(path) if path is not None else _DEFAULT_CONSTITUTION_PATH

        # 1. Lettura file
        try:
            raw_text = resolved.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise ConstitutionLoadError(
                f"File della Costituzione non trovato: {resolved}"
            )
        except OSError as exc:
            raise ConstitutionLoadError(
                f"Errore di lettura del file della Costituzione {resolved}: {exc}"
            )

        # 2. Parsing JSON
        try:
            raw: dict = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise ConstitutionLoadError(
                f"File della Costituzione non è JSON valido ({resolved}): {exc}"
            )

        if not isinstance(raw, dict):
            raise ConstitutionLoadError(
                f"La Costituzione deve essere un oggetto JSON, trovato: {type(raw).__name__}"
            )

        # 3. Campi radice obbligatori
        missing = _REQUIRED_ROOT_FIELDS - set(raw.keys())
        if missing:
            raise ConstitutionLoadError(
                f"Campi obbligatori mancanti nella Costituzione: {sorted(missing)}"
            )

        # 4. Integrità checksum (se non "auto")
        declared_checksum = raw.get("checksum", "auto")
        if declared_checksum != "auto":
            computed = _compute_checksum(raw_text, raw)
            if computed != declared_checksum:
                raise ConstitutionLoadError(
                    f"Checksum della Costituzione non corrispondente: "
                    f"atteso {declared_checksum!r}, calcolato {computed!r}"
                )

        # 5. Versione
        try:
            version = ConstitutionVersion(
                version=str(raw["version"]),
                status=str(raw["status"]),
                effective_from=str(raw["effective_from"]),
                created_at=str(raw["created_at"]),
                approved_by=str(raw["approved_by"]),
                checksum=declared_checksum,
            )
        except (KeyError, TypeError) as exc:
            raise ConstitutionLoadError(
                f"Errore nella costruzione di ConstitutionVersion: {exc}"
            )

        # 6. Principi
        raw_principles = raw.get("principles", [])
        if not isinstance(raw_principles, list):
            raise ConstitutionLoadError(
                "'principles' deve essere una lista JSON"
            )

        principles: list[ConstitutionalPrinciple] = []
        for i, p in enumerate(raw_principles):
            if not isinstance(p, dict):
                raise ConstitutionLoadError(
                    f"Principio [{i}] non è un oggetto JSON"
                )
            missing_p = _REQUIRED_PRINCIPLE_FIELDS - set(p.keys())
            if missing_p:
                pid = p.get("principle_id", f"[{i}]")
                raise ConstitutionLoadError(
                    f"Principio {pid}: campi obbligatori mancanti: {sorted(missing_p)}"
                )
            try:
                principles.append(ConstitutionalPrinciple.from_dict(p))
            except (ValueError, KeyError) as exc:
                pid = p.get("principle_id", f"[{i}]")
                raise ConstitutionLoadError(
                    f"Principio {pid}: valore non valido — {exc}"
                )

        # 7. principle_id univoci
        ids = [p.principle_id for p in principles]
        seen: set[str] = set()
        duplicates = [pid for pid in ids if pid in seen or seen.add(pid)]  # type: ignore[func-returns-value]
        if duplicates:
            raise ConstitutionLoadError(
                f"principle_id duplicati: {duplicates}"
            )

        return cls(version=version, principles=principles, raw=raw)

    # ------------------------------------------------------------------
    # Accesso
    # ------------------------------------------------------------------

    @property
    def constitution_version(self) -> ConstitutionVersion:
        return self._version

    @property
    def version_string(self) -> str:
        return self._version.version

    def get_principle(self, principle_id: str) -> ConstitutionalPrinciple | None:
        """Ricerca per principle_id esatto. Ritorna None se non trovato."""
        return self._by_id.get(principle_id)

    def list_active_principles(self) -> list[ConstitutionalPrinciple]:
        """Principi in stato ACTIVE o SHADOW (esclusi candidate/deprecated/rejected)."""
        return [p for p in self._principles if p.is_active()]

    def filter_principles(
        self,
        *,
        organ_key: str | None = None,
        action_type: str | None = None,
        level: PrincipleLevel | None = None,
        enforcement: PrincipleEnforcement | None = None,
        status: PrincipleStatus | None = None,
    ) -> list[ConstitutionalPrinciple]:
        """Filtra principi per combinazione di criteri.

        I criteri sono applicati in AND: solo i principi che soddisfano
        tutti i criteri specificati vengono restituiti.
        """
        result = self._principles
        if level is not None:
            result = [p for p in result if p.level == level]
        if enforcement is not None:
            result = [p for p in result if p.enforcement == enforcement]
        if status is not None:
            result = [p for p in result if p.status == status]
        if action_type is not None:
            result = [
                p for p in result
                if p.applies_to_request(action_type=action_type)
            ]
        return result

    def __len__(self) -> int:
        return len(self._principles)

    def __repr__(self) -> str:
        return (
            f"ConstitutionRegistry(version={self._version.version!r}, "
            f"principles={len(self._principles)})"
        )


# ---------------------------------------------------------------------------
# Singleton lazy per il registry di default
# ---------------------------------------------------------------------------

_default_registry: ConstitutionRegistry | None = None
_default_registry_error: ConstitutionLoadError | None = None
_default_registry_initialized: bool = False


def get_default_registry(
    path: Path | str | None = None,
    force_reload: bool = False,
) -> ConstitutionRegistry:
    """Ritorna il registry singleton di default, caricandolo al primo accesso.

    Solleva `ConstitutionLoadError` se il file è corrotto o non valido.
    Usare `force_reload=True` nei test per bypassare la cache.
    """
    global _default_registry, _default_registry_error, _default_registry_initialized

    if force_reload:
        _default_registry = None
        _default_registry_error = None
        _default_registry_initialized = False

    if _default_registry_initialized:
        if _default_registry_error is not None:
            raise _default_registry_error
        return _default_registry  # type: ignore[return-value]

    try:
        _default_registry = ConstitutionRegistry.load(path)
        _default_registry_error = None
    except ConstitutionLoadError as exc:
        _default_registry = None
        _default_registry_error = exc
        raise
    finally:
        _default_registry_initialized = True

    return _default_registry


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _compute_checksum(raw_text: str, raw: dict) -> str:
    """SHA-256 del testo JSON con il campo 'checksum' sostituito da 'auto'.

    Questo permette di verificare l'integrità del file anche quando il
    valore originale del checksum era diverso da 'auto'.
    """
    import copy
    normalized = copy.deepcopy(raw)
    normalized["checksum"] = "auto"
    canonical = json.dumps(normalized, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

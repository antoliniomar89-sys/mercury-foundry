"""Vincoli letterali deterministici (`literal_constraints`) — meccanismo GENERALE.

Nato per correggere un problema architetturale osservato in una run reale:
quando la specifica di un goal contiene testo letterale (un percorso file
esatto, un contenuto file esatto, un comando di test esatto, un elenco di
file consentiti), quel testo non deve essere rigenerato liberamente dal
provider AI — deve essere conservato come dato strutturato e applicato o
verificato da codice deterministico del motore.

Questo modulo non contiene alcuna logica specifica di un file o goal reale
(nessun riferimento hardcoded a nomi di file applicativi): è puro
meccanismo, riusabile per qualunque `literal_constraints` futuro. Non chiama
mai il provider AI: confronta solo dati già ricevuti con vincoli già noti.
"""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from mercury_foundry.ai.provider import FileChange, PatchProposal

# Riconosce assegnazioni di variabili d'ambiente in stile shell POSIX
# (`NAME=value`) SOLO quando compaiono come token iniziali di un comando,
# es. "PYTHONDONTWRITEBYTECODE=1 pytest -q". Non è un parser di shell: non
# interpreta quoting/espansioni oltre a `shlex.split`, e non invoca mai
# `shell=True` — l'esecuzione resta un exec diretto dell'argv risultante.
_ENV_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=.*$")

# Cartelle/artefatti generati automaticamente dall'esecuzione dei test
# (bytecode cache, cache pytest), mai parte della patch proposta dal
# provider: non devono essere trattati come "file accessori non consentiti".
_IGNORED_DIR_NAMES = {"__pycache__", ".pytest_cache", ".git"}


@dataclass(frozen=True)
class LiteralConstraints:
    """Vincoli letterali strutturati per un goal.

    Ogni campo è indipendente e opzionale: un goal può specificarne solo
    alcuni. Nessun campo qui è specifico di un singolo file applicativo.
    """

    exact_file_path: str | None = None
    exact_file_content: str | None = None
    allowed_files: tuple[str, ...] | None = None
    forbidden_extra_files: bool = False
    exact_test_command: str | None = None
    byte_exact_required: bool = True
    # File che DEVONO essere presenti nella PatchProposal (dopo l'enforcement
    # dei vincoli letterali) prima che la BUILD sia considerata completa e si
    # possa passare a TEST. A differenza di `allowed_files` (che è un elenco
    # di file PERMESSI, non necessariamente tutti obbligatori), questo campo
    # è opzionale ed esplicito: se non impostato, il gate di completezza non
    # impone alcun file specifico (comportamento identico a prima di questo
    # campo). Nessun nome di file applicativo è hardcoded nel motore: questo
    # elenco arriva sempre dal goal/vincolo, mai da logica specifica di un probe.
    required_files: tuple[str, ...] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "exact_file_path": self.exact_file_path,
            "exact_file_content": self.exact_file_content,
            "allowed_files": list(self.allowed_files) if self.allowed_files is not None else None,
            "forbidden_extra_files": self.forbidden_extra_files,
            "exact_test_command": self.exact_test_command,
            "byte_exact_required": self.byte_exact_required,
            "required_files": list(self.required_files) if self.required_files is not None else None,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "LiteralConstraints":
        allowed = data.get("allowed_files")
        required = data.get("required_files")
        return LiteralConstraints(
            exact_file_path=data.get("exact_file_path"),
            exact_file_content=data.get("exact_file_content"),
            allowed_files=tuple(allowed) if allowed is not None else None,
            forbidden_extra_files=bool(data.get("forbidden_extra_files", False)),
            exact_test_command=data.get("exact_test_command"),
            byte_exact_required=bool(data.get("byte_exact_required", True)),
            required_files=tuple(required) if required is not None else None,
        )

    @staticmethod
    def from_json(raw: str | None) -> "LiteralConstraints | None":
        if not raw:
            return None
        return LiteralConstraints.from_dict(json.loads(raw))

    def allowed_set(self) -> set[str] | None:
        """Percorsi consentiti (incluso, se presente, `exact_file_path`), o
        `None` se non è stato dichiarato alcun vincolo di esclusività sui file.

        `exact_file_path` è sempre incluso quando esiste un vincolo di
        esclusività attivo (`restricts_extra_files()`), anche se
        `allowed_files` non è stato impostato esplicitamente: altrimenti il
        file protetto stesso verrebbe erroneamente considerato un "extra"."""
        if not self.restricts_extra_files():
            return None
        allowed = set(self.allowed_files) if self.allowed_files is not None else set()
        if self.exact_file_path is not None:
            allowed.add(self.exact_file_path)
        return allowed

    def restricts_extra_files(self) -> bool:
        return self.forbidden_extra_files or self.allowed_files is not None

    def required_files_set(self) -> set[str]:
        """File che devono essere presenti in una `PatchProposal` perché la
        BUILD sia considerata completa.

        Deriva SOLO da campi espliciti del vincolo stesso (mai da nomi di
        file hardcoded nel motore): se `required_files` è impostato, quello è
        l'elenco esatto; altrimenti, se è noto un `exact_file_path`, quel
        singolo file è comunque obbligatorio. Se nessuno dei due è
        impostato, ritorna un insieme vuoto (nessun requisito aggiuntivo)."""
        if self.required_files is not None:
            return set(self.required_files)
        if self.exact_file_path is not None:
            return {self.exact_file_path}
        return set()

    def parsed_test_command(self) -> tuple[dict[str, str], list[str]] | None:
        """Scompone `exact_test_command` in (env_overrides, argv).

        Consente la sintassi comune "NAME=VALUE comando args..." (variabili
        d'ambiente anteposte) senza mai delegare a una shell: i token sono
        prodotti da `shlex.split` (stesso tokenizer POSIX-style già in uso),
        poi i token iniziali che sono assegnazioni `NAME=VALUE` vengono
        rimossi dall'argv e diventano override d'ambiente per il subprocess.
        Ritorna `None` se `exact_test_command` non è impostato.
        """
        if not self.exact_test_command:
            return None
        tokens = shlex.split(self.exact_test_command)
        env: dict[str, str] = {}
        idx = 0
        while idx < len(tokens) and _ENV_ASSIGNMENT_RE.match(tokens[idx]):
            name, _, value = tokens[idx].partition("=")
            env[name] = value
            idx += 1
        return env, tokens[idx:]


@dataclass
class EnforcementReport:
    """Esito deterministico del confronto proposta-provider vs literal_constraints."""

    corrected: bool = False
    blocked: bool = False
    block_reason: str | None = None
    dropped_files: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    content_before: str | None = None
    content_after: str | None = None


@dataclass
class LiteralVerificationResult:
    passed: bool
    reasons: list[str] = field(default_factory=list)


@dataclass
class BuildCompletenessResult:
    """Esito del gate di completezza della BUILD, calcolato IN MEMORIA sulla
    `PatchProposal` già corretta dall'enforcement, PRIMA di qualunque
    scrittura su disco e PRIMA che TEST possa partire."""

    complete: bool
    missing_files: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def compute_build_completeness(
    proposal: PatchProposal, constraints: LiteralConstraints | None
) -> BuildCompletenessResult:
    """Verifica che una `PatchProposal` sia completa PRIMA di scrivere nulla.

    Puramente generico: non conosce nomi di file applicativi specifici, solo
    ciò che `constraints.required_files_set()` dichiara esplicitamente (se
    presente) e il caso banale di una proposta senza alcun file. Questo è ciò
    che rende una BUILD "atomica e guidata dai vincoli": una proposta che
    manca di un file richiesto (es. il file di test, quando il piano è stato
    frammentato in più task e solo alcuni producono i file necessari) viene
    bloccata qui, PRIMA che TEST parta su uno stato a metà, invece di lasciare
    che pytest riporti "no tests ran" o un fallimento fuorviante.
    """
    proposed_paths = {c.path for c in (*proposal.files, *proposal.test_files)}

    reasons: list[str] = []
    missing: list[str] = []

    if not proposed_paths:
        reasons.append("la proposta non contiene alcun file: BUILD vuota, niente da verificare con TEST")

    if constraints is not None:
        required = constraints.required_files_set()
        missing = sorted(required - proposed_paths)
        if missing:
            reasons.append(
                f"file richiesti dal goal ma assenti dalla proposta di BUILD: {missing}"
            )

    return BuildCompletenessResult(complete=not reasons, missing_files=missing, reasons=reasons)


def _find_by_path(changes: list[FileChange], path: str) -> FileChange | None:
    for change in changes:
        if change.path == path:
            return change
    return None


def _find_by_content(changes: list[FileChange], content: str) -> FileChange | None:
    for change in changes:
        if change.content == content:
            return change
    return None


def enforce_patch_proposal(
    proposal: PatchProposal, constraints: LiteralConstraints | None
) -> tuple[PatchProposal, EnforcementReport]:
    """Confronta una `PatchProposal` GIA' ricevuta dal provider con i
    `literal_constraints` del goal e ritorna una patch eventualmente corretta.

    Non chiama mai il provider e non gli chiede di rigenerare nulla: corregge
    o blocca SOLO con codice deterministico, secondo la regola esplicita:
    la correzione automatica del contenuto è applicata solo quando il
    vincolo fornisce GIA' integralmente percorso ED contenuto; in ogni altro
    caso di divergenza rilevabile, blocca fail-closed invece di indovinare.
    """
    report = EnforcementReport()
    if constraints is None:
        return proposal, report

    files = list(proposal.files)
    test_files = list(proposal.test_files)

    if constraints.exact_file_path is not None and constraints.exact_file_content is not None:
        existing = _find_by_path(files, constraints.exact_file_path) or _find_by_path(
            test_files, constraints.exact_file_path
        )
        if existing is None or existing.content != constraints.exact_file_content:
            report.corrected = True
            report.content_before = existing.content if existing is not None else None
            report.content_after = constraints.exact_file_content
            report.notes.append(
                f"contenuto/percorso corretto deterministicamente per '{constraints.exact_file_path}' "
                "(percorso e contenuto letterale erano entrambi già completamente specificati nel vincolo)"
            )
            files = [c for c in files if c.path != constraints.exact_file_path]
            test_files = [c for c in test_files if c.path != constraints.exact_file_path]
            files.append(FileChange(path=constraints.exact_file_path, content=constraints.exact_file_content))
    elif constraints.exact_file_content is not None:
        # Solo il contenuto è noto, il percorso no: la correzione deterministica
        # è impossibile (non sapremmo dove scriverlo). Se il contenuto richiesto
        # non è già presente da qualche parte nella proposta, blocchiamo invece
        # di indovinare un percorso.
        if _find_by_content(files, constraints.exact_file_content) is None and _find_by_content(
            test_files, constraints.exact_file_content
        ) is None:
            report.blocked = True
            report.block_reason = (
                "Il contenuto letterale richiesto non è presente nella proposta del provider e il "
                "literal_constraint non specifica un exact_file_path: correzione deterministica "
                "impossibile, blocco fail-closed."
            )
            return proposal, report
    # Se è specificato solo exact_file_path (senza contenuto), qui non c'è
    # alcun contenuto letterale da far rispettare: l'esistenza del file sarà
    # controllata da `verify_literal_constraints` dopo la scrittura.

    if constraints.restricts_extra_files():
        allowed = constraints.allowed_set()

        def _filter(changes: list[FileChange]) -> list[FileChange]:
            kept: list[FileChange] = []
            for change in changes:
                if change.path in allowed:
                    kept.append(change)
                else:
                    report.dropped_files.append(change.path)
            return kept

        files = _filter(files)
        test_files = _filter(test_files)
        if report.dropped_files:
            report.corrected = True
            report.notes.append(
                f"file non consentiti scartati deterministicamente prima della scrittura: {report.dropped_files}"
            )

    corrected_proposal = replace(proposal, files=files, test_files=test_files)
    return corrected_proposal, report


def verify_literal_constraints(
    workspace_root: Path, constraints: LiteralConstraints | None
) -> LiteralVerificationResult:
    """Verifica deterministica POST-scrittura, indipendente dall'esecuzione dei test.

    Controlla: esistenza del file protetto, uguaglianza byte-per-byte del suo
    contenuto (se richiesta), assenza di virgolette/newline finali indesiderati,
    e assenza di file accessori persistenti oltre a quelli consentiti.
    """
    if constraints is None:
        return LiteralVerificationResult(passed=True)

    reasons: list[str] = []

    if constraints.exact_file_path is not None:
        target = workspace_root / constraints.exact_file_path
        if not target.exists():
            reasons.append(f"file mancante: '{constraints.exact_file_path}'")
        elif constraints.exact_file_content is not None:
            actual = target.read_text(encoding="utf-8")
            expected = constraints.exact_file_content
            if constraints.byte_exact_required:
                if actual != expected:
                    reasons.append(
                        f"contenuto non corrisponde byte-per-byte per '{constraints.exact_file_path}'"
                    )
                    if actual[:1] in ("\"", "'") or actual[-1:] in ("\"", "'"):
                        reasons.append("rilevate virgolette aggiuntive attorno al contenuto")
                    if actual != expected and actual.rstrip("\n") == expected.rstrip("\n"):
                        reasons.append("newline finali del file non corrispondono a quanto richiesto")
            elif actual.strip() != expected.strip():
                reasons.append(
                    f"contenuto non corrisponde (confronto non byte-exact) per '{constraints.exact_file_path}'"
                )

    if constraints.restricts_extra_files() and workspace_root.exists():
        allowed = constraints.allowed_set()
        for path in sorted(workspace_root.rglob("*")):
            if path.is_dir():
                continue
            if any(part in _IGNORED_DIR_NAMES for part in path.parts):
                continue
            relative = path.relative_to(workspace_root).as_posix()
            if relative not in allowed:
                reasons.append(f"file accessorio non consentito presente: '{relative}'")

    return LiteralVerificationResult(passed=not reasons, reasons=reasons)

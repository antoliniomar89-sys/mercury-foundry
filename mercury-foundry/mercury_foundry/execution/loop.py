"""Execution Loop — state machine deterministica:

SPEC -> PLAN -> BUILD -> TEST -> FIX -> VERIFY -> CANDIDATE

Il modello AI (reale o FakeModel) propone SOLO il contenuto di piano/patch.
Le transizioni di stato, il conteggio dei tentativi (max 3) e il giudizio
pass/fail sono decisi qui da codice deterministico, mai dal modello.

MF-INTEGRATE-001: integrazione adattiva di VerificationRunner.
Se iniettato (opzionale), viene usato per selezionare i test da eseguire
in base ai file modificati dal BUILD, evitando la suite completa a ogni
tentativo. Il comportamento legacy è preservato in assenza del runner
o quando ADAPTIVE_VERIFICATION_ENABLED=False.
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from mercury_foundry import config
from mercury_foundry.agents.builder import Builder
from mercury_foundry.agents.evaluator import Evaluator, EvalResult
from mercury_foundry.ai.errors import ProviderExecutionError
from mercury_foundry.audit.logger import log_action
from mercury_foundry.policy.errors import BuildIncompleteError, LiteralConstraintViolationError
from mercury_foundry.policy.literal_constraints import LiteralConstraints, verify_literal_constraints
from mercury_foundry.sandbox.staging import (
    compute_manifest,
    compute_tree_snapshot,
    create_staging,
    diff_snapshots,
    discard_staging,
    make_read_only,
)
from mercury_foundry.sandbox.test_env import sanitize_test_output
from mercury_foundry.sandbox.workspace import Workspace
from mercury_foundry.state import models

if TYPE_CHECKING:
    from mercury_foundry.verification.runner import VerificationRunner
    from mercury_foundry.verification.models import (
        CacheKey,
        CostBudget,
        TestRunRecord,
        VerificationLevel,
        VerificationPlan,
    )


def _persist_call_record(conn, *, goal_id: int, task_id: int, attempt_id: int | None, record) -> None:
    """Persiste un ProviderCallRecord, se il provider ne ha prodotto uno.

    I provider simulati (FakeModel) non fanno chiamate esterne e lasciano
    `last_call_record = None`: in quel caso non viene scritta alcuna riga.

    `run_id` è derivato da `goal_id` (stessa convenzione di
    `Orchestrator.submit_goal`: un goal sottomesso è un run del Foundry),
    così le chiamate PLAN e BUILD dello stesso goal condividono un run_id e
    la deduplicazione in `models.create_provider_call` funziona su tutto il
    ciclo di vita del goal, non solo entro una singola fase.
    """
    models.persist_provider_call_record(
        conn,
        run_id=str(goal_id),
        goal_id=goal_id,
        task_id=task_id,
        attempt_id=attempt_id,
        record=record,
    )


@dataclass
class TaskOutcome:
    task_id: int
    status: str  # candidate_created | blocked
    attempts_used: int
    candidate_id: int | None = None


# ---------------------------------------------------------------------------
# ExecutionVerificationResult — adattatore tra VerificationRunner e ciclo
# ---------------------------------------------------------------------------

@dataclass
class ExecutionVerificationResult:
    """Risultato della verifica adattiva per un tentativo, a scopo diagnostico."""
    passed: bool
    level: str                       # VerificationLevel.label() o "LEGACY" o "BUDGET_EXHAUSTED"
    selected_tests: list[str]
    executed_tests: list[str]
    duration_seconds: float
    cache_hits: int
    escalated: bool
    escalation_reason: str | None
    budget_exhausted: bool
    fallback_to_legacy: bool
    plan_id: str | None


# ---------------------------------------------------------------------------
# ExecutionLoop
# ---------------------------------------------------------------------------

class ExecutionLoop:
    def __init__(
        self,
        conn: sqlite3.Connection,
        builder: Builder,
        evaluator: Evaluator,
        staging_base_dir: Path | None = None,
        *,
        verification_runner: "VerificationRunner | None" = None,
    ):
        self.conn = conn
        self.builder = builder
        self.evaluator = evaluator
        # Radice sotto cui vivono gli staging per-tentativo. Parametro
        # opzionale con default sensato (`config.STAGING_BASE_DIR`), così
        # tutti i chiamanti esistenti (`ExecutionLoop(conn, builder,
        # evaluator)`, in wiring.py e nei test) restano validi senza modifiche.
        self.staging_base_dir = staging_base_dir if staging_base_dir is not None else config.STAGING_BASE_DIR
        # VerificationRunner opzionale: se presente e ADAPTIVE_VERIFICATION_ENABLED,
        # viene usato per selezionare i test dopo BUILD. Se None, percorso legacy.
        self._verification_runner = verification_runner

    def run_task(self, task: sqlite3.Row) -> TaskOutcome:
        task_id = task["id"]
        goal_id = task["goal_id"]
        description = task["description"]

        # Il target REALE non viene mai scritto direttamente da questo ciclo:
        # ogni tentativo opera su una copia isolata (`staging`), creata da
        # `create_staging` come snapshot del target al momento del tentativo.
        # `self.builder.workspace` resta il riferimento al target reale
        # (immutato dal costruttore del Builder), usato qui SOLO per leggerne
        # la root — mai per scriverci.
        target_root = self.builder.workspace.root
        run_id = str(goal_id)

        goal_row = models.get_goal(self.conn, goal_id)
        literal_constraints = LiteralConstraints.from_json(
            goal_row["literal_constraints_json"] if goal_row is not None else None
        )
        parsed_command = (
            literal_constraints.parsed_test_command() if literal_constraints is not None else None
        )
        exact_test_env, exact_test_command = parsed_command if parsed_command is not None else (None, None)

        models.update_task_status(self.conn, task_id, "in_progress")
        log_action(
            self.conn,
            entity_type="task",
            entity_id=task_id,
            action="TASK_STARTED",
            actor="system",
            payload={"description": description},
        )

        previous_failure: str | None = None

        for attempt_number in range(1, config.MAX_ATTEMPTS + 1):
            attempt_id = models.create_attempt(self.conn, task_id, attempt_number, "BUILD")

            # SNAPSHOT + STAGING: ogni tentativo riceve una copia isolata e
            # fisicamente separata del target reale, PRIMA che il Builder
            # scriva un solo byte. Il target non viene toccato da nessuna
            # fase di questo tentativo (BUILD/TEST/VERIFY): solo l'Approval
            # Gate, dopo un'approvazione umana esplicita, può promuovere le
            # differenze registrate qui.
            staging = create_staging(self.staging_base_dir, run_id, attempt_id, target_root)
            staging_workspace = Workspace(staging.root)
            log_action(
                self.conn,
                entity_type="attempt",
                entity_id=attempt_id,
                action="STAGING_CREATED",
                actor="system",
                payload={
                    "attempt_number": attempt_number,
                    "staging_root": str(staging.root),
                    "target_snapshot_hash": staging.initial_snapshot_hash,
                },
            )

            log_action(
                self.conn,
                entity_type="attempt",
                entity_id=attempt_id,
                action="BUILD_STARTED",
                actor="system",
                payload={"attempt_number": attempt_number, "previous_failure": previous_failure},
            )

            try:
                build_result = self.builder.build(
                    description, attempt_number, previous_failure, literal_constraints,
                    workspace=staging_workspace,
                )
            except LiteralConstraintViolationError as exc:
                # Fallimento in BUILD: lo staging di questo tentativo viene
                # scartato subito. Il target reale non è mai stato toccato,
                # quindi non c'è nulla da ripristinare lì — resta byte-identico
                # a prima di questo tentativo.
                discard_staging(staging.root)
                # La chiamata al provider È avvenuta con successo (altrimenti sarebbe
                # stata sollevata ProviderExecutionError sopra): va comunque registrata
                # in provider_calls. È l'ENFORCEMENT deterministico del motore, non il
                # provider, a bloccare qui — la proposta divergeva da un
                # literal_constraint e non era correggibile in modo sicuro.
                _persist_call_record(
                    self.conn,
                    goal_id=goal_id,
                    task_id=task_id,
                    attempt_id=attempt_id,
                    record=self.builder.ai_provider.last_call_record,
                )
                log_action(
                    self.conn,
                    entity_type="attempt",
                    entity_id=attempt_id,
                    action="LITERAL_CONSTRAINT_BLOCKED",
                    actor="system",
                    payload={"attempt_number": attempt_number, "reason": str(exc)},
                )
                models.update_attempt(
                    self.conn, attempt_id, phase="BLOCKED", status="failure", close=True
                )
                models.update_task_status(self.conn, task_id, "blocked")
                log_action(
                    self.conn,
                    entity_type="task",
                    entity_id=task_id,
                    action="TASK_BLOCKED",
                    actor="system",
                    payload={"reason": "literal_constraint_violation", "message": str(exc)},
                )
                # Fail-closed: nessuna scrittura è avvenuta in sandbox; niente da
                # correggere con un altro tentativo automatico, serve intervento umano.
                return TaskOutcome(task_id=task_id, status="blocked", attempts_used=attempt_number)
            except BuildIncompleteError as exc:
                # La chiamata al provider È avvenuta con successo: va comunque
                # registrata in provider_calls. Il blocco qui è del gate di
                # completezza del motore (non l'enforcement dei literal_constraints
                # sopra): la proposta non ha prodotto tutto ciò che serve per un
                # BUILD atomico (es. manca un file richiesto), quindi TEST non deve
                # nemmeno partire su uno stato a metà — nessun retry automatico,
                # nessuna scrittura in sandbox.
                discard_staging(staging.root)
                _persist_call_record(
                    self.conn,
                    goal_id=goal_id,
                    task_id=task_id,
                    attempt_id=attempt_id,
                    record=self.builder.ai_provider.last_call_record,
                )
                log_action(
                    self.conn,
                    entity_type="attempt",
                    entity_id=attempt_id,
                    action="BUILD_INCOMPLETE_BLOCKED",
                    actor="system",
                    payload={"attempt_number": attempt_number, "reason": str(exc)},
                )
                models.update_attempt(
                    self.conn, attempt_id, phase="BLOCKED", status="failure", close=True
                )
                models.update_task_status(self.conn, task_id, "blocked")
                log_action(
                    self.conn,
                    entity_type="task",
                    entity_id=task_id,
                    action="TASK_BLOCKED",
                    actor="system",
                    payload={"reason": "build_incomplete", "message": str(exc)},
                )
                return TaskOutcome(task_id=task_id, status="blocked", attempts_used=attempt_number)
            except ProviderExecutionError as exc:
                discard_staging(staging.root)
                _persist_call_record(
                    self.conn,
                    goal_id=goal_id,
                    task_id=task_id,
                    attempt_id=attempt_id,
                    record=self.builder.ai_provider.last_call_record,
                )
                log_action(
                    self.conn,
                    entity_type="attempt",
                    entity_id=attempt_id,
                    action="PROVIDER_CALL_BLOCKED",
                    actor="system",
                    payload={
                        "attempt_number": attempt_number,
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                        "provider_name": self.builder.ai_provider.name,
                    },
                )
                models.update_attempt(
                    self.conn, attempt_id, phase="BLOCKED", status="failure", close=True
                )
                models.update_task_status(self.conn, task_id, "blocked")
                log_action(
                    self.conn,
                    entity_type="task",
                    entity_id=task_id,
                    action="TASK_BLOCKED",
                    actor="system",
                    payload={"reason": "provider_execution_error", "error_type": type(exc).__name__},
                )
                # Fail-closed: un errore del provider reale non consuma un tentativo
                # automatico di retry, blocca subito il task per intervento umano.
                return TaskOutcome(task_id=task_id, status="blocked", attempts_used=attempt_number)

            _persist_call_record(
                self.conn,
                goal_id=goal_id,
                task_id=task_id,
                attempt_id=attempt_id,
                record=self.builder.ai_provider.last_call_record,
            )
            models.update_attempt(
                self.conn,
                attempt_id,
                status="success",
                provider_name=build_result.proposal.provider_name,
                is_simulated=build_result.proposal.is_simulated,
                diff_summary=build_result.diff_text,
                notes=build_result.proposal.summary,
            )
            log_action(
                self.conn,
                entity_type="attempt",
                entity_id=attempt_id,
                action="BUILD_COMPLETED",
                actor="system",
                payload={
                    "provider_name": build_result.proposal.provider_name,
                    "is_simulated": build_result.proposal.is_simulated,
                    "summary": build_result.proposal.summary,
                    "files_changed": [fw.path for fw in build_result.file_writes],
                    "diff": build_result.diff_text,
                },
            )
            if literal_constraints is not None:
                # Audit trail dell'enforcement deterministico, anche quando non
                # ha dovuto correggere nulla (per trasparenza: si può sempre
                # verificare che l'assenza di correzioni non nasconda una
                # divergenza non rilevata).
                log_action(
                    self.conn,
                    entity_type="attempt",
                    entity_id=attempt_id,
                    action="LITERAL_CONSTRAINTS_ENFORCED",
                    actor="system",
                    payload={
                        "corrected": build_result.enforcement.corrected,
                        "dropped_files": build_result.enforcement.dropped_files,
                        "notes": build_result.enforcement.notes,
                    },
                )

            # File modificati dal BUILD: usati dal VerificationRunner per
            # selezionare i test. Disponibili già qui (prima di TEST).
            changed_files_for_verification = [fw.path for fw in build_result.file_writes]

            models.update_attempt(self.conn, attempt_id, phase="TEST")
            log_action(
                self.conn,
                entity_type="attempt",
                entity_id=attempt_id,
                action="TEST_STARTED",
                actor="system",
                payload={},
            )

            # ---------------------------------------------------------------------------
            # TEST: percorso adattivo (VerificationRunner) o legacy (Evaluator diretto)
            #
            # Il percorso adattivo usa VerificationRunner.plan() per selezionare i
            # test in base ai file modificati, poi Evaluator.evaluate() per eseguirli
            # nello staging isolato. Se il runner non è presente, ADAPTIVE_VERIFICATION
            # è disabilitato, o la pianificazione fallisce, si usa il percorso legacy.
            # ---------------------------------------------------------------------------
            if self._verification_runner is not None and config.ADAPTIVE_VERIFICATION_ENABLED:
                eval_result, _verif_detail = self._run_adaptive_test(
                    staging=staging,
                    changed_files=changed_files_for_verification,
                    mission_id=f"t{task_id}",
                    attempt_id=attempt_id,
                    attempt_number=attempt_number,
                    exact_test_command=exact_test_command,
                    exact_test_env=exact_test_env,
                    goal_id=goal_id,
                    task_id=task_id,
                )
            else:
                # Percorso legacy invariato.
                # TEST gira SEMPRE dentro lo staging di questo tentativo, mai
                # contro il target reale: `cwd=staging.root` è ciò che rende
                # l'intero ciclo BUILD->TEST->VERIFY una transazione isolata.
                eval_result = self.evaluator.evaluate(
                    cwd=staging.root, command=exact_test_command, env=exact_test_env
                )

            # Redazione dei segreti + troncamento PRIMA di qualunque
            # persistenza (DB o audit log): l'output reale di pytest non deve
            # mai portare con sé un valore segreto dell'ambiente host, anche
            # se quel valore fosse comparso per errore (es. in un traceback).
            safe_output, output_truncated = sanitize_test_output(eval_result.output)
            models.record_test_result(
                self.conn,
                attempt_id,
                test_name="pytest_run",
                passed=eval_result.passed,
                output=safe_output,
                duration_ms=eval_result.duration_ms,
            )
            log_action(
                self.conn,
                entity_type="attempt",
                entity_id=attempt_id,
                action="TEST_COMPLETED",
                actor="system",
                payload={
                    "passed": eval_result.passed,
                    "duration_ms": eval_result.duration_ms,
                    "output": safe_output,
                    "output_truncated": output_truncated,
                },
            )

            if not eval_result.passed:
                # TEST fallito: staging scartato, target reale mai toccato.
                discard_staging(staging.root)

            if eval_result.passed:
                literal_result = verify_literal_constraints(staging.root, literal_constraints)
                if literal_constraints is not None:
                    models.record_test_result(
                        self.conn,
                        attempt_id,
                        test_name="literal_constraints_check",
                        passed=literal_result.passed,
                        output="; ".join(literal_result.reasons) if literal_result.reasons else "ok",
                        duration_ms=0,
                    )
                    log_action(
                        self.conn,
                        entity_type="attempt",
                        entity_id=attempt_id,
                        action="LITERAL_VERIFICATION_COMPLETED",
                        actor="system",
                        payload={"passed": literal_result.passed, "reasons": literal_result.reasons},
                    )

                if not literal_result.passed:
                    # Trattata come un fallimento di verifica: stesso percorso di un
                    # fallimento pytest (FIX se restano tentativi, altrimenti blocco),
                    # perché la divergenza qui è stata rilevata SOLO in fase di
                    # verifica post-scrittura (es. vincolo parzialmente specificato,
                    # oppure file accessori ricomparsi dopo l'enforcement).
                    # Staging scartato: il target reale non è mai stato toccato.
                    discard_staging(staging.root)
                    models.update_attempt(self.conn, attempt_id, phase="FIX", status="failure", close=True)
                    previous_failure = (
                        "Verifica letterale deterministica fallita: " + "; ".join(literal_result.reasons)
                    )
                    log_action(
                        self.conn,
                        entity_type="attempt",
                        entity_id=attempt_id,
                        action="FIX_REQUIRED" if attempt_number < config.MAX_ATTEMPTS else "MAX_ATTEMPTS_REACHED",
                        actor="system",
                        payload={
                            "attempt_number": attempt_number,
                            "max_attempts": config.MAX_ATTEMPTS,
                            "reason": "literal_verification_failed",
                        },
                    )
                    continue

                models.update_attempt(self.conn, attempt_id, phase="VERIFY", status="success", close=True)
                log_action(
                    self.conn,
                    entity_type="attempt",
                    entity_id=attempt_id,
                    action="VERIFY_PASSED",
                    actor="system",
                    payload={"attempt_number": attempt_number},
                )
                models.update_task_status(self.conn, task_id, "passed")

                # Manifest completo della candidate: diff dello staging rispetto
                # al target al momento della creazione, più tutto ciò che serve
                # per ricostruire il contesto della decisione senza tornare al
                # codice. Lo staging NON viene scartato qui: sopravvive finché
                # un umano non approva (promozione) o rifiuta (pulizia) la
                # candidate — è il riferimento immutabile che approve/reject
                # useranno.
                final_snapshot = compute_tree_snapshot(staging.root)
                diff = diff_snapshots(staging.initial_snapshot, final_snapshot, staging.root)
                # Rendicontazione a livello di RUN (non solo di task): include la
                # chiamata PLAN (task_id NULL) e ogni tentativo, anche quelli
                # falliti (FIX), coerentemente con `associate_candidate_provider_calls`
                # sotto — MF-FIX-005, gap 3. Il totale token/costo del manifest deve
                # coincidere con l'insieme di chiamate poi collegato alla candidate.
                provider_calls_for_run = models.list_provider_calls_for_run(self.conn, run_id)
                total_tokens = sum(
                    (json.loads(c["usage_json"]) or {}).get("total_tokens", 0)
                    for c in provider_calls_for_run
                    if c["usage_json"]
                )
                total_cost = sum(
                    c["estimated_cost_usd"] for c in provider_calls_for_run if c["estimated_cost_usd"] is not None
                )
                last_record = self.builder.ai_provider.last_call_record
                manifest = {
                    "run_id": run_id,
                    "attempt_id": attempt_id,
                    "task_id": task_id,
                    "goal_id": goal_id,
                    "provider_name": build_result.proposal.provider_name,
                    "model": last_record.model if last_record is not None else None,
                    "is_simulated": build_result.proposal.is_simulated,
                    "target_snapshot_hash": staging.initial_snapshot_hash,
                    "staging_reference": str(staging.root),
                    "target_root": str(staging.target_root),
                    "files": diff.to_dict(),
                    # Inventario COMPLETO dello staging al momento della creazione
                    # della candidate (non solo il diff): riferimento immutabile usato
                    # da `verify_staging_integrity` all'approvazione, per rilevare
                    # QUALUNQUE alterazione dello staging avvenuta dopo questo punto
                    # (MF-FIX-005, gap 1).
                    "staging_manifest": compute_manifest(staging.root),
                    "test_result": {
                        "passed": eval_result.passed,
                        "duration_ms": eval_result.duration_ms,
                        "output": safe_output,
                        "output_truncated": output_truncated,
                    },
                    "verify_result": {"passed": literal_result.passed, "reasons": literal_result.reasons},
                    "literal_constraints_applied": literal_constraints.to_dict()
                    if literal_constraints is not None
                    else None,
                    "provider_call_ids": [c["id"] for c in provider_calls_for_run],
                    "tokens_total": total_tokens,
                    "estimated_cost_usd_total": total_cost,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }

                candidate_id = models.create_candidate(
                    self.conn,
                    goal_id,
                    task_id,
                    summary=build_result.proposal.summary,
                    provider_name=build_result.proposal.provider_name,
                    is_simulated=build_result.proposal.is_simulated,
                    run_id=run_id,
                    attempt_id=attempt_id,
                    staging_root=str(staging.root),
                    target_snapshot_hash=staging.initial_snapshot_hash,
                    manifest_json=json.dumps(manifest, ensure_ascii=False),
                )
                log_action(
                    self.conn,
                    entity_type="candidate",
                    entity_id=candidate_id,
                    action="CANDIDATE_CREATED",
                    actor="system",
                    payload={
                        "task_id": task_id,
                        "goal_id": goal_id,
                        "status": "pending_review",
                        "requires_human_approval": True,
                        "provider_name": build_result.proposal.provider_name,
                        "is_simulated": build_result.proposal.is_simulated,
                        "staging_root": str(staging.root),
                        "target_snapshot_hash": staging.initial_snapshot_hash,
                        "files_created": diff.created,
                        "files_modified": diff.modified,
                        "files_deleted": diff.deleted,
                        "target_untouched": True,
                    },
                )
                models.associate_candidate_provider_calls(self.conn, run_id, candidate_id)

                # Difesa in profondità (MF-FIX-005, gap 1): rende lo staging
                # read-only ORA che la candidate esiste, dove il filesystem lo
                # consente. Il vero controllo di sicurezza resta
                # `verify_staging_integrity` in fase di approvazione (eseguito
                # SEMPRE, indipendentemente dall'esito di questo chmod) — un
                # filesystem che non supporta i permessi POSIX non riduce la
                # protezione, solo la difesa aggiuntiva.
                read_only_failures = make_read_only(staging.root)
                if read_only_failures:
                    log_action(
                        self.conn,
                        entity_type="candidate",
                        entity_id=candidate_id,
                        action="STAGING_READ_ONLY_INCOMPLETE",
                        actor="system",
                        payload={
                            "staging_root": str(staging.root),
                            "failed_paths_count": len(read_only_failures),
                        },
                    )
                return TaskOutcome(
                    task_id=task_id,
                    status="candidate_created",
                    attempts_used=attempt_number,
                    candidate_id=candidate_id,
                )

            # Test falliti: entra in FIX se restano tentativi, altrimenti si blocca
            # (lo staging è già stato scartato sopra: il target reale non è mai
            # stato toccato).
            models.update_attempt(
                self.conn, attempt_id, phase="FIX", status="failure", close=True
            )
            previous_failure = safe_output
            log_action(
                self.conn,
                entity_type="attempt",
                entity_id=attempt_id,
                action="FIX_REQUIRED" if attempt_number < config.MAX_ATTEMPTS else "MAX_ATTEMPTS_REACHED",
                actor="system",
                payload={"attempt_number": attempt_number, "max_attempts": config.MAX_ATTEMPTS},
            )

        models.update_task_status(self.conn, task_id, "blocked")
        log_action(
            self.conn,
            entity_type="task",
            entity_id=task_id,
            action="TASK_BLOCKED",
            actor="system",
            payload={"reason": "max_attempts_reached", "max_attempts": config.MAX_ATTEMPTS},
        )
        return TaskOutcome(task_id=task_id, status="blocked", attempts_used=config.MAX_ATTEMPTS)

    # ---------------------------------------------------------------------------
    # Adaptive verification — metodi privati (MF-INTEGRATE-001)
    # ---------------------------------------------------------------------------

    def _run_adaptive_test(
        self,
        *,
        staging,
        changed_files: list[str],
        mission_id: str,
        attempt_id: int,
        attempt_number: int,
        exact_test_command: list[str] | None,
        exact_test_env: dict[str, str] | None,
        goal_id: int,
        task_id: int,
    ) -> tuple[EvalResult, ExecutionVerificationResult]:
        """Esegue i test con selezione adattiva basata sui file modificati dal BUILD.

        Usa VerificationRunner per il piano e DevelopmentCostGovernor per il
        budget. L'esecuzione effettiva usa sempre Evaluator (staging isolato).

        Politica di escalation (conservativa):
        - Scala un solo livello (es. TARGETED→IMPACTED) solo se file non mappati
          o modifica trasversale, per garantire completezza della selezione.
        - Non scala automaticamente IMPACTED→FULL per semplice fallimento di test:
          in quel caso il ciclo FIX corregge il codice.

        In caso di eccezione nella fase di pianificazione, fallback al percorso
        legacy con log di VERIFICATION_FALLBACK_LEGACY.

        Returns:
            (EvalResult, ExecutionVerificationResult) — l'EvalResult è compatibile
            con il formato atteso dal resto del ciclo (same interface as legacy).
        """
        from mercury_foundry.verification.budget import BudgetExhaustedError, MissionNotStartedError
        from mercury_foundry.verification.models import CostBudget, VerificationLevel

        vr = self._verification_runner

        def _safe_log(action: str, payload: dict) -> None:
            """Registra evento audit senza bloccare il task in caso di errore."""
            try:
                log_action(
                    self.conn,
                    entity_type="attempt",
                    entity_id=attempt_id,
                    action=action,
                    actor="system",
                    payload={**payload, "goal_id": goal_id, "task_id": task_id},
                )
            except Exception as exc:  # noqa: BLE001
                print(
                    f"[WARN] verification audit log '{action}' failed: {exc}",
                    file=sys.stderr,
                )

        def _fallback(reason: str) -> tuple[EvalResult, ExecutionVerificationResult]:
            """Esegue il percorso legacy e registra il fallback."""
            _safe_log("VERIFICATION_FALLBACK_LEGACY", {"reason": reason})
            eval_r = self.evaluator.evaluate(
                cwd=staging.root, command=exact_test_command, env=exact_test_env
            )
            return eval_r, ExecutionVerificationResult(
                passed=eval_r.passed,
                level="LEGACY",
                selected_tests=[],
                executed_tests=[],
                duration_seconds=eval_r.duration_ms / 1000.0,
                cache_hits=0,
                escalated=False,
                escalation_reason=None,
                budget_exhausted=False,
                fallback_to_legacy=True,
                plan_id=None,
            )

        # ----------------------------------------------------------------
        # 1. Inizializza la missione nel governor (idempotente)
        # ----------------------------------------------------------------
        try:
            if not vr._governor.mission_exists(mission_id):
                budget = CostBudget(
                    mission_id=mission_id,
                    max_iterations=config.MAX_ATTEMPTS * 3,
                    max_test_runs=config.MAX_ATTEMPTS * 2 + 2,
                    max_full_suite_runs=1,
                    max_failed_runs_without_improvement=config.MAX_ATTEMPTS,
                    # Non blocca: gestiamo noi l'exhaustion con termine esplicito.
                    stop_on_budget_exhaustion=False,
                )
                vr.start_mission(budget)
        except Exception as exc:  # noqa: BLE001
            return _fallback(f"errore avvio governor: {exc}")

        # ----------------------------------------------------------------
        # 2. Check budget: se esaurito, termine esplicito (non eccezione generica)
        # ----------------------------------------------------------------
        try:
            budget_status = vr.status(mission_id)
            if budget_status.exhausted:
                _safe_log("VERIFICATION_BUDGET_EXHAUSTED", {
                    "mission_id": mission_id,
                    "reason": budget_status.exhaustion_reason,
                    "attempt_number": attempt_number,
                })
                exhausted_eval = EvalResult(
                    passed=False,
                    output=f"[BUDGET ESAURITO] {budget_status.exhaustion_reason}",
                    duration_ms=0,
                )
                return exhausted_eval, ExecutionVerificationResult(
                    passed=False,
                    level="BUDGET_EXHAUSTED",
                    selected_tests=[],
                    executed_tests=[],
                    duration_seconds=0.0,
                    cache_hits=0,
                    escalated=False,
                    escalation_reason=None,
                    budget_exhausted=True,
                    fallback_to_legacy=False,
                    plan_id=None,
                )
        except (MissionNotStartedError, Exception):  # noqa: BLE001
            pass  # Non critico: se non troviamo lo stato, proseguiamo

        # ----------------------------------------------------------------
        # 3. Piano di verifica
        # ----------------------------------------------------------------
        try:
            plan = vr.plan(
                changed_files=changed_files if changed_files else None,
                mission_id=mission_id,
            )
        except Exception as exc:  # noqa: BLE001
            return _fallback(f"errore generazione piano: {exc}")

        _safe_log("VERIFICATION_PLAN_CREATED", {
            "plan_id": plan.plan_id,
            "level": plan.level.label(),
            "risk_class": plan.risk_class.value,
            "changed_files_count": len(plan.changed_files),
            "selected_tests": plan.selected_tests,
            "full_suite_skipped": plan.full_suite_skipped,
        })

        # ----------------------------------------------------------------
        # 4. STATIC: nessun test necessario (modifica a basso rischio)
        # ----------------------------------------------------------------
        if plan.level == VerificationLevel.STATIC and not plan.selected_tests:
            _safe_log("VERIFICATION_COMPLETED", {
                "plan_id": plan.plan_id,
                "level": "STATIC",
                "passed": True,
                "note": "nessun test necessario per modifiche a basso rischio",
            })
            return (
                EvalResult(
                    passed=True,
                    output="[STATIC] Nessun test necessario per modifiche a basso rischio.",
                    duration_ms=0,
                ),
                ExecutionVerificationResult(
                    passed=True,
                    level="STATIC",
                    selected_tests=[],
                    executed_tests=[],
                    duration_seconds=0.0,
                    cache_hits=0,
                    escalated=False,
                    escalation_reason=None,
                    budget_exhausted=False,
                    fallback_to_legacy=False,
                    plan_id=plan.plan_id,
                ),
            )

        # ----------------------------------------------------------------
        # 5. Nessun test selezionato (file non mappati) → fallback legacy
        #    così la suite completa del target_project gira normalmente.
        # ----------------------------------------------------------------
        if not plan.selected_tests and plan.level < VerificationLevel.FULL:
            return _fallback(
                f"nessun test mappato per livello {plan.level.label()} "
                f"(file: {plan.changed_files[:3]}...)"
            )

        # ----------------------------------------------------------------
        # 6. Costruisce il comando adattivo
        # ----------------------------------------------------------------
        cmd = self._build_adaptive_command(plan, exact_test_command)

        # ----------------------------------------------------------------
        # 7. Cache check (TARGETED e IMPACTED; non FULL né STATIC)
        # ----------------------------------------------------------------
        cache_hits = 0
        if plan.level in (VerificationLevel.TARGETED, VerificationLevel.IMPACTED):
            try:
                should_inv, _inv_reason = vr._cache.should_invalidate(plan.changed_files)
                if not should_inv:
                    cache_key = self._build_staging_cache_key(vr, staging.root, plan, cmd)
                    cached_entry = vr._cache.get(cache_key)
                    if (
                        cached_entry is not None
                        and cached_entry.valid
                        and cached_entry.result.failed == 0
                    ):
                        cache_hits = 1
                        _safe_log("VERIFICATION_CACHE_HIT", {
                            "plan_id": plan.plan_id,
                            "level": plan.level.label(),
                            "selected_tests": plan.selected_tests,
                        })
                        try:
                            vr._record_run_in_governor(mission_id, plan, cached_entry.result)
                        except Exception:  # noqa: BLE001
                            pass
                        return (
                            EvalResult(
                                passed=True,
                                output=(
                                    f"[CACHE HIT level={plan.level.label()}] "
                                    + cached_entry.result.output_summary
                                ),
                                duration_ms=int(cached_entry.result.duration_seconds * 1000),
                            ),
                            ExecutionVerificationResult(
                                passed=True,
                                level=plan.level.label(),
                                selected_tests=plan.selected_tests,
                                executed_tests=plan.selected_tests,
                                duration_seconds=cached_entry.result.duration_seconds,
                                cache_hits=1,
                                escalated=False,
                                escalation_reason=None,
                                budget_exhausted=False,
                                fallback_to_legacy=False,
                                plan_id=plan.plan_id,
                            ),
                        )
            except Exception:  # noqa: BLE001
                pass  # Cache check è best-effort

        # ----------------------------------------------------------------
        # 8. Esecuzione effettiva (via Evaluator nel staging isolato)
        # ----------------------------------------------------------------
        _safe_log("VERIFICATION_STARTED", {
            "plan_id": plan.plan_id,
            "level": plan.level.label(),
            "command": cmd,
            "selected_tests": plan.selected_tests,
        })

        eval_result = self.evaluator.evaluate(
            cwd=staging.root, command=cmd, env=exact_test_env
        )

        # ----------------------------------------------------------------
        # 9. Aggiorna governor e cache
        # ----------------------------------------------------------------
        run_record = self._make_test_run_record(plan, eval_result)
        try:
            vr._record_run_in_governor(mission_id, plan, run_record)
        except Exception:  # noqa: BLE001
            pass
        if eval_result.passed and plan.level in (VerificationLevel.TARGETED, VerificationLevel.IMPACTED):
            try:
                cache_key = self._build_staging_cache_key(vr, staging.root, plan, cmd)
                vr._cache.put(cache_key, run_record)
            except Exception:  # noqa: BLE001
                pass

        # ----------------------------------------------------------------
        # 10. Gestione successo
        # ----------------------------------------------------------------
        if eval_result.passed:
            _safe_log("VERIFICATION_COMPLETED", {
                "plan_id": plan.plan_id,
                "level": plan.level.label(),
                "passed": True,
                "duration_ms": eval_result.duration_ms,
            })
            return eval_result, ExecutionVerificationResult(
                passed=True,
                level=plan.level.label(),
                selected_tests=plan.selected_tests,
                executed_tests=plan.selected_tests,
                duration_seconds=eval_result.duration_ms / 1000.0,
                cache_hits=cache_hits,
                escalated=False,
                escalation_reason=None,
                budget_exhausted=False,
                fallback_to_legacy=False,
                plan_id=plan.plan_id,
            )

        # ----------------------------------------------------------------
        # 11. Test falliti: considera escalation conservativa
        #
        # Scala solo quando la selezione potrebbe essere incompleta:
        # - file non mappati (domain="unknown") → mapping non copre il file
        # - modifica trasversale (>1 dominio) → rischio aggregato maggiore
        #
        # Non scala per semplice fallimento di test su file mappati:
        # in quel caso il ciclo FIX corregge il codice.
        # ----------------------------------------------------------------
        escalation_level = self._next_escalation_level(plan, attempt_number)
        escalated = False
        escalation_reason: str | None = None

        if escalation_level is not None:
            try:
                budget_status = vr.status(mission_id)
                if not budget_status.exhausted:
                    escalation_reason = (
                        f"test {plan.level.label()} falliti su file con mappatura "
                        f"incompleta o modifica trasversale"
                    )
                    _safe_log("VERIFICATION_ESCALATED", {
                        "from_level": plan.level.label(),
                        "to_level": escalation_level.label(),
                        "reason": escalation_reason,
                        "plan_id": plan.plan_id,
                    })
                    escalated_plan = vr.plan(
                        changed_files=changed_files if changed_files else None,
                        force_level=escalation_level,
                        mission_id=mission_id,
                    )
                    escalated_cmd = self._build_adaptive_command(escalated_plan, exact_test_command)
                    if escalated_plan.selected_tests or escalated_plan.level == VerificationLevel.FULL:
                        escalated_eval = self.evaluator.evaluate(
                            cwd=staging.root, command=escalated_cmd, env=exact_test_env
                        )
                        escalated_record = self._make_test_run_record(escalated_plan, escalated_eval)
                        try:
                            vr._record_run_in_governor(mission_id, escalated_plan, escalated_record)
                        except Exception:  # noqa: BLE001
                            pass
                        if escalated_eval.passed:
                            try:
                                ek = self._build_staging_cache_key(
                                    vr, staging.root, escalated_plan, escalated_cmd
                                )
                                vr._cache.put(ek, escalated_record)
                            except Exception:  # noqa: BLE001
                                pass
                        _safe_log(
                            "VERIFICATION_COMPLETED" if escalated_eval.passed else "VERIFICATION_FAILED",
                            {
                                "plan_id": escalated_plan.plan_id,
                                "level": escalated_plan.level.label(),
                                "passed": escalated_eval.passed,
                                "escalated": True,
                                "duration_ms": escalated_eval.duration_ms,
                            },
                        )
                        return escalated_eval, ExecutionVerificationResult(
                            passed=escalated_eval.passed,
                            level=escalated_plan.level.label(),
                            selected_tests=escalated_plan.selected_tests,
                            executed_tests=escalated_plan.selected_tests,
                            duration_seconds=escalated_eval.duration_ms / 1000.0,
                            cache_hits=0,
                            escalated=True,
                            escalation_reason=escalation_reason,
                            budget_exhausted=False,
                            fallback_to_legacy=False,
                            plan_id=escalated_plan.plan_id,
                        )
            except (MissionNotStartedError, Exception):  # noqa: BLE001
                pass  # Escalation è best-effort

        _safe_log("VERIFICATION_FAILED", {
            "plan_id": plan.plan_id,
            "level": plan.level.label(),
            "passed": False,
            "duration_ms": eval_result.duration_ms,
        })
        return eval_result, ExecutionVerificationResult(
            passed=False,
            level=plan.level.label(),
            selected_tests=plan.selected_tests,
            executed_tests=plan.selected_tests,
            duration_seconds=eval_result.duration_ms / 1000.0,
            cache_hits=cache_hits,
            escalated=escalated,
            escalation_reason=escalation_reason,
            budget_exhausted=False,
            fallback_to_legacy=False,
            plan_id=plan.plan_id,
        )

    # ----------------------------------------------------------------
    # Helpers privati
    # ----------------------------------------------------------------

    def _build_adaptive_command(
        self,
        plan: "VerificationPlan",
        exact_test_command: list[str] | None,
    ) -> list[str]:
        """Costruisce il comando pytest per il piano adattivo.

        - FULL: usa exact_test_command se disponibile, altrimenti tests/
        - TARGETED/IMPACTED con test selezionati: pytest sui test selezionati
        - Fallback: exact_test_command o pytest -q generico
        """
        from mercury_foundry.verification.models import VerificationLevel

        if plan.level == VerificationLevel.FULL:
            if exact_test_command is not None:
                return exact_test_command
            return [sys.executable, "-m", "pytest", "--tb=short", "-q", "tests/"]

        if plan.selected_tests:
            cmd = [sys.executable, "-m", "pytest", "--tb=short", "-q"]
            cmd.extend(plan.selected_tests)
            return cmd

        # Nessun test selezionato (STATIC o fallback): usa legacy o generico
        if exact_test_command is not None:
            return exact_test_command
        return [sys.executable, "-m", "pytest", "-q"]

    def _make_test_run_record(
        self,
        plan: "VerificationPlan",
        eval_result: EvalResult,
    ) -> "TestRunRecord":
        """Crea un TestRunRecord dall'EvalResult per il governor e la cache."""
        from mercury_foundry.verification.models import TestRunRecord, _new_id, _now_iso

        output = eval_result.output
        passed_m = re.search(r"(\d+) passed", output)
        failed_m = re.search(r"(\d+) failed", output)
        errors_m = re.search(r"(\d+) error", output)
        n_passed = int(passed_m.group(1)) if passed_m else (1 if eval_result.passed else 0)
        n_failed = int(failed_m.group(1)) if failed_m else (0 if eval_result.passed else 1)
        n_errors = int(errors_m.group(1)) if errors_m else 0

        failed_ids: list[str] = []
        for line in output.splitlines():
            if line.startswith("FAILED "):
                tid = line[len("FAILED "):].strip().split(" ")[0]
                if tid:
                    failed_ids.append(tid)

        now = _now_iso()
        return TestRunRecord(
            run_id=_new_id(),
            plan_id=plan.plan_id,
            command=self._build_adaptive_command(plan, None),
            level=plan.level,
            started_at=now,
            completed_at=now,
            passed=n_passed,
            failed=n_failed,
            errors=n_errors,
            duration_seconds=eval_result.duration_ms / 1000.0,
            failed_test_ids=failed_ids,
            from_cache=False,
            exit_code=0 if eval_result.passed else 1,
            output_summary=output[-500:] if len(output) > 500 else output,
        )

    def _build_staging_cache_key(
        self,
        vr: "VerificationRunner",
        staging_root: Path,
        plan: "VerificationPlan",
        cmd: list[str],
    ) -> "CacheKey":
        """Costruisce una CacheKey usando il contenuto reale dei file nello staging.

        Il source_hash è calcolato leggendo i file effettivi in staging_root,
        non usando config.BASE_DIR — così la cache è sensibile al contenuto
        scritto dal Builder, non al nome del file.
        """
        import hashlib
        from mercury_foundry.verification.models import CacheKey

        h = hashlib.sha256()
        for rel_path in sorted(plan.changed_files):
            full = staging_root / rel_path
            if full.exists():
                h.update(full.read_bytes())
            else:
                h.update(rel_path.encode())
        source_hash = h.hexdigest()

        # Delega test_hash, lockfile_hash e config_hash alla cache del runner
        # (usa config.BASE_DIR per le dipendenze del progetto principale).
        base_key = vr._cache.build_key(
            source_files=[],
            test_files=plan.selected_tests,
            command=cmd,
        )
        return CacheKey(
            source_hash=source_hash,
            test_hash=base_key.test_hash,
            command_hash=base_key.command_hash,
            python_version=(
                f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
            ),
            lockfile_hash=base_key.lockfile_hash,
            config_hash=base_key.config_hash,
        )

    def _next_escalation_level(
        self,
        plan: "VerificationPlan",
        attempt_number: int,
    ) -> "VerificationLevel | None":
        """Determina il livello di escalation (un solo livello in più).

        Scala solo quando la selezione dei test potrebbe essere incompleta:
        - File non mappati (domain="unknown"): il mapping non copre i file
        - Modifica trasversale (>1 dominio distinto): rischio aggregato elevato

        Non scala mai automaticamente oltre IMPACTED: FULL è riservata a
        milestone/release o trigger espliciti, mai a semplici fallimenti.
        """
        from mercury_foundry.verification.models import VerificationLevel

        current = plan.level
        if current >= VerificationLevel.IMPACTED:
            return None

        has_unknown = any(fc.domain == "unknown" for fc in plan.classified_files)
        domains = {fc.domain for fc in plan.classified_files if fc.domain != "unknown"}
        is_cross_domain = len(domains) > 1

        if not (has_unknown or is_cross_domain):
            return None

        return VerificationLevel(int(current) + 1)

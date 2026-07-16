"""Registry del Replication Layer — MF-REPL-001.

CRUD raw sqlite3 per le 6 tabelle replication.
Nessun ORM. Pattern idempotency_key UNIQUE, optimistic locking su version.
"""

from __future__ import annotations

import json
import sqlite3

from mercury_foundry.replication.models import (
    DedicatedMercuryGenesisRequest,
    DedicatedMercuryIndependenceContract,
    GenesisIdempotencyReplay,
    GenesisRequestNotFoundError,
    GenesisVersionConflict,
    MercuryGeneticPackage,
    GeneticPackageSealed,
    ProductFamilyAssessment,
    ReplicationGateResult,
    GenesisStatus,
    _now_iso,
)


# ---------------------------------------------------------------------------
# Genesis Request
# ---------------------------------------------------------------------------

def create_genesis_request(
    conn: sqlite3.Connection,
    *,
    genesis_request_id: str,
    idempotency_key: str,
    correlation_id: str,
    source_mission_id: str,
    proposed_instance_name: str,
    proposed_instance_slug: str,
    genesis_reason: str,
    target_market: str,
    target_customer: str,
    business_model: str,
    constitutional_version: str,
    kernel_version: str,
    requested_by: str,
    requested_at: str,
    # JSON-encoded list fields
    validated_product_ids_json: str = "[]",
    validation_evidence_refs_json: str = "[]",
    requested_capability_bundle_ids_json: str = "[]",
    requested_knowledge_package_ids_json: str = "[]",
    requested_authority_profile_json: str = "{}",
    requested_isolation_profile_json: str = "{}",
    requested_federation_profile_json: str = "{}",
    requested_reporting_profile_json: str = "{}",
    requested_parent_relationship_json: str = "{}",
    # Optional
    source_expedition_id: str | None = None,
    product_family_key: str | None = None,
    product_validation_score: float | None = None,
    pmf_confidence: float | None = None,
    requested_genesis_profile: str = "standard",
    requested_budget_envelope: float = 0.0,
    metadata_json: str = "{}",
) -> int:
    """Crea una Genesis Request. Ritorna il rowid. Solleva GenesisIdempotencyReplay se già esiste."""
    existing = conn.execute(
        "SELECT genesis_request_id FROM dedicated_mercury_genesis_requests WHERE idempotency_key = ?",
        (idempotency_key,),
    ).fetchone()
    if existing is not None:
        raise GenesisIdempotencyReplay(existing["genesis_request_id"])

    now = _now_iso()
    cur = conn.execute(
        """
        INSERT INTO dedicated_mercury_genesis_requests (
            genesis_request_id, idempotency_key, correlation_id,
            source_mission_id, source_expedition_id,
            validated_product_ids_json, product_family_key,
            proposed_instance_name, proposed_instance_slug,
            genesis_reason, validation_evidence_refs_json,
            product_validation_score, pmf_confidence,
            target_market, target_customer, business_model,
            constitutional_version, kernel_version,
            requested_genesis_profile,
            requested_capability_bundle_ids_json, requested_knowledge_package_ids_json,
            requested_budget_envelope,
            requested_authority_profile_json, requested_isolation_profile_json,
            requested_federation_profile_json, requested_reporting_profile_json,
            requested_parent_relationship_json,
            requested_by, requested_at,
            status, created_at, updated_at, version, metadata_json
        ) VALUES (
            ?, ?, ?,
            ?, ?,
            ?, ?,
            ?, ?,
            ?, ?,
            ?, ?,
            ?, ?, ?,
            ?, ?,
            ?,
            ?, ?,
            ?,
            ?, ?,
            ?, ?,
            ?,
            ?, ?,
            'draft', ?, ?, 1, ?
        )
        """,
        (
            genesis_request_id, idempotency_key, correlation_id,
            source_mission_id, source_expedition_id,
            validated_product_ids_json, product_family_key,
            proposed_instance_name, proposed_instance_slug,
            genesis_reason, validation_evidence_refs_json,
            product_validation_score, pmf_confidence,
            target_market, target_customer, business_model,
            constitutional_version, kernel_version,
            requested_genesis_profile,
            requested_capability_bundle_ids_json, requested_knowledge_package_ids_json,
            requested_budget_envelope,
            requested_authority_profile_json, requested_isolation_profile_json,
            requested_federation_profile_json, requested_reporting_profile_json,
            requested_parent_relationship_json,
            requested_by, requested_at,
            now, now, metadata_json,
        ),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore


def get_genesis_request(
    conn: sqlite3.Connection,
    genesis_request_id: str,
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM dedicated_mercury_genesis_requests WHERE genesis_request_id = ?",
        (genesis_request_id,),
    ).fetchone()
    if row is None:
        raise GenesisRequestNotFoundError(f"Genesis request {genesis_request_id!r} non trovata")
    return row


def get_genesis_by_idempotency_key(
    conn: sqlite3.Connection,
    idempotency_key: str,
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM dedicated_mercury_genesis_requests WHERE idempotency_key = ?",
        (idempotency_key,),
    ).fetchone()


def list_genesis_requests(
    conn: sqlite3.Connection,
    *,
    limit: int = 100,
    offset: int = 0,
) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM dedicated_mercury_genesis_requests ORDER BY id ASC LIMIT ? OFFSET ?",
        (limit, offset),
    ).fetchall()


def list_genesis_by_status(
    conn: sqlite3.Connection,
    status: str,
) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM dedicated_mercury_genesis_requests WHERE status = ? ORDER BY id ASC",
        (status,),
    ).fetchall()


def list_genesis_by_source_mission(
    conn: sqlite3.Connection,
    source_mission_id: str,
) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM dedicated_mercury_genesis_requests WHERE source_mission_id = ? ORDER BY id ASC",
        (source_mission_id,),
    ).fetchall()


def archive_genesis_request(
    conn: sqlite3.Connection,
    *,
    genesis_request_id: str,
    current_version: int,
    reason: str,
    requested_by: str,
    correlation_id: str,
) -> None:
    """Archivia una genesis request tramite state machine."""
    from mercury_foundry.replication.lifecycle import apply_genesis_transition
    row = get_genesis_request(conn, genesis_request_id)
    apply_genesis_transition(
        conn,
        genesis_request_id=genesis_request_id,
        current_status=row["status"],
        current_version=current_version,
        to_status=GenesisStatus.ARCHIVED.value,
        requested_by=requested_by,
        reason=reason,
        correlation_id=correlation_id,
    )


# ---------------------------------------------------------------------------
# Genetic Package
# ---------------------------------------------------------------------------

def store_genetic_package(
    conn: sqlite3.Connection,
    *,
    package: MercuryGeneticPackage,
    genesis_request_id: str,
) -> int:
    now = _now_iso()
    cur = conn.execute(
        """
        INSERT INTO mercury_genetic_packages (
            package_id, package_version, genesis_request_id,
            source_instance_id, target_instance_id,
            status, checksum, package_json,
            generated_at, generated_by, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            package.package_id, package.package_version, genesis_request_id,
            package.source_instance_id, package.target_instance_id,
            package.status, package.checksum,
            json.dumps(package.to_dict(), ensure_ascii=False, sort_keys=True),
            package.generated_at, package.generated_by, now,
        ),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore


def get_genetic_package(
    conn: sqlite3.Connection,
    package_id: str,
) -> MercuryGeneticPackage:
    row = conn.execute(
        "SELECT * FROM mercury_genetic_packages WHERE package_id = ?", (package_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"Genetic package {package_id!r} non trovato")
    return MercuryGeneticPackage.from_dict(json.loads(row["package_json"]))


def seal_genetic_package(
    conn: sqlite3.Connection,
    package_id: str,
) -> None:
    """Sigilla il package rendendolo immutabile. Solleva GeneticPackageSealed se già sealed."""
    row = conn.execute(
        "SELECT status FROM mercury_genetic_packages WHERE package_id = ?", (package_id,)
    ).fetchone()
    if row is None:
        raise KeyError(f"Genetic package {package_id!r} non trovato")
    if row["status"] == "sealed":
        raise GeneticPackageSealed(f"Genetic package {package_id!r} è già sealed")
    conn.execute(
        "UPDATE mercury_genetic_packages SET status = 'sealed' WHERE package_id = ?",
        (package_id,),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Independence Contract
# ---------------------------------------------------------------------------

def store_independence_contract(
    conn: sqlite3.Connection,
    *,
    contract: DedicatedMercuryIndependenceContract,
    genesis_request_id: str,
) -> int:
    now = _now_iso()
    cur = conn.execute(
        """
        INSERT INTO dedicated_mercury_independence_contracts (
            contract_id, genesis_request_id, instance_id,
            status, contract_json, evaluated_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            contract.contract_id, genesis_request_id, contract.instance_id,
            contract.status.value,
            json.dumps(contract.to_dict(), ensure_ascii=False, sort_keys=True),
            contract.evaluated_at, now,
        ),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore


def get_independence_contract(
    conn: sqlite3.Connection,
    contract_id: str,
) -> DedicatedMercuryIndependenceContract:
    row = conn.execute(
        "SELECT * FROM dedicated_mercury_independence_contracts WHERE contract_id = ?",
        (contract_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"Independence contract {contract_id!r} non trovato")
    return DedicatedMercuryIndependenceContract.from_dict(json.loads(row["contract_json"]))


def get_independence_contract_by_genesis(
    conn: sqlite3.Connection,
    genesis_request_id: str,
) -> DedicatedMercuryIndependenceContract | None:
    row = conn.execute(
        "SELECT * FROM dedicated_mercury_independence_contracts "
        "WHERE genesis_request_id = ? ORDER BY id DESC LIMIT 1",
        (genesis_request_id,),
    ).fetchone()
    if row is None:
        return None
    return DedicatedMercuryIndependenceContract.from_dict(json.loads(row["contract_json"]))


# ---------------------------------------------------------------------------
# Product Family Assessment
# ---------------------------------------------------------------------------

def store_family_assessment(
    conn: sqlite3.Connection,
    *,
    assessment: ProductFamilyAssessment,
    genesis_request_id: str,
) -> int:
    now = _now_iso()
    cur = conn.execute(
        """
        INSERT INTO product_family_assessments (
            assessment_id, genesis_request_id,
            coherence_score, recommendation,
            assessment_json, evaluated_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            assessment.assessment_id, genesis_request_id,
            assessment.coherence_score, assessment.recommendation.value,
            json.dumps(assessment.to_dict(), ensure_ascii=False, sort_keys=True),
            assessment.evaluated_at, now,
        ),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore


def get_family_assessment(
    conn: sqlite3.Connection,
    assessment_id: str,
) -> ProductFamilyAssessment:
    row = conn.execute(
        "SELECT * FROM product_family_assessments WHERE assessment_id = ?",
        (assessment_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"Family assessment {assessment_id!r} non trovato")
    return ProductFamilyAssessment.from_dict(json.loads(row["assessment_json"]))


# ---------------------------------------------------------------------------
# Replication Gate Result
# ---------------------------------------------------------------------------

def store_gate_result(
    conn: sqlite3.Connection,
    *,
    result: ReplicationGateResult,
    genesis_request_id: str,
) -> int:
    now = _now_iso()
    cur = conn.execute(
        """
        INSERT INTO replication_gate_results (
            gate_result_id, genesis_request_id,
            approved, gate_status, validation_score,
            independence_status, result_json,
            evaluated_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            result.gate_result_id, genesis_request_id,
            1 if result.approved else 0,
            result.status.value,
            result.validation_score,
            result.independence_status.value,
            json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True),
            result.evaluated_at, now,
        ),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore


def get_gate_result(
    conn: sqlite3.Connection,
    gate_result_id: str,
) -> ReplicationGateResult:
    row = conn.execute(
        "SELECT * FROM replication_gate_results WHERE gate_result_id = ?",
        (gate_result_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"Gate result {gate_result_id!r} non trovato")
    d = json.loads(row["result_json"])
    from mercury_foundry.replication.models import GateStatus, IndependenceStatus
    return ReplicationGateResult(
        gate_result_id=d["gate_result_id"],
        approved=d["approved"],
        status=GateStatus(d["status"]),
        validation_score=d["validation_score"],
        independence_status=IndependenceStatus(d["independence_status"]),
        family_coherence_status=d["family_coherence_status"],
        constitutional_status=d["constitutional_status"],
        authority_status=d["authority_status"],
        economic_readiness=d["economic_readiness"],
        unresolved_assumptions=d.get("unresolved_assumptions", []),
        blockers=d.get("blockers", []),
        warnings=d.get("warnings", []),
        required_actions=d.get("required_actions", []),
        approved_genesis_profile=d.get("approved_genesis_profile"),
        evaluated_at=d["evaluated_at"],
        explanation=d["explanation"],
        constitutional_validation_id=d.get("constitutional_validation_id"),
        authority_decision_id=d.get("authority_decision_id"),
    )

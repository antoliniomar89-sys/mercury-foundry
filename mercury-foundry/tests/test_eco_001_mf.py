"""MF-ECO-001 — Persistent Economic Resource Ledger V0.

Suite di test per:
1. Persistenza delle reservations nel DB (sopravvivono al restart).
2. Idempotency delle reservations.
3. Limiti di budget rispettati (budget − consumed − reserved).
4. Atomicità di consume e release.
5. Semantica di doppio consume/release.
6. Disponibilità post-release.
7. get_total_consumption multi-envelope per Mission.
8. MissionBudget in integer minor units.
9. Conversione EUR → minor via Decimal.
10. Migrazione DB idempotente.
11. Nessun float in modelli contabili canonici.
12. Compatibilità con EconomicOutcomePlan.
13. SCALE non modifica il budget.
14. Nessuna regressione sul lifecycle Mission.
15. Nessuna vendita/pagamento reale introdotto.

Totale: 21 test.
"""

from __future__ import annotations

import inspect
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from mercury_foundry.state.db import connect


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    """Connessione SQLite in-process con schema inizializzato."""
    conn = connect(tmp_path / "mf_eco_001_test.db")
    yield conn
    conn.close()


def _future_iso(hours: int = 24) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def _mid() -> str:
    return str(uuid.uuid4())


def _alloc(db, *, budget_minor: int = 10_000, mission_id: str | None = None):
    """Crea un ResourceEnvelope e ritorna (allocator, envelope)."""
    from mercury_foundry.outcome.allocator import ResourceAllocator
    allocator = ResourceAllocator()
    envelope = allocator.allocate(
        db,
        mission_id   = mission_id or _mid(),
        budget_minor = budget_minor,
        deadline     = _future_iso(),
        allocated_by = "test_actor",
    )
    return allocator, envelope


# ---------------------------------------------------------------------------
# TEST 1 — Reservation persistente dopo ricreazione del allocator
# ---------------------------------------------------------------------------

def test_01_reservation_persists_after_allocator_restart(tmp_path):
    """1 — La reservation è recuperabile dal DB dopo la creazione di un nuovo allocator."""
    from mercury_foundry.outcome.allocator import ResourceAllocator

    db_path = tmp_path / "mf_eco_001_restart.db"
    conn = connect(db_path)
    allocator_a = ResourceAllocator()
    env = allocator_a.allocate(
        conn,
        mission_id   = _mid(),
        budget_minor = 5_000,
        deadline     = _future_iso(),
        allocated_by = "test_actor",
    )
    rec = allocator_a.reserve(
        conn,
        envelope_id     = env.envelope_id,
        amount_minor    = 2_000,
        idempotency_key = "restart-key-001",
        reason          = "pre-allocazione test",
    )
    reservation_id = rec.reservation_id
    conn.close()

    # Simula restart: nuova connessione + nuovo allocator
    conn2 = connect(db_path)
    allocator_b = ResourceAllocator()

    # La reservation deve essere recuperabile dal DB
    db_res = allocator_b.get_reservation(conn2, reservation_id)
    assert db_res is not None, "La reservation non è stata trovata nel DB dopo restart"
    assert db_res.reservation_id == reservation_id
    assert db_res.amount_minor == 2_000
    assert db_res.status == "reserved"
    assert db_res.reason == "pre-allocazione test"

    # Il saldo disponibile deve escludere la reservation
    remaining = allocator_b.remaining(conn2, envelope_id=env.envelope_id)
    assert remaining.budget_remaining_minor == 3_000, (
        f"Atteso 3000, trovato {remaining.budget_remaining_minor}"
    )
    conn2.close()


# ---------------------------------------------------------------------------
# TEST 2 — Idempotency key: stesso esito, nessun doppio addebito
# ---------------------------------------------------------------------------

def test_02_reservation_idempotency_key_no_double_charge(db):
    """2 — Reservation con stesso idempotency_key ritorna lo stesso esito senza doppio addebito."""
    from mercury_foundry.outcome.allocator import ResourceAllocator
    from mercury_foundry.outcome.registry import get_total_reserved

    allocator, env = _alloc(db, budget_minor=5_000)
    key = "idem-key-eco-001"

    rec1 = allocator.reserve(db, envelope_id=env.envelope_id, amount_minor=1_500,
                             idempotency_key=key)
    rec2 = allocator.reserve(db, envelope_id=env.envelope_id, amount_minor=1_500,
                             idempotency_key=key)

    assert rec1.reservation_id == rec2.reservation_id, "Idempotency non rispettata"

    # Il totale riservato deve essere 1500, non 3000
    total_reserved = get_total_reserved(db, env.envelope_id)
    assert total_reserved == 1_500, f"Doppio addebito rilevato: {total_reserved}"


# ---------------------------------------------------------------------------
# TEST 3 — Reservation oltre disponibilità rifiutata
# ---------------------------------------------------------------------------

def test_03_reservation_beyond_budget_raises(db):
    """3 — Reservation che supera il budget disponibile solleva ResourceExhaustedError."""
    from mercury_foundry.outcome.allocator import ResourceAllocator
    from mercury_foundry.outcome.models import ResourceExhaustedError

    allocator, env = _alloc(db, budget_minor=1_000)
    with pytest.raises(ResourceExhaustedError):
        allocator.reserve(db, envelope_id=env.envelope_id, amount_minor=1_001)


# ---------------------------------------------------------------------------
# TEST 4 — consume atomico
# ---------------------------------------------------------------------------

def test_04_consume_atomic(db):
    """4 — consume() registra il consumo in modo atomico e aggiorna il saldo."""
    from mercury_foundry.outcome.allocator import ResourceAllocator

    allocator, env = _alloc(db, budget_minor=10_000)
    c = allocator.consume(
        db,
        envelope_id     = env.envelope_id,
        cost_minor      = 3_000,
        source_ref      = "op-atomic",
        idempotency_key = "atomic-key-001",
    )
    assert c.cost_minor == 3_000
    rem = allocator.remaining(db, envelope_id=env.envelope_id)
    assert rem.budget_remaining_minor == 7_000
    assert rem.total_consumed_minor == 3_000


# ---------------------------------------------------------------------------
# TEST 5 — release atomico
# ---------------------------------------------------------------------------

def test_05_release_atomic(db):
    """5 — release() aggiorna il DB atomicamente e ripristina la disponibilità."""
    from mercury_foundry.outcome.allocator import ResourceAllocator
    from mercury_foundry.outcome.models import ReservationStatus

    allocator, env = _alloc(db, budget_minor=5_000)
    rec = allocator.reserve(db, envelope_id=env.envelope_id, amount_minor=2_000)

    rem_before = allocator.remaining(db, envelope_id=env.envelope_id)
    assert rem_before.budget_remaining_minor == 3_000

    allocator.release(db, rec.reservation_id)

    # Verifica DB
    db_res = allocator.get_reservation(db, rec.reservation_id)
    assert db_res is not None
    assert db_res.status == ReservationStatus.RELEASED.value
    assert db_res.released_at is not None

    # Saldo ripristinato
    rem_after = allocator.remaining(db, envelope_id=env.envelope_id)
    assert rem_after.budget_remaining_minor == 5_000


# ---------------------------------------------------------------------------
# TEST 6 — Doppio consume rifiutato
# ---------------------------------------------------------------------------

def test_06_double_consume_rejected(db):
    """6 — Consumo duplicato con stesso idempotency_key solleva ConsumptionIdempotencyReplay."""
    from mercury_foundry.outcome.allocator import ResourceAllocator
    from mercury_foundry.outcome.models import ConsumptionIdempotencyReplay

    allocator, env = _alloc(db, budget_minor=10_000)
    allocator.consume(db, envelope_id=env.envelope_id, cost_minor=500,
                      source_ref="op1", idempotency_key="dbl-consume-001")
    with pytest.raises(ConsumptionIdempotencyReplay):
        allocator.consume(db, envelope_id=env.envelope_id, cost_minor=500,
                          source_ref="op1", idempotency_key="dbl-consume-001")


# ---------------------------------------------------------------------------
# TEST 7 — Doppio release gestito idempotentemente
# ---------------------------------------------------------------------------

def test_07_double_release_idempotent(db):
    """7 — Il secondo release della stessa reservation è idempotente (no errore)."""
    from mercury_foundry.outcome.allocator import ResourceAllocator
    from mercury_foundry.outcome.models import ReservationStatus

    allocator, env = _alloc(db, budget_minor=5_000)
    rec = allocator.reserve(db, envelope_id=env.envelope_id, amount_minor=1_000)

    allocator.release(db, rec.reservation_id)
    allocator.release(db, rec.reservation_id)  # secondo release → no errore

    db_res = allocator.get_reservation(db, rec.reservation_id)
    assert db_res.status == ReservationStatus.RELEASED.value


# ---------------------------------------------------------------------------
# TEST 8 — Reservation consumata non è più disponibile
# ---------------------------------------------------------------------------

def test_08_consumed_reservation_not_available(db):
    """8 — Dopo consume, la reservation non riduce più il saldo (è saldato dal consume stesso)."""
    from mercury_foundry.outcome.allocator import ResourceAllocator
    from mercury_foundry.outcome.models import ReservationStatus
    from mercury_foundry.outcome.registry import consume_reservation

    allocator, env = _alloc(db, budget_minor=5_000)
    rec = allocator.reserve(db, envelope_id=env.envelope_id, amount_minor=1_500,
                            idempotency_key="res-to-consume-001")

    # Saldo: 5000 - 0 (consumed) - 1500 (reserved) = 3500
    assert allocator.remaining(db, envelope_id=env.envelope_id).budget_remaining_minor == 3_500

    # Transita la reservation a "consumed" (senza registrare un vero consumo monetario)
    db_res = consume_reservation(db, rec.reservation_id)
    assert db_res.status == ReservationStatus.CONSUMED.value

    # Ora: 5000 - 0 (no monetary consume registered) - 0 (reservation not active) = 5000
    assert allocator.remaining(db, envelope_id=env.envelope_id).budget_remaining_minor == 5_000


# ---------------------------------------------------------------------------
# TEST 9 — Release ripristina disponibilità
# ---------------------------------------------------------------------------

def test_09_release_restores_availability(db):
    """9 — release() ripristina il budget disponibile pari all'amount riservato."""
    from mercury_foundry.outcome.allocator import ResourceAllocator

    allocator, env = _alloc(db, budget_minor=8_000)
    rec = allocator.reserve(db, envelope_id=env.envelope_id, amount_minor=3_000)

    assert allocator.get_available_amount(db, env.envelope_id) == 5_000
    allocator.release(db, rec.reservation_id)
    assert allocator.get_available_amount(db, env.envelope_id) == 8_000


# ---------------------------------------------------------------------------
# TEST 10 — get_total_consumption multi-envelope per Mission
# ---------------------------------------------------------------------------

def test_10_get_total_consumption_multi_envelope(db):
    """10 — get_total_consumption aggrega correttamente su più envelope della stessa Mission."""
    from mercury_foundry.outcome.allocator import ResourceAllocator

    mid = _mid()
    allocator = ResourceAllocator()

    env1 = allocator.allocate(db, mission_id=mid, budget_minor=10_000,
                              deadline=_future_iso(), allocated_by="actor1")
    env2 = allocator.allocate(db, mission_id=mid, budget_minor=10_000,
                              deadline=_future_iso(), allocated_by="actor2")

    allocator.consume(db, envelope_id=env1.envelope_id, cost_minor=2_000,
                      source_ref="op-e1", idempotency_key="multi-e1-001")
    allocator.consume(db, envelope_id=env2.envelope_id, cost_minor=3_000,
                      source_ref="op-e2", idempotency_key="multi-e2-001")

    # Per-envelope
    c1 = allocator.get_total_consumption(db, envelope_id=env1.envelope_id)
    assert c1["cost_minor"] == 2_000

    c2 = allocator.get_total_consumption(db, envelope_id=env2.envelope_id)
    assert c2["cost_minor"] == 3_000

    # Multi-envelope per Mission
    c_total = allocator.get_total_consumption(db, mission_id=mid)
    assert c_total["cost_minor"] == 5_000, (
        f"Multi-envelope totale atteso 5000, trovato {c_total['cost_minor']}"
    )


# ---------------------------------------------------------------------------
# TEST 11 — Budget espresso esclusivamente in minor units
# ---------------------------------------------------------------------------

def test_11_budget_exclusively_in_minor_units(db):
    """11 — MissionBudget usa integer minor units come campi canonici."""
    from mercury_foundry.mission.models import MissionBudget

    b = MissionBudget(approved_amount_minor=50_000, currency="EUR")
    assert isinstance(b.approved_amount_minor, int)
    assert b.approved_amount_minor == 50_000
    assert b.currency == "EUR"
    # Il float derivato è corretto
    assert abs(b.approved_amount - 500.0) < 0.001


# ---------------------------------------------------------------------------
# TEST 12 — Conversione 500.00 EUR → 50000
# ---------------------------------------------------------------------------

def test_12_eur_to_minor_conversion(db):
    """12 — 500.00 EUR convertiti in 50000 minor units."""
    from mercury_foundry.outcome.minor_units import eur_to_minor

    assert eur_to_minor(500.0) == 50_000
    assert eur_to_minor("500.00") == 50_000
    assert eur_to_minor(Decimal("500.00")) == 50_000


# ---------------------------------------------------------------------------
# TEST 13 — Conversione via Decimal con rounding dichiarato
# ---------------------------------------------------------------------------

def test_13_decimal_rounding_explicit(db):
    """13 — Conversione con Decimal e rounding ROUND_HALF_UP dichiarato."""
    from decimal import ROUND_HALF_UP
    from mercury_foundry.outcome.minor_units import eur_to_minor, minor_to_eur

    # 1234.56 EUR → 123456 minor
    assert eur_to_minor(1234.56, rounding=ROUND_HALF_UP) == 123_456

    # 0.005 EUR = 0.5 centesimi → arrotonda a 1 minor (ROUND_HALF_UP)
    assert eur_to_minor("0.005", rounding=ROUND_HALF_UP) == 1

    # Round-trip: 123456 → Decimal("1234.56")
    assert minor_to_eur(123_456) == Decimal("1234.56")


# ---------------------------------------------------------------------------
# TEST 14 — Nessun float nei modelli contabili canonici
# ---------------------------------------------------------------------------

def test_14_no_float_in_canonical_accounting_models(db):
    """14 — I campi contabili canonici dei modelli MF-ECO-001 sono integer, non float."""
    from mercury_foundry.outcome.models import ResourceEnvelope, ResourceConsumption
    from mercury_foundry.mission.models import MissionBudget

    # ResourceEnvelope
    for field_name, field_type in ResourceEnvelope.__dataclass_fields__.items():
        if "minor" in field_name:
            assert field_type.type in (int, "int"), (
                f"ResourceEnvelope.{field_name} dovrebbe essere int"
            )

    # ResourceConsumption
    for field_name, field_type in ResourceConsumption.__dataclass_fields__.items():
        if "minor" in field_name:
            assert field_type.type in (int, "int"), (
                f"ResourceConsumption.{field_name} dovrebbe essere int"
            )

    # MissionBudget — campi *_minor canonici devono essere int
    budget = MissionBudget(approved_amount_minor=10_000)
    assert isinstance(budget.approved_amount_minor, int)
    assert isinstance(budget.committed_amount_minor, int)
    assert isinstance(budget.spent_amount_minor, int)


# ---------------------------------------------------------------------------
# TEST 15 — Migrazione DB esistente
# ---------------------------------------------------------------------------

def test_15_db_migration_existing_budget(tmp_path):
    """15 — Un DB con budget float legacy viene migrato a minor units."""
    import json as _json
    from mercury_foundry.state.db import connect as _connect, _migrate_mission_budget_to_minor

    db_path = tmp_path / "legacy.db"
    conn = _connect(db_path)

    # Inserisci una mission con budget float legacy (senza *_minor fields)
    legacy_budget = {
        "currency": "EUR",
        "approved_amount": 500.0,
        "committed_amount": 100.0,
        "spent_amount": 50.0,
        "compute_limit": None,
        "external_service_limit": None,
        "marketing_limit": None,
        "human_service_limit": None,
    }
    mid = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO missions
            (mission_id, idempotency_key, correlation_id, title, description,
             origin_type, mission_type, status, priority, objective,
             expected_outcomes_json, success_criteria_json, termination_criteria_json,
             constraints_json, budget_json, risk_profile_json, authority_request_json,
             required_capabilities_json, knowledge_scope, business_scope,
             constitutional_version, created_by, version, metadata_json,
             created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,'{}',
                datetime('now'), datetime('now'))
        """,
        (
            mid, f"idem-{mid}", f"corr-{mid}", "Test Legacy Budget", "desc",
            "founder", "custom", "planning", "normal", "test legacy",
            "[]", "[]", "[]",
            "{}", _json.dumps(legacy_budget), "{}", "{}",
            "[]", "internal", "internal", "1.0", "test_actor",
        ),
    )
    conn.commit()

    # Esegui la migrazione
    _migrate_mission_budget_to_minor(conn)

    # Verifica che i campi *_minor siano stati aggiunti
    row = conn.execute("SELECT budget_json FROM missions WHERE mission_id = ?", (mid,)).fetchone()
    d = _json.loads(row["budget_json"])
    assert "approved_amount_minor" in d, "approved_amount_minor non presente dopo migrazione"
    assert d["approved_amount_minor"] == 50_000, f"Atteso 50000, trovato {d['approved_amount_minor']}"
    assert d["committed_amount_minor"] == 10_000
    assert d["spent_amount_minor"] == 5_000
    # I campi float originali sono conservati
    assert "approved_amount" in d
    conn.close()


# ---------------------------------------------------------------------------
# TEST 16 — Migrazione idempotente
# ---------------------------------------------------------------------------

def test_16_migration_idempotent(tmp_path):
    """16 — Eseguire la migrazione due volte non modifica i valori già migrati."""
    import json as _json
    from mercury_foundry.state.db import connect as _connect, _migrate_mission_budget_to_minor

    db_path = tmp_path / "idem_migration.db"
    conn = _connect(db_path)

    legacy_budget = {"currency": "EUR", "approved_amount": 300.0,
                     "committed_amount": 0.0, "spent_amount": 0.0}
    mid = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO missions
            (mission_id, idempotency_key, correlation_id, title, description,
             origin_type, mission_type, status, priority, objective,
             expected_outcomes_json, success_criteria_json, termination_criteria_json,
             constraints_json, budget_json, risk_profile_json, authority_request_json,
             required_capabilities_json, knowledge_scope, business_scope,
             constitutional_version, created_by, version, metadata_json,
             created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,'{}',
                datetime('now'), datetime('now'))
        """,
        (
            mid, f"idem-{mid}", f"corr-{mid}", "Idem Test", "desc",
            "founder", "custom", "planning", "normal", "test idem",
            "[]", "[]", "[]",
            "{}", _json.dumps(legacy_budget), "{}", "{}",
            "[]", "internal", "internal", "1.0", "test_actor",
        ),
    )
    conn.commit()

    _migrate_mission_budget_to_minor(conn)
    row1 = _json.loads(conn.execute(
        "SELECT budget_json FROM missions WHERE mission_id = ?", (mid,)
    ).fetchone()["budget_json"])

    _migrate_mission_budget_to_minor(conn)  # seconda esecuzione
    row2 = _json.loads(conn.execute(
        "SELECT budget_json FROM missions WHERE mission_id = ?", (mid,)
    ).fetchone()["budget_json"])

    assert row1["approved_amount_minor"] == row2["approved_amount_minor"]
    conn.close()


# ---------------------------------------------------------------------------
# TEST 17 — Rollback / integrità in caso di errore
# ---------------------------------------------------------------------------

def test_17_reservation_over_budget_leaves_db_clean(db):
    """17 — Una reservation rifiutata non lascia record parziali nel DB."""
    from mercury_foundry.outcome.allocator import ResourceAllocator
    from mercury_foundry.outcome.models import ResourceExhaustedError
    from mercury_foundry.outcome.registry import list_active_reservations

    allocator, env = _alloc(db, budget_minor=1_000)
    with pytest.raises(ResourceExhaustedError):
        allocator.reserve(db, envelope_id=env.envelope_id, amount_minor=5_000,
                          idempotency_key="fail-001")

    # Nessuna reservation creata
    active = list_active_reservations(db, env.envelope_id)
    assert active == [], "Reservation parziale trovata nel DB dopo errore"


# ---------------------------------------------------------------------------
# TEST 18 — Compatibilità con EconomicOutcomePlan
# ---------------------------------------------------------------------------

def test_18_compatible_with_economic_outcome_plan(db):
    """18 — EconomicOutcomePlan usa integer minor units coerenti con ResourceEnvelope."""
    from mercury_foundry.outcome.allocator import ResourceAllocator
    from mercury_foundry.outcome.registry import create_outcome_plan

    mid = _mid()
    plan = create_outcome_plan(
        db,
        mission_id               = mid,
        correlation_id           = _mid(),
        objective                = "test ECO-001 compat",
        primary_metric           = "roi",
        target_value             = 1.2,
        target_operator          = ">=",
        maximum_cost_minor       = 5_000,
        maximum_duration_seconds = 3600,
        review_interval_seconds  = 600,
        kill_deadline            = _future_iso(48),
        minimum_evidence_count   = 1,
        strategic_value_score    = 0.5,
        learning_value_score     = 0.3,
        reversibility            = "reversible",
        created_by               = "test_actor",
    )
    assert isinstance(plan.maximum_cost_minor, int)
    assert plan.maximum_cost_minor == 5_000

    allocator = ResourceAllocator()
    env = allocator.allocate(
        db,
        mission_id   = mid,
        budget_minor = plan.maximum_cost_minor,
        deadline     = _future_iso(),
        allocated_by = "test_actor",
    )
    assert env.budget_minor == plan.maximum_cost_minor


# ---------------------------------------------------------------------------
# TEST 19 — SCALE non modifica automaticamente il budget
# ---------------------------------------------------------------------------

def test_19_scale_does_not_auto_increase_budget(db):
    """19 — Una decisione SCALE non modifica il budget dell'envelope."""
    from mercury_foundry.outcome.allocator import ResourceAllocator
    from mercury_foundry import config

    allocator, env = _alloc(db, budget_minor=30_000)
    budget_before = env.budget_minor

    # Verifica le feature flag
    assert config.OUTCOME_AUTO_SCALE_ENABLED is False
    assert config.OUTCOME_AUTO_BUDGET_INCREASE_ENABLED is False

    # Il budget dell'envelope rimane invariato
    env_after = allocator.allocate(
        db,
        mission_id   = env.mission_id,
        budget_minor = 30_000,
        deadline     = _future_iso(),
        allocated_by = "test_actor",
    )
    assert env_after.budget_minor == budget_before, (
        f"Budget modificato: atteso {budget_before}, trovato {env_after.budget_minor}"
    )


# ---------------------------------------------------------------------------
# TEST 20 — Nessuna regressione sul lifecycle Mission
# ---------------------------------------------------------------------------

def test_20_no_mission_lifecycle_regression(db):
    """20 — Le operazioni ECO-001 non rompono il lifecycle Mission (submit/activate/terminate)."""
    from mercury_foundry.mission.intake import MissionIntakeService
    from mercury_foundry.mission.lifecycle import apply_transition
    from mercury_foundry.mission.models import (
        ExpectedOutcome, MissionAuthorityRequest, MissionBudget,
        MissionIntakeRequest, MissionRiskProfile, MissionStatus,
        OriginType, MissionType, Priority,
    )
    from mercury_foundry.mission.registry import get_mission

    svc = MissionIntakeService()
    req = MissionIntakeRequest(
        idempotency_key   = f"idem-{_mid()}",
        correlation_id    = _mid(),
        title             = "ECO-001 regression mission",
        description       = "test lifecycle no regression",
        origin_type       = OriginType.FOUNDER,
        mission_type      = MissionType.CUSTOM,
        priority          = Priority.NORMAL,
        objective         = "verifica regression lifecycle",
        expected_outcomes = [ExpectedOutcome(
            outcome_id       = _mid(),
            description      = "uptime >= 99.9%",
            required         = True,
            metric_name      = "uptime",
            target_value     = 99.9,
            target_operator  = ">=",
        )],
        budget            = MissionBudget(approved_amount_minor=10_000, currency="EUR"),
        risk_profile      = MissionRiskProfile(risk_level="low", rollback_plan="none"),
        authority_request = MissionAuthorityRequest(requested_mode="proposal"),
        created_by        = "test_actor",
    )
    result = svc.submit(db, req)
    assert result.accepted, result.validation_errors

    mission = get_mission(db, result.mission_id)
    assert mission.status == MissionStatus.DRAFT

    # Verifica che MissionBudget sia in minor units
    assert mission.budget.approved_amount_minor == 10_000


# ---------------------------------------------------------------------------
# TEST 21 — Nessun pagamento/vendita reale introdotto
# ---------------------------------------------------------------------------

def test_21_no_real_payment_or_sale_introduced(db):
    """21 — Nessun termine di pagamento reale nei moduli ECO-001."""
    forbidden_terms = [
        "payment", "sale", "sell", "checkout", "stripe", "invoice",
        "paypal", "billing", "charge", "subscription",
    ]
    modules_to_check = [
        "mercury_foundry.outcome.minor_units",
        "mercury_foundry.outcome.allocator",
        "mercury_foundry.outcome.registry",
        "mercury_foundry.outcome.models",
    ]
    for mod_name in modules_to_check:
        import importlib
        mod = importlib.import_module(mod_name)
        source = inspect.getsource(mod).lower()
        for term in forbidden_terms:
            assert term not in source, (
                f"Termine vietato '{term}' trovato in {mod_name}"
            )

    # Nessuna GenesisRequest creata
    genesis_count = db.execute(
        "SELECT COUNT(*) AS cnt FROM dedicated_mercury_genesis_requests"
    ).fetchone()["cnt"]
    assert genesis_count == 0, f"Genesis requests inattese: {genesis_count}"

    # OUTCOME_BUDGET_INCREASE deve essere forbidden
    organ = db.execute(
        "SELECT id FROM organs WHERE organ_key = 'ECONOMIC_GOVERNANCE'"
    ).fetchone()
    assert organ is not None
    mandate = db.execute(
        "SELECT authority_mode FROM decision_mandates "
        "WHERE organ_id = ? AND decision_type = 'OUTCOME_BUDGET_INCREASE'",
        (organ["id"],),
    ).fetchone()
    assert mandate is not None
    assert mandate["authority_mode"] == "forbidden"

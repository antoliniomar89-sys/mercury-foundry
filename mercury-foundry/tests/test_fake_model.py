from mercury_foundry.ai.fake_model import FakeModel


def test_fake_model_is_declared_as_simulated():
    model = FakeModel()
    assert model.is_simulated is True
    assert model.name == "fake-deterministic"


def test_plan_for_health_check_goal():
    model = FakeModel()
    plan = model.propose_plan("aggiungi una capability health check")
    assert len(plan) == 1
    assert "health check" in plan[0].lower()


def test_patch_attempt_1_has_known_bug_attempt_2_is_fixed():
    model = FakeModel()

    patch_1 = model.propose_patch(
        "Implementare la capability di health check", {"attempt_number": 1, "previous_failure": None}
    )
    module_1 = next(f for f in patch_1.files if f.path == "health.py")
    assert '"timestamp"' in module_1.content
    assert '"checked_at"' not in module_1.content

    patch_2 = model.propose_patch(
        "Implementare la capability di health check",
        {"attempt_number": 2, "previous_failure": "KeyError: 'checked_at'"},
    )
    module_2 = next(f for f in patch_2.files if f.path == "health.py")
    assert '"checked_at"' in module_2.content

    assert patch_1.is_simulated is True
    assert patch_2.is_simulated is True

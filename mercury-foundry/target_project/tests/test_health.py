import health


def test_check_health_returns_ok_status():
    result = health.check_health()
    assert result["status"] == "ok"
    assert "checked_at" in result

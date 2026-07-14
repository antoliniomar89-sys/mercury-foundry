"""Capability: health check (corretta dopo fallimento reale dei test)."""

from datetime import datetime, timezone


def check_health() -> dict:
    return {
        "status": "ok",
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


if __name__ == "__main__":
    import json

    print(json.dumps(check_health()))

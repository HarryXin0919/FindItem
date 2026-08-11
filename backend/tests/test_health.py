"""S02 acceptance tests: the backend imports cleanly, /health returns 200,
and no endpoint or settings surface leaks a secret."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import ARCHITECTURE, NODE_DESCRIPTION, Settings, get_settings
from app.main import app

client = TestClient(app)


def test_health_returns_200_ok():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_architecture_matches_the_locked_topology():
    r = client.get("/api/architecture")
    assert r.status_code == 200
    body = r.json()
    assert body["controllers"] == 5
    assert body["leds_per_controller"] == 10
    assert body["total_drawers"] == 50
    assert body["node"] == NODE_DESCRIPTION
    assert body["topology"] == ARCHITECTURE


def test_total_drawers_is_derived_not_hardcoded():
    s = get_settings()
    assert s.total_drawers == s.controller_count * s.leds_per_controller == 50


def test_topology_is_locked_against_configuration_changes():
    for field, bad in (("controller_count", 4), ("leds_per_controller", 12)):
        try:
            Settings(**{field: bad})
        except ValueError as exc:
            assert "ADR" in str(exc)
        else:  # pragma: no cover - only runs if the lock regresses
            raise AssertionError(f"{field}={bad} should have been rejected")


def test_config_endpoint_never_exposes_the_database_password():
    r = client.get("/api/config")
    assert r.status_code == 200
    body = r.json()
    assert "findit_dev" not in str(body)
    assert "***" in body["database"]
    assert "mqtt_password" not in body
    assert "database_url" not in body


def test_redacted_database_url_keeps_host_and_drops_password():
    s = Settings(database_url="postgresql+psycopg://u:supersecret@db.example:5432/findit")
    red = s.redacted_database_url
    assert "supersecret" not in red
    assert "db.example:5432" in red
    assert red.startswith("postgresql+psycopg://u:***@")


def test_settings_read_from_environment(monkeypatch):
    monkeypatch.setenv("MQTT_HOST", "10.0.0.9")
    monkeypatch.setenv("MQTT_PORT", "8883")
    s = Settings()
    assert s.mqtt_host == "10.0.0.9"
    assert s.mqtt_port == 8883


def test_invalid_mqtt_port_is_rejected():
    try:
        Settings(mqtt_port=70000)
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("mqtt_port=70000 should have been rejected")

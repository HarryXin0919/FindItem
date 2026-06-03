"""REST-layer tests for the FastAPI backend.

The MQTT bridge is replaced with an in-memory fake, so these run with no broker
and no network — they exercise routing, validation, and status-code logic only.
"""
import pytest
from fastapi.testclient import TestClient

import backend.app.main as main


class FakeBridge:
    """Stand-in for FindItBridge: records calls, no MQTT."""

    def __init__(self):
        self.busy = False
        self.started = []
        self.stopped = []

    def start(self):
        pass

    def stop(self):
        pass

    def try_start(self, device_id, item_id, user_id, user_name, duration, buzzer):
        if self.busy:
            return None
        self.started.append((device_id, item_id, duration, buzzer))
        return "evt-123"

    def send_stop(self, device_id, item_id):
        self.stopped.append((device_id, item_id))

    def device_state(self, device_id):
        return {"device_id": device_id, "state": "idle"}

    def all_device_states(self):
        return {"esp32-001": {"device_id": "esp32-001", "state": "idle"}}

    def recent_events(self, limit=50):
        return [{"type": "started", "event_id": "evt-123"}][:limit]


@pytest.fixture
def client(monkeypatch):
    fake = FakeBridge()
    monkeypatch.setattr(main, "bridge", fake)
    with TestClient(main.app) as c:
        c.fake = fake
        yield c


def test_get_items_returns_catalog(client):
    r = client.get("/api/items")
    assert r.status_code == 200
    assert "FINDIT-001" in [i["id"] for i in r.json()["items"]]


def test_start_ok(client):
    r = client.post("/api/search-events", json={
        "user_id": "u1", "user_name": "Harry", "item_id": "FINDIT-001", "action": "start"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] and body["event_id"] == "evt-123" and body["device_id"] == "esp32-001"
    assert client.fake.started and client.fake.started[0][0] == "esp32-001"


def test_start_busy_returns_409(client):
    client.fake.busy = True
    r = client.post("/api/search-events", json={
        "user_id": "u1", "user_name": "Harry", "item_id": "FINDIT-001", "action": "start"})
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "device_busy"


def test_unknown_item_returns_404(client):
    r = client.post("/api/search-events", json={
        "user_id": "u1", "user_name": "H", "item_id": "NOPE", "action": "start"})
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "unknown_item"


def test_stop_ok(client):
    r = client.post("/api/search-events", json={
        "user_id": "u1", "user_name": "H", "item_id": "FINDIT-001", "action": "stop"})
    assert r.status_code == 200
    assert client.fake.stopped == [("esp32-001", "FINDIT-001")]


def test_request_validation_422(client):
    # missing user_name
    assert client.post("/api/search-events", json={
        "user_id": "u1", "item_id": "FINDIT-001", "action": "start"}).status_code == 422
    # invalid action
    assert client.post("/api/search-events", json={
        "user_id": "u1", "user_name": "H", "item_id": "FINDIT-001", "action": "boom"}).status_code == 422
    # duration out of range (1..120)
    assert client.post("/api/search-events", json={
        "user_id": "u1", "user_name": "H", "item_id": "FINDIT-001",
        "action": "start", "duration": 999}).status_code == 422


def test_devices_and_events(client):
    assert client.get("/api/devices").json()["devices"]["esp32-001"]["state"] == "idle"
    assert client.get("/api/devices/esp32-001").json()["state"] == "idle"
    assert client.get("/api/events").json()["events"][0]["event_id"] == "evt-123"


def test_catalog_unavailable_503(client, monkeypatch, tmp_path):
    monkeypatch.setattr(main, "CONFIG_PATH", tmp_path / "missing.json")
    r = client.get("/api/items")
    assert r.status_code == 503 and r.json()["detail"]["error"] == "catalog_unavailable"


def test_catalog_invalid_503(client, monkeypatch, tmp_path):
    bad = tmp_path / "items.json"
    bad.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(main, "CONFIG_PATH", bad)
    r = client.get("/api/items")
    assert r.status_code == 503 and r.json()["detail"]["error"] == "catalog_invalid"

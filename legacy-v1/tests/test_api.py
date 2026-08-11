"""REST-layer tests for the FastAPI backend.

The MQTT bridge is replaced with an in-memory fake, so these run with no broker
and no network — they exercise routing, validation, and status-code logic only.
"""
import pytest
from fastapi.testclient import TestClient

import backend.app.main as main


class FakeBridge:
    """Stand-in for FindItBridge: records calls, no MQTT.

    支持模式切换以模拟 busy / publish 失败等场景。
    """

    def __init__(self):
        self._busy = False
        self._start_ok = True
        self._stop_ok = True
        self.started = []
        self.stopped = []

    def start(self):
        pass

    def stop(self):
        pass

    @property
    def busy(self):
        return self._busy

    @busy.setter
    def busy(self, val):
        self._busy = val

    def set_start_ok(self, val):
        self._start_ok = val

    def set_stop_ok(self, val):
        self._stop_ok = val

    def is_busy(self, device_id):
        return self._busy

    def try_start(self, device_id, item_id, user_id, user_name, duration, buzzer):
        if self._busy:
            return None
        if not self._start_ok:
            return None
        self.started.append((device_id, item_id, duration, buzzer))
        return "evt-123"

    def send_stop(self, device_id, item_id):
        if not self._stop_ok:
            return False
        self.stopped.append((device_id, item_id))
        return True

    def device_state(self, device_id):
        if self._busy:
            return {"device_id": device_id, "state": "ringing",
                    "current_item": "FINDIT-001", "current_user_name": "Someone"}
        return {"device_id": device_id, "state": "idle"}

    def all_device_states(self):
        return {"esp32-001": self.device_state("esp32-001")}

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


# ---------- start 成功 ----------

def test_start_ok(client):
    r = client.post("/api/search-events", json={
        "user_id": "u1", "user_name": "Harry", "item_id": "FINDIT-001", "action": "start"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["event_id"] == "evt-123"
    assert body["device_id"] == "esp32-001"
    assert body["status"] == "submitted"
    assert "等待设备确认" in body["message"]
    assert client.fake.started and client.fake.started[0][0] == "esp32-001"


# ---------- busy:409 ----------

def test_start_busy_returns_409(client):
    client.fake.busy = True
    r = client.post("/api/search-events", json={
        "user_id": "u1", "user_name": "Harry", "item_id": "FINDIT-001", "action": "start"})
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "device_busy"


# ---------- 未收到状态也不硬拦截命令 ----------

def test_start_does_not_require_unreliable_availability_gate(client):
    assert not hasattr(client.fake, "is_device_available")
    r = client.post("/api/search-events", json={
        "user_id": "u1", "user_name": "Harry", "item_id": "FINDIT-001", "action": "start"})
    assert r.status_code == 200


def test_stop_does_not_require_unreliable_availability_gate(client):
    assert not hasattr(client.fake, "is_device_available")
    r = client.post("/api/search-events", json={
        "user_id": "u1", "user_name": "H", "item_id": "FINDIT-001", "action": "stop"})
    assert r.status_code == 200


# ---------- command_unavailable:503 (publish 失败) ----------

def test_start_command_unavailable_503(client):
    """try_start 返回 None 且不是 busy → 判定为命令发送失败。"""
    client.fake.set_start_ok(False)
    r = client.post("/api/search-events", json={
        "user_id": "u1", "user_name": "Harry", "item_id": "FINDIT-001", "action": "start"})
    assert r.status_code == 503
    assert r.json()["detail"]["error"] == "command_unavailable"


def test_stop_command_unavailable_503(client):
    client.fake.set_stop_ok(False)
    r = client.post("/api/search-events", json={
        "user_id": "u1", "user_name": "H", "item_id": "FINDIT-001", "action": "stop"})
    assert r.status_code == 503
    assert r.json()["detail"]["error"] == "command_unavailable"


# ---------- stop 成功 ----------

def test_stop_ok(client):
    r = client.post("/api/search-events", json={
        "user_id": "u1", "user_name": "H", "item_id": "FINDIT-001", "action": "stop"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["status"] == "stop_submitted"
    assert "等待设备确认" in body["message"]
    assert client.fake.stopped == [("esp32-001", "FINDIT-001")]


# ---------- 404 ----------

def test_unknown_item_returns_404(client):
    r = client.post("/api/search-events", json={
        "user_id": "u1", "user_name": "H", "item_id": "NOPE", "action": "start"})
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "unknown_item"


# ---------- 422 ----------

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


# ---------- devices / events ----------

def test_devices_and_events(client):
    assert client.get("/api/devices").json()["devices"]["esp32-001"]["state"] == "idle"
    assert client.get("/api/devices/esp32-001").json()["state"] == "idle"
    assert client.get("/api/events").json()["events"][0]["event_id"] == "evt-123"


# ---------- catalog ----------

def test_catalog_unavailable_503(client, monkeypatch):
    missing = main.BASE_DIR / "config" / "__missing_catalog_test__.json"
    assert not missing.exists()
    monkeypatch.setattr(main, "CONFIG_PATH", missing)
    r = client.get("/api/items")
    assert r.status_code == 503 and r.json()["detail"]["error"] == "catalog_unavailable"


def test_catalog_invalid_503(client, monkeypatch):
    # README 是确定存在且不是 JSON 的只读文件,避免测试依赖临时目录写权限。
    monkeypatch.setattr(main, "CONFIG_PATH", main.BASE_DIR / "README.md")
    r = client.get("/api/items")
    assert r.status_code == 503 and r.json()["detail"]["error"] == "catalog_invalid"


# ---------- frontend 状态操作 ----------

def test_frontend_unknown_state_can_attempt_find():
    html = (main.FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    assert "if (isBusy || isUnknown)" not in html
    assert "if (isBusy)" in html
    assert 'unknown:  "状态未知"' in html


def test_frontend_starting_state_can_stop():
    html = (main.FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    assert "const isBusy = isRinging || isStarting;" in html
    assert "if (!isBusy)" in html

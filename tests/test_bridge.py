"""Unit tests for FindItBridge's core robustness logic — without a broker.

We stub ``_client.publish`` so the bridge never touches the network. These lock
in the audit fixes: atomic busy check-and-set, publish-failure rollback (no
permanent wedge), stale-state auto-unlock, and the status→event flow.
"""
import json
import time
import types

import paho.mqtt.client as mqtt

from backend.app.mqtt_bridge import FindItBridge, STALE_GRACE_SEC


def make_bridge(publish_ok=True):
    b = FindItBridge("127.0.0.1", 1883, "u", "p")

    class Info:
        rc = mqtt.MQTT_ERR_SUCCESS if publish_ok else mqtt.MQTT_ERR_NO_CONN

    b._client.publish = lambda *a, **k: Info()   # never hit the network
    return b


def test_try_start_is_atomic_and_busy_blocks_second():
    b = make_bridge()
    eid = b.try_start("d1", "i1", "u1", "Harry", duration=15, buzzer=True)
    assert eid and b.is_busy("d1")
    # a second start while the device is ringing must be refused (-> 409 upstream)
    assert b.try_start("d1", "i2", "u2", "Bob", duration=15, buzzer=False) is None


def test_try_start_rolls_back_when_publish_fails():
    b = make_bridge(publish_ok=False)
    assert b.try_start("d1", "i1", "u1", "H", duration=15, buzzer=True) is None
    assert not b.is_busy("d1")          # rolled back — the item is not wedged


def test_stale_state_auto_unlocks():
    b = make_bridge()
    b.try_start("d1", "i1", "u1", "H", duration=15, buzzer=True)
    assert b.is_busy("d1")
    with b._lock:                       # force the status older than duration + grace
        b._device_status["d1"]["updated_at"] = time.time() - (15 + STALE_GRACE_SEC + 5)
    assert not b.is_busy("d1")          # stale device -> not busy (no permanent lock)
    assert any(e.get("stop_reason") == "timeout" for e in b.recent_events())


def test_on_message_records_stopped_event():
    b = make_bridge()
    b.try_start("d1", "i1", "u1", "H", duration=15, buzzer=True)
    ring = types.SimpleNamespace(topic="findit/device/d1/status",
                                 payload=json.dumps({"state": "ringing"}).encode())
    b._on_message(None, None, ring)
    idle = types.SimpleNamespace(topic="findit/device/d1/status",
                                 payload=json.dumps({"state": "idle", "stop_reason": "button"}).encode())
    b._on_message(None, None, idle)
    assert b.device_state("d1")["state"] == "idle"
    assert any(e.get("stop_reason") == "button" for e in b.recent_events())


def test_on_message_ignores_malformed_topic_and_payload():
    b = make_bridge()
    b._on_message(None, None, types.SimpleNamespace(topic="garbage/topic", payload=b"{}"))
    b._on_message(None, None, types.SimpleNamespace(topic="findit/device/d1/status", payload=b"not json"))
    assert b.all_device_states() == {}   # nothing recorded from bad input


def test_send_stop_optimistically_idles():
    b = make_bridge()
    b.try_start("d1", "i1", "u1", "H", duration=15, buzzer=True)
    b.send_stop("d1", "i1")
    assert b.device_state("d1")["state"] == "idle"

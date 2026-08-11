"""Unit tests for FindItBridge's core robustness logic — without a broker.

We stub ``_client.publish`` so the bridge never touches the network. These lock
in the audit fixes: atomic busy check-and-set, per-command publish rollback,
stale-state auto-unlock, status→event flow, payload validation, and command
delivery before the first device status arrives.
"""
import copy
import json
import math
import time
import types

import pytest
import paho.mqtt.client as mqtt

from backend.app.mqtt_bridge import FindItBridge, STALE_GRACE_SEC, MAX_RING_DURATION


def make_bridge(publish_ok=True, publish_raises=False):
    b = FindItBridge("127.0.0.1", 1883, "u", "p")

    class Info:
        rc = mqtt.MQTT_ERR_SUCCESS if publish_ok else mqtt.MQTT_ERR_NO_CONN

    call_count = [0]

    def fake_publish(*a, **k):
        call_count[0] += 1
        if publish_raises:
            raise RuntimeError("simulated publish error")
        return Info()

    b._client.publish = fake_publish
    b._publish_call_count = call_count
    return b


def _simulate_device_online(bridge, device_id, state="idle", **extra):
    """Inject a status message so the bridge thinks the device exists."""
    payload = {"state": state, **extra}
    msg = types.SimpleNamespace(
        topic=f"findit/device/{device_id}/status",
        payload=json.dumps(payload).encode(),
    )
    bridge._on_message(None, None, msg)


# ---------- busy / 原子性 ----------

def test_try_start_is_atomic_and_busy_blocks_second():
    b = make_bridge()
    _simulate_device_online(b, "d1")
    eid = b.try_start("d1", "i1", "u1", "Harry", duration=15, buzzer=True)
    assert eid and b.is_busy("d1")
    assert b.try_start("d1", "i2", "u2", "Bob", duration=15, buzzer=False) is None


def test_try_start_allows_unknown_device_when_publish_succeeds():
    b = make_bridge()
    event_id = b.try_start("d-never-seen", "i1", "u1", "H", duration=15, buzzer=True)
    assert event_id
    assert b.is_busy("d-never-seen")
    assert b.device_state("d-never-seen")["state"] == "starting"


def test_try_start_unknown_device_rolls_back_when_publish_fails():
    b = make_bridge(publish_ok=False)
    assert b.try_start("d-never-seen", "i1", "u1", "H", duration=15, buzzer=True) is None
    assert not b.is_busy("d-never-seen")
    assert b.device_state("d-never-seen")["state"] == "unknown"
    assert not b.recent_events()


# ---------- P1-A1: 完整快照回滚 ----------

def test_try_start_rollback_restores_full_snapshot_rc():
    """publish rc 失败时,设备状态必须与调用前逐字段完全相同。"""
    b = make_bridge(publish_ok=False)
    _simulate_device_online(b, "d1", firmware="v1.2.3", battery=87,
                            diagnostics={"heap": 12345}, custom_field="preserve-me")

    before = copy.deepcopy(b.device_state("d1"))
    before_ts = before.get("updated_at")

    result = b.try_start("d1", "i1", "u1", "H", duration=15, buzzer=True)
    assert result is None

    after = b.device_state("d1")
    assert after["state"] == before["state"] == "idle"
    assert after.get("firmware") == "v1.2.3"
    assert after.get("battery") == 87
    assert after.get("diagnostics") == {"heap": 12345}
    assert after.get("custom_field") == "preserve-me"
    assert "current_item" not in after
    assert "current_event_id" not in after
    assert "current_user_id" not in after
    assert "current_user_name" not in after
    assert "buzzer_on" not in after
    assert "_ring_duration" not in after
    assert after.get("updated_at") == before_ts


def test_try_start_rollback_restores_full_snapshot_exception():
    """publish 抛异常时,设备状态必须与调用前逐字段完全相同。"""
    b = make_bridge(publish_raises=True)
    _simulate_device_online(b, "d1", firmware="v0.9", battery=42,
                            extra1="hello", extra2={"nested": True})

    before = copy.deepcopy(b.device_state("d1"))
    before_ts = before.get("updated_at")

    result = b.try_start("d1", "i1", "u1", "H", duration=15, buzzer=True)
    assert result is None

    after = b.device_state("d1")
    assert after["state"] == "idle"
    assert after.get("firmware") == "v0.9"
    assert after.get("battery") == 42
    assert after.get("extra1") == "hello"
    assert after.get("extra2") == {"nested": True}
    assert "current_item" not in after
    assert "current_event_id" not in after
    assert "buzzer_on" not in after
    assert "_ring_duration" not in after
    assert after.get("updated_at") == before_ts


def test_try_start_rollback_restores_events_deque():
    """publish 失败时,事件 deque 必须与调用前完全相同。"""
    b = make_bridge(publish_ok=False)
    _simulate_device_online(b, "d1")
    _simulate_device_online(b, "d1")

    before_events = copy.deepcopy(b.recent_events())

    b.try_start("d1", "i1", "u1", "H", duration=15, buzzer=True)

    after_events = b.recent_events()
    assert len(after_events) == len(before_events)
    assert not any(e.get("type") == "started" for e in after_events)


def test_failed_publish_keeps_other_device_concurrent_event():
    """一个设备 publish 失败时,不得覆盖另一设备并发成功写入的事件。"""
    b = make_bridge()
    nested: dict[str, str | None] = {}

    class OkInfo:
        rc = mqtt.MQTT_ERR_SUCCESS

    class FailInfo:
        rc = mqtt.MQTT_ERR_NO_CONN

    def interleaved_publish(topic, *args, **kwargs):
        if topic == "findit/device/d1/command":
            nested["event_id"] = b.try_start(
                "d2", "i2", "u2", "Other", duration=15, buzzer=True
            )
            return FailInfo()
        return OkInfo()

    b._client.publish = interleaved_publish

    assert b.try_start("d1", "i1", "u1", "First", duration=15, buzzer=True) is None
    assert nested.get("event_id")
    started = [e for e in b.recent_events() if e.get("type") == "started"]
    assert [e.get("event_id") for e in started] == [nested["event_id"]]
    assert b.device_state("d1")["state"] == "unknown"
    assert b.device_state("d2")["state"] == "starting"


def test_device_confirmation_wins_over_publish_error():
    """publish rc 失败前若设备已确认同一 event,不得把真实 ringing 回滚。"""
    b = make_bridge()

    class FailInfo:
        rc = mqtt.MQTT_ERR_NO_CONN

    def publish_then_confirm(topic, payload, **kwargs):
        command = json.loads(payload)
        msg = types.SimpleNamespace(
            topic="findit/device/d1/status",
            payload=json.dumps({
                "state": "ringing",
                "current_event_id": command["event_id"],
            }).encode(),
        )
        b._handle_status_message(msg)
        return FailInfo()

    b._client.publish = publish_then_confirm
    event_id = b.try_start("d1", "i1", "u1", "H", duration=15, buzzer=True)

    assert event_id
    state = b.device_state("d1")
    assert state["state"] == "ringing"
    assert state["current_event_id"] == event_id
    events = b.recent_events()
    assert any(e.get("type") == "started" and e.get("event_id") == event_id for e in events)
    assert any(e.get("type") == "device_ringing" and e.get("event_id") == event_id for e in events)


def test_try_start_rollback_on_ringing_device():
    """已在 ringing 的设备 publish 失败回滚后,仍保持 ringing。"""
    b = make_bridge(publish_ok=False)
    _simulate_device_online(b, "d1")
    b2 = make_bridge()
    _simulate_device_online(b2, "d1")
    eid_good = b2.try_start("d1", "i-good", "u-good", "GoodUser", duration=15, buzzer=True)
    assert eid_good
    ring = types.SimpleNamespace(topic="findit/device/d1/status",
                                 payload=json.dumps({"state": "ringing",
                                                     "firmware": "v1.0",
                                                     "battery": 99}).encode())
    b2._on_message(None, None, ring)

    with b._lock:
        b._device_status["d1"] = copy.deepcopy(b2.device_state("d1"))

    before = copy.deepcopy(b.device_state("d1"))
    before_ts = before.get("updated_at")

    assert b.try_start("d1", "i2", "u2", "Bad", duration=10, buzzer=False) is None

    after = b.device_state("d1")
    assert after["state"] == "ringing"
    assert after.get("current_item") == before.get("current_item")
    assert after.get("current_event_id") == before.get("current_event_id")
    assert after.get("firmware") == "v1.0"
    assert after.get("battery") == 99
    assert after.get("updated_at") == before_ts


# ---------- P1-A2: 伪在线与 busy 绕过 ----------

def test_empty_dict_payload_keeps_unknown_state():
    b = make_bridge()
    msg = types.SimpleNamespace(
        topic="findit/device/d1/status",
        payload=b"{}",
    )
    b._on_message(None, None, msg)
    assert b.device_state("d1")["state"] == "unknown"


def test_missing_state_keeps_unknown_state():
    b = make_bridge()
    msg = types.SimpleNamespace(
        topic="findit/device/d1/status",
        payload=json.dumps({"firmware": "v1.0", "battery": 50}).encode(),
    )
    b._on_message(None, None, msg)
    assert b.device_state("d1")["state"] == "unknown"


def test_unknown_state_is_recorded_as_unknown():
    b = make_bridge()
    msg = types.SimpleNamespace(
        topic="findit/device/d1/status",
        payload=json.dumps({"state": "unknown"}).encode(),
    )
    b._on_message(None, None, msg)
    assert b.device_state("d1")["state"] == "unknown"


def test_busy_device_unknown_state_does_not_clear_busy():
    """设备处于 ringing 时,收到 state=unknown 不能清除 busy 或允许第二次 start。"""
    b = make_bridge()
    _simulate_device_online(b, "d1")
    eid = b.try_start("d1", "i1", "u1", "H", duration=15, buzzer=True)
    assert eid
    ring = types.SimpleNamespace(topic="findit/device/d1/status",
                                 payload=json.dumps({"state": "ringing"}).encode())
    b._on_message(None, None, ring)
    assert b.is_busy("d1")

    unknown_msg = types.SimpleNamespace(
        topic="findit/device/d1/status",
        payload=json.dumps({"state": "unknown"}).encode(),
    )
    b._on_message(None, None, unknown_msg)

    assert b.is_busy("d1")
    assert b.device_state("d1")["state"] in ("ringing", "starting")


def test_handle_status_busy_plus_unknown_is_idempotent_no_throw():
    """直接调用 _handle_status_message():busy + state=unknown 必须不抛异常,
    且处理前后设备状态快照与事件列表完全相同,不刷新时间戳。
    同时确认设备仍 busy 且不可再次 start。
    走 _on_message 会吞异常,旧测试因此假通过。"""
    b = make_bridge()
    _simulate_device_online(b, "d1", firmware="v1.2.3", battery=88,
                            diagnostics={"heap": 12000}, custom_field="keep")
    eid = b.try_start("d1", "i1", "u1", "UserA", duration=15, buzzer=True)
    assert eid
    ring = types.SimpleNamespace(topic="findit/device/d1/status",
                                 payload=json.dumps({"state": "ringing",
                                                     "firmware": "v1.2.3"}).encode())
    b._on_message(None, None, ring)
    assert b.is_busy("d1")

    before_state = copy.deepcopy(b.device_state("d1"))
    before_events = copy.deepcopy(b.recent_events())
    before_updated = before_state.get("updated_at")

    unknown_msg = types.SimpleNamespace(
        topic="findit/device/d1/status",
        payload=json.dumps({"state": "unknown",
                            "current_item": "INJECTED",
                            "firmware": "v9.9.9",
                            "_ring_duration": 999}).encode(),
    )

    try:
        b._handle_status_message(unknown_msg)
    except UnboundLocalError as e:
        pytest.fail(f"_handle_status_message raised UnboundLocalError: {e}")
    except Exception as e:
        pytest.fail(f"_handle_status_message raised unexpected {type(e).__name__}: {e}")

    after_state = b.device_state("d1")
    after_events = b.recent_events()

    assert after_state == before_state, \
        "busy+unknown 后设备状态快照必须与处理前完全相同"
    assert after_events == before_events, \
        "busy+unknown 后事件列表必须与处理前完全相同"
    assert after_state.get("updated_at") == before_updated, \
        "busy+unknown 不得刷新 updated_at"
    assert b.is_busy("d1"), \
        "busy+unknown 后设备必须仍为 busy"
    assert b.try_start("d1", "i2", "u2", "Other", duration=10, buzzer=False) is None, \
        "busy+unknown 后第二次 start 必须被拒绝"


def test_handle_status_starting_plus_unknown_is_idempotent():
    """starting 状态 + unknown 也必须幂等不抛异常。"""
    b = make_bridge()
    _simulate_device_online(b, "d1")
    eid = b.try_start("d1", "i1", "u1", "U", duration=15, buzzer=True)
    assert eid
    assert b.device_state("d1")["state"] == "starting"

    before_state = copy.deepcopy(b.device_state("d1"))
    before_events = copy.deepcopy(b.recent_events())

    msg = types.SimpleNamespace(
        topic="findit/device/d1/status",
        payload=json.dumps({"state": "unknown"}).encode(),
    )
    try:
        b._handle_status_message(msg)
    except Exception as e:
        pytest.fail(f"_handle_status_message raised {type(e).__name__}: {e}")

    assert b.device_state("d1") == before_state
    assert b.recent_events() == before_events
    assert b.is_busy("d1")


def test_busy_device_missing_state_does_not_clear_busy():
    """设备处于 starting 时,收到缺失 state 的 payload 不能清除 busy。"""
    b = make_bridge()
    _simulate_device_online(b, "d1")
    eid = b.try_start("d1", "i1", "u1", "H", duration=15, buzzer=True)
    assert eid
    assert b.is_busy("d1")

    msg = types.SimpleNamespace(
        topic="findit/device/d1/status",
        payload=json.dumps({"firmware": "v2.0", "battery": 80}).encode(),
    )
    b._on_message(None, None, msg)

    assert b.is_busy("d1")


def test_second_http_start_while_ringing_returns_409_path():
    """第二次真实 HTTP start 必须仍为 busy(通过 bridge 层表现为 None → 409),
    不能因为任何 payload 干扰而变成 200。"""
    b = make_bridge()
    _simulate_device_online(b, "d1")
    first = b.try_start("d1", "i1", "u1", "UserA", duration=15, buzzer=True)
    assert first is not None

    ring = types.SimpleNamespace(topic="findit/device/d1/status",
                                 payload=json.dumps({"state": "ringing"}).encode())
    b._on_message(None, None, ring)

    second = b.try_start("d1", "i2", "u2", "UserB", duration=10, buzzer=False)
    assert second is None
    assert b.is_busy("d1")


# ---------- P1-A3: 字段验证 ----------

def test_non_string_state_is_rejected_entirely():
    b = make_bridge()
    _simulate_device_online(b, "d1")
    for bad in [123, 3.14, True, None, [], {}]:
        msg = types.SimpleNamespace(
            topic="findit/device/d1/status",
            payload=json.dumps({"state": bad}).encode(),
        )
        b._on_message(None, None, msg)
        assert b.device_state("d1")["state"] == "idle"


def test_ring_duration_negative_rejected():
    b = make_bridge()
    _simulate_device_online(b, "d1")
    msg = types.SimpleNamespace(
        topic="findit/device/d1/status",
        payload=json.dumps({"state": "ringing", "_ring_duration": -5}).encode(),
    )
    b._on_message(None, None, msg)
    s = b.device_state("d1")
    assert "_ring_duration" not in s


def test_ring_duration_zero_rejected():
    b = make_bridge()
    _simulate_device_online(b, "d1")
    msg = types.SimpleNamespace(
        topic="findit/device/d1/status",
        payload=json.dumps({"state": "ringing", "_ring_duration": 0}).encode(),
    )
    b._on_message(None, None, msg)
    s = b.device_state("d1")
    assert "_ring_duration" not in s


def test_ring_duration_nan_rejected():
    b = make_bridge()
    _simulate_device_online(b, "d1")
    msg = types.SimpleNamespace(
        topic="findit/device/d1/status",
        payload=json.dumps({"state": "ringing", "_ring_duration": float("nan")}).encode(),
    )
    b._on_message(None, None, msg)
    s = b.device_state("d1")
    assert "_ring_duration" not in s


def test_ring_duration_infinity_rejected():
    b = make_bridge()
    _simulate_device_online(b, "d1")
    for inf_val in [float("inf"), float("-inf")]:
        msg = types.SimpleNamespace(
            topic="findit/device/d1/status",
            payload=json.dumps({"state": "ringing", "_ring_duration": inf_val}).encode(),
        )
        b._on_message(None, None, msg)
    s = b.device_state("d1")
    assert "_ring_duration" not in s


def test_ring_duration_string_rejected():
    b = make_bridge()
    _simulate_device_online(b, "d1")
    msg = types.SimpleNamespace(
        topic="findit/device/d1/status",
        payload=json.dumps({"state": "ringing", "_ring_duration": "15"}).encode(),
    )
    b._on_message(None, None, msg)
    s = b.device_state("d1")
    assert "_ring_duration" not in s


def test_ring_duration_bool_rejected():
    b = make_bridge()
    _simulate_device_online(b, "d1")
    msg = types.SimpleNamespace(
        topic="findit/device/d1/status",
        payload=json.dumps({"state": "ringing", "_ring_duration": True}).encode(),
    )
    b._on_message(None, None, msg)
    s = b.device_state("d1")
    assert "_ring_duration" not in s


def test_ring_duration_array_rejected():
    b = make_bridge()
    _simulate_device_online(b, "d1")
    msg = types.SimpleNamespace(
        topic="findit/device/d1/status",
        payload=json.dumps({"state": "ringing", "_ring_duration": [15]}).encode(),
    )
    b._on_message(None, None, msg)
    s = b.device_state("d1")
    assert "_ring_duration" not in s


def test_ring_duration_object_rejected():
    b = make_bridge()
    _simulate_device_online(b, "d1")
    msg = types.SimpleNamespace(
        topic="findit/device/d1/status",
        payload=json.dumps({"state": "ringing", "_ring_duration": {"sec": 15}}).encode(),
    )
    b._on_message(None, None, msg)
    s = b.device_state("d1")
    assert "_ring_duration" not in s


def test_ring_duration_over_max_rejected():
    b = make_bridge()
    _simulate_device_online(b, "d1")
    msg = types.SimpleNamespace(
        topic="findit/device/d1/status",
        payload=json.dumps({"state": "ringing",
                            "_ring_duration": MAX_RING_DURATION + 100}).encode(),
    )
    b._on_message(None, None, msg)
    s = b.device_state("d1")
    assert "_ring_duration" not in s


def test_current_item_non_string_rejected():
    b = make_bridge()
    _simulate_device_online(b, "d1")
    for bad in [123, 3.14, True, None, [], {}, ["x"]]:
        msg = types.SimpleNamespace(
            topic="findit/device/d1/status",
            payload=json.dumps({"state": "ringing", "current_item": bad}).encode(),
        )
        b._on_message(None, None, msg)
        s = b.device_state("d1")
        assert not isinstance(s.get("current_item"), (int, float, bool, list, dict))


def test_current_event_id_non_string_rejected():
    b = make_bridge()
    _simulate_device_online(b, "d1")
    msg = types.SimpleNamespace(
        topic="findit/device/d1/status",
        payload=json.dumps({"state": "ringing", "current_event_id": 12345}).encode(),
    )
    b._on_message(None, None, msg)
    s = b.device_state("d1")
    assert not isinstance(s.get("current_event_id"), (int, float))


def test_current_user_id_non_string_rejected():
    b = make_bridge()
    _simulate_device_online(b, "d1")
    msg = types.SimpleNamespace(
        topic="findit/device/d1/status",
        payload=json.dumps({"state": "ringing", "current_user_id": ["u1"]}).encode(),
    )
    b._on_message(None, None, msg)
    s = b.device_state("d1")
    assert not isinstance(s.get("current_user_id"), list)


def test_current_user_name_non_string_rejected():
    b = make_bridge()
    _simulate_device_online(b, "d1")
    msg = types.SimpleNamespace(
        topic="findit/device/d1/status",
        payload=json.dumps({"state": "ringing", "current_user_name": {"n": "x"}}).encode(),
    )
    b._on_message(None, None, msg)
    s = b.device_state("d1")
    assert not isinstance(s.get("current_user_name"), dict)


# ---------- P1-A4: idle 清理 ----------

def test_idle_clears_all_context_fields():
    """设备回报 idle 后,current_* 与 buzzer_on/_ring_duration 必须全部清理,
    GET 不得继续暴露陈旧占用信息。"""
    b = make_bridge()
    _simulate_device_online(b, "d1")
    b.try_start("d1", "i1", "u1", "UserA", duration=15, buzzer=True)
    ring = types.SimpleNamespace(topic="findit/device/d1/status",
                                 payload=json.dumps({"state": "ringing"}).encode())
    b._on_message(None, None, ring)
    assert b.device_state("d1").get("current_item") == "i1"

    idle = types.SimpleNamespace(topic="findit/device/d1/status",
                                 payload=json.dumps({"state": "idle",
                                                     "stop_reason": "button"}).encode())
    b._on_message(None, None, idle)

    s = b.device_state("d1")
    assert s["state"] == "idle"
    assert "current_item" not in s
    assert "current_event_id" not in s
    assert "current_user_id" not in s
    assert "current_user_name" not in s
    assert "buzzer_on" not in s
    assert "_ring_duration" not in s


def test_idle_generates_stopped_event_before_clear():
    """idle 时必须先用旧上下文生成 stopped 事件,再清理字段。"""
    b = make_bridge()
    _simulate_device_online(b, "d1")
    b.try_start("d1", "i-keep", "u-keep", "NameKeep", duration=15, buzzer=True)
    ring = types.SimpleNamespace(topic="findit/device/d1/status",
                                 payload=json.dumps({"state": "ringing"}).encode())
    b._on_message(None, None, ring)

    idle = types.SimpleNamespace(topic="findit/device/d1/status",
                                 payload=json.dumps({"state": "idle",
                                                     "stop_reason": "button"}).encode())
    b._on_message(None, None, idle)

    events = b.recent_events()
    stopped = [e for e in events if e.get("type") == "stopped"]
    assert len(stopped) >= 1
    latest_stopped = stopped[0]
    assert latest_stopped["item_id"] == "i-keep"
    assert latest_stopped["user_id"] == "u-keep"
    assert latest_stopped["user_name"] == "NameKeep"


# ---------- P1-A5: 状态观测不作为命令硬门禁 ----------

def test_valid_status_is_recorded():
    b = make_bridge()
    _simulate_device_online(b, "d1", state="idle", firmware="v1.0")
    assert b.device_state("d1")["state"] == "idle"
    assert b.device_state("d1")["firmware"] == "v1.0"


def test_explicit_unknown_after_idle_is_recorded():
    b = make_bridge()
    _simulate_device_online(b, "d1", state="idle")

    msg = types.SimpleNamespace(
        topic="findit/device/d1/status",
        payload=json.dumps({"state": "unknown"}).encode(),
    )
    b._on_message(None, None, msg)
    assert b.device_state("d1")["state"] == "unknown"


# ---------- stale ----------

def test_stale_state_auto_unlocks():
    b = make_bridge()
    _simulate_device_online(b, "d1")
    b.try_start("d1", "i1", "u1", "H", duration=15, buzzer=True)
    assert b.is_busy("d1")
    with b._lock:
        b._device_status["d1"]["updated_at"] = time.time() - (15 + STALE_GRACE_SEC + 5)
    assert not b.is_busy("d1")
    assert any(e.get("stop_reason") == "timeout" for e in b.recent_events())


def test_stale_unlock_clears_context():
    b = make_bridge()
    _simulate_device_online(b, "d1")
    b.try_start("d1", "i1", "u1", "H", duration=15, buzzer=True)
    with b._lock:
        b._device_status["d1"]["updated_at"] = time.time() - (15 + STALE_GRACE_SEC + 5)
    assert not b.is_busy("d1")
    s = b.device_state("d1")
    assert "current_item" not in s
    assert "current_event_id" not in s


def test_all_device_states_poll_cleans_stale_state():
    """前端 GET /api/devices 的轮询必须触发 starting 超时清理。"""
    b = make_bridge()
    b.try_start("d1", "i1", "u1", "H", duration=15, buzzer=True)
    with b._lock:
        b._device_status["d1"]["updated_at"] = time.time() - (15 + STALE_GRACE_SEC + 5)

    states = b.all_device_states()
    assert states["d1"]["state"] == "idle"
    assert "current_item" not in states["d1"]
    assert any(e.get("stop_reason") == "timeout" for e in b.recent_events())


# ---------- on_message ----------

def test_on_message_records_stopped_event():
    b = make_bridge()
    _simulate_device_online(b, "d1")
    b.try_start("d1", "i1", "u1", "H", duration=15, buzzer=True)
    ring = types.SimpleNamespace(topic="findit/device/d1/status",
                                 payload=json.dumps({"state": "ringing"}).encode())
    b._on_message(None, None, ring)
    idle = types.SimpleNamespace(topic="findit/device/d1/status",
                                 payload=json.dumps({"state": "idle", "stop_reason": "button"}).encode())
    b._on_message(None, None, idle)
    assert b.device_state("d1")["state"] == "idle"
    assert any(e.get("stop_reason") == "button" for e in b.recent_events())


def test_on_message_device_ringing_is_separate_event():
    b = make_bridge()
    _simulate_device_online(b, "d1")
    b.try_start("d1", "i1", "u1", "H", duration=15, buzzer=True)
    before_types = [e["type"] for e in b.recent_events()]
    assert "device_ringing" not in before_types

    ring = types.SimpleNamespace(topic="findit/device/d1/status",
                                 payload=json.dumps({"state": "ringing"}).encode())
    b._on_message(None, None, ring)

    after_types = [e["type"] for e in b.recent_events()]
    assert "device_ringing" in after_types
    assert b.device_state("d1")["state"] == "ringing"


def test_on_message_ignores_malformed_topic_and_payload():
    b = make_bridge()
    b._on_message(None, None, types.SimpleNamespace(topic="garbage/topic", payload=b"{}"))
    b._on_message(None, None, types.SimpleNamespace(topic="findit/device/d1/status", payload=b"not json"))
    assert b.all_device_states() == {}


def test_on_message_rejects_non_object_json():
    b = make_bridge()
    for bad_payload in ["[]", "null", '"just a string"', "123", "3.14", "true", "false"]:
        msg = types.SimpleNamespace(
            topic="findit/device/d1/status",
            payload=bad_payload.encode(),
        )
        b._on_message(None, None, msg)
    assert "d1" not in b.all_device_states()


def test_on_message_unknown_state_degrades_to_unknown():
    b = make_bridge()
    _simulate_device_online(b, "d1")
    msg = types.SimpleNamespace(
        topic="findit/device/d1/status",
        payload=json.dumps({"state": "super_crazy_state"}).encode(),
    )
    b._on_message(None, None, msg)
    assert b.device_state("d1")["state"] == "unknown"


def test_on_message_does_not_throw_on_anything():
    b = make_bridge()
    weird_cases = [
        ("findit/device/d1/status", b""),
        ("findit/device/d1/status", b"{"),
        ("findit/device/!!invalid!!/status", json.dumps({"state": "idle"}).encode()),
        ("", b"{}"),
    ]
    for topic, payload in weird_cases:
        msg = types.SimpleNamespace(topic=topic, payload=payload)
        try:
            b._on_message(None, None, msg)
        except Exception as e:
            pytest.fail(f"_on_message raised on {topic}: {e}")


# ---------- send_stop ----------

def test_send_stop_does_not_optimistically_idle():
    b = make_bridge()
    _simulate_device_online(b, "d1")
    b.try_start("d1", "i1", "u1", "H", duration=15, buzzer=True)
    ring = types.SimpleNamespace(topic="findit/device/d1/status",
                                 payload=json.dumps({"state": "ringing"}).encode())
    b._on_message(None, None, ring)
    assert b.device_state("d1")["state"] == "ringing"

    ok = b.send_stop("d1", "i1")
    assert ok is True
    assert b.device_state("d1")["state"] == "ringing"


def test_send_stop_returns_false_on_rc_failure():
    b = make_bridge(publish_ok=False)
    _simulate_device_online(b, "d1")
    b.try_start("d1", "i1", "u1", "H", duration=15, buzzer=True)
    ring = types.SimpleNamespace(topic="findit/device/d1/status",
                                 payload=json.dumps({"state": "ringing"}).encode())
    b._on_message(None, None, ring)

    ok = b.send_stop("d1", "i1")
    assert ok is False
    assert b.device_state("d1")["state"] == "ringing"


def test_send_stop_returns_false_on_exception():
    b = make_bridge(publish_raises=True)
    _simulate_device_online(b, "d1")
    b.try_start("d1", "i1", "u1", "H", duration=15, buzzer=True)
    ring = types.SimpleNamespace(topic="findit/device/d1/status",
                                 payload=json.dumps({"state": "ringing"}).encode())
    b._on_message(None, None, ring)

    ok = b.send_stop("d1", "i1")
    assert ok is False


# ---------- device_state 默认 unknown ----------

def test_device_state_unknown_for_unseen():
    b = make_bridge()
    s = b.device_state("ghost")
    assert s["state"] == "unknown"
    assert s["device_id"] == "ghost"

"""S05 acceptance tests: MQTT contract, topic builders and command idempotency.

No physical ESP32 is involved - everything runs against `FakePublisher`.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select

from app.models import CommandStatus, DeviceEvent, DeviceEventType, LocateCommand
from app.seed import seed
from app.services.command_service import (
    CommandService,
    ControllerMismatchError,
    RetryPolicy,
    UnknownCommandError,
)
from app.services.mqtt_client import (
    CONTROLLER_IDS,
    AckPayload,
    AckState,
    Channel,
    FakePublisher,
    InvalidControllerError,
    InvalidPayloadError,
    LocatePayload,
    Pattern,
    ack_topic,
    command_topic,
    controller_topic,
    heartbeat_topic,
    status_topic,
    subscription_topics,
)


@pytest.fixture()
def seeded(db_session):
    seed(db_session)
    return db_session


@pytest.fixture()
def bus():
    return FakePublisher()


@pytest.fixture()
def service(seeded, bus):
    return CommandService(seeded, bus)


# --- Topic builders ---------------------------------------------------------

def test_topic_builders_cover_all_five_controllers():
    assert CONTROLLER_IDS == ("CTRL-01", "CTRL-02", "CTRL-03", "CTRL-04", "CTRL-05")
    for cid in CONTROLLER_IDS:
        assert command_topic(cid) == f"findit/controllers/{cid}/command"
        assert ack_topic(cid) == f"findit/controllers/{cid}/ack"
        assert status_topic(cid) == f"findit/controllers/{cid}/status"
        assert heartbeat_topic(cid) == f"findit/controllers/{cid}/heartbeat"


def test_every_channel_is_addressable():
    for channel in Channel:
        assert controller_topic("CTRL-02", channel).endswith(f"/{channel.value}")


def test_subscriptions_are_per_controller_and_never_wildcards():
    topics = subscription_topics("CTRL-04")
    assert topics == [
        "findit/controllers/CTRL-04/ack",
        "findit/controllers/CTRL-04/status",
        "findit/controllers/CTRL-04/heartbeat",
    ]
    assert not any("+" in t or "#" in t for t in topics)


@pytest.mark.parametrize("bad", ["+", "#", "CTRL-+", "all", "ALL", "CTRL-06", "ctrl-01", "", "CTRL-1"])
def test_broadcast_and_unknown_controllers_are_refused(bad):
    with pytest.raises(InvalidControllerError):
        command_topic(bad)


# --- Acceptance: drawer 1 and drawer 50 -------------------------------------

def test_drawer_1_publishes_only_ctrl01_local0(service, bus):
    result = service.locate_drawer(1, command_id="cmd-d1")
    assert result.published is True

    assert bus.topics == ["findit/controllers/CTRL-01/command"]
    payload = bus.payloads()[0]
    assert payload["controller_id"] == "CTRL-01"
    assert payload["local_led_index"] == 0
    assert payload["action"] == "locate"
    # Nothing was sent to any other controller.
    for other in ("CTRL-02", "CTRL-03", "CTRL-04", "CTRL-05"):
        assert command_topic(other) not in bus.topics


def test_drawer_50_publishes_only_ctrl05_local9(service, bus):
    service.locate_drawer(50, command_id="cmd-d50")
    assert bus.topics == ["findit/controllers/CTRL-05/command"]
    payload = bus.payloads()[0]
    assert (payload["controller_id"], payload["local_led_index"]) == ("CTRL-05", 9)


@pytest.mark.parametrize(
    ("drawer", "controller", "led"),
    [(1, "CTRL-01", 0), (10, "CTRL-01", 9), (11, "CTRL-02", 0), (20, "CTRL-02", 9),
     (21, "CTRL-03", 0), (30, "CTRL-03", 9), (31, "CTRL-04", 0), (40, "CTRL-04", 9),
     (41, "CTRL-05", 0), (50, "CTRL-05", 9)],
)
def test_every_controller_boundary_routes_to_exactly_one_topic(
    service, bus, drawer, controller, led
):
    service.locate_drawer(drawer, command_id=f"cmd-{drawer}")
    assert len(bus.messages) == 1
    assert bus.messages[0].topic == f"findit/controllers/{controller}/command"
    assert bus.payloads()[0]["local_led_index"] == led


def test_all_fifty_drawers_hit_the_right_topic_and_only_one_message_each(service, bus):
    for drawer in range(1, 51):
        bus.clear()
        service.locate_drawer(drawer, command_id=f"sweep-{drawer}")
        assert len(bus.messages) == 1, f"drawer {drawer} produced {len(bus.messages)} messages"
        expected_controller = f"CTRL-{(drawer - 1) // 10 + 1:02d}"
        assert bus.messages[0].topic == command_topic(expected_controller)
        assert bus.payloads()[0]["local_led_index"] == (drawer - 1) % 10


@pytest.mark.parametrize("bad_drawer", [0, 51, -1, 999])
def test_out_of_range_drawer_publishes_nothing(service, bus, bad_drawer):
    with pytest.raises(ValueError):
        service.locate_drawer(bad_drawer)
    assert bus.messages == []


# --- Acceptance: duplicate command ids --------------------------------------

def test_duplicate_command_id_does_not_publish_twice(service, bus, seeded):
    first = service.locate_drawer(23, command_id="cmd-dup")
    second = service.locate_drawer(23, command_id="cmd-dup")

    assert first.published is True and first.deduplicated is False
    assert second.published is False and second.deduplicated is True
    assert len(bus.messages) == 1
    assert seeded.scalar(select(func.count()).select_from(LocateCommand)) == 1


def test_duplicate_command_id_with_a_different_drawer_still_does_not_reactivate(
    service, bus, seeded
):
    service.locate_drawer(1, command_id="cmd-same")
    replay = service.locate_drawer(50, command_id="cmd-same")

    assert replay.deduplicated is True
    assert len(bus.messages) == 1  # still only the original CTRL-01 message
    stored = seeded.get(LocateCommand, "cmd-same")
    assert (stored.controller_id, stored.led_index) == ("CTRL-01", 0)


def test_replaying_an_acked_command_does_not_republish(service, bus):
    service.locate_drawer(7, command_id="cmd-acked")
    service.handle_ack(
        json.dumps({"command_id": "cmd-acked", "controller_id": "CTRL-01", "state": "completed"})
    )
    bus.clear()
    replay = service.locate_drawer(7, command_id="cmd-acked")
    assert replay.deduplicated is True
    assert bus.messages == []
    assert replay.command.status is CommandStatus.ACKED


def test_generated_command_ids_are_unique(service):
    ids = {service.new_command_id() for _ in range(200)}
    assert len(ids) == 200


# --- Lifecycle --------------------------------------------------------------

def test_publish_success_is_not_treated_as_completion(service, seeded):
    result = service.locate_drawer(12, command_id="cmd-lifecycle")
    assert result.command.status is CommandStatus.PUBLISHED
    assert result.command.published_at is not None
    assert result.command.acked_at is None


def test_ack_received_then_completed(service):
    service.locate_drawer(12, command_id="cmd-two-step")
    after_received = service.handle_ack(
        json.dumps({"command_id": "cmd-two-step", "controller_id": "CTRL-02", "state": "received"})
    )
    assert after_received.status is CommandStatus.PUBLISHED
    assert after_received.acked_at is None

    after_completed = service.handle_ack(
        json.dumps({"command_id": "cmd-two-step", "controller_id": "CTRL-02", "state": "completed"})
    )
    assert after_completed.status is CommandStatus.ACKED
    assert after_completed.acked_at is not None


def test_ack_rejected_records_the_device_message(service):
    service.locate_drawer(12, command_id="cmd-rejected")
    command = service.handle_ack(
        json.dumps(
            {
                "command_id": "cmd-rejected",
                "controller_id": "CTRL-02",
                "state": "rejected",
                "message": "led index out of range",
            }
        )
    )
    assert command.status is CommandStatus.FAILED
    assert command.error == "led index out of range"


def test_publish_failure_marks_the_command_failed_without_leaking_detail(seeded, bus):
    # S09 added bounded retries, so a *persistent* failure is what reaches the
    # failed state; a single transient one is now retried and recovers.
    service = CommandService(seeded, bus, retry=RetryPolicy(max_attempts=1))
    bus.fail_next = ConnectionRefusedError("mqtt://user:hunter2@broker:1883 refused")
    result = service.locate_drawer(33, command_id="cmd-broker-down")

    assert result.published is False
    assert result.command.status is CommandStatus.FAILED
    assert result.command.error == "publish failed after 1 attempt(s): ConnectionRefusedError"
    assert "hunter2" not in (result.command.error or "")
    assert bus.messages == []


def test_a_single_transient_publish_failure_is_retried(seeded, bus):
    service = CommandService(seeded, bus, retry=RetryPolicy(max_attempts=3, backoff_s=0))
    bus.fail_next = ConnectionRefusedError("transient")
    result = service.locate_drawer(33, command_id="cmd-transient")

    assert result.published is True
    assert result.attempts == 2
    assert result.command.status is CommandStatus.PUBLISHED
    assert len(bus.messages) == 1


def test_ack_for_an_unknown_command_is_rejected_and_logged(service, seeded):
    with pytest.raises(UnknownCommandError):
        service.handle_ack(
            json.dumps({"command_id": "never-issued", "controller_id": "CTRL-01", "state": "completed"})
        )
    event = seeded.scalars(select(DeviceEvent)).one()
    assert event.type is DeviceEventType.ERROR
    assert event.payload["reason"] == "ack for unknown command_id"


def test_ack_from_the_wrong_controller_is_rejected(service, seeded):
    service.locate_drawer(1, command_id="cmd-mismatch")  # CTRL-01
    with pytest.raises(ControllerMismatchError):
        service.handle_ack(
            json.dumps({"command_id": "cmd-mismatch", "controller_id": "CTRL-05", "state": "completed"})
        )
    assert seeded.get(LocateCommand, "cmd-mismatch").status is CommandStatus.PUBLISHED


def test_successful_ack_is_recorded_as_a_device_event(service, seeded):
    service.locate_drawer(1, command_id="cmd-event")
    service.handle_ack(
        json.dumps({"command_id": "cmd-event", "controller_id": "CTRL-01", "state": "completed"})
    )
    event = seeded.scalars(select(DeviceEvent)).one()
    assert event.type is DeviceEventType.ACK
    assert event.payload["state"] == "completed"


# --- Payload contract -------------------------------------------------------

def test_locate_payload_carries_every_contract_field(service, bus):
    service.locate_drawer(25, command_id="cmd-fields", pattern=Pattern.BLINK, duration_ms=5000)
    payload = bus.payloads()[0]
    assert set(payload) == {
        "command_id", "controller_id", "local_led_index",
        "action", "pattern", "duration_ms", "issued_at",
    }
    assert payload["pattern"] == "blink"
    assert payload["duration_ms"] == 5000


@pytest.mark.parametrize("bad_led", [-1, 10, 99])
def test_locate_payload_rejects_an_out_of_range_led(bad_led):
    with pytest.raises(InvalidPayloadError):
        LocatePayload(command_id="x", controller_id="CTRL-01", local_led_index=bad_led)


def test_locate_payload_rejects_an_unknown_controller():
    with pytest.raises(InvalidControllerError):
        LocatePayload(command_id="x", controller_id="CTRL-09", local_led_index=0)


@pytest.mark.parametrize(
    "raw",
    ["not json", "[]", '{"controller_id":"CTRL-01","state":"completed"}',
     '{"command_id":"a","state":"completed"}',
     '{"command_id":"a","controller_id":"CTRL-01","state":"exploded"}',
     '{"command_id":"a","controller_id":"CTRL-99","state":"completed"}'],
)
def test_malformed_ack_payloads_are_refused(raw):
    with pytest.raises((InvalidPayloadError, InvalidControllerError)):
        AckPayload.from_json(raw)


def test_malformed_ack_is_logged_without_the_raw_payload(service, seeded):
    with pytest.raises(InvalidPayloadError):
        service.handle_malformed_ack('{"secret":"hunter2"}', "CTRL-03")
    event = seeded.scalars(select(DeviceEvent)).one()
    assert event.type is DeviceEventType.ERROR
    assert "hunter2" not in json.dumps(event.payload)


def test_ack_states_match_the_contract():
    assert {s.value for s in AckState} == {"received", "completed", "rejected"}

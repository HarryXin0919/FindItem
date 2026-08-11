"""S09 acceptance tests: every documented failure case, plus log safety.

The rule under test throughout is the S09 forbidden scope: a failure is never
hidden by auto-marking success.
"""
from __future__ import annotations

import io
import json
import logging
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.logging_config import (
    RedactingFilter,
    StructuredFormatter,
    configure_logging,
    redact,
)
from app.models import (
    CommandStatus,
    Controller,
    ControllerStatus,
    DeviceEvent,
    DeviceEventType,
    LocateCommand,
)
from app.seed import seed
from app.services.command_service import (
    CommandService,
    ControllerMismatchError,
    RetryPolicy,
    StaleAckError,
    UnknownCommandError,
)
from app.services.mqtt_client import FakePublisher, InvalidControllerError, InvalidPayloadError

SECRET = "hunter2-super-secret"


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


class FlakyPublisher(FakePublisher):
    """Fails the first `fail_count` publishes, then succeeds."""

    def __init__(self, fail_count: int) -> None:
        super().__init__()
        self.fail_count = fail_count
        self.attempts = 0

    def publish(self, topic, payload, *, qos=1, retain=False):
        self.attempts += 1
        if self.attempts <= self.fail_count:
            raise ConnectionRefusedError(f"broker mqtt://user:{SECRET}@host:1883 refused")
        super().publish(topic, payload, qos=qos, retain=retain)


# --- 1. Offline controller --------------------------------------------------

def test_locate_to_an_offline_controller_is_refused_not_published(seeded, bus):
    service = CommandService(seeded, bus)
    service.mark_offline("CTRL-03")

    result = service.locate_drawer(25, command_id="off-1")

    assert result.published is False
    assert result.command.status is CommandStatus.FAILED
    assert result.command.error == "controller CTRL-03 is offline"
    assert bus.messages == []  # nothing was sent into the void
    assert result.command.acked_at is None


def test_offline_refusal_is_recorded_as_an_event(seeded, bus):
    service = CommandService(seeded, bus)
    service.mark_offline("CTRL-01", reason="missed 3 heartbeats")
    service.locate_drawer(1, command_id="off-2")

    reasons = [e.payload.get("reason") for e in seeded.scalars(select(DeviceEvent))]
    assert "missed 3 heartbeats" in reasons
    assert "controller offline" in reasons


def test_a_controller_that_comes_back_online_can_be_located_again(seeded, bus):
    service = CommandService(seeded, bus)
    service.mark_offline("CTRL-02")
    assert service.locate_drawer(11, command_id="off-3").published is False

    service.handle_status(
        json.dumps({"controller_id": "CTRL-02", "online": True, "fw_version": "1.2.3"})
    )
    assert seeded.get(Controller, "CTRL-02").status is ControllerStatus.ONLINE
    assert seeded.get(Controller, "CTRL-02").fw_version == "1.2.3"

    assert service.locate_drawer(11, command_id="off-4").published is True


# --- 2. Timeout -------------------------------------------------------------

def test_a_command_with_no_ack_expires_rather_than_succeeding(seeded, bus):
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    clock = {"now": t0}
    service = CommandService(seeded, bus, now=lambda: clock["now"], ack_timeout_s=15)

    service.locate_drawer(5, command_id="timeout-1")
    assert seeded.get(LocateCommand, "timeout-1").status is CommandStatus.PUBLISHED

    clock["now"] = t0 + timedelta(seconds=16)
    expired = service.expire_stale_commands()

    assert [c.command_id for c in expired] == ["timeout-1"]
    command = seeded.get(LocateCommand, "timeout-1")
    assert command.status is CommandStatus.EXPIRED
    assert command.error == "no ACK within 15s"
    assert command.acked_at is None  # never auto-marked as success


def test_a_command_inside_the_window_is_not_expired(seeded, bus):
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    clock = {"now": t0}
    service = CommandService(seeded, bus, now=lambda: clock["now"], ack_timeout_s=15)
    service.locate_drawer(5, command_id="timeout-2")

    clock["now"] = t0 + timedelta(seconds=14)
    assert service.expire_stale_commands() == []
    assert seeded.get(LocateCommand, "timeout-2").status is CommandStatus.PUBLISHED


def test_an_acked_command_is_never_expired(seeded, bus):
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    clock = {"now": t0}
    service = CommandService(seeded, bus, now=lambda: clock["now"])
    service.locate_drawer(5, command_id="timeout-3")
    service.handle_ack(
        json.dumps({"command_id": "timeout-3", "controller_id": "CTRL-01", "state": "completed"})
    )

    clock["now"] = t0 + timedelta(hours=1)
    assert service.expire_stale_commands() == []
    assert seeded.get(LocateCommand, "timeout-3").status is CommandStatus.ACKED


def test_expiry_is_recorded_as_an_event(seeded, bus):
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    clock = {"now": t0}
    service = CommandService(seeded, bus, now=lambda: clock["now"], ack_timeout_s=5)
    service.locate_drawer(5, command_id="timeout-4")
    clock["now"] = t0 + timedelta(seconds=10)
    service.expire_stale_commands()

    assert any(
        e.payload.get("reason") == "ack timeout" for e in seeded.scalars(select(DeviceEvent))
    )


# --- 3. Duplicate command ---------------------------------------------------

def test_duplicate_command_cannot_double_actuate(seeded, bus):
    service = CommandService(seeded, bus)
    first = service.locate_drawer(23, command_id="dup-a")
    second = service.locate_drawer(23, command_id="dup-a")
    third = service.locate_drawer(23, command_id="dup-a")

    assert first.published is True
    assert (second.published, second.deduplicated) == (False, True)
    assert (third.published, third.deduplicated) == (False, True)
    assert len(bus.messages) == 1
    assert (
        len(list(seeded.scalars(select(LocateCommand).where(LocateCommand.command_id == "dup-a"))))
        == 1
    )


def test_duplicate_after_expiry_still_does_not_republish(seeded, bus):
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    clock = {"now": t0}
    service = CommandService(seeded, bus, now=lambda: clock["now"], ack_timeout_s=5)
    service.locate_drawer(9, command_id="dup-b")
    clock["now"] = t0 + timedelta(seconds=10)
    service.expire_stale_commands()

    replay = service.locate_drawer(9, command_id="dup-b")
    assert replay.deduplicated is True
    assert len(bus.messages) == 1
    # A retry needs a NEW command_id - that is the caller's responsibility.
    fresh = service.locate_drawer(9, command_id="dup-b-retry")
    assert fresh.published is True
    assert len(bus.messages) == 2


# --- 4. Stale ACK -----------------------------------------------------------

def test_ack_for_an_expired_command_is_rejected(seeded, bus):
    t0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    clock = {"now": t0}
    service = CommandService(seeded, bus, now=lambda: clock["now"], ack_timeout_s=5)
    service.locate_drawer(2, command_id="stale-1")
    clock["now"] = t0 + timedelta(seconds=10)
    service.expire_stale_commands()

    with pytest.raises(StaleAckError):
        service.handle_ack(
            json.dumps({"command_id": "stale-1", "controller_id": "CTRL-01", "state": "completed"})
        )

    command = seeded.get(LocateCommand, "stale-1")
    assert command.status is CommandStatus.EXPIRED  # not resurrected
    assert command.acked_at is None
    assert any(
        e.payload.get("reason") == "stale ack for expired command"
        for e in seeded.scalars(select(DeviceEvent))
    )


def test_ack_for_an_unknown_command_is_rejected(service):
    with pytest.raises(UnknownCommandError):
        service.handle_ack(
            json.dumps({"command_id": "never", "controller_id": "CTRL-01", "state": "completed"})
        )


def test_ack_from_the_wrong_controller_is_rejected(service, seeded):
    service.locate_drawer(1, command_id="stale-2")
    with pytest.raises(ControllerMismatchError):
        service.handle_ack(
            json.dumps({"command_id": "stale-2", "controller_id": "CTRL-04", "state": "completed"})
        )
    assert seeded.get(LocateCommand, "stale-2").status is CommandStatus.PUBLISHED


def test_a_repeated_completed_ack_does_not_move_the_timestamp(service, seeded):
    service.locate_drawer(1, command_id="stale-3")
    ack = json.dumps(
        {"command_id": "stale-3", "controller_id": "CTRL-01", "state": "completed"}
    )
    first = service.handle_ack(ack).acked_at
    second = service.handle_ack(ack).acked_at
    assert first == second


# --- 5. Malformed controller id / payload -----------------------------------

@pytest.mark.parametrize("bad", ["CTRL-99", "ctrl-01", "+", "#", "all", "", "CTRL-1"])
def test_malformed_controller_id_is_refused_everywhere(seeded, bus, bad):
    service = CommandService(seeded, bus)
    with pytest.raises((InvalidControllerError, InvalidPayloadError)):
        service.handle_ack(
            json.dumps({"command_id": "x", "controller_id": bad, "state": "completed"})
        )
    assert bus.messages == []


def test_malformed_ack_is_recorded_without_its_payload(service, seeded):
    with pytest.raises(InvalidPayloadError):
        service.handle_malformed_ack(json.dumps({"password": SECRET}), "CTRL-02")
    event = seeded.scalars(select(DeviceEvent)).one()
    assert event.type is DeviceEventType.ERROR
    assert SECRET not in json.dumps(event.payload)


def test_status_from_an_unknown_controller_is_refused(service):
    with pytest.raises(InvalidControllerError):
        service.handle_status(json.dumps({"controller_id": "CTRL-42", "online": True}))


# --- 6. Bounded retry -------------------------------------------------------

def test_publish_retries_then_succeeds(seeded):
    flaky = FlakyPublisher(fail_count=2)
    service = CommandService(seeded, flaky, retry=RetryPolicy(max_attempts=3, backoff_s=0))

    result = service.locate_drawer(14, command_id="retry-1")

    assert result.published is True
    assert result.attempts == 3
    assert flaky.attempts == 3
    assert result.command.status is CommandStatus.PUBLISHED


def test_retries_are_bounded_and_then_it_fails(seeded):
    flaky = FlakyPublisher(fail_count=99)
    service = CommandService(seeded, flaky, retry=RetryPolicy(max_attempts=3, backoff_s=0))

    result = service.locate_drawer(14, command_id="retry-2")

    assert result.published is False
    assert flaky.attempts == 3  # exactly the budget, not one more
    assert result.command.status is CommandStatus.FAILED
    assert "after 3 attempt(s)" in result.command.error
    assert SECRET not in result.command.error


def test_no_retry_policy_can_be_unbounded():
    for bad in (0, -1, 11, 100):
        with pytest.raises(ValueError):
            RetryPolicy(max_attempts=bad)


def test_max_attempts_one_means_no_retry(seeded):
    flaky = FlakyPublisher(fail_count=1)
    service = CommandService(seeded, flaky, retry=RetryPolicy(max_attempts=1, backoff_s=0))
    result = service.locate_drawer(14, command_id="retry-3")
    assert result.published is False
    assert flaky.attempts == 1


def test_backoff_is_called_between_attempts_but_not_after_the_last(seeded):
    waits: list[float] = []
    flaky = FlakyPublisher(fail_count=99)
    service = CommandService(
        seeded, flaky, retry=RetryPolicy(max_attempts=3, backoff_s=0.5), sleep=waits.append
    )
    service.locate_drawer(14, command_id="retry-4")
    assert waits == [0.5, 1.0]  # two gaps for three attempts


# --- 7. Log safety ----------------------------------------------------------

@pytest.mark.parametrize(
    ("raw", "must_not_contain"),
    [
        (f"password={SECRET}", SECRET),
        (f'"token": "{SECRET}"', SECRET),
        (f"api_key = {SECRET}", SECRET),
        (f"postgresql+psycopg://findit:{SECRET}@localhost:5432/findit", SECRET),
        (f"mqtt://user:{SECRET}@broker:1883", SECRET),
    ],
)
def test_redact_removes_credentials(raw, must_not_contain):
    out = redact(raw)
    assert must_not_contain not in out
    assert "***" in out


def test_redact_keeps_the_useful_part_of_a_url():
    out = redact(f"postgresql+psycopg://findit:{SECRET}@localhost:5432/findit")
    assert "localhost:5432/findit" in out
    assert "findit:***@" in out


def test_redact_scrubs_nested_structures():
    out = redact({"mqtt": {"password": SECRET, "host": "broker"}, "list": [f"token={SECRET}"]})
    assert SECRET not in json.dumps(out)
    assert out["mqtt"]["host"] == "broker"


def test_logger_never_emits_a_secret_even_via_extra():
    stream = io.StringIO()
    logger = configure_logging(level=logging.INFO, stream=stream)
    logger.warning(
        "publish failed for %s",
        f"mqtt://user:{SECRET}@broker",
        extra={"event": "test", "reason": f"password={SECRET}", "ctx_url": f"token={SECRET}"},
    )
    output = stream.getvalue()
    assert SECRET not in output
    assert "***" in output
    assert json.loads(output)["event"] == "test"


def test_log_lines_are_structured_json():
    stream = io.StringIO()
    logger = configure_logging(level=logging.INFO, stream=stream)
    logger.info(
        "locate published",
        extra={"event": "locate.published", "command_id": "c1", "controller_id": "CTRL-01"},
    )
    record = json.loads(stream.getvalue())
    assert record["level"] == "INFO"
    assert record["event"] == "locate.published"
    assert record["command_id"] == "c1"
    assert record["controller_id"] == "CTRL-01"


def test_exception_logging_records_the_type_not_the_traceback():
    stream = io.StringIO()
    logger = configure_logging(level=logging.INFO, stream=stream)
    try:
        raise ConnectionRefusedError(f"mqtt://user:{SECRET}@broker refused")
    except ConnectionRefusedError:
        logger.error("publish failed", exc_info=True, extra={"event": "locate.failed"})
    output = stream.getvalue()
    assert SECRET not in output
    assert json.loads(output)["error"] == "ConnectionRefusedError"


def test_the_service_itself_logs_a_failure_without_the_connection_string(seeded):
    stream = io.StringIO()
    configure_logging(level=logging.INFO, stream=stream)
    flaky = FlakyPublisher(fail_count=99)
    service = CommandService(seeded, flaky, retry=RetryPolicy(max_attempts=2, backoff_s=0))
    service.locate_drawer(3, command_id="log-1")

    output = stream.getvalue()
    assert SECRET not in output
    events = [json.loads(line)["event"] for line in output.strip().splitlines()]
    assert "locate.publish_failed" in events
    assert "locate.failed" in events


def test_redacting_filter_is_installed_on_the_configured_logger():
    logger = configure_logging(stream=io.StringIO())
    assert any(isinstance(f, RedactingFilter) for h in logger.handlers for f in h.filters)
    assert all(isinstance(h.formatter, StructuredFormatter) for h in logger.handlers)

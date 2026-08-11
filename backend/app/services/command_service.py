"""Command Service - the locate lifecycle, including how it fails.

Responsibilities per the architecture document: create an idempotent
`command_id`, resolve the drawer through the Routing Service, and hand the
payload to the MQTT abstraction. It owns no broker details and no search logic.

Failure rules (S09):

* **Idempotency** - the same `command_id` never triggers a second LED action.
* **Offline controller** - refuse before publishing, with a named reason.
* **Publish failure** - bounded retries, then `failed`. Never an infinite loop.
* **Timeout** - a command that is published and never acknowledged becomes
  `expired`, never silently `acked`.
* **Stale ACK** - an ACK for an expired or unknown command is recorded and
  rejected; it cannot resurrect a command.
* **Malformed input** - refused by the contract layer and logged without the
  payload.

Nothing here ever marks success on its own. A publish that succeeds only means
the message left the backend (MQTT contract section 3).
"""
from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.logging_config import get_logger
from app.models import (
    CommandStatus,
    Controller,
    ControllerStatus,
    DeviceEvent,
    DeviceEventType,
    Item,
    LocateCommand,
)
from app.services.mqtt_client import (
    AckPayload,
    AckState,
    InvalidPayloadError,
    LocatePayload,
    Pattern,
    Publisher,
    command_topic,
    validate_controller_id,
)
from app.services.routing import drawer_to_route

DEFAULT_DURATION_MS = 30_000
DEFAULT_ACK_TIMEOUT_S = 15

log = get_logger()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UnknownCommandError(LookupError):
    """An ACK arrived for a command_id the backend never issued."""


class ControllerMismatchError(ValueError):
    """An ACK claims a different controller than the command was sent to."""


class StaleAckError(ValueError):
    """An ACK arrived for a command that has already expired or failed."""


class ControllerOfflineError(RuntimeError):
    """The target controller is known to be offline."""


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded retry. `max_attempts` counts the first try, so 1 means no retry
    and there is no configuration that produces an unbounded loop."""

    max_attempts: int = 3
    backoff_s: float = 0.2

    def __post_init__(self) -> None:
        if not 1 <= self.max_attempts <= 10:
            raise ValueError("max_attempts must be between 1 and 10")


DEFAULT_RETRY = RetryPolicy()


@dataclass
class LocateResult:
    command: LocateCommand
    published: bool
    deduplicated: bool
    attempts: int = 1


class CommandService:
    """Stateless apart from the injected session, publisher, clock and policy."""

    def __init__(
        self,
        session: Session,
        publisher: Publisher,
        *,
        now: Callable[[], datetime] = _utcnow,
        retry: RetryPolicy = DEFAULT_RETRY,
        sleep: Callable[[float], None] = lambda _s: None,
        ack_timeout_s: int = DEFAULT_ACK_TIMEOUT_S,
    ) -> None:
        self.session = session
        self.publisher = publisher
        self.now = now
        self.retry = retry
        self.sleep = sleep
        self.ack_timeout_s = ack_timeout_s

    # -- issuing -------------------------------------------------------------

    @staticmethod
    def new_command_id() -> str:
        return f"cmd-{uuid.uuid4()}"

    def locate_drawer(
        self,
        drawer_number: int,
        *,
        command_id: str | None = None,
        item_id: int | None = None,
        pattern: Pattern | str = Pattern.SOLID,
        duration_ms: int = DEFAULT_DURATION_MS,
    ) -> LocateResult:
        """Illuminate exactly one LED on exactly one controller."""
        route = drawer_to_route(drawer_number)  # raises ValueError outside 1..50
        cid = command_id or self.new_command_id()

        existing = self.session.get(LocateCommand, cid)
        if existing is not None:
            log.info(
                "duplicate command_id ignored",
                extra={
                    "event": "locate.deduplicated",
                    "command_id": cid,
                    "controller_id": existing.controller_id,
                    "status": existing.status.value,
                },
            )
            return LocateResult(command=existing, published=False, deduplicated=True, attempts=0)

        command = LocateCommand(
            command_id=cid,
            item_id=item_id,
            drawer_number=drawer_number,
            controller_id=route.controller_id,
            led_index=route.led_index,
            status=CommandStatus.PENDING,
        )
        self.session.add(command)
        self.session.flush()

        # Refuse before publishing if the node is known to be down. Failing
        # here is far more diagnosable than a publish that vanishes.
        controller = self.session.get(Controller, route.controller_id)
        if controller is not None and controller.status is ControllerStatus.OFFLINE:
            command.status = CommandStatus.FAILED
            command.error = f"controller {route.controller_id} is offline"
            self._record_event(
                route.controller_id,
                DeviceEventType.ERROR,
                {"reason": "controller offline", "command_id": cid},
            )
            log.warning(
                "locate refused: controller offline",
                extra={
                    "event": "locate.offline",
                    "command_id": cid,
                    "controller_id": route.controller_id,
                    "drawer_number": drawer_number,
                },
            )
            self.session.flush()
            return LocateResult(command=command, published=False, deduplicated=False, attempts=0)

        payload = LocatePayload(
            command_id=cid,
            controller_id=route.controller_id,
            local_led_index=route.led_index,
            pattern=Pattern(pattern).value,
            duration_ms=duration_ms,
            issued_at=self.now().isoformat(),
        )
        topic = command_topic(route.controller_id)

        last_error: Exception | None = None
        for attempt in range(1, self.retry.max_attempts + 1):
            try:
                self.publisher.publish(topic, payload.to_json())
            except Exception as exc:  # broker down, auth failure, timeout
                last_error = exc
                log.warning(
                    "publish attempt failed",
                    extra={
                        "event": "locate.publish_failed",
                        "command_id": cid,
                        "controller_id": route.controller_id,
                        "attempt": attempt,
                        "reason": type(exc).__name__,
                    },
                )
                if attempt < self.retry.max_attempts:
                    self.sleep(self.retry.backoff_s * attempt)
                continue

            command.status = CommandStatus.PUBLISHED
            command.published_at = self.now()
            self.session.flush()
            log.info(
                "locate published",
                extra={
                    "event": "locate.published",
                    "command_id": cid,
                    "controller_id": route.controller_id,
                    "drawer_number": drawer_number,
                    "led_index": route.led_index,
                    "attempt": attempt,
                },
            )
            return LocateResult(
                command=command, published=True, deduplicated=False, attempts=attempt
            )

        # Every attempt failed. Message only - never the payload or the URL,
        # which could carry credentials.
        command.status = CommandStatus.FAILED
        command.error = (
            f"publish failed after {self.retry.max_attempts} attempt(s): "
            f"{type(last_error).__name__}"
        )[:255]
        self._record_event(
            route.controller_id,
            DeviceEventType.ERROR,
            {
                "reason": "publish failed",
                "command_id": cid,
                "attempts": self.retry.max_attempts,
                "error": type(last_error).__name__ if last_error else "unknown",
            },
        )
        log.error(
            "locate failed",
            extra={
                "event": "locate.failed",
                "command_id": cid,
                "controller_id": route.controller_id,
                "attempt": self.retry.max_attempts,
                "reason": type(last_error).__name__ if last_error else "unknown",
            },
        )
        self.session.flush()
        return LocateResult(
            command=command, published=False, deduplicated=False, attempts=self.retry.max_attempts
        )

    def locate_item(
        self,
        item_id: int,
        *,
        command_id: str | None = None,
        pattern: Pattern | str = Pattern.SOLID,
        duration_ms: int = DEFAULT_DURATION_MS,
    ) -> LocateResult:
        item = self.session.get(Item, item_id)
        if item is None:
            raise LookupError(f"item {item_id} not found")
        if item.drawer_id is None:
            raise ValueError(f"item {item_id} has no drawer assigned")
        return self.locate_drawer(
            item.drawer_id,
            command_id=command_id,
            item_id=item.id,
            pattern=pattern,
            duration_ms=duration_ms,
        )

    # -- acknowledging -------------------------------------------------------

    def handle_ack(self, raw: str | bytes) -> LocateCommand:
        """Apply a device ACK. Every rejection is recorded before it is raised."""
        ack = AckPayload.from_json(raw)

        command = self.session.get(LocateCommand, ack.command_id)
        if command is None:
            self._record_event(
                ack.controller_id,
                DeviceEventType.ERROR,
                {"reason": "ack for unknown command_id", "command_id": ack.command_id},
            )
            log.warning(
                "ack for unknown command",
                extra={
                    "event": "ack.unknown",
                    "command_id": ack.command_id,
                    "controller_id": ack.controller_id,
                },
            )
            raise UnknownCommandError(f"unknown command_id: {ack.command_id}")

        if command.controller_id != ack.controller_id:
            self._record_event(
                ack.controller_id,
                DeviceEventType.ERROR,
                {
                    "reason": "controller mismatch",
                    "command_id": ack.command_id,
                    "expected": command.controller_id,
                    "received": ack.controller_id,
                },
            )
            log.warning(
                "ack from the wrong controller",
                extra={
                    "event": "ack.mismatch",
                    "command_id": ack.command_id,
                    "controller_id": ack.controller_id,
                },
            )
            raise ControllerMismatchError(
                f"command {ack.command_id} belongs to {command.controller_id}, "
                f"ACK came from {ack.controller_id}"
            )

        if command.status is CommandStatus.EXPIRED:
            # The LED has already been given up on. Accepting this would claim
            # a success that the operator was already told did not happen.
            self._record_event(
                ack.controller_id,
                DeviceEventType.ERROR,
                {"reason": "stale ack for expired command", "command_id": ack.command_id},
            )
            log.warning(
                "stale ack discarded",
                extra={
                    "event": "ack.stale",
                    "command_id": ack.command_id,
                    "controller_id": ack.controller_id,
                    "status": command.status.value,
                },
            )
            raise StaleAckError(f"command {ack.command_id} already expired")

        state = AckState(ack.state)
        if state is AckState.RECEIVED:
            if command.status is CommandStatus.PENDING:
                command.status = CommandStatus.PUBLISHED
        elif state is AckState.COMPLETED:
            if command.status is not CommandStatus.ACKED:
                command.status = CommandStatus.ACKED
                command.acked_at = self.now()
        elif state is AckState.REJECTED:
            command.status = CommandStatus.FAILED
            command.error = (ack.message or "rejected by device")[:255]

        self._record_event(
            ack.controller_id,
            DeviceEventType.ACK,
            {"command_id": ack.command_id, "state": ack.state, "message": ack.message},
        )
        log.info(
            "ack applied",
            extra={
                "event": "ack.applied",
                "command_id": ack.command_id,
                "controller_id": ack.controller_id,
                "status": command.status.value,
            },
        )
        self.session.flush()
        return command

    def handle_malformed_ack(self, raw: str | bytes, controller_id: str) -> None:
        """Record a diagnosable, secret-free error event for a bad payload."""
        try:
            AckPayload.from_json(raw)
        except InvalidPayloadError as exc:
            self._record_event(
                controller_id,
                DeviceEventType.ERROR,
                {"reason": "malformed ack", "detail": str(exc)[:200]},
            )
            log.warning(
                "malformed ack discarded",
                extra={
                    "event": "ack.malformed",
                    "controller_id": controller_id,
                    "reason": str(exc)[:120],
                },
            )
            self.session.flush()
            raise

    # -- timeouts ------------------------------------------------------------

    def expire_stale_commands(self, *, timeout_s: int | None = None) -> list[LocateCommand]:
        """Mark published-but-unacknowledged commands as `expired`.

        This is the honest alternative to assuming a publish worked: after the
        timeout the operator is told the command was never confirmed, not that
        it succeeded.
        """
        limit = self.now() - timedelta(seconds=timeout_s or self.ack_timeout_s)
        stale = list(
            self.session.scalars(
                select(LocateCommand).where(
                    LocateCommand.status == CommandStatus.PUBLISHED,
                    LocateCommand.published_at.is_not(None),
                    LocateCommand.published_at < limit,
                )
            )
        )
        for command in stale:
            command.status = CommandStatus.EXPIRED
            command.error = f"no ACK within {timeout_s or self.ack_timeout_s}s"
            self._record_event(
                command.controller_id,
                DeviceEventType.ERROR,
                {"reason": "ack timeout", "command_id": command.command_id},
            )
            log.warning(
                "command expired without an ack",
                extra={
                    "event": "command.expired",
                    "command_id": command.command_id,
                    "controller_id": command.controller_id,
                    "status": command.status.value,
                },
            )
        if stale:
            self.session.flush()
        return stale

    # -- device status -------------------------------------------------------

    def handle_status(self, raw: str | bytes) -> Controller:
        """Apply a controller status message, marking the node online."""
        import json

        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("status payload must be an object")
        except (TypeError, ValueError) as exc:
            raise InvalidPayloadError("status payload is not valid JSON") from exc

        controller_id = validate_controller_id(str(data.get("controller_id", "")))
        controller = self.session.get(Controller, controller_id)
        if controller is None:
            raise UnknownCommandError(f"unknown controller: {controller_id}")

        controller.status = (
            ControllerStatus.ONLINE if data.get("online", True) else ControllerStatus.OFFLINE
        )
        controller.last_seen = self.now()
        if data.get("fw_version"):
            controller.fw_version = str(data["fw_version"])[:32]

        self._record_event(controller_id, DeviceEventType.HEARTBEAT, {"source": "status"})
        log.info(
            "controller status applied",
            extra={
                "event": "status.applied",
                "controller_id": controller_id,
                "status": controller.status.value,
            },
        )
        self.session.flush()
        return controller

    def mark_offline(self, controller_id: str, *, reason: str = "missed heartbeats") -> Controller:
        controller = self.session.get(Controller, validate_controller_id(controller_id))
        if controller is None:
            raise UnknownCommandError(f"unknown controller: {controller_id}")
        controller.status = ControllerStatus.OFFLINE
        self._record_event(
            controller_id, DeviceEventType.ERROR, {"reason": reason, "state": "offline"}
        )
        log.warning(
            "controller marked offline",
            extra={"event": "controller.offline", "controller_id": controller_id, "reason": reason},
        )
        self.session.flush()
        return controller

    # -- helpers -------------------------------------------------------------

    def _record_event(
        self, controller_id: str, event_type: DeviceEventType, payload: dict
    ) -> DeviceEvent:
        event = DeviceEvent(controller_id=controller_id, type=event_type, payload=payload)
        self.session.add(event)
        # Flush here so the diagnostic survives the exceptions raised by the
        # rejection paths above - an error we raise on must still be recorded.
        self.session.flush()
        return event

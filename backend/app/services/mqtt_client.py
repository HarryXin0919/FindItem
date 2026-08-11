"""MQTT abstraction and the topic/payload contract.

Topics follow `02_Core_Documents/05_MQTT_Protocol_and_Controller_Contract_CN`:

    findit/controllers/{id}/command     backend -> ESP32   locate / cancel / test
    findit/controllers/{id}/ack         ESP32   -> backend received / completed / rejected
    findit/controllers/{id}/status      ESP32   -> backend online / fw / config
    findit/controllers/{id}/heartbeat   ESP32   -> backend liveness

The broker is always behind the `Publisher` interface, so tests and the S06
simulator run with `FakePublisher` and no physical ESP32 is ever required.

Broadcast is deliberately impossible: `controller_topic` rejects wildcards and
any id that is not one of the five configured controllers.
"""
from __future__ import annotations

import enum
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

TOPIC_ROOT = "findit/controllers"
CONTROLLER_ID_RE = re.compile(r"^CTRL-(0[1-9]|[1-9][0-9])$")

# The topology is locked by ADR-001, so these are the same numbers whether they
# come from Settings or from the fallback. The fallback exists so the contract
# can be imported by the S06 simulator with nothing but the standard library -
# the simulator must speak the identical contract, and forcing it to install the
# backend's dependencies would be a good way to end up with two contracts.
_LOCKED_CONTROLLER_COUNT = 5
_LOCKED_LEDS_PER_CONTROLLER = 10

try:  # pragma: no cover - exercised by the simulator, not the backend suite
    from app.config import get_settings

    _controller_count = get_settings().controller_count
    _leds_per_controller = get_settings().leds_per_controller
except Exception:  # pydantic-settings unavailable (bare interpreter)
    get_settings = None  # type: ignore[assignment]
    _controller_count = _LOCKED_CONTROLLER_COUNT
    _leds_per_controller = _LOCKED_LEDS_PER_CONTROLLER

CONTROLLER_IDS: tuple[str, ...] = tuple(
    f"CTRL-{n:02d}" for n in range(1, _controller_count + 1)
)
LEDS_PER_CONTROLLER = _leds_per_controller


class Channel(str, enum.Enum):
    COMMAND = "command"
    ACK = "ack"
    STATUS = "status"
    HEARTBEAT = "heartbeat"


class Action(str, enum.Enum):
    LOCATE = "locate"
    CANCEL = "cancel"
    TEST = "test"


class Pattern(str, enum.Enum):
    SOLID = "solid"
    BLINK = "blink"
    PULSE = "pulse"


class AckState(str, enum.Enum):
    RECEIVED = "received"
    COMPLETED = "completed"
    REJECTED = "rejected"


class InvalidControllerError(ValueError):
    """Unknown controller id, or an attempt to address more than one node."""


class InvalidPayloadError(ValueError):
    """Malformed command or ACK payload. Never echoes secrets (contract 4)."""


def validate_controller_id(controller_id: str) -> str:
    """Reject wildcards, empty ids and anything outside CTRL-01..CTRL-05.

    This is the single place that makes broadcast locate impossible, which the
    S05 forbidden scope requires.
    """
    if not isinstance(controller_id, str) or not controller_id:
        raise InvalidControllerError("controller_id must be a non-empty string")
    if any(ch in controller_id for ch in "+#*") or controller_id.lower() == "all":
        raise InvalidControllerError(
            f"broadcast/wildcard controller ids are not permitted: {controller_id!r}"
        )
    if not CONTROLLER_ID_RE.match(controller_id):
        raise InvalidControllerError(f"malformed controller_id: {controller_id!r}")
    if controller_id not in CONTROLLER_IDS:
        raise InvalidControllerError(
            f"unknown controller_id {controller_id!r}; configured: {list(CONTROLLER_IDS)}"
        )
    return controller_id


def validate_led_index(led_index: int) -> int:
    if not isinstance(led_index, int) or isinstance(led_index, bool):
        raise InvalidPayloadError("local_led_index must be an integer")
    if not 0 <= led_index < LEDS_PER_CONTROLLER:
        raise InvalidPayloadError(
            f"local_led_index {led_index} outside 0..{LEDS_PER_CONTROLLER - 1}"
        )
    return led_index


def controller_topic(controller_id: str, channel: Channel) -> str:
    """`findit/controllers/CTRL-03/command` and friends."""
    return f"{TOPIC_ROOT}/{validate_controller_id(controller_id)}/{Channel(channel).value}"


def command_topic(controller_id: str) -> str:
    return controller_topic(controller_id, Channel.COMMAND)


def ack_topic(controller_id: str) -> str:
    return controller_topic(controller_id, Channel.ACK)


def status_topic(controller_id: str) -> str:
    return controller_topic(controller_id, Channel.STATUS)


def heartbeat_topic(controller_id: str) -> str:
    return controller_topic(controller_id, Channel.HEARTBEAT)


def subscription_topics(controller_id: str) -> list[str]:
    """What the backend subscribes to for one controller. Never a wildcard, so
    a device can only ever be addressed individually."""
    return [
        ack_topic(controller_id),
        status_topic(controller_id),
        heartbeat_topic(controller_id),
    ]


@dataclass(frozen=True)
class LocatePayload:
    """Backend -> device. The device must re-validate `controller_id` and
    `local_led_index` before lighting anything (contract 2)."""

    command_id: str
    controller_id: str
    local_led_index: int
    action: str = Action.LOCATE.value
    pattern: str = Pattern.SOLID.value
    duration_ms: int = 30_000
    issued_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self) -> None:
        validate_controller_id(self.controller_id)
        validate_led_index(self.local_led_index)
        if not self.command_id:
            raise InvalidPayloadError("command_id must not be empty")
        Action(self.action)
        Pattern(self.pattern)
        if not 0 < self.duration_ms <= 600_000:
            raise InvalidPayloadError("duration_ms must be in 1..600000")

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)


@dataclass(frozen=True)
class AckPayload:
    """Device -> backend."""

    command_id: str
    controller_id: str
    state: str
    message: str = ""
    device_time: str | None = None

    @classmethod
    def from_json(cls, raw: str | bytes) -> AckPayload:
        try:
            data = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise InvalidPayloadError("ack payload is not valid JSON") from exc
        if not isinstance(data, dict):
            raise InvalidPayloadError("ack payload must be a JSON object")

        missing = [k for k in ("command_id", "controller_id", "state") if not data.get(k)]
        if missing:
            raise InvalidPayloadError(f"ack payload missing fields: {missing}")
        validate_controller_id(data["controller_id"])
        try:
            state = AckState(data["state"]).value
        except ValueError as exc:
            raise InvalidPayloadError(f"unknown ack state: {data['state']!r}") from exc

        return cls(
            command_id=str(data["command_id"]),
            controller_id=data["controller_id"],
            state=state,
            message=str(data.get("message", ""))[:255],
            device_time=data.get("device_time"),
        )


@dataclass(frozen=True)
class Message:
    topic: str
    payload: str
    qos: int
    retain: bool


@runtime_checkable
class Publisher(Protocol):
    """Everything the command service needs from a broker."""

    def publish(self, topic: str, payload: str, *, qos: int = 1, retain: bool = False) -> None: ...


class FakePublisher:
    """In-memory publisher used by tests and by the S06 simulator.

    Records every message so a test can assert not only *what* was published
    but that nothing else was - which is how "drawer 1 lights only CTRL-01
    local 0" is actually proven.
    """

    def __init__(self) -> None:
        self.messages: list[Message] = []
        self.fail_next: Exception | None = None

    def publish(self, topic: str, payload: str, *, qos: int = 1, retain: bool = False) -> None:
        if self.fail_next is not None:
            error, self.fail_next = self.fail_next, None
            raise error
        self.messages.append(Message(topic=topic, payload=payload, qos=qos, retain=retain))

    # -- test helpers --------------------------------------------------------
    @property
    def topics(self) -> list[str]:
        return [m.topic for m in self.messages]

    def payloads(self, topic: str | None = None) -> list[dict]:
        return [
            json.loads(m.payload)
            for m in self.messages
            if topic is None or m.topic == topic
        ]

    def clear(self) -> None:
        self.messages.clear()


class PahoPublisher:
    """Real broker publisher. Imported lazily so `paho` is not needed for the
    all-software path, and never used before S12."""

    def __init__(self, host: str | None = None, port: int | None = None, *, client_id: str = "findit-backend") -> None:
        import paho.mqtt.client as mqtt  # local import: optional at test time

        from app.config import get_settings as _get_settings  # requires the backend env

        settings = _get_settings()
        self._client = mqtt.Client(client_id=client_id)
        if settings.mqtt_username:
            # Credentials come from the environment only (contract 4).
            self._client.username_pw_set(settings.mqtt_username, settings.mqtt_password)
        self._host = host or settings.mqtt_host
        self._port = port or settings.mqtt_port
        self._connected = False

    def connect(self) -> None:
        self._client.connect(self._host, self._port)
        self._client.loop_start()
        self._connected = True

    def disconnect(self) -> None:
        if self._connected:
            self._client.loop_stop()
            self._client.disconnect()
            self._connected = False

    def publish(self, topic: str, payload: str, *, qos: int = 1, retain: bool = False) -> None:
        if not self._connected:
            self.connect()
        info = self._client.publish(topic, payload, qos=qos, retain=retain)
        info.wait_for_publish(timeout=5)

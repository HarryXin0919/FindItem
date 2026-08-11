"""Five-controller / fifty-LED software simulator.

Each `SimulatedController` stands in for one physical
`ESP32-C3 + MCP23017 + 10 x WS2812` node. The simulator imports the *same*
topic and payload contract the firmware will implement
(`app.services.mqtt_client`), so what S08 proves in software carries over to
S12-S15 instead of having to be re-proved.

A device re-validates `controller_id` and `local_led_index` before lighting
anything (MQTT contract section 2), rejects a payload it cannot parse, and
never acts twice on the same `command_id` (section 4). All three behaviours are
modelled here, including the rejections - a simulator that only models the
happy path proves nothing about bring-up.

No physical hardware is involved.

    cd 09_Code
    python simulator/five_node_simulator.py
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

# The backend package holds the single copy of the contract.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.mqtt_client import (  # noqa: E402
    CONTROLLER_IDS,
    LEDS_PER_CONTROLLER,
    Action,
    AckState,
    Channel,
    ack_topic,
    command_topic,
    heartbeat_topic,
    status_topic,
)

FW_VERSION = "sim-0.6.0"


@dataclass
class SimulatedController:
    """One node. `leds` is the WS2812 chain; index is controller-local 0-9."""

    controller_id: str
    drawer_start: int
    led_count: int = LEDS_PER_CONTROLLER
    leds: list[bool] = field(default_factory=lambda: [False] * LEDS_PER_CONTROLLER)
    online: bool = True
    seen_command_ids: set[str] = field(default_factory=set)
    heartbeats: int = 0

    # -- the device side of the contract -------------------------------------

    def handle_command(self, topic: str, raw: str) -> str:
        """Consume one command message; return the ACK payload it would publish.

        Mirrors what the firmware must do: check the topic is its own, parse,
        re-validate identity and index, then act.
        """
        if topic != command_topic(self.controller_id):
            return self._ack("", AckState.REJECTED, "topic does not belong to this controller")

        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("payload is not an object")
        except (TypeError, ValueError):
            return self._ack("", AckState.REJECTED, "unparsable payload")

        command_id = str(payload.get("command_id") or "")
        if not command_id:
            return self._ack("", AckState.REJECTED, "missing command_id")

        # Device-side re-validation (contract section 2).
        if payload.get("controller_id") != self.controller_id:
            return self._ack(command_id, AckState.REJECTED, "controller_id mismatch")

        index = payload.get("local_led_index")
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < self.led_count:
            return self._ack(command_id, AckState.REJECTED, "local_led_index out of range")

        try:
            action = Action(payload.get("action", Action.LOCATE.value))
        except ValueError:
            return self._ack(command_id, AckState.REJECTED, "unknown action")

        if command_id in self.seen_command_ids:
            # Already acted on; acknowledge again but do not touch the LEDs.
            return self._ack(command_id, AckState.COMPLETED, "duplicate command_id ignored")
        self.seen_command_ids.add(command_id)

        if action is Action.LOCATE:
            self.leds = [False] * self.led_count
            self.leds[index] = True
        elif action is Action.CANCEL:
            self.leds = [False] * self.led_count
        elif action is Action.TEST:
            self.leds = [True] * self.led_count

        return self._ack(command_id, AckState.COMPLETED, f"{action.value} applied")

    def clear(self) -> None:
        self.leds = [False] * self.led_count

    # -- outbound device messages --------------------------------------------

    def _ack(self, command_id: str, state: AckState, message: str) -> str:
        return json.dumps(
            {
                "command_id": command_id,
                "controller_id": self.controller_id,
                "state": state.value,
                "message": message,
                "device_time": None,
            },
            separators=(",", ":"),
            sort_keys=True,
        )

    def status_payload(self) -> str:
        return json.dumps(
            {
                "controller_id": self.controller_id,
                "online": self.online,
                "fw_version": FW_VERSION,
                "led_count": self.led_count,
                "drawer_start": self.drawer_start,
                "drawer_end": self.drawer_start + self.led_count - 1,
                "active_leds": self.active_indexes,
            },
            separators=(",", ":"),
            sort_keys=True,
        )

    def heartbeat_payload(self) -> str:
        self.heartbeats += 1
        return json.dumps(
            {"controller_id": self.controller_id, "seq": self.heartbeats},
            separators=(",", ":"),
            sort_keys=True,
        )

    @property
    def active_indexes(self) -> list[int]:
        return [i for i, on in enumerate(self.leds) if on]


class FiveNodeSimulator:
    """The five nodes plus the broker fan-out.

    `publish` routes a message to the one controller that owns the topic, so a
    cross-controller delivery is impossible to fake: if the backend addresses
    the wrong node, the wrong node's LEDs light and the matrix test sees it.
    """

    def __init__(self) -> None:
        self.controllers: dict[str, SimulatedController] = {
            cid: SimulatedController(
                controller_id=cid,
                drawer_start=i * LEDS_PER_CONTROLLER + 1,
            )
            for i, cid in enumerate(CONTROLLER_IDS)
        }
        self.acks: list[tuple[str, str]] = []  # (ack_topic, payload)
        self.rejections: list[dict] = []

    # -- broker side ---------------------------------------------------------

    def publish(self, topic: str, payload: str, *, qos: int = 1, retain: bool = False) -> None:
        """Publisher-compatible entry point, so the backend's CommandService can
        drive the simulator directly with no adapter."""
        for cid, controller in self.controllers.items():
            if topic == command_topic(cid):
                ack = controller.handle_command(topic, payload)
                self.acks.append((ack_topic(cid), ack))
                parsed = json.loads(ack)
                if parsed["state"] == AckState.REJECTED.value:
                    self.rejections.append(parsed)
                return
        raise ValueError(f"no simulated controller subscribes to {topic!r}")

    # -- inspection ----------------------------------------------------------

    def active_leds(self) -> list[tuple[str, int]]:
        """Every lit pixel across all 50 channels, in a stable order."""
        return [
            (cid, index)
            for cid in CONTROLLER_IDS
            for index in self.controllers[cid].active_indexes
        ]

    def clear_all(self) -> None:
        for controller in self.controllers.values():
            controller.clear()

    def reset(self) -> None:
        self.clear_all()
        self.acks.clear()
        self.rejections.clear()
        for controller in self.controllers.values():
            controller.seen_command_ids.clear()

    def controller_for_drawer(self, drawer_number: int) -> tuple[str, int]:
        """Independent of the backend router on purpose: the matrix test needs
        an expectation the backend did not produce."""
        if not 1 <= drawer_number <= len(CONTROLLER_IDS) * LEDS_PER_CONTROLLER:
            raise ValueError(f"drawer {drawer_number} outside 1..{len(CONTROLLER_IDS) * LEDS_PER_CONTROLLER}")
        return (
            f"CTRL-{(drawer_number - 1) // LEDS_PER_CONTROLLER + 1:02d}",
            (drawer_number - 1) % LEDS_PER_CONTROLLER,
        )

    def status_snapshot(self) -> list[str]:
        return [c.status_payload() for c in self.controllers.values()]

    def heartbeat_round(self) -> list[tuple[str, str]]:
        return [
            (heartbeat_topic(cid), self.controllers[cid].heartbeat_payload())
            for cid in CONTROLLER_IDS
        ]


# --- Backwards-compatible module-level API ----------------------------------
# The original package shipped `controllers` and `locate(controller_id, index)`.
# Both still work so anything written against the v5 package keeps running.

simulator = FiveNodeSimulator()
controllers = simulator.controllers


def locate(controller_id: str, led_index: int, *, command_id: str | None = None) -> str:
    """Light one LED on one controller via the real command contract."""
    simulator.clear_all()
    cid = command_id or f"sim-{controller_id}-{led_index}-{len(simulator.acks)}"
    payload = json.dumps(
        {
            "command_id": cid,
            "controller_id": controller_id,
            "local_led_index": led_index,
            "action": Action.LOCATE.value,
            "pattern": "solid",
            "duration_ms": 30000,
            "issued_at": "1970-01-01T00:00:00+00:00",
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    simulator.publish(command_topic(controller_id), payload)
    return cid


def locate_drawer(drawer_number: int, *, command_id: str | None = None) -> str:
    cid, index = simulator.controller_for_drawer(drawer_number)
    return locate(cid, index, command_id=command_id)


if __name__ == "__main__":
    print(f"FindIt five-node simulator ({len(CONTROLLER_IDS)} controllers x {LEDS_PER_CONTROLLER} LEDs)")
    print(f"channels: {', '.join(c.value for c in Channel)}")
    print(f"status topic example: {status_topic(CONTROLLER_IDS[0])}")
    print()

    locate_drawer(25)
    print("locate drawer 25:")
    for cid in CONTROLLER_IDS:
        print(f"  {cid} active LED indexes: {simulator.controllers[cid].active_indexes}")
    print()
    print("ACK published:")
    for topic, payload in simulator.acks[-1:]:
        print(f"  {topic}  {payload}")

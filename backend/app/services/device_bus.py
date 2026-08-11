"""Device bus: the seam between the backend and whatever is on the other end.

In the software phase (S01-S10) the other end is the five-node simulator
running in this process. From S12 the same seam is filled by `PahoPublisher`
talking to a real broker. Both satisfy `Publisher`, so nothing above this line
changes at bring-up - which is the whole point of the software-first plan.

`SimulatedDeviceBus` also collects the ACKs the simulated devices publish back,
so the backend can apply them exactly as it will apply real ones. Publishing
still never marks a command complete on its own (MQTT contract section 3); the
ACK does.
"""
from __future__ import annotations

import sys
from pathlib import Path

# The simulator is a sibling package, not a backend dependency.
SIMULATOR_DIR = Path(__file__).resolve().parents[3] / "simulator"
if str(SIMULATOR_DIR) not in sys.path:
    sys.path.insert(0, str(SIMULATOR_DIR))

from five_node_simulator import FiveNodeSimulator  # noqa: E402


class SimulatedDeviceBus:
    """A `Publisher` backed by five in-process simulated controllers."""

    def __init__(self) -> None:
        self.simulator = FiveNodeSimulator()
        self._inbox: list[str] = []

    # -- Publisher -----------------------------------------------------------

    def publish(self, topic: str, payload: str, *, qos: int = 1, retain: bool = False) -> None:
        seen = len(self.simulator.acks)
        self.simulator.publish(topic, payload, qos=qos, retain=retain)
        # Everything the devices published in response is now inbound traffic.
        self._inbox.extend(ack for _, ack in self.simulator.acks[seen:])

    # -- inbound -------------------------------------------------------------

    def drain_acks(self) -> list[str]:
        """Take every ACK received since the last drain."""
        acks, self._inbox = self._inbox, []
        return acks

    # -- inspection ----------------------------------------------------------

    def active_leds(self) -> list[tuple[str, int]]:
        return self.simulator.active_leds()

    def status_payloads(self) -> list[str]:
        return self.simulator.status_snapshot()

    def reset(self) -> None:
        self.simulator.reset()
        self._inbox.clear()

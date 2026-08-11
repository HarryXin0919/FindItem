"""50-command routing matrix for the five-node simulator.

Standalone by design - it runs on a bare interpreter with no pytest and no
backend dependencies:

    cd 09_Code
    python simulator/test_routing_matrix.py

Exit code 0 means all 50 routes are correct, exactly one LED was lit per
command, no cross-controller activation occurred, and no controller identity is
duplicated.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from five_node_simulator import (  # noqa: E402
    CONTROLLER_IDS,
    LEDS_PER_CONTROLLER,
    FiveNodeSimulator,
    locate,
)
from five_node_simulator import simulator as legacy_simulator  # noqa: E402

TOTAL_DRAWERS = len(CONTROLLER_IDS) * LEDS_PER_CONTROLLER

failures: list[str] = []
checks = 0


def check(condition: bool, message: str) -> None:
    global checks
    checks += 1
    if not condition:
        failures.append(message)


def expected(drawer_number: int) -> tuple[str, int]:
    """Computed here from first principles, not taken from the backend router,
    so a bug in the router cannot make this test agree with itself."""
    return (
        f"CTRL-{(drawer_number - 1) // LEDS_PER_CONTROLLER + 1:02d}",
        (drawer_number - 1) % LEDS_PER_CONTROLLER,
    )


print("FindIt S06 - five-controller / fifty-LED routing matrix")
print("=" * 66)

# --- 1. Controller identity --------------------------------------------------
sim = FiveNodeSimulator()
ids = [c.controller_id for c in sim.controllers.values()]
check(len(ids) == 5, f"expected 5 controllers, got {len(ids)}")
check(len(set(ids)) == len(ids), f"duplicate controller identity: {ids}")
check(sorted(ids) == list(CONTROLLER_IDS), f"unexpected controller ids: {ids}")
for controller in sim.controllers.values():
    check(
        len(controller.leds) == LEDS_PER_CONTROLLER,
        f"{controller.controller_id} has {len(controller.leds)} LEDs",
    )
ranges = [(c.drawer_start, c.drawer_start + c.led_count - 1) for c in sim.controllers.values()]
covered = [d for lo, hi in ranges for d in range(lo, hi + 1)]
check(sorted(covered) == list(range(1, TOTAL_DRAWERS + 1)), f"drawer coverage gap: {ranges}")
print(f"[1] identity      {len(ids)} unique controllers, ranges {ranges}")

# --- 2. The 50-route matrix --------------------------------------------------
cross_activations = 0
for drawer in range(1, TOTAL_DRAWERS + 1):
    sim.reset()
    controller_id, led_index = expected(drawer)
    payload = json.dumps(
        {
            "command_id": f"matrix-{drawer}",
            "controller_id": controller_id,
            "local_led_index": led_index,
            "action": "locate",
            "pattern": "solid",
            "duration_ms": 30000,
            "issued_at": "1970-01-01T00:00:00+00:00",
        }
    )
    sim.publish(f"findit/controllers/{controller_id}/command", payload)

    active = sim.active_leds()
    check(len(active) == 1, f"drawer {drawer}: {len(active)} LEDs active, expected exactly 1")
    check(
        active == [(controller_id, led_index)],
        f"drawer {drawer}: lit {active}, expected [('{controller_id}', {led_index})]",
    )
    if active and active[0][0] != controller_id:
        cross_activations += 1

    acks = [json.loads(p) for _, p in sim.acks]
    check(len(acks) == 1, f"drawer {drawer}: {len(acks)} ACKs, expected 1")
    check(
        acks and acks[0]["state"] == "completed" and acks[0]["controller_id"] == controller_id,
        f"drawer {drawer}: unexpected ACK {acks}",
    )

print(f"[2] routing       {TOTAL_DRAWERS}/{TOTAL_DRAWERS} routes checked, "
      f"cross-controller activations: {cross_activations}")

# --- 3. Device-side rejections ----------------------------------------------
sim.reset()
sim.publish(
    "findit/controllers/CTRL-01/command",
    json.dumps({"command_id": "x", "controller_id": "CTRL-02", "local_led_index": 0}),
)
check(sim.active_leds() == [], "a controller_id mismatch still lit an LED")
check(len(sim.rejections) == 1, "controller_id mismatch was not rejected")

sim.reset()
sim.publish(
    "findit/controllers/CTRL-01/command",
    json.dumps({"command_id": "y", "controller_id": "CTRL-01", "local_led_index": 10}),
)
check(sim.active_leds() == [], "an out-of-range LED index still lit an LED")
check(len(sim.rejections) == 1, "out-of-range local_led_index was not rejected")

sim.reset()
sim.publish("findit/controllers/CTRL-01/command", "{not json")
check(sim.active_leds() == [], "an unparsable payload still lit an LED")
check(len(sim.rejections) == 1, "unparsable payload was not rejected")
print(f"[3] rejections    mismatch / out-of-range / unparsable all refused, no LED lit")

# --- 4. Idempotency ----------------------------------------------------------
sim.reset()
dup = json.dumps(
    {"command_id": "dup-1", "controller_id": "CTRL-03", "local_led_index": 4, "action": "locate"}
)
sim.publish("findit/controllers/CTRL-03/command", dup)
first_state = sim.active_leds()
sim.controllers["CTRL-03"].clear()  # simulate the LED having timed out
sim.publish("findit/controllers/CTRL-03/command", dup)
check(first_state == [("CTRL-03", 4)], f"first locate lit {first_state}")
check(sim.active_leds() == [], "a duplicate command_id re-activated the LED")
check(len(sim.acks) == 2, "a duplicate command should still be acknowledged")
print("[4] idempotency   duplicate command_id acknowledged but not re-activated")

# --- 5. Status and heartbeat -------------------------------------------------
sim.reset()
statuses = [json.loads(s) for s in sim.status_snapshot()]
check(len(statuses) == 5, "expected five status payloads")
check(
    all(s["online"] and s["led_count"] == LEDS_PER_CONTROLLER for s in statuses),
    "a controller reported offline or the wrong LED count",
)
beats = sim.heartbeat_round()
check(len(beats) == 5, "expected five heartbeats")
check(
    all(t == f"findit/controllers/{c}/heartbeat" for (t, _), c in zip(beats, CONTROLLER_IDS)),
    "heartbeat topics do not match the contract",
)
print("[5] status/beat   5 status payloads, 5 heartbeats on contract topics")

# --- 6. The legacy module-level API still works ------------------------------
legacy_simulator.reset()
locate("CTRL-05", 9)
check(
    legacy_simulator.active_leds() == [("CTRL-05", 9)],
    f"legacy locate() API broke: {legacy_simulator.active_leds()}",
)
print("[6] legacy api    module-level controllers/locate() still behave as before")

# --- Result ------------------------------------------------------------------
print("=" * 66)
if failures:
    print(f"FAIL: {len(failures)} of {checks} checks failed")
    for f in failures[:20]:
        print(f"  - {f}")
    sys.exit(1)
print(f"PASS: {checks} checks, {TOTAL_DRAWERS}/{TOTAL_DRAWERS} simulated drawer routes "
      f"unique and correct, 0 cross-controller activations.")

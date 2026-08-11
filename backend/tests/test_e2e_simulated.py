"""S08 acceptance tests: the whole path, with no hardware anywhere.

    search -> resolve -> command -> simulated controller -> ACK -> visible state

Every controller in this test is `SimulatedController` running in-process.
Nothing here opens a serial port, contacts a broker, or needs an ESP32; the
suite passes on a machine that has never seen the hardware.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import get_db
from app.main import app
from app.models import CommandStatus, DeviceEvent, DeviceEventType, Item, LocateCommand
from app.routes_controllers import get_device_bus, get_publisher
from app.seed import seed
from app.services.device_bus import SimulatedDeviceBus


@pytest.fixture()
def seeded(db_session):
    seed(db_session)
    return db_session


@pytest.fixture()
def bus():
    return SimulatedDeviceBus()


@pytest.fixture()
def client(seeded, bus):
    app.dependency_overrides[get_db] = lambda: seeded
    app.dependency_overrides[get_publisher] = lambda: bus
    app.dependency_overrides[get_device_bus] = lambda: bus
    try:
        yield TestClient(app)
    finally:
        for dep in (get_db, get_publisher, get_device_bus):
            app.dependency_overrides.pop(dep, None)


# --- The full loop ----------------------------------------------------------

def test_search_to_ack_round_trip(client, bus):
    """One user action, all the way to a lit simulated LED and back."""
    found = client.get("/api/search", params={"q": "spark max"}).json()
    assert found["outcome"] == "found"
    route = found["route"]
    assert (route["drawer_number"], route["controller_id"], route["led_index"]) == (
        30,
        "CTRL-03",
        9,
    )

    located = client.post("/api/locate", json={"drawer_number": route["drawer_number"]}).json()
    command = located["command"]

    assert located["published"] is True
    assert located["acks_applied"] == 1
    assert command["controller_id"] == "CTRL-03"
    assert command["led_index"] == 9
    # The ACK - not the publish - is what completes it.
    assert command["status"] == "acked"
    assert command["acked_at"] is not None
    assert located["note"] == "acknowledged by the controller"

    # Exactly one pixel is lit across all fifty channels.
    assert located["active_leds"] == [{"controller_id": "CTRL-03", "led_index": 9}]
    assert bus.active_leds() == [("CTRL-03", 9)]


def test_locate_by_item_id_takes_the_same_path(client):
    found = client.get("/api/search", params={"q": "neopixel"}).json()
    item_id = found["item"]["id"]

    located = client.post("/api/locate", json={"item_id": item_id}).json()
    assert located["command"]["status"] == "acked"
    assert located["active_leds"] == [{"controller_id": "CTRL-03", "led_index": 4}]


@pytest.mark.parametrize(
    ("drawer", "controller", "led"),
    [(1, "CTRL-01", 0), (10, "CTRL-01", 9), (11, "CTRL-02", 0), (25, "CTRL-03", 4),
     (40, "CTRL-04", 9), (41, "CTRL-05", 0), (50, "CTRL-05", 9)],
)
def test_boundaries_light_exactly_one_led_end_to_end(client, bus, drawer, controller, led):
    bus.reset()
    located = client.post("/api/locate", json={"drawer_number": drawer}).json()
    assert located["command"]["status"] == "acked"
    assert located["active_leds"] == [{"controller_id": controller, "led_index": led}]


def test_all_fifty_drawers_close_the_loop(client, bus):
    """The full sweep: every drawer acknowledged, exactly one LED each, no
    cross-controller activation anywhere."""
    cross = 0
    for drawer in range(1, 51):
        bus.reset()
        located = client.post(
            "/api/locate", json={"drawer_number": drawer, "command_id": f"e2e-{drawer}"}
        ).json()
        assert located["command"]["status"] == "acked", f"drawer {drawer} not acknowledged"
        lit = located["active_leds"]
        assert len(lit) == 1, f"drawer {drawer} lit {len(lit)} LEDs"
        expected = {
            "controller_id": f"CTRL-{(drawer - 1) // 10 + 1:02d}",
            "led_index": (drawer - 1) % 10,
        }
        if lit[0] != expected:
            cross += 1
    assert cross == 0


# --- ACK state is visible ---------------------------------------------------

def test_ack_is_readable_from_the_command_endpoint(client):
    located = client.post(
        "/api/locate", json={"drawer_number": 7, "command_id": "e2e-visible"}
    ).json()
    assert located["command"]["status"] == "acked"

    fetched = client.get("/api/commands/e2e-visible").json()
    assert fetched["status"] == "acked"
    assert fetched["acked_at"] is not None
    assert fetched["controller_id"] == "CTRL-01"


def test_ack_is_recorded_as_a_device_event(client, seeded):
    client.post("/api/locate", json={"drawer_number": 7, "command_id": "e2e-event"})
    events = list(seeded.scalars(select(DeviceEvent)))
    assert any(
        e.type is DeviceEventType.ACK and e.payload.get("command_id") == "e2e-event"
        for e in events
    )


def test_led_state_endpoint_reports_the_device_truth(client, bus):
    bus.reset()
    assert client.get("/api/leds").json() == {"device_mode": "simulator", "active_leds": []}
    client.post("/api/locate", json={"drawer_number": 44})
    assert client.get("/api/leds").json()["active_leds"] == [
        {"controller_id": "CTRL-05", "led_index": 3}
    ]


# --- Failure paths stay honest ---------------------------------------------

def test_replayed_command_id_does_not_relight(client, bus, seeded):
    client.post("/api/locate", json={"drawer_number": 12, "command_id": "e2e-dup"})
    bus.simulator.controllers["CTRL-02"].clear()  # LED timed out on the device

    replay = client.post(
        "/api/locate", json={"drawer_number": 12, "command_id": "e2e-dup"}
    ).json()
    assert replay["deduplicated"] is True
    assert replay["published"] is False
    assert replay["active_leds"] == []  # not re-lit
    assert seeded.scalar(
        select(LocateCommand).where(LocateCommand.command_id == "e2e-dup")
    ).status is CommandStatus.ACKED


def test_locate_requires_exactly_one_target(client):
    assert client.post("/api/locate", json={}).status_code == 400
    assert (
        client.post("/api/locate", json={"drawer_number": 1, "item_id": 1}).status_code == 400
    )


def test_locate_rejects_an_out_of_range_drawer(client, bus):
    assert client.post("/api/locate", json={"drawer_number": 51}).status_code == 422
    assert bus.active_leds() == []


def test_locate_for_an_item_without_a_drawer_is_409(client, seeded, bus):
    orphan = Item(name="Unsorted mystery bracket", aliases=[], drawer_id=None)
    seeded.add(orphan)
    seeded.flush()
    assert client.post("/api/locate", json={"item_id": orphan.id}).status_code == 409
    assert bus.active_leds() == []


def test_unknown_item_search_never_publishes(client, bus):
    body = client.get("/api/search", params={"q": "flux capacitor"}).json()
    assert body["outcome"] == "not_found"
    assert body["route"] is None
    assert bus.active_leds() == []


# --- No physical dependency -------------------------------------------------

def test_no_serial_or_broker_module_is_imported_by_the_software_path():
    """A physical dependency would show up as an imported transport module."""
    import sys

    forbidden = {"serial", "pyserial", "paho.mqtt.client"}
    assert forbidden.isdisjoint(sys.modules), (
        f"software tests pulled in a physical transport: {forbidden & set(sys.modules)}"
    )


def test_device_bus_is_pure_python_simulation(bus):
    from five_node_simulator import FiveNodeSimulator, SimulatedController

    assert isinstance(bus.simulator, FiveNodeSimulator)
    assert len(bus.simulator.controllers) == 5
    assert all(
        isinstance(c, SimulatedController) and len(c.leds) == 10
        for c in bus.simulator.controllers.values()
    )

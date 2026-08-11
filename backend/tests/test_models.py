"""S03 acceptance tests: the data model and the seed, against real PostgreSQL."""
from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DataError, IntegrityError

from app.models import (
    CommandStatus,
    Controller,
    ControllerStatus,
    DeviceEvent,
    DeviceEventType,
    Drawer,
    Item,
    LocateCommand,
)
from app.seed import seed
from app.services.routing import drawer_to_route


@pytest.fixture()
def seeded(db_session):
    seed(db_session)
    return db_session


# --- Acceptance criteria ----------------------------------------------------

def test_exactly_five_controller_records(seeded):
    assert seeded.scalar(select(func.count()).select_from(Controller)) == 5


def test_exactly_fifty_drawer_records(seeded):
    assert seeded.scalar(select(func.count()).select_from(Drawer)) == 50


@pytest.mark.parametrize(
    ("drawer_number", "controller_id", "local_led_index"),
    [(1, "CTRL-01", 0), (10, "CTRL-01", 9), (11, "CTRL-02", 0), (50, "CTRL-05", 9)],
)
def test_boundary_drawers_map_to_expected_controller_and_index(
    seeded, drawer_number, controller_id, local_led_index
):
    drawer = seeded.get(Drawer, drawer_number)
    assert drawer is not None
    assert drawer.controller_id == controller_id
    assert drawer.local_led_index == local_led_index


# --- Mapping is deterministic and agrees with the router --------------------

def test_every_drawer_matches_the_routing_service(seeded):
    for drawer in seeded.scalars(select(Drawer)):
        route = drawer_to_route(drawer.drawer_number)
        assert drawer.controller_id == route.controller_id
        assert drawer.local_led_index == route.led_index


def test_each_controller_owns_ten_contiguous_drawers(seeded):
    for controller in seeded.scalars(select(Controller)):
        owned = sorted(
            d.drawer_number
            for d in seeded.scalars(
                select(Drawer).where(Drawer.controller_id == controller.controller_id)
            )
        )
        assert len(owned) == 10
        assert owned == list(range(controller.drawer_start, controller.drawer_end + 1))
        assert owned == list(range(owned[0], owned[0] + 10))


def test_seed_is_idempotent(seeded):
    counts = seed(seeded)
    assert (counts["controllers"], counts["drawers"]) == (5, 50)
    counts_again = seed(seeded)
    assert counts_again == counts


# --- Duplicate / out-of-range protection ------------------------------------

def test_duplicate_drawer_number_is_rejected(seeded):
    seeded.add(Drawer(drawer_number=1, controller_id="CTRL-01", local_led_index=5))
    with pytest.raises(IntegrityError):
        seeded.flush()


def test_duplicate_controller_id_is_rejected(seeded):
    seeded.add(Controller(controller_id="CTRL-01", drawer_start=1, drawer_end=10, led_count=10))
    with pytest.raises(IntegrityError):
        seeded.flush()


def test_two_drawers_cannot_share_one_controller_led_slot(seeded):
    # drawer_number 7 is free of conflict, but CTRL-01[0] is already drawer 1.
    seeded.add(Drawer(drawer_number=51, controller_id="CTRL-01", local_led_index=0))
    with pytest.raises(IntegrityError):
        seeded.flush()


@pytest.mark.parametrize("bad_number", [0, 51, -1])
def test_drawer_number_outside_1_50_is_rejected(seeded, bad_number):
    seeded.add(Drawer(drawer_number=bad_number, controller_id="CTRL-01", local_led_index=3))
    with pytest.raises(IntegrityError):
        seeded.flush()


@pytest.mark.parametrize("bad_index", [-1, 10, 99])
def test_local_led_index_outside_0_9_is_rejected(seeded, bad_index):
    seeded.add(Drawer(drawer_number=51, controller_id="CTRL-01", local_led_index=bad_index))
    with pytest.raises(IntegrityError):
        seeded.flush()


def test_controller_led_count_is_locked_to_ten(seeded):
    seeded.add(Controller(controller_id="CTRL-06", drawer_start=1, drawer_end=8, led_count=8))
    with pytest.raises(IntegrityError):
        seeded.flush()


def test_drawer_cannot_reference_an_unknown_controller(seeded):
    seeded.add(Drawer(drawer_number=51, controller_id="CTRL-99", local_led_index=0))
    with pytest.raises(IntegrityError):
        seeded.flush()


# --- The remaining entities round-trip --------------------------------------

def test_item_round_trips_with_aliases_and_drawer(seeded):
    item = Item(name="M3 hex nut", aliases=["m3 nut", "hex nut"], drawer_id=23)
    seeded.add(item)
    seeded.flush()
    loaded = seeded.get(Item, item.id)
    assert loaded.aliases == ["m3 nut", "hex nut"]
    assert loaded.drawer.controller_id == "CTRL-03"
    assert loaded.drawer.local_led_index == 2


def test_locate_command_defaults_to_pending_and_command_id_is_unique(seeded):
    seeded.add(
        LocateCommand(
            command_id="cmd-1", drawer_number=23, controller_id="CTRL-03", led_index=2
        )
    )
    seeded.flush()
    assert seeded.get(LocateCommand, "cmd-1").status is CommandStatus.PENDING

    seeded.add(
        LocateCommand(
            command_id="cmd-1", drawer_number=24, controller_id="CTRL-03", led_index=3
        )
    )
    with pytest.raises(IntegrityError):
        seeded.flush()


def test_device_event_stores_a_json_payload(seeded):
    seeded.add(
        DeviceEvent(
            controller_id="CTRL-02",
            type=DeviceEventType.HEARTBEAT,
            payload={"uptime_s": 42, "rssi": -61},
        )
    )
    seeded.flush()
    event = seeded.scalars(select(DeviceEvent)).one()
    assert event.type is DeviceEventType.HEARTBEAT
    assert event.payload["uptime_s"] == 42


def test_controller_status_defaults_to_unknown(seeded):
    controller = seeded.get(Controller, "CTRL-04")
    assert controller.status is ControllerStatus.UNKNOWN
    assert controller.last_seen is None

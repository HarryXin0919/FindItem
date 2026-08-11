"""Deterministic seed: exactly 5 controllers and exactly 50 drawers.

The mapping is derived from `app.services.routing.drawer_to_route`, so the seed
and the runtime router can never disagree - there is one formula, not two.

Idempotent: running it twice leaves the same 5 + 50 rows. Run it directly with

    cd 09_Code/backend
    .venv/Scripts/python.exe -m app.seed
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import Base, engine, session_scope
from app.models import Controller, Drawer, Item
from app.services.routing import drawer_to_route

settings = get_settings()
TOTAL_DRAWERS = settings.total_drawers
CONTROLLER_COUNT = settings.controller_count
LEDS_PER_CONTROLLER = settings.leds_per_controller

# Twelve items placed on purpose: both edges of every controller (drawers 1, 10,
# 11, 20, 21, 30, 31, 40, 41, 50) so boundary routing is exercised by real data,
# plus two mid-range items. "M3 x 10 hex bolt" and "M3 hex nut" deliberately
# share the prefix "m3 hex" so the ambiguous path has real data behind it.
SEED_ITEMS: list[dict] = [
    {"name": "M3 x 10 hex bolt", "aliases": ["m3 bolt", "m3x10"], "drawer_id": 1},
    {"name": "M3 hex nut", "aliases": ["m3 nut"], "drawer_id": 10},
    {"name": "M5 x 20 socket screw", "aliases": ["m5 screw", "m5x20"], "drawer_id": 11},
    {"name": "M5 nyloc nut", "aliases": ["m5 nyloc"], "drawer_id": 20},
    {"name": "REV NEO brushless motor", "aliases": ["neo", "neo motor"], "drawer_id": 21},
    {"name": "SPARK MAX motor controller", "aliases": ["spark max"], "drawer_id": 30},
    {"name": "1/2 inch hex shaft", "aliases": ["hex shaft", "half inch shaft"], "drawer_id": 31},
    {"name": "Thunderhex bearing", "aliases": ["bearing", "flanged bearing"], "drawer_id": 40},
    {"name": "Anderson PowerPole connector", "aliases": ["powerpole", "anderson"], "drawer_id": 41},
    {"name": "120A main breaker", "aliases": ["breaker", "main breaker"], "drawer_id": 50},
    {"name": "WS2812B LED strip", "aliases": ["ws2812", "neopixel"], "drawer_id": 25},
    {"name": "MCP23017 expander", "aliases": ["mcp23017", "io expander"], "drawer_id": 35},
]


def expected_controllers() -> list[dict]:
    """The five controller records implied by the locked topology."""
    rows = []
    for n in range(1, CONTROLLER_COUNT + 1):
        start = (n - 1) * LEDS_PER_CONTROLLER + 1
        rows.append(
            {
                "controller_id": f"CTRL-{n:02d}",
                "drawer_start": start,
                "drawer_end": start + LEDS_PER_CONTROLLER - 1,
                "led_count": LEDS_PER_CONTROLLER,
            }
        )
    return rows


def seed(session: Session) -> dict[str, int]:
    """Insert any missing controller/drawer rows. Returns the resulting counts."""
    existing_controllers = {c.controller_id for c in session.scalars(select(Controller))}
    for row in expected_controllers():
        if row["controller_id"] not in existing_controllers:
            session.add(Controller(**row))
    session.flush()

    existing_drawers = set(session.scalars(select(Drawer.drawer_number)))
    for drawer_number in range(1, TOTAL_DRAWERS + 1):
        if drawer_number in existing_drawers:
            continue
        route = drawer_to_route(drawer_number)
        session.add(
            Drawer(
                drawer_number=drawer_number,
                controller_id=route.controller_id,
                local_led_index=route.led_index,
                label=f"Drawer {drawer_number:02d}",
            )
        )
    session.flush()

    # Items are keyed by name so re-seeding never duplicates them.
    existing_items = set(session.scalars(select(Item.name)))
    for row in SEED_ITEMS:
        if row["name"] not in existing_items:
            session.add(Item(**row))
    session.flush()

    return {
        "controllers": session.scalar(select(func.count()).select_from(Controller)) or 0,
        "drawers": session.scalar(select(func.count()).select_from(Drawer)) or 0,
        "items": session.scalar(select(func.count()).select_from(Item)) or 0,
    }


def create_all() -> None:
    Base.metadata.create_all(engine)


def main() -> None:
    create_all()
    with session_scope() as session:
        counts = seed(session)
    print(
        f"seeded: {counts['controllers']} controllers, "
        f"{counts['drawers']} drawers, {counts['items']} items"
    )
    expected = {
        "controllers": CONTROLLER_COUNT,
        "drawers": TOTAL_DRAWERS,
        "items": len(SEED_ITEMS),
    }
    if counts != expected:
        raise SystemExit(f"seed produced {counts}, expected {expected}")


if __name__ == "__main__":
    main()

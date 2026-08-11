"""S04 acceptance tests: item search and drawer resolution."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import get_db
from app.main import app
from app.models import Item
from app.seed import SEED_ITEMS, seed
from app.services.search import EmptyQueryError, SearchOutcome, search

# Both edges of every controller, taken from the seed data.
BOUNDARY_CASES = [
    ("M3 x 10 hex bolt", 1, "CTRL-01", 0),
    ("M3 hex nut", 10, "CTRL-01", 9),
    ("M5 x 20 socket screw", 11, "CTRL-02", 0),
    ("M5 nyloc nut", 20, "CTRL-02", 9),
    ("REV NEO brushless motor", 21, "CTRL-03", 0),
    ("SPARK MAX motor controller", 30, "CTRL-03", 9),
    ("1/2 inch hex shaft", 31, "CTRL-04", 0),
    ("Thunderhex bearing", 40, "CTRL-04", 9),
    ("Anderson PowerPole connector", 41, "CTRL-05", 0),
    ("120A main breaker", 50, "CTRL-05", 9),
]


@pytest.fixture()
def seeded(db_session):
    seed(db_session)
    # One deliberately unlocated item, so the "known item, no drawer" branch
    # has real data (finding F7 from S03).
    db_session.add(Item(name="Unsorted mystery bracket", aliases=["mystery"], drawer_id=None))
    db_session.flush()
    return db_session


@pytest.fixture()
def client(seeded):
    """TestClient bound to the rolled-back test session, not the dev database."""
    app.dependency_overrides[get_db] = lambda: seeded
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)


# --- Seed data --------------------------------------------------------------

def test_seed_provides_at_least_ten_items_across_controller_boundaries():
    assert len(SEED_ITEMS) >= 10
    drawers = {row["drawer_id"] for row in SEED_ITEMS}
    # Every controller edge is represented.
    assert {1, 10, 11, 20, 21, 30, 31, 40, 41, 50} <= drawers
    controllers = {(d - 1) // 10 + 1 for d in drawers}
    assert controllers == {1, 2, 3, 4, 5}


# --- Acceptance: known item resolves correctly ------------------------------

@pytest.mark.parametrize(("name", "drawer", "controller", "led"), BOUNDARY_CASES)
def test_boundary_items_resolve_to_the_expected_controller_and_led(
    seeded, name, drawer, controller, led
):
    result = search(seeded, name)
    assert result.outcome is SearchOutcome.FOUND
    assert result.item.drawer_id == drawer
    assert result.route.controller_id == controller
    assert result.route.led_index == led


def test_search_matches_an_alias(seeded):
    result = search(seeded, "neopixel")
    assert result.outcome is SearchOutcome.FOUND
    assert result.item.name == "WS2812B LED strip"
    assert result.item.drawer_id == 25
    assert (result.route.controller_id, result.route.led_index) == ("CTRL-03", 4)


def test_search_is_case_and_whitespace_insensitive(seeded):
    for variant in ("spark max", "SPARK MAX", "  Spark   Max  "):
        result = search(seeded, variant)
        assert result.outcome is SearchOutcome.FOUND
        assert result.item.drawer_id == 30


# --- Acceptance: unknown item returns a controlled response -----------------

def test_unknown_item_returns_not_found_with_no_candidates(seeded):
    result = search(seeded, "flux capacitor")
    assert result.outcome is SearchOutcome.NOT_FOUND
    assert result.item is None
    assert result.route is None
    assert result.candidates == []


def test_blank_query_raises_rather_than_guessing(seeded):
    for blank in ("", "   ", "\t\n"):
        with pytest.raises(EmptyQueryError):
            search(seeded, blank)


def test_known_item_without_a_drawer_is_unlocated_not_not_found(seeded):
    result = search(seeded, "mystery")
    assert result.outcome is SearchOutcome.UNLOCATED
    assert result.item.name == "Unsorted mystery bracket"
    assert result.route is None


# --- Ambiguity is deterministic ---------------------------------------------

def test_ambiguous_query_lists_candidates_in_a_stable_order(seeded):
    first = search(seeded, "m3")
    assert first.outcome is SearchOutcome.AMBIGUOUS
    assert first.route is None
    names = [c.name for c in first.candidates]
    assert names == ["M3 hex nut", "M3 x 10 hex bolt"]
    assert [c.drawer_number for c in first.candidates] == [10, 1]
    # Same query, same order, every time.
    for _ in range(3):
        assert [c.name for c in search(seeded, "m3").candidates] == names


def test_exact_name_wins_over_the_substring_sibling(seeded):
    result = search(seeded, "m3 hex nut")
    assert result.outcome is SearchOutcome.FOUND
    assert result.item.name == "M3 hex nut"


def test_limit_caps_the_candidate_list(seeded):
    result = search(seeded, "m", limit=2)
    assert result.outcome is SearchOutcome.AMBIGUOUS
    assert len(result.candidates) == 2


# --- HTTP surface -----------------------------------------------------------

def test_api_search_found(client):
    r = client.get("/api/search", params={"q": "spark max"})
    assert r.status_code == 200
    body = r.json()
    assert body["outcome"] == "found"
    assert body["route"] == {"drawer_number": 30, "controller_id": "CTRL-03", "led_index": 9}


def test_api_search_not_found_is_200_with_an_outcome(client):
    r = client.get("/api/search", params={"q": "flux capacitor"})
    assert r.status_code == 200
    assert r.json()["outcome"] == "not_found"
    assert r.json()["route"] is None


def test_api_search_ambiguous(client):
    r = client.get("/api/search", params={"q": "m3"})
    assert r.status_code == 200
    body = r.json()
    assert body["outcome"] == "ambiguous"
    assert [c["name"] for c in body["candidates"]] == ["M3 hex nut", "M3 x 10 hex bolt"]


def test_api_blank_query_is_400(client):
    r = client.get("/api/search", params={"q": "   "})
    assert r.status_code == 400
    assert "empty" in r.json()["detail"]


def test_api_missing_query_parameter_is_422(client):
    assert client.get("/api/search").status_code == 422


@pytest.mark.parametrize(
    ("drawer", "controller", "led"),
    [(1, "CTRL-01", 0), (10, "CTRL-01", 9), (11, "CTRL-02", 0), (50, "CTRL-05", 9)],
)
def test_api_drawer_route(client, drawer, controller, led):
    r = client.get(f"/api/drawers/{drawer}/route")
    assert r.status_code == 200
    assert r.json() == {
        "drawer_number": drawer,
        "controller_id": controller,
        "led_index": led,
    }


@pytest.mark.parametrize("bad", [0, 51, 999])
def test_api_drawer_route_rejects_out_of_range(client, bad):
    assert client.get(f"/api/drawers/{bad}/route").status_code == 422


def test_api_item_route_and_its_error_cases(client, seeded):
    located = seeded.scalars(select(Item).where(Item.drawer_id == 21)).one()
    r = client.get(f"/api/items/{located.id}/route")
    assert r.status_code == 200
    assert r.json()["controller_id"] == "CTRL-03"

    unlocated = seeded.scalars(select(Item).where(Item.drawer_id.is_(None))).one()
    assert client.get(f"/api/items/{unlocated.id}/route").status_code == 409
    assert client.get("/api/items/999999/route").status_code == 404

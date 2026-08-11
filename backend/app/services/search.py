"""Search Service - item lookup and drawer resolution.

Service boundary per `02_Core_Documents/04_Software_Architecture_and_Data_Model_CN`:
this module answers "which drawer holds this item", nothing more. It never
publishes MQTT and never creates commands.

Every outcome is explicit. A search either resolves to exactly one located
item, or it says precisely why it did not - it never guesses (acceptance
target F2).
"""
from __future__ import annotations

import enum
import unicodedata
from dataclasses import dataclass, field

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from app.models import Drawer, Item
from app.services.routing import Route, drawer_to_route

MAX_CANDIDATES = 10


class SearchOutcome(str, enum.Enum):
    FOUND = "found"            # exactly one match, and it has a drawer
    AMBIGUOUS = "ambiguous"    # several matches; caller must disambiguate
    NOT_FOUND = "not_found"    # nothing matched
    UNLOCATED = "unlocated"    # exactly one match, but it has no drawer yet


@dataclass(frozen=True)
class Candidate:
    item_id: int
    name: str
    drawer_number: int | None


@dataclass(frozen=True)
class SearchResult:
    query: str
    outcome: SearchOutcome
    item: Item | None = None
    route: Route | None = None
    candidates: list[Candidate] = field(default_factory=list)


class EmptyQueryError(ValueError):
    """Raised for a blank query. Callers turn this into a 400, never a guess."""


def normalize(text: str) -> str:
    """Casefold + collapse whitespace + NFKC, so `  M3   NUT ` == `m3 nut`."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _rank(item: Item, needle: str) -> tuple[int, str, int]:
    """Deterministic ordering key: exact match, then prefix, then substring;
    ties broken by name then id so the same query always returns the same
    order regardless of database row order."""
    name = normalize(item.name)
    aliases = [normalize(a) for a in (item.aliases or [])]

    if name == needle or needle in aliases:
        tier = 0
    elif name.startswith(needle) or any(a.startswith(needle) for a in aliases):
        tier = 1
    else:
        tier = 2
    return (tier, name, item.id)


def find_items(session: Session, query: str, *, limit: int = MAX_CANDIDATES) -> list[Item]:
    """Items whose name or any alias contains `query`, in deterministic order."""
    needle = normalize(query)
    if not needle:
        raise EmptyQueryError("query must not be empty")

    pattern = f"%{needle}%"
    stmt = select(Item).where(
        or_(
            func.lower(Item.name).like(pattern),
            func.lower(cast(Item.aliases, String)).like(pattern),
        )
    )
    rows = list(session.scalars(stmt))

    # The SQL filter is a cheap pre-selection over the JSONB text; re-check in
    # Python so an alias substring cannot match on JSON punctuation.
    matched = [
        item
        for item in rows
        if needle in normalize(item.name)
        or any(needle in normalize(a) for a in (item.aliases or []))
    ]
    matched.sort(key=lambda i: _rank(i, needle))
    return matched[:limit]


def resolve_drawer(drawer_number: int) -> Route:
    """Drawer number -> (controller_id, led_index). Raises ValueError if out of
    range; the routing formula is the single source of truth (D007)."""
    return drawer_to_route(drawer_number)


def search(session: Session, query: str, *, limit: int = MAX_CANDIDATES) -> SearchResult:
    """The one entry point the API uses. Raises `EmptyQueryError` on a blank
    query; every other situation is reported as a typed outcome."""
    needle = normalize(query)
    matches = find_items(session, query, limit=limit)

    if not matches:
        return SearchResult(query=needle, outcome=SearchOutcome.NOT_FOUND)

    if len(matches) > 1:
        return SearchResult(
            query=needle,
            outcome=SearchOutcome.AMBIGUOUS,
            candidates=[Candidate(i.id, i.name, i.drawer_id) for i in matches],
        )

    item = matches[0]
    if item.drawer_id is None:
        return SearchResult(
            query=needle,
            outcome=SearchOutcome.UNLOCATED,
            item=item,
            candidates=[Candidate(item.id, item.name, None)],
        )

    return SearchResult(
        query=needle,
        outcome=SearchOutcome.FOUND,
        item=item,
        route=resolve_drawer(item.drawer_id),
        candidates=[Candidate(item.id, item.name, item.drawer_id)],
    )


def drawer_row(session: Session, drawer_number: int) -> Drawer | None:
    """The persisted drawer record, for callers that want the stored mapping
    rather than the computed one. S03 proved the two always agree."""
    return session.get(Drawer, drawer_number)

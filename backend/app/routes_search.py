"""Search and drawer-resolution endpoints.

Read-only. No MQTT publishing happens here - that is S05.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.schemas import ItemOut, RouteOut
from app.services import search as search_service
from app.services.search import EmptyQueryError, SearchOutcome

router = APIRouter(prefix="/api", tags=["search"])

TOTAL_DRAWERS = get_settings().total_drawers


class CandidateOut(BaseModel):
    item_id: int
    name: str
    drawer_number: int | None = None


class SearchResponse(BaseModel):
    """One shape for every outcome, so the frontend branches on `outcome`
    instead of on HTTP status codes."""

    query: str
    outcome: SearchOutcome
    item: ItemOut | None = None
    route: RouteOut | None = None
    candidates: list[CandidateOut] = Field(default_factory=list)


@router.get("/search", response_model=SearchResponse)
def search_items(
    q: str = Query(..., description="Item name or alias fragment"),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
) -> SearchResponse:
    try:
        result = search_service.search(db, q, limit=limit)
    except EmptyQueryError as exc:
        # A blank query is a client error, never an empty-result guess.
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    route = None
    if result.route is not None and result.item is not None:
        route = RouteOut(
            drawer_number=result.item.drawer_id,
            controller_id=result.route.controller_id,
            led_index=result.route.led_index,
        )

    return SearchResponse(
        query=result.query,
        outcome=result.outcome,
        item=ItemOut.model_validate(result.item) if result.item else None,
        route=route,
        candidates=[
            CandidateOut(item_id=c.item_id, name=c.name, drawer_number=c.drawer_number)
            for c in result.candidates
        ],
    )


@router.get("/drawers/{drawer_number}/route", response_model=RouteOut)
def resolve_drawer(
    drawer_number: int = Path(..., ge=1, le=TOTAL_DRAWERS),
    db: Session = Depends(get_db),
) -> RouteOut:
    """Where drawer N physically is. Uses the stored row when present and falls
    back to the routing formula, which S03 proved identical."""
    stored = search_service.drawer_row(db, drawer_number)
    if stored is not None:
        return RouteOut(
            drawer_number=stored.drawer_number,
            controller_id=stored.controller_id,
            led_index=stored.local_led_index,
        )
    route = search_service.resolve_drawer(drawer_number)
    return RouteOut(
        drawer_number=drawer_number,
        controller_id=route.controller_id,
        led_index=route.led_index,
    )


@router.get("/items/{item_id}/route", response_model=RouteOut)
def resolve_item(item_id: int, db: Session = Depends(get_db)) -> RouteOut:
    from app.models import Item  # local import keeps the module import graph flat

    item = db.get(Item, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"item {item_id} not found")
    if item.drawer_id is None:
        raise HTTPException(status_code=409, detail=f"item {item_id} has no drawer assigned")
    route = search_service.resolve_drawer(item.drawer_id)
    return RouteOut(
        drawer_number=item.drawer_id,
        controller_id=route.controller_id,
        led_index=route.led_index,
    )

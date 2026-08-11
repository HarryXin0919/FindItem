"""Controller, drawer-map and locate endpoints for the dashboard.

The publisher is a dependency so S08 can swap the in-memory `FakePublisher`
for the five-node simulator without touching these handlers. Until a device
actually acknowledges, a command stays `published` - the UI must show that
distinction rather than implying the LED is lit (MQTT contract section 3).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import Controller, Drawer, Item, LocateCommand
from app.schemas import ControllerOut, LocateCommandOut
from app.services.command_service import CommandService
from app.services.device_bus import SimulatedDeviceBus
from app.services.mqtt_client import FakePublisher, Pattern, Publisher

router = APIRouter(prefix="/api", tags=["controllers"])

settings = get_settings()

# Software-first default (`device_mode = "simulator"`): the five simulated
# controllers run in this process, so the full search -> command -> ACK loop
# closes with no hardware and no broker. `device_mode = "broker"` is for S12+.
_device_bus: SimulatedDeviceBus | None = (
    SimulatedDeviceBus() if settings.device_mode == "simulator" else None
)
_publisher: Publisher = _device_bus if _device_bus is not None else FakePublisher()


def get_publisher() -> Publisher:
    return _publisher


def get_device_bus() -> SimulatedDeviceBus | None:
    return _device_bus


class DrawerCell(BaseModel):
    drawer_number: int
    controller_id: str
    local_led_index: int
    label: str | None = None
    item_count: int = 0


class DrawerMapOut(BaseModel):
    total_drawers: int
    controllers: int
    leds_per_controller: int
    drawers: list[DrawerCell]


class LocateRequest(BaseModel):
    drawer_number: int | None = Field(default=None, ge=1, le=50)
    item_id: int | None = None
    command_id: str | None = Field(default=None, max_length=64)
    pattern: Pattern = Pattern.SOLID
    duration_ms: int = Field(default=30_000, gt=0, le=600_000)


class LocateResponse(BaseModel):
    command: LocateCommandOut
    published: bool
    deduplicated: bool
    acks_applied: int = 0
    active_leds: list[dict] = Field(default_factory=list)
    note: str


class LedStateOut(BaseModel):
    device_mode: str
    active_leds: list[dict] = Field(default_factory=list)


@router.get("/controllers", response_model=list[ControllerOut])
def list_controllers(db: Session = Depends(get_db)) -> list[Controller]:
    return list(db.scalars(select(Controller).order_by(Controller.controller_id)))


@router.get("/drawers", response_model=DrawerMapOut)
def drawer_map(db: Session = Depends(get_db)) -> DrawerMapOut:
    """All 50 cells in one request - the dashboard must not issue 50."""
    counts = dict(
        db.execute(
            select(Item.drawer_id, func.count()).where(Item.drawer_id.is_not(None)).group_by(Item.drawer_id)
        ).all()
    )
    rows = db.scalars(select(Drawer).order_by(Drawer.drawer_number))
    return DrawerMapOut(
        total_drawers=settings.total_drawers,
        controllers=settings.controller_count,
        leds_per_controller=settings.leds_per_controller,
        drawers=[
            DrawerCell(
                drawer_number=d.drawer_number,
                controller_id=d.controller_id,
                local_led_index=d.local_led_index,
                label=d.label,
                item_count=counts.get(d.drawer_number, 0),
            )
            for d in rows
        ],
    )


@router.post("/locate", response_model=LocateResponse)
def locate(
    body: LocateRequest,
    db: Session = Depends(get_db),
    publisher: Publisher = Depends(get_publisher),
    bus: SimulatedDeviceBus | None = Depends(get_device_bus),
) -> LocateResponse:
    if (body.drawer_number is None) == (body.item_id is None):
        raise HTTPException(
            status_code=400, detail="provide exactly one of drawer_number or item_id"
        )

    service = CommandService(db, publisher)
    # Give up on anything that was published and never acknowledged before
    # issuing something new, so the operator sees `expired` rather than a
    # command stuck at `published` forever.
    service.expire_stale_commands()
    try:
        if body.item_id is not None:
            result = service.locate_item(
                body.item_id,
                command_id=body.command_id,
                pattern=body.pattern,
                duration_ms=body.duration_ms,
            )
        else:
            result = service.locate_drawer(
                body.drawer_number,
                command_id=body.command_id,
                pattern=body.pattern,
                duration_ms=body.duration_ms,
            )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    # Apply whatever the devices sent back. With the simulator this closes the
    # loop synchronously; against a real broker the ACK arrives on its own
    # subscription and this drain is simply empty.
    acks_applied = 0
    if bus is not None:
        for raw in bus.drain_acks():
            try:
                service.handle_ack(raw)
                acks_applied += 1
            except (LookupError, ValueError):
                # Already recorded as a DeviceEvent by handle_ack.
                pass

    db.commit()
    db.refresh(result.command)

    if result.deduplicated:
        note = "command_id already issued; nothing was re-published"
    elif not result.published:
        note = f"publish failed: {result.command.error or 'unknown error'}"
    elif result.command.acked_at is not None:
        note = "acknowledged by the controller"
    else:
        note = "published to the controller; waiting for the device to acknowledge"

    return LocateResponse(
        command=LocateCommandOut.model_validate(result.command),
        published=result.published,
        deduplicated=result.deduplicated,
        acks_applied=acks_applied,
        active_leds=[
            {"controller_id": cid, "led_index": idx}
            for cid, idx in (bus.active_leds() if bus is not None else [])
        ],
        note=note,
    )


@router.get("/leds", response_model=LedStateOut)
def led_state(bus: SimulatedDeviceBus | None = Depends(get_device_bus)) -> LedStateOut:
    """Which pixels are lit right now. In simulator mode this is the device
    truth; with a real broker it would come from controller status messages."""
    return LedStateOut(
        device_mode=settings.device_mode,
        active_leds=[
            {"controller_id": cid, "led_index": idx}
            for cid, idx in (bus.active_leds() if bus is not None else [])
        ],
    )


@router.get("/commands/{command_id}", response_model=LocateCommandOut)
def get_command(command_id: str, db: Session = Depends(get_db)) -> LocateCommand:
    command = db.get(LocateCommand, command_id)
    if command is None:
        raise HTTPException(status_code=404, detail=f"command {command_id} not found")
    return command

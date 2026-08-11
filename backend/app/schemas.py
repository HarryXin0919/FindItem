"""Pydantic API schemas. These are the wire contract; `models.py` is storage."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import CommandStatus, ControllerStatus, DeviceEventType

ORM = ConfigDict(from_attributes=True)


class ControllerOut(BaseModel):
    model_config = ORM

    controller_id: str
    drawer_start: int
    drawer_end: int
    led_count: int
    status: ControllerStatus
    last_seen: datetime | None = None
    fw_version: str | None = None


class DrawerOut(BaseModel):
    model_config = ORM

    drawer_number: int = Field(ge=1, le=50)
    controller_id: str
    local_led_index: int = Field(ge=0, le=9)
    label: str | None = None


class ItemOut(BaseModel):
    model_config = ORM

    id: int
    name: str
    aliases: list[str] = Field(default_factory=list)
    drawer_id: int | None = None


class RouteOut(BaseModel):
    """Where a drawer physically is: which controller, which local pixel."""

    drawer_number: int = Field(ge=1, le=50)
    controller_id: str
    led_index: int = Field(ge=0, le=9)


class LocateCommandOut(BaseModel):
    model_config = ORM

    command_id: str
    item_id: int | None = None
    drawer_number: int
    controller_id: str
    led_index: int
    status: CommandStatus
    created_at: datetime
    published_at: datetime | None = None
    acked_at: datetime | None = None
    error: str | None = None


class DeviceEventOut(BaseModel):
    model_config = ORM

    id: int
    controller_id: str
    type: DeviceEventType
    payload: dict
    created_at: datetime

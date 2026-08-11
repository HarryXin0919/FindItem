"""FindIt domain tables.

Entity set per `02_Core_Documents/04_Software_Architecture_and_Data_Model_CN`:
Item, Drawer, Controller, LocateCommand, DeviceEvent.

The physical topology is enforced at the database level, not merely in Python:
a drawer number outside 1-50, a local LED index outside 0-9, a duplicate drawer
number, a duplicate controller id, or two drawers claiming the same
(controller, LED) slot are all rejected by constraints.
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

TOTAL_DRAWERS = 50
LEDS_PER_CONTROLLER = 10


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _pg_enum(enum_cls, name: str) -> Enum:
    """Store the enum's *value* in PostgreSQL, not its Python member name, so
    the column reads as `online` / `pending` rather than `ONLINE` / `PENDING`."""
    return Enum(
        enum_cls,
        name=name,
        values_callable=lambda members: [m.value for m in members],
    )


class ControllerStatus(str, enum.Enum):
    UNKNOWN = "unknown"
    ONLINE = "online"
    OFFLINE = "offline"
    DEGRADED = "degraded"


class CommandStatus(str, enum.Enum):
    """Lifecycle of one locate request."""

    PENDING = "pending"      # created, not yet published
    PUBLISHED = "published"  # handed to the MQTT abstraction
    ACKED = "acked"          # controller confirmed illumination
    FAILED = "failed"        # publish or ACK failed
    EXPIRED = "expired"      # superseded or timed out


class DeviceEventType(str, enum.Enum):
    HEARTBEAT = "heartbeat"
    INPUT = "input"
    ACK = "ack"
    ERROR = "error"


class Controller(Base):
    """One of the five ESP32-C3 + MCP23017 + 10 x WS2812 nodes."""

    __tablename__ = "controllers"
    __table_args__ = (
        CheckConstraint(
            "drawer_end - drawer_start + 1 = led_count",
            name="ck_controller_range_matches_led_count",
        ),
        CheckConstraint("led_count = 10", name="ck_controller_led_count_is_ten"),
        CheckConstraint(
            "drawer_start >= 1 AND drawer_end <= 50 AND drawer_start <= drawer_end",
            name="ck_controller_range_within_1_50",
        ),
    )

    controller_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    drawer_start: Mapped[int] = mapped_column(Integer, nullable=False)
    drawer_end: Mapped[int] = mapped_column(Integer, nullable=False)
    led_count: Mapped[int] = mapped_column(Integer, nullable=False, default=LEDS_PER_CONTROLLER)
    status: Mapped[ControllerStatus] = mapped_column(
        _pg_enum(ControllerStatus, "controller_status"),
        nullable=False,
        default=ControllerStatus.UNKNOWN,
    )
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fw_version: Mapped[str | None] = mapped_column(String(32), nullable=True)

    drawers: Mapped[list["Drawer"]] = relationship(back_populates="controller")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Controller {self.controller_id} {self.drawer_start}-{self.drawer_end}>"


class Drawer(Base):
    """Static mapping from a global drawer number to (controller, local LED)."""

    __tablename__ = "drawers"
    __table_args__ = (
        UniqueConstraint("controller_id", "local_led_index", name="uq_drawer_controller_led"),
        CheckConstraint("drawer_number BETWEEN 1 AND 50", name="ck_drawer_number_range"),
        CheckConstraint("local_led_index BETWEEN 0 AND 9", name="ck_drawer_led_index_range"),
    )

    # The global drawer number *is* the identity - this is what makes a
    # duplicate drawer number impossible rather than merely unlikely.
    drawer_number: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    controller_id: Mapped[str] = mapped_column(
        ForeignKey("controllers.controller_id", ondelete="RESTRICT"), nullable=False
    )
    local_led_index: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str | None] = mapped_column(String(64), nullable=True)

    controller: Mapped[Controller] = relationship(back_populates="drawers")
    items: Mapped[list["Item"]] = relationship(back_populates="drawer")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Drawer {self.drawer_number} -> {self.controller_id}[{self.local_led_index}]>"


class Item(Base):
    """A searchable part living in exactly one drawer (assumption A5)."""

    __tablename__ = "items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    aliases: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    drawer_id: Mapped[int | None] = mapped_column(
        ForeignKey("drawers.drawer_number", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    drawer: Mapped[Drawer | None] = relationship(back_populates="items")


Index("ix_items_name_lower", func.lower(Item.name))


class LocateCommand(Base):
    """One locate lifecycle. `command_id` is supplied by the caller and is the
    idempotency key: re-issuing the same id must not create a second command."""

    __tablename__ = "locate_commands"
    __table_args__ = (
        CheckConstraint("drawer_number BETWEEN 1 AND 50", name="ck_command_drawer_range"),
        CheckConstraint("led_index BETWEEN 0 AND 9", name="ck_command_led_range"),
    )

    command_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    item_id: Mapped[int | None] = mapped_column(
        ForeignKey("items.id", ondelete="SET NULL"), nullable=True
    )
    drawer_number: Mapped[int] = mapped_column(Integer, nullable=False)
    controller_id: Mapped[str] = mapped_column(String(16), nullable=False)
    led_index: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[CommandStatus] = mapped_column(
        _pg_enum(CommandStatus, "command_status"), nullable=False, default=CommandStatus.PENDING
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(String(255), nullable=True)


Index("ix_locate_commands_created_at", LocateCommand.created_at.desc())


class DeviceEvent(Base):
    """Anything a controller reports: heartbeat, input, ACK or error."""

    __tablename__ = "device_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    controller_id: Mapped[str] = mapped_column(String(16), nullable=False)
    type: Mapped[DeviceEventType] = mapped_column(
        _pg_enum(DeviceEventType, "device_event_type"), nullable=False
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


Index("ix_device_events_controller_created", DeviceEvent.controller_id, DeviceEvent.created_at.desc())

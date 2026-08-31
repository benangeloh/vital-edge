"""Model armada: kapal, perangkat, sensor, pelayaran, konfigurasi."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811
from sqlalchemy.orm import Mapped, mapped_column

from fleetview_central.platform.db import Base

__all__ = ["Device", "Sensor", "Ship", "ShipConfig", "Voyage"]


class Ship(Base):
    __tablename__ = "ships"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(128), index=True)
    imo_number: Mapped[str | None] = mapped_column(String(16), unique=True)
    call_sign: Mapped[str | None] = mapped_column(String(16))
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    """Dipakai untuk nama folder export USB, mis. `SHIP-023`."""
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Device(Base):
    """Edge Agent yang terpasang di sebuah kapal."""

    __tablename__ = "devices"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    ship_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("ships.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(128))
    hardware: Mapped[str | None] = mapped_column(String(128))
    """Mis. `Raspberry Pi 4 8GB`."""
    field_device: Mapped[str | None] = mapped_column(String(128))
    """Perangkat lapangan yang tersambung, mis. `Autonics LP-A104`."""
    agent_version: Mapped[str | None] = mapped_column(String(32))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Sensor(Base):
    """Registry sensor.

    Sensor yang belum dikenal **didaftarkan otomatis** saat pertama terlihat,
    dengan `status='unregistered'`. Datanya tetap disimpan. Inilah yang membuat
    janji "sensor bisa bertambah di masa depan" jadi nyata tanpa deploy.
    """

    __tablename__ = "sensors"
    __table_args__ = (UniqueConstraint("ship_id", "sensor_id", name="uq_sensor_per_ship"),)

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    ship_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("ships.id", ondelete="CASCADE"), index=True
    )
    sensor_id: Mapped[str] = mapped_column(String(64), index=True)
    metric: Mapped[str] = mapped_column(String(64))
    unit: Mapped[str | None] = mapped_column(String(32))
    display_name: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="unregistered")
    tags: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    min_value: Mapped[float | None] = mapped_column(Float)
    max_value: Mapped[float | None] = mapped_column(Float)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Voyage(Base):
    __tablename__ = "voyages"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    ship_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("ships.id", ondelete="CASCADE"), index=True
    )
    voyage_number: Mapped[str] = mapped_column(String(64))
    departure_port: Mapped[str | None] = mapped_column(String(128))
    arrival_port: Mapped[str | None] = mapped_column(String(128))
    departed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    arrived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ShipConfig(Base):
    """Konfigurasi kapal, berversi.

    Konfigurasi **tidak pernah diedit di tempat** — setiap perubahan membuat
    versi baru. Versi yang sedang berjalan dilaporkan edge di setiap heartbeat
    dan ikut di setiap batch, sehingga saat sebuah kapal berperilaku aneh kita
    tahu persis konfigurasi mana yang menghasilkannya.
    """

    __tablename__ = "ship_configs"
    __table_args__ = (UniqueConstraint("ship_id", "version", name="uq_config_version"),)

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    ship_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("ships.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[UUID | None] = mapped_column(PgUUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revision: Mapped[int] = mapped_column(Integer, default=1)

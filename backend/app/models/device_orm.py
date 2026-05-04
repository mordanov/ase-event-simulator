from __future__ import annotations

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    device_type: Mapped[str] = mapped_column(String(50), nullable=False)
    user_id: Mapped[str] = mapped_column(String(50), nullable=False)
    firmware_version: Mapped[str] = mapped_column(String(20), nullable=False)
    gps_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    gps_lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    height_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(10), nullable=True)
    birth_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[str] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    # X.509 device certificate (generated on first registration)
    cert_pem: Mapped[str | None] = mapped_column(Text, nullable=True)
    cert_key_pem: Mapped[str | None] = mapped_column(Text, nullable=True)
    cert_serial: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cert_fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    registration_status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'unregistered'")
    )
    registered_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_devices_type_active", "device_type", "is_active"),
        Index("idx_devices_reg_status", "registration_status"),
    )

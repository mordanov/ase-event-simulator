"""
JITR (Just-In-Time Registration) simulation for device authentication.

Flow per device:
  1. Generate X.509 cert signed by CA  → store in DB  (status: pending)
  2. Simulate first connection attempt  → REJECTED     (AWS IoT JITR behavior)
  3. Wait CERT_APPROVAL_DELAY_SECONDS   → Lambda/rule activates the cert
  4. Second connection attempt          → ACCEPTED     (status: registered)

With a real AWS IoT Core setup:
  - Register the CA cert with AWS IoT Core
  - Enable JITR / set up a registration rule + Lambda
  - Set CERT_APPROVAL_DELAY_SECONDS to match Lambda processing time (~2-10s)

Devices that are already registered skip all steps and proceed immediately.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import select, update

from app.db import async_session_factory
from app.models.device_orm import Device
from app.services.cert_service import generate_device_cert

logger = logging.getLogger(__name__)

CERT_APPROVAL_DELAY: float = float(os.getenv("CERT_APPROVAL_DELAY_SECONDS", "5"))

# Registration status constants
STATUS_UNREGISTERED = "unregistered"
STATUS_PENDING      = "pending"
STATUS_REGISTERED   = "registered"
STATUS_REJECTED     = "rejected"   # permanent rejection (not used in simulation, for future use)


# ─── Event callback type ──────────────────────────────────────────────────────

RegistrationCallback = Callable[[str, str, str], None]
"""callback(device_id, status, message) — called at each registration step."""


# ─── Core registration logic ──────────────────────────────────────────────────

async def register_devices(
    device_ids: list[str],
    on_event: RegistrationCallback | None = None,
) -> dict[str, str]:
    """
    Register all *device_ids* that are not yet registered.

    Returns a mapping device_id → cert_fingerprint for all devices
    (including already-registered ones).
    """
    fingerprints: dict[str, str] = {}

    async with async_session_factory() as db:
        rows = (
            await db.execute(
                select(Device).where(Device.device_id.in_(device_ids))
            )
        ).scalars().all()

    by_id = {d.device_id: d for d in rows}

    # Partition: already registered vs needs registration
    already_registered = [d for d in rows if d.registration_status == STATUS_REGISTERED]
    needs_registration  = [d for d in rows if d.registration_status != STATUS_REGISTERED]

    for d in already_registered:
        fingerprints[d.device_id] = d.cert_fingerprint or ""
        _emit(on_event, d.device_id, STATUS_REGISTERED, "cert already active — skipping registration")

    if not needs_registration:
        return fingerprints

    # ── Phase 1: generate certs, persist, mark pending ────────────────────────
    cert_map: dict[str, object] = {}  # device_id → DeviceCert
    for d in needs_registration:
        dev_cert = generate_device_cert(d.device_id)
        cert_map[d.device_id] = dev_cert
        _emit(on_event, d.device_id, STATUS_PENDING,
              f"cert generated (serial={dev_cert.serial[:16]}…), submitting to registration authority")

    async with async_session_factory() as db:
        for d in needs_registration:
            dc = cert_map[d.device_id]
            await db.execute(
                update(Device)
                .where(Device.device_id == d.device_id)
                .values(
                    cert_pem=dc.cert_pem,
                    cert_key_pem=dc.key_pem,
                    cert_serial=dc.serial,
                    cert_fingerprint=dc.fingerprint,
                    registration_status=STATUS_PENDING,
                )
            )
        await db.commit()

    # ── Phase 2: simulate first connection → JITR rejection ───────────────────
    for d in needs_registration:
        _emit(on_event, d.device_id, "rejected",
              f"first connection rejected (JITR) — waiting {CERT_APPROVAL_DELAY}s for approval")

    logger.info(
        "JITR: %d device(s) in pending state — waiting %.1fs for cert activation",
        len(needs_registration), CERT_APPROVAL_DELAY,
    )
    await asyncio.sleep(CERT_APPROVAL_DELAY)

    # ── Phase 3: second connection → accepted, mark registered ───────────────
    now = datetime.now(timezone.utc)
    async with async_session_factory() as db:
        for d in needs_registration:
            await db.execute(
                update(Device)
                .where(Device.device_id == d.device_id)
                .values(registration_status=STATUS_REGISTERED, registered_at=now)
            )
        await db.commit()

    for d in needs_registration:
        dc = cert_map[d.device_id]
        fingerprints[d.device_id] = dc.fingerprint
        _emit(on_event, d.device_id, STATUS_REGISTERED,
              "cert activated — device registered, telemetry will start")

    return fingerprints


def _emit(cb: RegistrationCallback | None, device_id: str, status: str, msg: str) -> None:
    logger.info("[reg] %s → %s: %s", device_id, status, msg)
    if cb:
        cb(device_id, status, msg)

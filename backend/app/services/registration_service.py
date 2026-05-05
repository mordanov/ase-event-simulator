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
import json
import logging
import os
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select, update

from app.db import async_session_factory
from app.models.device_orm import Device
from app.services.cert_service import DeviceCert, generate_device_cert
from app.services.runtime_mode import is_cloud_mode

logger = logging.getLogger(__name__)

CERT_APPROVAL_DELAY: float = float(os.getenv("CERT_APPROVAL_DELAY_SECONDS", "5"))
IOT_POLICY_NAME = os.getenv("AWS_IOT_POLICY_NAME", "HealthSimulatorDevicePolicy")
IOT_THING_TYPE = os.getenv("AWS_IOT_THING_TYPE", "HealthSimulatorDevice")
IOT_TOPIC_PREFIX = os.getenv(
    "AWS_IOT_TOPIC_PREFIX", os.getenv("DEFAULT_MQTT_TOPIC", "health/telemetry")
)
IOT_REGISTRATION_MODE = os.getenv("AWS_IOT_REGISTRATION_MODE", "direct").strip().lower()

# Registration status constants
STATUS_UNREGISTERED = "unregistered"
STATUS_PENDING = "pending"
STATUS_REGISTERED = "registered"
STATUS_REJECTED = "rejected"  # permanent rejection (not used in simulation, for future use)

REG_MODE_DIRECT = "direct"
REG_MODE_JITR = "jitr"


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
            (await db.execute(select(Device).where(Device.device_id.in_(device_ids))))
            .scalars()
            .all()
        )

    # Partition: already registered vs needs registration
    already_registered = [d for d in rows if d.registration_status == STATUS_REGISTERED]
    needs_registration = [d for d in rows if d.registration_status != STATUS_REGISTERED]

    for d in already_registered:
        fingerprints[d.device_id] = d.cert_fingerprint or ""
        _emit(
            on_event, d.device_id, STATUS_REGISTERED, "cert already active — skipping registration"
        )

    if not needs_registration:
        return fingerprints

    # ── Phase 1: generate certs, persist, mark pending ────────────────────────
    cert_map: dict[str, DeviceCert] = {}
    for d in needs_registration:
        dev_cert = generate_device_cert(d.device_id)
        cert_map[d.device_id] = dev_cert
        _emit(
            on_event,
            d.device_id,
            STATUS_PENDING,
            f"cert generated (serial={dev_cert.serial[:16]}…), submitting to registration authority",
        )

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

    # In cloud mode, registration behavior is controlled explicitly via
    # AWS_IOT_REGISTRATION_MODE=direct|jitr.
    if is_cloud_mode():
        reg_mode = _get_iot_registration_mode()

        if reg_mode == REG_MODE_DIRECT:
            await _register_devices_in_aws(needs_registration, cert_map, on_event)
        else:
            # Strict JITR mode: do not call RegisterCertificateWithoutCA.
            # Certs are generated/stored here; first MQTT mTLS connect triggers
            # IoT JITR rule + Lambda activation/thing creation.
            for d in needs_registration:
                _emit(
                    on_event,
                    d.device_id,
                    STATUS_PENDING,
                    "awaiting AWS IoT JITR activation on first MQTT mTLS connect",
                )

        for d in needs_registration:
            dc = cert_map[d.device_id]
            fingerprints[d.device_id] = dc.fingerprint
        return fingerprints

    # ── Phase 2: simulate first connection → JITR rejection ───────────────────
    for d in needs_registration:
        _emit(
            on_event,
            d.device_id,
            "rejected",
            f"first connection rejected (JITR) — waiting {CERT_APPROVAL_DELAY}s for approval",
        )

    logger.info(
        "JITR: %d device(s) in pending state — waiting %.1fs for cert activation",
        len(needs_registration),
        CERT_APPROVAL_DELAY,
    )
    await asyncio.sleep(CERT_APPROVAL_DELAY)

    # ── Phase 3: second connection → accepted, mark registered ───────────────
    now = datetime.now(UTC)
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
        _emit(
            on_event,
            d.device_id,
            STATUS_REGISTERED,
            "cert activated — device registered, telemetry will start",
        )

    return fingerprints


def _emit(cb: RegistrationCallback | None, device_id: str, status: str, msg: str) -> None:
    logger.info("[reg] %s → %s: %s", device_id, status, msg)
    if cb:
        cb(device_id, status, msg)


def _get_iot_registration_mode() -> str:
    mode = IOT_REGISTRATION_MODE
    if mode not in {REG_MODE_DIRECT, REG_MODE_JITR}:
        raise RuntimeError(
            f"Invalid AWS_IOT_REGISTRATION_MODE. Expected 'direct' or 'jitr', got: {mode!r}"
        )
    return mode


def _default_iot_policy_document() -> str:
    import boto3

    session = boto3.session.Session()
    region = session.region_name or os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
    if not region:
        raise RuntimeError("AWS region is not configured for IoT policy provisioning")

    account = boto3.client("sts").get_caller_identity()["Account"]
    base = f"arn:aws:iot:{region}:{account}"
    return json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": "iot:Connect",
                    "Resource": f"{base}:client/${{iot:Connection.Thing.ThingName}}",
                },
                {
                    "Effect": "Allow",
                    "Action": ["iot:Publish", "iot:Receive"],
                    "Resource": f"{base}:topic/{IOT_TOPIC_PREFIX}/${{iot:Connection.Thing.ThingName}}",
                },
                {
                    "Effect": "Allow",
                    "Action": "iot:Subscribe",
                    "Resource": f"{base}:topicfilter/{IOT_TOPIC_PREFIX}/${{iot:Connection.Thing.ThingName}}",
                },
            ],
        }
    )


def _provision_device_in_aws(device_id: str, cert_pem: str) -> None:
    import boto3

    iot = boto3.client("iot")

    # Register CA-signed client cert explicitly so thing creation does not depend on
    # delayed first-connect JITR event timing.
    reg = iot.register_certificate_without_ca(
        certificatePem=cert_pem,
        status="ACTIVE",
    )
    cert_arn = reg["certificateArn"]

    try:
        iot.create_thing(thingName=device_id, thingTypeName=IOT_THING_TYPE)
    except iot.exceptions.ResourceAlreadyExistsException:
        pass

    try:
        iot.get_policy(policyName=IOT_POLICY_NAME)
    except iot.exceptions.ResourceNotFoundException:
        iot.create_policy(
            policyName=IOT_POLICY_NAME,
            policyDocument=_default_iot_policy_document(),
        )

    iot.attach_policy(policyName=IOT_POLICY_NAME, target=cert_arn)
    iot.attach_thing_principal(thingName=device_id, principal=cert_arn)


async def _register_devices_in_aws(
    devices: list[Device],
    cert_map: dict[str, DeviceCert],
    on_event: RegistrationCallback | None,
) -> None:
    now = datetime.now(UTC)
    failed: list[str] = []

    for d in devices:
        dc = cert_map[d.device_id]
        try:
            _emit(
                on_event,
                d.device_id,
                STATUS_PENDING,
                "provisioning certificate and thing in AWS IoT",
            )
            await asyncio.to_thread(_provision_device_in_aws, d.device_id, dc.cert_pem)

            async with async_session_factory() as db:
                await db.execute(
                    update(Device)
                    .where(Device.device_id == d.device_id)
                    .values(registration_status=STATUS_REGISTERED, registered_at=now)
                )
                await db.commit()

            _emit(on_event, d.device_id, STATUS_REGISTERED, "registered in AWS IoT Core")
        except Exception as exc:
            failed.append(d.device_id)
            logger.exception("AWS IoT registration failed for %s", d.device_id)
            _emit(on_event, d.device_id, STATUS_REJECTED, f"AWS IoT registration failed: {exc}")

    if failed:
        raise RuntimeError(
            f"AWS IoT registration failed for {len(failed)} device(s): {', '.join(failed[:10])}"
        )

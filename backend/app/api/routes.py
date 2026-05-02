from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.api.session import session_manager
from app.models.telemetry import (
    SessionConfig,
    StartSessionRequest,
    StartSessionResponse,
    StopSessionResponse,
    SessionStatus,
    DeviceType,
    ScenarioType,
    TransportProtocol,
    SendMode,
    DeviceStats,
    SeedDevicesRequest,
    SeedDevicesResponse,
)

router = APIRouter(prefix="/api/v1")


# ── Session control ──────────────────────────────────────────────────────────

@router.post("/sessions/start", response_model=StartSessionResponse)
async def start_session(body: StartSessionRequest):
    """Start a new simulation session."""
    if not body.config.devices:
        raise HTTPException(400, "At least one device profile required")
    if not body.config.endpoints:
        raise HTTPException(400, "At least one endpoint required")
    if body.config.batch_size < 1:
        raise HTTPException(400, "batch_size must be >= 1")

    session_id = await session_manager.start_session(body.config)
    return StartSessionResponse(
        session_id=session_id,
        message=f"Session {session_id} started",
    )


@router.post("/sessions/{session_id}/stop", response_model=StopSessionResponse)
async def stop_session(session_id: str):
    """Stop a running simulation session."""
    status = await session_manager.stop_session(session_id)
    if status is None:
        raise HTTPException(404, f"Session {session_id} not found")
    return StopSessionResponse(
        session_id=session_id,
        message="Session stopped",
        final_stats=status,
    )


@router.get("/sessions/{session_id}/status", response_model=SessionStatus)
async def get_session_status(session_id: str):
    """Get current status and stats for a session."""
    status = session_manager.get_status(session_id)
    if status is None:
        raise HTTPException(404, f"Session {session_id} not found")
    return status


@router.get("/sessions", response_model=list[SessionStatus])
async def list_sessions():
    """List all sessions (active and stopped)."""
    return session_manager.list_sessions()


@router.post("/sessions/stop-all")
async def stop_all_sessions():
    """Stop every running session."""
    await session_manager.stop_all()
    return {"message": "All sessions stopped"}


# ── Schema / meta endpoints (used by UI dropdowns) ───────────────────────────

@router.get("/meta/device-types")
async def get_device_types():
    return [{"value": d.value, "label": d.value.replace("_", " ").title()} for d in DeviceType]


@router.get("/meta/scenarios")
async def get_scenarios():
    descriptions = {
        ScenarioType.WORKOUT:   "Elevated HR, high steps, increased temperature",
        ScenarioType.SLEEP:     "Low HR, high HRV, sleep metrics populated",
        ScenarioType.REST:      "Normal resting vitals, minimal activity",
        ScenarioType.EMERGENCY: "Out-of-range vitals: tachycardia, low SpO2, fever",
        ScenarioType.RANDOM:    "Fully random across all valid ranges",
    }
    return [
        {"value": s.value, "label": s.value.title(), "description": descriptions[s]}
        for s in ScenarioType
    ]


@router.get("/meta/protocols")
async def get_protocols():
    notes = {
        TransportProtocol.HTTP:      "Real HTTP POST to endpoint URL",
        TransportProtocol.MQTT:      "Mock MQTT publish (no broker required)",
        TransportProtocol.WEBSOCKET: "Mock WebSocket frame send",
        TransportProtocol.GRPC:      "Mock gRPC unary call",
    }
    return [
        {"value": p.value, "label": p.value.upper(), "note": notes[p]}
        for p in TransportProtocol
    ]


@router.get("/meta/defaults")
async def get_defaults():
    """Return simulator defaults read from environment variables."""
    import os

    endpoints = []

    # HTTP endpoints — comma-separated "name::url" pairs
    for pair in filter(None, (p.strip() for p in os.getenv("DEFAULT_HTTP_ENDPOINTS", "").split(","))):
        if "::" in pair:
            name, url = pair.split("::", 1)
            endpoints.append({"name": name.strip(), "url": url.strip(),
                               "protocol": "http", "enabled": True, "headers": {}})

    # MQTT
    mqtt_url   = os.getenv("DEFAULT_MQTT_BROKER_URL", "")
    mqtt_topic = os.getenv("DEFAULT_MQTT_TOPIC", "health/telemetry")
    if mqtt_url:
        endpoints.append({"name": f"mqtt/{mqtt_topic}", "url": mqtt_url,
                           "protocol": "mqtt", "enabled": False, "headers": {}})

    # WebSocket
    ws_url = os.getenv("DEFAULT_WS_URL", "")
    if ws_url:
        endpoints.append({"name": "local-ws", "url": ws_url,
                           "protocol": "websocket", "enabled": False, "headers": {}})

    # gRPC
    grpc_host = os.getenv("DEFAULT_GRPC_HOST", "")
    grpc_port = os.getenv("DEFAULT_GRPC_PORT", "50051")
    if grpc_host:
        endpoints.append({"name": "local-grpc", "url": f"grpc://{grpc_host}:{grpc_port}",
                           "protocol": "grpc", "enabled": False, "headers": {}})

    return {
        "batch_size":       int(os.getenv("DEFAULT_BATCH_SIZE", "10")),
        "interval_seconds": float(os.getenv("DEFAULT_INTERVAL_SECONDS", "5")),
        "anomaly_rate":     float(os.getenv("DEFAULT_ANOMALY_RATE", "0.1")),
        "max_devices":      int(os.getenv("DEFAULT_MAX_DEVICES", "200")),
        "mqtt_topic":       mqtt_topic,
        "endpoints":        endpoints,
    }


@router.get("/meta/send-modes")
async def get_send_modes():
    return [
        {"value": SendMode.IMMEDIATE.value, "label": "Immediate",
         "description": "Send each event right after generation"},
        {"value": SendMode.BATCH.value,     "label": "Batch",
         "description": "Accumulate N events then send as one payload"},
    ]


# ── Device registry ──────────────────────────────────────────────────────────

@router.get("/devices/stats", response_model=DeviceStats)
async def get_device_stats():
    """Return device counts broken down by type."""
    try:
        from app.db import async_session_factory
        from app.models.device_orm import Device
        from sqlalchemy import func, select

        async with async_session_factory() as db:
            rows = (
                await db.execute(
                    select(Device.device_type, func.count(Device.id))
                    .where(Device.is_active.is_(True))
                    .group_by(Device.device_type)
                )
            ).all()

        by_type = {r[0]: r[1] for r in rows}
        return DeviceStats(total=sum(by_type.values()), by_type=by_type)
    except Exception as exc:
        from fastapi import HTTPException
        raise HTTPException(503, f"Database unavailable: {exc}") from exc


@router.post("/devices/seed", response_model=SeedDevicesResponse)
async def seed_devices(body: SeedDevicesRequest):
    """Add new devices of the given type to the registry."""
    import random
    import uuid

    try:
        from app.db import async_session_factory
        from app.models.device_orm import Device
        from app.generators.telemetry import FIRMWARE_VERSIONS, GPS_BOUNDS, _rf
        from sqlalchemy import func, select

        GPS_CAPABLE = {"smartwatch", "smartphone"}

        async with async_session_factory() as db:
            # Reuse existing user pool or create new users at 1:4 ratio
            existing_users = (
                await db.execute(
                    select(Device.user_id).distinct().limit(500)
                )
            ).scalars().all()

            user_pool = (
                list(existing_users)
                if existing_users
                else [f"user-{uuid.uuid4().hex[:6]}" for _ in range(max(1, body.count // 4))]
            )
            if len(user_pool) < body.count // 4:
                # Grow the pool if adding many devices
                new_users = [f"user-{uuid.uuid4().hex[:6]}" for _ in range(body.count // 4)]
                user_pool = user_pool + new_users

            dtype = body.device_type.value
            has_gps = dtype in GPS_CAPABLE

            records = []
            for _ in range(body.count):
                short = uuid.uuid4().hex[:8]
                records.append(
                    Device(
                        device_id=f"{dtype}-{short}",
                        device_type=dtype,
                        user_id=random.choice(user_pool),
                        firmware_version=random.choice(FIRMWARE_VERSIONS[body.device_type]),
                        gps_lat=_rf(*GPS_BOUNDS["lat"], 5) if has_gps else None,
                        gps_lon=_rf(*GPS_BOUNDS["lon"], 5) if has_gps else None,
                        is_active=True,
                    )
                )

            db.add_all(records)
            await db.commit()

            total = (
                await db.execute(select(func.count(Device.id)).where(Device.is_active.is_(True)))
            ).scalar_one()

        return SeedDevicesResponse(
            created=body.count,
            total=total,
            message=f"Created {body.count} {dtype} devices. Total active: {total}.",
        )
    except Exception as exc:
        from fastapi import HTTPException
        raise HTTPException(503, f"Database unavailable: {exc}") from exc


# ── Quick generate (preview without sending) ─────────────────────────────────

@router.post("/preview")
async def preview_event(
    device_type: DeviceType = DeviceType.SMARTWATCH,
    scenario: ScenarioType = ScenarioType.REST,
    protocol: TransportProtocol = TransportProtocol.HTTP,
):
    """Generate a single event without sending — useful for UI preview."""
    from app.generators.telemetry import TelemetryGenerator
    from app.models.telemetry import DeviceProfile

    gen = TelemetryGenerator(
        device_profiles=[DeviceProfile(device_type=device_type, count=1)],
        anomaly_rate=0.0,
    )
    event = gen.generate_event(scenario=scenario, protocol=protocol)
    return event

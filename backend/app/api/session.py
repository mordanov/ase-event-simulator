"""
Session manager.

One Session = one running simulation.
The manager keeps a registry of active sessions and exposes
start / stop / status operations.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select

from app.generators.telemetry import GPS_BOUNDS, FIRMWARE_VERSIONS, TelemetryGenerator, _rf
from app.models.telemetry import (
    BatchPayload,
    DeviceType,
    EndpointMode,
    EndpointStatus,
    RegistrationEvent,
    ScenarioType,
    SendMode,
    SessionConfig,
    SessionStatus,
    TelemetryEvent,
    TransportProtocol,
)
from app.services.runtime_mode import is_cloud_mode
from app.transports.sender import AWSIoTMQTTTransport, BaseTransport, HTTPTransport, make_transport

logger = logging.getLogger(__name__)

MAX_RECENT_EVENTS = 50
MAX_REG_LOG = 200


class Session:
    def __init__(self, config: SessionConfig, preloaded_devices: list[dict] | None = None):
        self.config = config
        self.session_id = config.session_id
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._started_at: Optional[float] = None

        # Telemetry stats
        self._events_generated = 0
        self._events_sent = 0
        self._events_failed = 0
        self._batches_sent = 0
        self._anomalies = 0
        self._recent: deque[TelemetryEvent] = deque(maxlen=MAX_RECENT_EVENTS)
        self._rr_index = 0  # round-robin transport cursor

        # Registration state
        self._registration_phase = True
        self._reg_log: deque[RegistrationEvent] = deque(maxlen=MAX_REG_LOG)
        self._devices_registered = 0
        self._devices_pending = 0

        # Build generator (DB-backed devices take priority over ephemeral profiles)
        self._generator = TelemetryGenerator(
            device_profiles=config.devices,
            preloaded_devices=preloaded_devices,
            anomaly_rate=config.anomaly_rate,
        )

        # Build transports (one per endpoint)
        self._transports: list[BaseTransport] = [
            make_transport(ep)
            for ep in config.endpoints
            if ep.enabled
        ]

    # ── lifecycle ──────────────────────────────────────────────────────────

    def start(self):
        if self._running:
            return
        self._running = True
        self._started_at = time.monotonic()
        self._task = asyncio.create_task(self._run(), name=f"session-{self.session_id}")
        logger.info("Session %s started (%d devices, mode=%s)",
                    self.session_id, self._generator.device_count, self.config.send_mode)

    async def stop(self):
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self._close_transports()
        logger.info("Session %s stopped. Generated=%d Sent=%d Failed=%d",
                    self.session_id, self._events_generated, self._events_sent, self._events_failed)

    @property
    def running(self) -> bool:
        return self._running

    # ── main loop ──────────────────────────────────────────────────────────

    async def _run(self):
        try:
            await self._run_registration()
            self._registration_phase = False
            if self.config.send_mode == SendMode.IMMEDIATE:
                await self._run_immediate()
            else:
                await self._run_batch()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Session %s crashed", self.session_id)

    async def _run_registration(self):
        """JITR registration phase: runs before any telemetry is sent."""
        device_ids = [d["device_id"] for d in self._generator.devices]
        if not device_ids:
            return

        self._devices_pending = len(device_ids)

        def on_reg_event(device_id: str, status: str, message: str) -> None:
            self._reg_log.append(RegistrationEvent(
                device_id=device_id, status=status, message=message,
            ))
            if status == "registered":
                self._devices_registered += 1
                self._devices_pending = max(0, self._devices_pending - 1)

        try:
            from app.services.registration_service import register_devices
            fingerprints = await register_devices(device_ids, on_event=on_reg_event)
            # Stamp each device dict with its cert fingerprint
            for d in self._generator.devices:
                d["cert_fingerprint"] = fingerprints.get(d["device_id"], "")
            # Load cert PEM data and wire up mTLS on HTTP transports
            await self._register_mtls_certs(fingerprints)
            # Send a registration event (with biometrics) to all enabled endpoints
            await self._send_registration_events()
        except Exception as exc:
            if is_cloud_mode():
                raise RuntimeError(f"Registration failed in cloud mode: {exc}") from exc
            logger.warning("Registration failed, proceeding without certs: %s", exc)
            self._devices_registered = len(device_ids)
            self._devices_pending = 0

    async def _register_mtls_certs(self, fingerprints: dict[str, str]) -> None:
        """Query DB for cert PEM data and register each device cert with transports."""
        http_transports = [t for t in self._transports if isinstance(t, HTTPTransport)]
        mqtt_transports = [t for t in self._transports if isinstance(t, AWSIoTMQTTTransport)]
        if not http_transports and not mqtt_transports:
            return
        try:
            from sqlalchemy import select
            from app.db import async_session_factory
            from app.models.device_orm import Device
            device_ids = list(fingerprints.keys())
            async with async_session_factory() as db:
                rows = (
                    await db.execute(
                        select(Device).where(Device.device_id.in_(device_ids))
                    )
                ).scalars().all()
            for d in rows:
                fp = fingerprints.get(d.device_id, "")
                if not (d.cert_pem and d.cert_key_pem):
                    continue
                # HTTP mTLS: keyed by fingerprint
                if fp:
                    for t in http_transports:
                        t.register_device_cert(fp, d.cert_pem, d.cert_key_pem)
                # MQTT mTLS: keyed by device_id (= MQTT client_id)
                for t in mqtt_transports:
                    t.register_device_cert(d.device_id, d.cert_pem, d.cert_key_pem)
        except Exception as exc:
            logger.warning("mTLS cert registration skipped: %s", exc)

    async def _send_registration_events(self):
        """POST a RegistrationEvent with biometrics to every enabled endpoint,
        then append an enriched log entry with the request payload and responses."""
        for d in self._generator.devices:
            event = RegistrationEvent(
                device_id=d["device_id"],
                status="registered",
                message="Device registered — biometric profile attached",
                height_cm=d.get("height_cm"),
                weight_kg=d.get("weight_kg"),
                gender=d.get("gender"),
                birth_date=d.get("birth_date"),
            )
            import json as _json
            request_payload = _json.loads(event.model_dump_json(
                exclude={"request_payload", "endpoint_responses"}
            ))

            responses = await asyncio.gather(
                *[t.send_registration_event(event) for t in self._transports],
                return_exceptions=True,
            )
            endpoint_responses = [
                r if isinstance(r, dict) else {"name": "?", "url": "?", "status_code": None, "body": str(r)}
                for r in responses
            ]

            self._reg_log.append(RegistrationEvent(
                device_id=d["device_id"],
                status="registered",
                message="Biometric registration event sent to endpoints",
                height_cm=d.get("height_cm"),
                weight_kg=d.get("weight_kg"),
                gender=d.get("gender"),
                birth_date=d.get("birth_date"),
                request_payload=request_payload,
                endpoint_responses=endpoint_responses,
            ))

    async def _run_immediate(self):
        """Per interval: one event per device fanned out to every transport.

        Each device produces ONE event per tick (same event_id, same metrics).
        Copies with the appropriate protocol field are delivered to each transport
        in parallel, so backends can deduplicate by event_id.
        """
        sent = 0
        device_count = self._generator.device_count
        primary_protocol = self._transports[0].endpoint.protocol if self._transports else None

        while self._running:
            if self.config.total_events and sent >= self.config.total_events:
                break

            # Generate ONE event per device (protocol = primary transport)
            base_events: list[TelemetryEvent] = [
                self._generator.generate_event(
                    scenario=self.config.scenario,
                    protocol=primary_protocol,
                    device_index=i,
                )
                for i in range(device_count)
            ]

            self._events_generated += len(base_events)
            self._anomalies += sum(1 for e in base_events if e.is_anomaly)
            for e in base_events:
                self._recent.append(e)

            tasks = []
            if self.config.endpoint_mode == EndpointMode.ROUND_ROBIN and self._transports:
                # Each event → one transport, cycling round-robin
                for event in base_events:
                    transport = self._transports[self._rr_index % len(self._transports)]
                    self._rr_index += 1
                    delivered = (
                        event if event.protocol == transport.endpoint.protocol
                        else event.model_copy(update={"protocol": transport.endpoint.protocol})
                    )
                    tasks.append(transport.send_event(delivered))
            else:
                # Fan out: same event_id/data, protocol field reflects delivery path
                for event in base_events:
                    for transport in self._transports:
                        delivered = (
                            event if event.protocol == transport.endpoint.protocol
                            else event.model_copy(update={"protocol": transport.endpoint.protocol})
                        )
                        tasks.append(transport.send_event(delivered))

            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, Exception) or r is False:
                    self._events_failed += 1
                else:
                    self._events_sent += 1
                    sent += 1

            await asyncio.sleep(self.config.interval_seconds)

    async def _run_batch(self):
        """Per interval: one event per device, grouped into batch_size chunks per transport."""
        total_sent = 0
        device_count = self._generator.device_count
        primary_protocol = self._transports[0].endpoint.protocol if self._transports else None

        while self._running:
            if self.config.total_events and total_sent >= self.config.total_events:
                break

            n = (
                min(device_count, self.config.total_events - total_sent)
                if self.config.total_events
                else device_count
            )

            # Generate ONE set of events (same event_id/metrics across all transports)
            base_events: list[TelemetryEvent] = [
                self._generator.generate_event(
                    scenario=self.config.scenario,
                    protocol=primary_protocol,
                    device_index=i,
                )
                for i in range(n)
            ]
            self._events_generated += len(base_events)
            self._anomalies += sum(1 for e in base_events if e.is_anomaly)
            for e in base_events:
                self._recent.append(e)

            send_tasks: list[tuple] = []
            if self.config.endpoint_mode == EndpointMode.ROUND_ROBIN and self._transports:
                # Assign each event to a transport round-robin, then batch per transport
                transport_events: dict[int, list[TelemetryEvent]] = {
                    i: [] for i in range(len(self._transports))
                }
                for event in base_events:
                    t_idx = self._rr_index % len(self._transports)
                    self._rr_index += 1
                    transport = self._transports[t_idx]
                    delivered = (
                        event if event.protocol == transport.endpoint.protocol
                        else event.model_copy(update={"protocol": transport.endpoint.protocol})
                    )
                    transport_events[t_idx].append(delivered)
                for t_idx, t_events in transport_events.items():
                    if not t_events:
                        continue
                    transport = self._transports[t_idx]
                    for start in range(0, len(t_events), self.config.batch_size):
                        chunk = t_events[start : start + self.config.batch_size]
                        send_tasks.append(
                            (transport, BatchPayload(event_count=len(chunk), events=chunk))
                        )
            else:
                for transport in self._transports:
                    # Fan out: reuse same event_id/metrics, override protocol field
                    events = [
                        e if e.protocol == transport.endpoint.protocol
                        else e.model_copy(update={"protocol": transport.endpoint.protocol})
                        for e in base_events
                    ]
                    for start in range(0, len(events), self.config.batch_size):
                        chunk = events[start : start + self.config.batch_size]
                        send_tasks.append(
                            (transport, BatchPayload(event_count=len(chunk), events=chunk))
                        )

            results = await asyncio.gather(
                *[t.send_batch(b) for t, b in send_tasks],
                return_exceptions=True,
            )
            for (_, batch), result in zip(send_tasks, results):
                if isinstance(result, Exception) or result is False:
                    self._events_failed += len(batch.events)
                else:
                    self._events_sent += len(batch.events)
                    total_sent += len(batch.events)
                    self._batches_sent += 1

            await asyncio.sleep(self.config.interval_seconds)

    # ── status ─────────────────────────────────────────────────────────────

    def get_status(self) -> SessionStatus:
        elapsed = 0.0
        if self._started_at:
            elapsed = round(time.monotonic() - self._started_at, 1)

        endpoint_statuses: list[EndpointStatus] = [t.status for t in self._transports]

        return SessionStatus(
            session_id=self.session_id,
            running=self._running,
            events_generated=self._events_generated,
            events_sent=self._events_sent,
            events_failed=self._events_failed,
            batches_sent=self._batches_sent,
            anomalies_generated=self._anomalies,
            started_at=datetime.now(timezone.utc).isoformat() if self._started_at else None,
            elapsed_seconds=elapsed,
            endpoints=endpoint_statuses,
            recent_events=list(self._recent)[-20:],
            registration_phase=self._registration_phase,
            devices_total=self._generator.device_count,
            devices_registered=self._devices_registered,
            devices_pending=self._devices_pending,
            registration_log=list(self._reg_log),
        )

    # ── cleanup ────────────────────────────────────────────────────────────

    async def _close_transports(self):
        for t in self._transports:
            if hasattr(t, "close"):
                try:
                    await t.close()
                except Exception:
                    pass


# ─── DB device loader ─────────────────────────────────────────────────────────

async def _load_devices_from_db(device_profiles: list) -> list[dict] | None:
    """
    Query the devices table and return a flat list of device dicts compatible
    with TelemetryGenerator._devices.  Returns None if DB is unavailable.
    """
    try:
        from app.db import async_session_factory
        from app.models.device_orm import Device

        devices: list[dict] = []
        async with async_session_factory() as db:
            for profile in device_profiles:
                stmt = select(Device).where(Device.is_active.is_(True))
                if profile.device_type.value != "random":
                    stmt = stmt.where(Device.device_type == profile.device_type.value)
                stmt = stmt.order_by(func.random()).limit(profile.count)
                rows = (await db.execute(stmt)).scalars().all()
                if not rows:
                    continue
                # Cycle rows if fewer DB devices than requested
                for i in range(profile.count):
                    d = rows[i % len(rows)]
                    devices.append({
                        "device_id": d.device_id,
                        "user_id": d.user_id,
                        "device_type": DeviceType(d.device_type),
                        "firmware": d.firmware_version,
                        "gps_lat": d.gps_lat if d.gps_lat is not None else _rf(*GPS_BOUNDS["lat"], 5),
                        "gps_lon": d.gps_lon if d.gps_lon is not None else _rf(*GPS_BOUNDS["lon"], 5),
                        "height_cm": d.height_cm,
                        "weight_kg": d.weight_kg,
                        "gender": d.gender,
                        "birth_date": d.birth_date,
                    })
        return devices if devices else None
    except Exception as exc:
        logger.warning("DB unavailable — falling back to ephemeral device IDs: %s", exc)
        return None


# ─── Manager (singleton) ──────────────────────────────────────────────────────

class SessionManager:
    def __init__(self):
        self._sessions: dict[str, Session] = {}

    async def start_session(self, config: SessionConfig) -> str:
        # Try to load real device records from DB
        preloaded = await _load_devices_from_db(config.devices)

        # Stop any existing session with same id
        old = self._sessions.get(config.session_id)
        if old:
            asyncio.create_task(old.stop())

        session = Session(config, preloaded_devices=preloaded)
        self._sessions[config.session_id] = session
        session.start()
        return config.session_id

    async def stop_session(self, session_id: str) -> Optional[SessionStatus]:
        session = self._sessions.get(session_id)
        if not session:
            return None
        await session.stop()
        status = session.get_status()
        return status

    def get_status(self, session_id: str) -> Optional[SessionStatus]:
        session = self._sessions.get(session_id)
        if not session:
            return None
        return session.get_status()

    def list_sessions(self) -> list[SessionStatus]:
        return [s.get_status() for s in self._sessions.values()]

    async def stop_all(self):
        for session in list(self._sessions.values()):
            await session.stop()
        self._sessions.clear()


# Global singleton
session_manager = SessionManager()

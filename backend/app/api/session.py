"""
Session manager.

One Session = one running simulation.
The manager keeps a registry of active sessions and exposes
start / stop / status operations.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select

from app.generators.telemetry import GPS_BOUNDS, FIRMWARE_VERSIONS, TelemetryGenerator, _rf
import httpx

from app.models.telemetry import (
    ActivityEvent,
    BatchPayload,
    DeviceType,
    EndpointMode,
    EndpointStatus,
    RecommendationLog,
    RegistrationEvent,
    ScenarioType,
    SendMode,
    SessionConfig,
    SessionStatus,
    TelemetryEvent,
    TransportProtocol,
)
from app.services.runtime_mode import is_cloud_mode
from app.transports.sender import AWSIoTMQTTTransport, BaseTransport, HTTPTransport, make_transport, build_registration_payload

logger = logging.getLogger(__name__)

MAX_RECENT_EVENTS = 50
MAX_REG_LOG = 200
MAX_REC_LOG = 500
MAX_ACTIVITY_LOG = 1000


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

        # Recommendation log
        self._rec_log: deque[RecommendationLog] = deque(maxlen=MAX_REC_LOG)
        self._rec_client: Optional[httpx.AsyncClient] = None

        # Unified activity log (all backend calls)
        self._activity_log: deque[ActivityEvent] = deque(maxlen=MAX_ACTIVITY_LOG)

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
            for d in self._generator.devices:
                d["cert_fingerprint"] = fingerprints.get(d["device_id"], "")
            await self._register_mtls_certs(fingerprints)
        except Exception as exc:
            if is_cloud_mode():
                raise RuntimeError(f"Registration failed in cloud mode: {exc}") from exc
            logger.warning("Registration failed, proceeding without certs: %s", exc)
            self._devices_registered = len(device_ids)
            self._devices_pending = 0

        # Always send device profiles to configured endpoints regardless of JITR outcome.
        await self._send_registration_events()

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
        """POST device profile + biometrics to every enabled HTTP endpoint
        (ingestion pipeline /api/v1/devices), then append an enriched log entry."""
        for d in self._generator.devices:
            dt = d.get("device_type")
            event = RegistrationEvent(
                device_id=d["device_id"],
                status="registered",
                message="Device registered — profile attached",
                device_type=dt.value if hasattr(dt, "value") else str(dt) if dt else None,
                model=d.get("model"),
                firmware_version=d.get("firmware"),
                os=d.get("os"),
                user_id=d.get("user_id"),
                height_cm=d.get("height_cm"),
                weight_kg=d.get("weight_kg"),
                gender=d.get("gender"),
                birth_date=d.get("birth_date"),
            )
            request_payload = build_registration_payload(event)

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
            first_ok = next((r for r in endpoint_responses if r.get("status_code") and r["status_code"] < 400), None)
            reg_status = "registered" if first_ok else ("error" if endpoint_responses else "ok")
            self._activity_log.append(ActivityEvent(
                timestamp=datetime.now(timezone.utc).isoformat(),
                event_type="registration",
                device_id=d["device_id"],
                status=reg_status,
                data={
                    "model": d.get("model"),
                    "firmware_version": d.get("firmware"),
                    "os": d.get("os"),
                    "height_cm": d.get("height_cm"),
                    "weight_kg": d.get("weight_kg"),
                    "gender": d.get("gender"),
                    "request": request_payload,
                    "responses": endpoint_responses,
                },
            ))

    def _collect_credit_results(self) -> list[dict]:
        """Gather last_credit_results from all HTTP transports, deduplicated by device_id."""
        seen: set[str] = set()
        results: list[dict] = []
        for t in self._transports:
            if isinstance(t, HTTPTransport):
                for cr in t.last_credit_results:
                    did = cr.get("device_id", "")
                    if did and did not in seen:
                        seen.add(did)
                        results.append(cr)
        return results

    async def _maybe_recommend(self, credit_results: list[dict]) -> None:
        """Call the recommendation API for any device whose balance meets the handicap."""
        handicap = self.config.recommendation_handicap
        if handicap <= 0 or not credit_results:
            return

        # Derive the recommendation base URL from the first HTTP transport
        from urllib.parse import urlparse
        rec_base: Optional[str] = None
        for t in self._transports:
            if isinstance(t, HTTPTransport):
                p = urlparse(t.endpoint.url)
                rec_base = f"{p.scheme}://{p.netloc}"
                break
        if not rec_base:
            return

        api_key = os.getenv("INGESTION_API_KEY", "")

        if self._rec_client is None or self._rec_client.is_closed:
            self._rec_client = httpx.AsyncClient(timeout=httpx.Timeout(10.0))

        for cr in credit_results:
            device_id = cr.get("device_id", "")
            balance_before = cr.get("resulting_balance", 0)
            tier = cr.get("reward_tier", "bronze")
            if not device_id or balance_before < handicap:
                continue

            rec_url = f"{rec_base}/api/v1/devices/{device_id}/recommendations"
            try:
                resp = await self._rec_client.post(
                    rec_url, content='{"min_confidence": 0.2}',
                    headers={"Content-Type": "application/json", "X-API-Key": api_key},
                )
                if resp.status_code < 400:
                    body = resp.json()
                    balance_after = body.get("credits_remaining", balance_before)
                    ts_rec = datetime.now(timezone.utc).isoformat()
                    self._rec_log.append(RecommendationLog(
                        timestamp=ts_rec,
                        device_id=device_id,
                        reward_tier=body.get("reward_tier", tier),
                        balance_before=balance_before,
                        balance_after=balance_after,
                        credits_spent=balance_before - balance_after,
                        request={"device_id": device_id, "min_confidence": 0.2},
                        response={
                            "recommendations": body.get("recommendations", []),
                            "credits_remaining": balance_after,
                            "reward_tier": body.get("reward_tier", tier),
                            "providers_called": body.get("providers_called", 0),
                            "providers_succeeded": body.get("providers_succeeded", 0),
                            "duration_ms": body.get("duration_ms", 0),
                            "trace_id": body.get("trace_id", ""),
                        },
                    ))
                    self._activity_log.append(ActivityEvent(
                        timestamp=ts_rec,
                        event_type="recommendation",
                        device_id=device_id,
                        status="ok",
                        data={
                            "request": {"device_id": device_id, "min_confidence": 0.2},
                            "response": {
                                "recommendations": body.get("recommendations", []),
                                "credits_remaining": balance_after,
                                "reward_tier": body.get("reward_tier", tier),
                                "providers_called": body.get("providers_called", 0),
                                "providers_succeeded": body.get("providers_succeeded", 0),
                                "duration_ms": body.get("duration_ms", 0),
                            },
                            "balance_before": balance_before,
                            "balance_after": balance_after,
                            "credits_spent": balance_before - balance_after,
                        },
                    ))
                else:
                    ts_rec = datetime.now(timezone.utc).isoformat()
                    self._rec_log.append(RecommendationLog(
                        timestamp=ts_rec,
                        device_id=device_id,
                        reward_tier=tier,
                        balance_before=balance_before,
                        balance_after=balance_before,
                        credits_spent=0,
                        request={"device_id": device_id, "min_confidence": 0.2},
                        error=f"HTTP {resp.status_code}: {resp.text[:200]}",
                    ))
                    self._activity_log.append(ActivityEvent(
                        timestamp=ts_rec,
                        event_type="recommendation",
                        device_id=device_id,
                        status="error",
                        data={
                            "request": {"device_id": device_id, "min_confidence": 0.2},
                            "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
                        },
                    ))
            except Exception as exc:
                ts_rec = datetime.now(timezone.utc).isoformat()
                self._rec_log.append(RecommendationLog(
                    timestamp=ts_rec,
                    device_id=device_id,
                    reward_tier=tier,
                    balance_before=balance_before,
                    balance_after=balance_before,
                    credits_spent=0,
                    request={"device_id": device_id, "min_confidence": 0.2},
                    error=str(exc),
                ))
                self._activity_log.append(ActivityEvent(
                    timestamp=ts_rec,
                    event_type="recommendation",
                    device_id=device_id,
                    status="error",
                    data={
                        "request": {"device_id": device_id, "min_confidence": 0.2},
                        "error": str(exc)[:200],
                    },
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

            # Clear accumulated credit results from the previous cycle
            for t in self._transports:
                if isinstance(t, HTTPTransport):
                    t.last_credit_results.clear()

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

            task_meta: list[tuple[TelemetryEvent, BaseTransport]] = []
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
                    task_meta.append((event, transport))
                    tasks.append(transport.send_event(delivered))
            else:
                # Fan out: same event_id/data, protocol field reflects delivery path
                for event in base_events:
                    for transport in self._transports:
                        delivered = (
                            event if event.protocol == transport.endpoint.protocol
                            else event.model_copy(update={"protocol": transport.endpoint.protocol})
                        )
                        task_meta.append((event, transport))
                        tasks.append(transport.send_event(delivered))

            results = await asyncio.gather(*tasks, return_exceptions=True)
            event_responses: dict[str, list] = defaultdict(list)
            for (event, transport), r in zip(task_meta, results):
                if isinstance(r, Exception):
                    self._events_failed += 1
                    event_responses[event.event_id].append({
                        "name": transport.endpoint.name, "status_code": None,
                        "body": None, "error": str(r)[:300],
                    })
                else:
                    if r.get("ok"):
                        self._events_sent += 1
                        sent += 1
                    else:
                        self._events_failed += 1
                    event_responses[event.event_id].append({
                        "name": transport.endpoint.name,
                        "status_code": r.get("status_code"),
                        "body": r.get("body"),
                        "error": r.get("error"),
                    })

            credit_results = self._collect_credit_results()
            ts_ev = datetime.now(timezone.utc).isoformat()
            for event in base_events:
                self._activity_log.append(ActivityEvent(
                    timestamp=ts_ev,
                    event_type=event.scenario.value,
                    device_id=event.device_id,
                    status="anomaly" if event.is_anomaly else "ok",
                    data={
                        "payload": event.model_dump(mode="json"),
                        "endpoint_responses": event_responses.get(event.event_id, []),
                    },
                ))
            for cr in credit_results:
                if cr.get("activity_reward", 0) > 0:
                    self._activity_log.append(ActivityEvent(
                        timestamp=ts_ev,
                        event_type="rewards",
                        device_id=cr.get("device_id", ""),
                        status="ok",
                        data=cr,
                    ))
            await self._maybe_recommend(credit_results)
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

            # Clear accumulated credit results from the previous cycle
            for t in self._transports:
                if isinstance(t, HTTPTransport):
                    t.last_credit_results.clear()

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
            event_responses: dict[str, list] = defaultdict(list)
            for (transport, batch), result in zip(send_tasks, results):
                if isinstance(result, Exception):
                    self._events_failed += len(batch.events)
                    r_entry = {"name": transport.endpoint.name, "status_code": None,
                               "body": None, "error": str(result)[:300]}
                else:
                    ok = result.get("ok", False)
                    if ok:
                        self._events_sent += len(batch.events)
                        total_sent += len(batch.events)
                        self._batches_sent += 1
                    else:
                        self._events_failed += len(batch.events)
                    r_entry = {
                        "name": transport.endpoint.name,
                        "status_code": result.get("status_code"),
                        "body": result.get("body"),
                        "error": result.get("error"),
                    }
                for event in batch.events:
                    event_responses[event.event_id].append(r_entry)

            credit_results = self._collect_credit_results()
            ts_ev = datetime.now(timezone.utc).isoformat()
            for event in base_events:
                self._activity_log.append(ActivityEvent(
                    timestamp=ts_ev,
                    event_type=event.scenario.value,
                    device_id=event.device_id,
                    status="anomaly" if event.is_anomaly else "ok",
                    data={
                        "payload": event.model_dump(mode="json"),
                        "endpoint_responses": event_responses.get(event.event_id, []),
                    },
                ))
            for cr in credit_results:
                if cr.get("activity_reward", 0) > 0:
                    self._activity_log.append(ActivityEvent(
                        timestamp=ts_ev,
                        event_type="rewards",
                        device_id=cr.get("device_id", ""),
                        status="ok",
                        data=cr,
                    ))
            await self._maybe_recommend(credit_results)
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
            recommendation_log=list(self._rec_log),
            activity_log=list(self._activity_log),
        )

    # ── cleanup ────────────────────────────────────────────────────────────

    async def _close_transports(self):
        for t in self._transports:
            if hasattr(t, "close"):
                try:
                    await t.close()
                except Exception:
                    pass
        if self._rec_client and not self._rec_client.is_closed:
            await self._rec_client.aclose()
            self._rec_client = None


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
                        "model": d.model,
                        "os": d.os,
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

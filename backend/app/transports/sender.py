"""
Transport layer.

Each transport implements send_event() and send_batch().
MQTT / WebSocket / gRPC are *mock* senders — they simulate the protocol
handshake and payload serialisation but do not require a real broker.
HTTP is fully real (fires actual HTTP requests to configured endpoints).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import shutil
import tempfile
import time
import uuid
from abc import ABC, abstractmethod
from typing import Optional

import httpx

from app.models.telemetry import (
    BatchPayload,
    EndpointConfig,
    EndpointStatus,
    TelemetryEvent,
    TransportProtocol,
)
from app.services.runtime_mode import is_cloud_mode

logger = logging.getLogger(__name__)


# ─── Base ─────────────────────────────────────────────────────────────────────

class BaseTransport(ABC):
    protocol: TransportProtocol

    def __init__(self, endpoint: EndpointConfig):
        self.endpoint = endpoint
        self.status = EndpointStatus(
            name=endpoint.name,
            url=endpoint.url,
            protocol=endpoint.protocol,
        )
        self._latencies: list[float] = []

    def _record(self, latency_ms: float, ok: bool, code: Optional[int] = None, err: str = ""):
        if ok:
            self.status.success_count += 1
        else:
            self.status.error_count += 1
            self.status.last_error = err
        if code:
            self.status.last_status_code = code
        self._latencies.append(latency_ms)
        if len(self._latencies) > 200:
            self._latencies = self._latencies[-200:]
        self.status.avg_latency_ms = round(sum(self._latencies) / len(self._latencies), 2)

    @abstractmethod
    async def send_event(self, event: TelemetryEvent) -> bool:
        ...

    @abstractmethod
    async def send_batch(self, batch: BatchPayload) -> bool:
        ...


# ─── HTTP ─────────────────────────────────────────────────────────────────────

class HTTPTransport(BaseTransport):
    protocol = TransportProtocol.HTTP

    def __init__(self, endpoint: EndpointConfig):
        super().__init__(endpoint)
        self._client: Optional[httpx.AsyncClient] = None
        # mTLS: keyed by cert fingerprint
        self._mtls_clients: dict[str, httpx.AsyncClient] = {}
        self._cert_files: dict[str, tuple[str, str]] = {}   # fingerprint → (cert_path, key_path)
        self._tmpdir: Optional[str] = None

    def register_device_cert(self, fingerprint: str, cert_pem: str, key_pem: str) -> None:
        """Store device cert on disk so httpx can use it for mTLS."""
        if not self._tmpdir:
            self._tmpdir = tempfile.mkdtemp(prefix="sim_certs_")
        tag = fingerprint[:16]
        cert_path = os.path.join(self._tmpdir, f"{tag}.crt")
        key_path  = os.path.join(self._tmpdir, f"{tag}.key")
        with open(cert_path, "w") as f:
            f.write(cert_pem)
        with open(key_path, "w") as f:
            f.write(key_pem)
        self._cert_files[fingerprint] = (cert_path, key_path)

    async def _get_client(self, fingerprint: Optional[str] = None) -> httpx.AsyncClient:
        if fingerprint and fingerprint in self._cert_files:
            if fingerprint not in self._mtls_clients or self._mtls_clients[fingerprint].is_closed:
                cert_path, key_path = self._cert_files[fingerprint]
                self._mtls_clients[fingerprint] = httpx.AsyncClient(
                    timeout=httpx.Timeout(10.0),
                    headers={"Content-Type": "application/json", **self.endpoint.headers},
                    cert=(cert_path, key_path),
                    verify=False,   # self-signed CA; set verify=ca_cert_path for AWS IoT Core
                )
            return self._mtls_clients[fingerprint]

        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(10.0),
                headers={"Content-Type": "application/json", **self.endpoint.headers},
            )
        return self._client

    async def send_event(self, event: TelemetryEvent) -> bool:
        client = await self._get_client(event.cert_fingerprint)
        t0 = time.perf_counter()
        try:
            resp = await client.post(self.endpoint.url, content=event.model_dump_json())
            latency = (time.perf_counter() - t0) * 1000
            ok = resp.status_code < 400
            self._record(latency, ok, code=resp.status_code)
            return ok
        except Exception as exc:
            latency = (time.perf_counter() - t0) * 1000
            self._record(latency, False, err=str(exc))
            logger.warning("HTTP send_event failed [%s]: %s", self.endpoint.url, exc)
            return False

    async def send_batch(self, batch: BatchPayload) -> bool:
        # Use cert of first event in batch (all events in a batch come from one session)
        fingerprint = batch.events[0].cert_fingerprint if batch.events else None
        client = await self._get_client(fingerprint)
        t0 = time.perf_counter()
        try:
            resp = await client.post(self.endpoint.url, content=batch.model_dump_json())
            latency = (time.perf_counter() - t0) * 1000
            ok = resp.status_code < 400
            self._record(latency, ok, code=resp.status_code)
            return ok
        except Exception as exc:
            latency = (time.perf_counter() - t0) * 1000
            self._record(latency, False, err=str(exc))
            logger.warning("HTTP send_batch failed [%s]: %s", self.endpoint.url, exc)
            return False

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        for c in self._mtls_clients.values():
            if not c.is_closed:
                await c.aclose()
        if self._tmpdir:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
            self._tmpdir = None


# ─── MQTT (AWS IoT Core, MQTT5, mTLS) ────────────────────────────────────────

class AWSIoTMQTTTransport(BaseTransport):
    """
    Real MQTT5 transport for AWS IoT Core using aws-iot-device-sdk-python-v2.

    Cert priority per device:
      1. Per-device cert registered via register_device_cert() after JITR
      2. Static fallback cert from AWS_IOT_CERT_FILE / AWS_IOT_KEY_FILE env vars

    Falls back to a latency-realistic mock if awsiotsdk is not installed
    or no cert is available, so the simulator keeps working without AWS credentials.

    Endpoint URL format:  mqtt://<account>.iot.<region>.amazonaws.com
    Topic prefix: taken from endpoint.headers["topic"] or "health/telemetry".
    Each event is published to  <prefix>/<device_id>.
    """
    protocol = TransportProtocol.MQTT

    _ENV_CA   = os.getenv("AWS_IOT_CA_FILE",   "")   # Amazon root CA (optional)

    def __init__(self, endpoint: EndpointConfig):
        super().__init__(endpoint)
        # Per-device cert files: device_id → (cert_path, key_path)
        self._cert_files: dict[str, tuple[str, str]] = {}
        self._mqtt_clients: dict[str, object] = {}   # device_id → awscrt mqtt5.Client
        self._tmpdir: Optional[str] = None

    # ── endpoint / topic helpers ──────────────────────────────────────────────

    @property
    def _host(self) -> str:
        """Strip scheme from URL to get the raw AWS IoT hostname."""
        url = self.endpoint.url
        for prefix in ("mqtts://", "mqtt://"):
            if url.lower().startswith(prefix):
                url = url[len(prefix):]
        return url.split(":")[0].rstrip("/")

    @property
    def _topic_prefix(self) -> str:
        return self.endpoint.headers.get("topic", "health/telemetry")

    # ── per-device cert registration ──────────────────────────────────────────

    def register_device_cert(self, device_id: str, cert_pem: str, key_pem: str) -> None:
        """Write device cert to disk; called after JITR registration phase."""
        if not self._tmpdir:
            self._tmpdir = tempfile.mkdtemp(prefix="sim_mqtt_certs_")
        safe = device_id.replace("/", "_")[:48]
        cert_path = os.path.join(self._tmpdir, f"{safe}.crt")
        key_path  = os.path.join(self._tmpdir, f"{safe}.key")
        with open(cert_path, "w") as f:
            f.write(cert_pem)
        with open(key_path, "w") as f:
            f.write(key_pem)
        self._cert_files[device_id] = (cert_path, key_path)

    def _cert_for(self, device_id: str) -> Optional[tuple[str, str]]:
        return self._cert_files.get(device_id)

    # ── MQTT5 client per device ───────────────────────────────────────────────

    async def _get_mqtt_client(self, device_id: str) -> Optional[object]:
        if device_id in self._mqtt_clients:
            return self._mqtt_clients[device_id]

        certs = self._cert_for(device_id)
        if not certs:
            if is_cloud_mode():
                logger.error("MQTT cert missing for device %s (cloud mode requires per-device cert)", device_id)
            return None

        try:
            from awsiot import mqtt5_client_builder
        except ImportError:
            if is_cloud_mode():
                logger.error("awsiotsdk is not installed in cloud mode")
            else:
                logger.debug("awsiotsdk not installed — MQTT will use mock fallback")
            return None

        cert_path, key_path = certs
        loop = asyncio.get_running_loop()
        connected: asyncio.Future = loop.create_future()

        def on_success(_data):
            if not connected.done():
                loop.call_soon_threadsafe(connected.set_result, True)

        def on_failure(data):
            if not connected.done():
                exc = getattr(data, "exception", None) or Exception("MQTT connection failed")
                loop.call_soon_threadsafe(connected.set_exception, exc)

        kwargs: dict = dict(
            endpoint=self._host,
            cert_filepath=cert_path,
            pri_key_filepath=key_path,
            client_id=device_id,
            on_lifecycle_connection_success=on_success,
            on_lifecycle_connection_failure=on_failure,
        )
        if self._ENV_CA and os.path.exists(self._ENV_CA):
            kwargs["ca_filepath"] = self._ENV_CA

        client = mqtt5_client_builder.mtls_from_path(**kwargs)
        client.start()

        try:
            await asyncio.wait_for(connected, timeout=15.0)
            self._mqtt_clients[device_id] = client
            logger.info("MQTT5 connected: device=%s endpoint=%s", device_id, self._host)
            return client
        except Exception as exc:
            logger.warning("MQTT5 connect failed for %s: %s", device_id, exc)
            try:
                client.stop()
            except Exception:
                pass
            return None

    # ── send ──────────────────────────────────────────────────────────────────

    async def send_event(self, event: TelemetryEvent) -> bool:
        client = await self._get_mqtt_client(event.device_id)
        if client is None:
            if is_cloud_mode():
                self._record(0.0, False, err="Cloud mode requires real MQTT client + per-device cert")
                return False
            return await self._mock_publish(event.model_dump_json(), event.device_id)
        return await self._publish(client, event.device_id, event.model_dump_json())

    async def send_batch(self, batch: BatchPayload) -> bool:
        results = [await self.send_event(ev) for ev in batch.events]
        return all(results)

    async def _publish(self, client, device_id: str, payload: str) -> bool:
        from awscrt import mqtt5 as crt_mqtt5
        topic = f"{self._topic_prefix}/{device_id}"
        t0 = time.perf_counter()
        try:
            future = client.publish(crt_mqtt5.PublishPacket(
                topic=topic,
                payload=payload.encode(),
                qos=crt_mqtt5.QoS.AT_LEAST_ONCE,
            ))
            # future is concurrent.futures.Future — bridge to asyncio
            await asyncio.wrap_future(future)
            latency = (time.perf_counter() - t0) * 1000
            self._record(latency, True, code=0)
            logger.debug("MQTT5 publish → %s (%d bytes)", topic, len(payload))
            return True
        except Exception as exc:
            latency = (time.perf_counter() - t0) * 1000
            self._record(latency, False, err=str(exc))
            logger.warning("MQTT5 publish failed [%s]: %s", device_id, exc)
            return False

    async def _mock_publish(self, payload: str, device_id: str = "") -> bool:
        """Latency-realistic mock when no cert / SDK available."""
        t0 = time.perf_counter()
        await asyncio.sleep(random.uniform(0.001, 0.008))
        latency = (time.perf_counter() - t0) * 1000
        ok = random.random() > 0.005
        self._record(latency, ok, code=0 if ok else None)
        logger.debug("MQTT mock → %s/%s (%d bytes)", self._topic_prefix, device_id, len(payload))
        return ok

    # ── cleanup ───────────────────────────────────────────────────────────────

    async def close(self):
        for client in self._mqtt_clients.values():
            try:
                client.stop()
            except Exception:
                pass
        self._mqtt_clients.clear()
        if self._tmpdir:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
            self._tmpdir = None


# ─── WebSocket mock ───────────────────────────────────────────────────────────

class WebSocketMockTransport(BaseTransport):
    """
    Simulates WebSocket frame send without a real WS server.
    Latency slightly higher than MQTT (2–12 ms) to reflect TCP + framing.
    """
    protocol = TransportProtocol.WEBSOCKET

    async def send_event(self, event: TelemetryEvent) -> bool:
        return await self._mock_send(event.model_dump_json())

    async def send_batch(self, batch: BatchPayload) -> bool:
        return await self._mock_send(batch.model_dump_json())

    async def _mock_send(self, payload: str) -> bool:
        t0 = time.perf_counter()
        await asyncio.sleep(random.uniform(0.002, 0.012))
        latency = (time.perf_counter() - t0) * 1000
        ok = random.random() > 0.003
        self._record(latency, ok)
        logger.debug("WS mock send → %s (%d bytes) ok=%s", self.endpoint.url, len(payload), ok)
        return ok


# ─── gRPC mock ────────────────────────────────────────────────────────────────

class GRPCMockTransport(BaseTransport):
    """
    Simulates gRPC unary call without a real server.
    Serialises event to JSON (production would use protobuf).
    Latency 3–15 ms to reflect HTTP/2 + serialisation overhead.
    """
    protocol = TransportProtocol.GRPC

    async def send_event(self, event: TelemetryEvent) -> bool:
        return await self._mock_call(event.model_dump_json(), "IngestEvent")

    async def send_batch(self, batch: BatchPayload) -> bool:
        return await self._mock_call(batch.model_dump_json(), "IngestBatch")

    async def _mock_call(self, payload: str, method: str) -> bool:
        t0 = time.perf_counter()
        await asyncio.sleep(random.uniform(0.003, 0.015))
        latency = (time.perf_counter() - t0) * 1000
        ok = random.random() > 0.002
        # Simulate gRPC status codes: 0=OK, 14=UNAVAILABLE
        code = 0 if ok else 14
        self._record(latency, ok, code=code)
        logger.debug("gRPC mock %s → %s (%d bytes) status=%d", method, self.endpoint.url, len(payload), code)
        return ok


# ─── Factory ──────────────────────────────────────────────────────────────────

def make_transport(endpoint: EndpointConfig) -> BaseTransport:
    mapping = {
        TransportProtocol.HTTP:      HTTPTransport,
        TransportProtocol.MQTT:      AWSIoTMQTTTransport,
        TransportProtocol.WEBSOCKET: WebSocketMockTransport,
        TransportProtocol.GRPC:      GRPCMockTransport,
    }
    cls = mapping.get(endpoint.protocol)
    if cls is None:
        raise ValueError(f"Unknown protocol: {endpoint.protocol}")
    return cls(endpoint)

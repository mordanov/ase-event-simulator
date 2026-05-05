"""
Transport implementations used by simulation sessions.

Each transport implements send_event() and send_batch().
MQTT, WebSocket, and gRPC include mock senders that simulate protocol
handshake and payload serialization without requiring a real broker.
HTTP transport performs real outbound requests to configured endpoints.
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
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import TypedDict
from urllib.parse import urlparse

import httpx

from app.models.telemetry import (
    BatchPayload,
    EndpointConfig,
    EndpointError,
    EndpointStatus,
    RegistrationEvent,
    TelemetryEvent,
    TransportProtocol,
)
from app.services.runtime_mode import is_cloud_mode

logger = logging.getLogger(__name__)

HTTP_TIMEOUT_SECONDS = 10.0
MAX_RECENT_ENDPOINT_ERRORS = 50
MAX_LATENCY_SAMPLES = 200


class SendResult(TypedDict, total=False):
    ok: bool
    status_code: int | None
    body: dict | None
    error: str | None


def build_registration_payload(event: RegistrationEvent) -> dict:
    """Return the exact JSON body sent to /api/v1/devices for a device registration."""
    raw = {
        "device_id": event.device_id,
        "device_type": event.device_type,
        "model": event.model or "SimDevice",
        "firmware_version": event.firmware_version or "1.0.0",
        "os": event.os or "SimOS",
        "user_id": event.user_id or event.device_id,
        "height_cm": event.height_cm,
        "weight_kg": event.weight_kg,
    }
    return {k: v for k, v in raw.items() if v is not None}


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

    def _record(self, latency_ms: float, ok: bool, code: int | None = None, err: str = ""):
        if ok:
            self.status.success_count += 1
        else:
            self.status.error_count += 1
            self.status.last_error = err
            entry = EndpointError(
                timestamp=datetime.now(UTC).isoformat(),
                message=err or (f"HTTP {code}" if code else "unknown error"),
                status_code=code,
            )
            self.status.recent_errors.append(entry)
            if len(self.status.recent_errors) > MAX_RECENT_ENDPOINT_ERRORS:
                self.status.recent_errors = self.status.recent_errors[-MAX_RECENT_ENDPOINT_ERRORS:]
        if code:
            self.status.last_status_code = code
        self._latencies.append(latency_ms)
        if len(self._latencies) > MAX_LATENCY_SAMPLES:
            self._latencies = self._latencies[-MAX_LATENCY_SAMPLES:]
        self.status.avg_latency_ms = round(sum(self._latencies) / len(self._latencies), 2)

    @abstractmethod
    async def send_event(self, event: TelemetryEvent) -> SendResult: ...

    @abstractmethod
    async def send_batch(self, batch: BatchPayload) -> SendResult: ...

    async def send_registration_event(self, event: RegistrationEvent) -> dict:
        """Send a registration event. Returns {"name", "url", "status_code", "body"}."""
        return {
            "name": self.endpoint.name,
            "url": self.endpoint.url,
            "status_code": None,
            "body": "(mock transport — not sent)",
        }


# ─── HTTP ─────────────────────────────────────────────────────────────────────


class HTTPTransport(BaseTransport):
    protocol = TransportProtocol.HTTP

    def __init__(self, endpoint: EndpointConfig):
        super().__init__(endpoint)
        self._client: httpx.AsyncClient | None = None
        # mTLS: keyed by cert fingerprint
        self._mtls_clients: dict[str, httpx.AsyncClient] = {}
        self._cert_files: dict[str, tuple[str, str]] = {}  # fingerprint → (cert_path, key_path)
        self._tmpdir: str | None = None
        self.last_credit_results: list[dict] = []  # populated after each send_event/send_batch

    def _build_default_headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json", **self.endpoint.headers}

    def _parse_response_body(self, response: httpx.Response) -> dict | None:
        try:
            body = response.json()
        except Exception:
            return None

        if response.status_code < 400 and body:
            self.last_credit_results.extend(body.get("credit_results", []))
        return body

    def _result_from_response(self, response: httpx.Response, latency_ms: float) -> SendResult:
        ok = response.status_code < 400
        error = "" if ok else f"HTTP {response.status_code}: {response.text[:300]}"
        self._record(latency_ms, ok, code=response.status_code, err=error)
        return {
            "ok": ok,
            "status_code": response.status_code,
            "body": self._parse_response_body(response),
            "error": error or None,
        }

    async def _post_payload(
        self,
        url: str,
        payload: str,
        *,
        fingerprint: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[SendResult, float]:
        client = await self._get_client(fingerprint)
        t0 = time.perf_counter()
        response = await client.post(url, content=payload, headers=headers)
        latency_ms = (time.perf_counter() - t0) * 1000
        return self._result_from_response(response, latency_ms), latency_ms

    def register_device_cert(self, fingerprint: str, cert_pem: str, key_pem: str) -> None:
        """Store device cert on disk so httpx can use it for mTLS."""
        if not self._tmpdir:
            self._tmpdir = tempfile.mkdtemp(prefix="sim_certs_")
        tag = fingerprint[:16]
        cert_path = os.path.join(self._tmpdir, f"{tag}.crt")
        key_path = os.path.join(self._tmpdir, f"{tag}.key")
        with open(cert_path, "w") as f:
            f.write(cert_pem)
        with open(key_path, "w") as f:
            f.write(key_pem)
        self._cert_files[fingerprint] = (cert_path, key_path)

    async def _get_client(self, fingerprint: str | None = None) -> httpx.AsyncClient:
        if fingerprint and fingerprint in self._cert_files:
            if fingerprint not in self._mtls_clients or self._mtls_clients[fingerprint].is_closed:
                cert_path, key_path = self._cert_files[fingerprint]
                self._mtls_clients[fingerprint] = httpx.AsyncClient(
                    timeout=httpx.Timeout(HTTP_TIMEOUT_SECONDS),
                    headers=self._build_default_headers(),
                    cert=(cert_path, key_path),
                    verify=False,  # self-signed CA; set verify=ca_cert_path for AWS IoT Core
                )
            return self._mtls_clients[fingerprint]

        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(HTTP_TIMEOUT_SECONDS),
                headers=self._build_default_headers(),
            )
        return self._client

    async def send_event(self, event: TelemetryEvent) -> SendResult:
        try:
            result, _ = await self._post_payload(
                self.endpoint.url,
                event.model_dump_json(),
                fingerprint=event.cert_fingerprint,
            )
            return result
        except Exception as exc:
            latency = 0.0
            self._record(latency, False, err=str(exc))
            logger.warning("HTTP send_event failed [%s]: %s", self.endpoint.url, exc)
            return {"ok": False, "status_code": None, "body": None, "error": str(exc)}

    async def send_registration_event(self, event: RegistrationEvent) -> dict:
        # Derive the ingestion pipeline device-registration URL from the base host.
        # If the user configured http://host/ingest for telemetry, registration
        # goes to http://host/api/v1/devices.
        parsed = urlparse(self.endpoint.url)
        reg_url = f"{parsed.scheme}://{parsed.netloc}/api/v1/devices"

        api_key = os.getenv("INGESTION_API_KEY", "dev-key")
        payload = build_registration_payload(event)

        base = {"name": self.endpoint.name, "url": reg_url}
        try:
            client = await self._get_client()
            t0 = time.perf_counter()
            resp = await client.post(
                reg_url,
                content=json.dumps(payload),
                headers={"X-API-Key": api_key},
            )
            latency = (time.perf_counter() - t0) * 1000
            ok = resp.status_code < 400
            self._record(latency, ok, code=resp.status_code)
            return {**base, "status_code": resp.status_code, "body": resp.text[:2000]}
        except Exception as exc:
            latency = (time.perf_counter() - t0) * 1000
            self._record(latency, False, err=str(exc))
            logger.warning("HTTP send_registration_event failed [%s]: %s", reg_url, exc)
            return {**base, "status_code": None, "body": str(exc)}

    async def send_batch(self, batch: BatchPayload) -> SendResult:
        # Use cert of first event in batch (all events in a batch come from one session)
        fingerprint = batch.events[0].cert_fingerprint if batch.events else None
        try:
            result, _ = await self._post_payload(
                self.endpoint.url,
                batch.model_dump_json(),
                fingerprint=fingerprint,
            )
            return result
        except Exception as exc:
            latency = 0.0
            self._record(latency, False, err=str(exc))
            logger.warning("HTTP send_batch failed [%s]: %s", self.endpoint.url, exc)
            return {"ok": False, "status_code": None, "body": None, "error": str(exc)}

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

    _ENV_CA = os.getenv("AWS_IOT_CA_FILE", "")  # Amazon root CA (optional)

    def __init__(self, endpoint: EndpointConfig):
        super().__init__(endpoint)
        # Per-device cert files: device_id → (cert_path, key_path)
        self._cert_files: dict[str, tuple[str, str]] = {}
        self._mqtt_clients: dict[str, object] = {}  # device_id → awscrt mqtt5.Client
        self._tmpdir: str | None = None

    # ── endpoint / topic helpers ──────────────────────────────────────────────

    @property
    def _host(self) -> str:
        """Strip scheme from URL to get the raw AWS IoT hostname."""
        url = self.endpoint.url
        for prefix in ("mqtts://", "mqtt://"):
            if url.lower().startswith(prefix):
                url = url[len(prefix) :]
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
        key_path = os.path.join(self._tmpdir, f"{safe}.key")
        with open(cert_path, "w") as f:
            f.write(cert_pem)
        with open(key_path, "w") as f:
            f.write(key_pem)
        self._cert_files[device_id] = (cert_path, key_path)

    def _cert_for(self, device_id: str) -> tuple[str, str] | None:
        return self._cert_files.get(device_id)

    # ── MQTT5 client per device ───────────────────────────────────────────────

    async def _get_mqtt_client(self, device_id: str) -> object | None:
        if device_id in self._mqtt_clients:
            return self._mqtt_clients[device_id]

        certs = self._cert_for(device_id)
        if not certs:
            if is_cloud_mode():
                logger.error(
                    "MQTT cert missing for device %s (cloud mode requires per-device cert)",
                    device_id,
                )
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

    async def send_event(self, event: TelemetryEvent) -> SendResult:
        client = await self._get_mqtt_client(event.device_id)
        if client is None:
            if is_cloud_mode():
                _err = "Cloud mode requires real MQTT client + per-device cert"
                self._record(0.0, False, err=_err)
                return {"ok": False, "status_code": None, "body": None, "error": _err}
            return await self._mock_publish(event.model_dump_json(), event.device_id)
        return await self._publish(client, event.device_id, event.model_dump_json())

    async def send_batch(self, batch: BatchPayload) -> SendResult:
        results = [await self.send_event(ev) for ev in batch.events]
        ok = all(r.get("ok", False) for r in results)
        return {"ok": ok, "status_code": None, "body": None, "error": None}

    async def _publish(self, client, device_id: str, payload: str) -> SendResult:
        from awscrt import mqtt5 as crt_mqtt5

        topic = f"{self._topic_prefix}/{device_id}"
        t0 = time.perf_counter()
        try:
            future = client.publish(
                crt_mqtt5.PublishPacket(
                    topic=topic,
                    payload=payload.encode(),
                    qos=crt_mqtt5.QoS.AT_LEAST_ONCE,
                )
            )
            # future is concurrent.futures.Future — bridge to asyncio
            await asyncio.wrap_future(future)
            latency = (time.perf_counter() - t0) * 1000
            self._record(latency, True, code=0)
            logger.debug("MQTT5 publish → %s (%d bytes)", topic, len(payload))
            return {"ok": True, "status_code": None, "body": None, "error": None}
        except Exception as exc:
            latency = (time.perf_counter() - t0) * 1000
            self._record(latency, False, err=str(exc))
            logger.warning("MQTT5 publish failed [%s]: %s", device_id, exc)
            return {"ok": False, "status_code": None, "body": None, "error": str(exc)}

    async def _mock_publish(self, payload: str, device_id: str = "") -> SendResult:
        """Latency-realistic mock when no cert / SDK available."""
        t0 = time.perf_counter()
        await asyncio.sleep(random.uniform(0.001, 0.008))
        latency = (time.perf_counter() - t0) * 1000
        ok = random.random() > 0.005
        self._record(latency, ok, code=0 if ok else None)
        logger.debug("MQTT mock → %s/%s (%d bytes)", self._topic_prefix, device_id, len(payload))
        return {"ok": ok, "status_code": None, "body": None, "error": None}

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

    async def send_event(self, event: TelemetryEvent) -> SendResult:
        return await self._mock_send(event.model_dump_json())

    async def send_batch(self, batch: BatchPayload) -> SendResult:
        return await self._mock_send(batch.model_dump_json())

    async def _mock_send(self, payload: str) -> SendResult:
        t0 = time.perf_counter()
        await asyncio.sleep(random.uniform(0.002, 0.012))
        latency = (time.perf_counter() - t0) * 1000
        ok = random.random() > 0.003
        self._record(latency, ok)
        logger.debug("WS mock send → %s (%d bytes) ok=%s", self.endpoint.url, len(payload), ok)
        return {"ok": ok, "status_code": None, "body": None, "error": None}


# ─── gRPC mock ────────────────────────────────────────────────────────────────


class GRPCMockTransport(BaseTransport):
    """
    Simulates gRPC unary call without a real server.
    Serialises event to JSON (production would use protobuf).
    Latency 3–15 ms to reflect HTTP/2 + serialisation overhead.
    """

    protocol = TransportProtocol.GRPC

    async def send_event(self, event: TelemetryEvent) -> SendResult:
        return await self._mock_call(event.model_dump_json(), "IngestEvent")

    async def send_batch(self, batch: BatchPayload) -> SendResult:
        return await self._mock_call(batch.model_dump_json(), "IngestBatch")

    async def _mock_call(self, payload: str, method: str) -> SendResult:
        t0 = time.perf_counter()
        await asyncio.sleep(random.uniform(0.003, 0.015))
        latency = (time.perf_counter() - t0) * 1000
        ok = random.random() > 0.002
        # Simulate gRPC status codes: 0=OK, 14=UNAVAILABLE
        code = 0 if ok else 14
        self._record(latency, ok, code=code)
        logger.debug(
            "gRPC mock %s → %s (%d bytes) status=%d", method, self.endpoint.url, len(payload), code
        )
        return {"ok": ok, "status_code": code, "body": None, "error": None}


# ─── Local plain-MQTT transport ──────────────────────────────────────────────


class LocalMqttTransport(BaseTransport):
    """Plain MQTT (no TLS) transport for local brokers such as Mosquitto.

    Used when the broker URL does not match the AWS IoT Core hostname pattern.
    Publishes each event as a JSON payload to `<topic_prefix>/<device_id>`.
    """

    protocol = TransportProtocol.MQTT

    def __init__(self, endpoint: EndpointConfig):
        super().__init__(endpoint)
        parsed = urlparse(endpoint.url)
        self._host = parsed.hostname or "localhost"
        self._port = parsed.port or 1883
        self._topic_prefix = os.getenv("DEFAULT_MQTT_TOPIC", "health/telemetry")

    async def send_event(self, event: TelemetryEvent) -> SendResult:
        return await self._publish(event.model_dump_json(), event.device_id)

    async def send_batch(self, batch: BatchPayload) -> SendResult:
        results = [await self.send_event(ev) for ev in batch.events]
        ok = all(r.get("ok", False) for r in results)
        return {"ok": ok, "status_code": None, "body": None, "error": None}

    async def _publish(self, payload: str, device_id: str) -> SendResult:
        t0 = time.perf_counter()
        try:
            import aiomqtt

            topic = f"{self._topic_prefix}/{device_id}"
            async with aiomqtt.Client(hostname=self._host, port=self._port) as client:
                await client.publish(topic, payload.encode(), qos=1)
            latency = (time.perf_counter() - t0) * 1000
            self._record(latency, True)
            logger.debug("MQTT publish → %s (%d bytes)", topic, len(payload))
            return {"ok": True, "status_code": None, "body": None, "error": None}
        except Exception as exc:
            latency = (time.perf_counter() - t0) * 1000
            self._record(latency, False, err=str(exc))
            logger.warning("MQTT publish failed [%s]: %s", device_id, exc)
            return {"ok": False, "status_code": None, "body": None, "error": str(exc)}


# ─── Factory ──────────────────────────────────────────────────────────────────

_AWS_IOT_HOST_PATTERN = ".iot."


def make_transport(endpoint: EndpointConfig) -> BaseTransport:
    if endpoint.protocol == TransportProtocol.MQTT:
        parsed = urlparse(endpoint.url)
        host = parsed.hostname or ""
        # Route to AWS IoT transport only for real AWS IoT Core endpoints
        cls = (
            AWSIoTMQTTTransport
            if _AWS_IOT_HOST_PATTERN in host and "amazonaws.com" in host
            else LocalMqttTransport
        )
        return cls(endpoint)

    mapping = {
        TransportProtocol.HTTP: HTTPTransport,
        TransportProtocol.WEBSOCKET: WebSocketMockTransport,
        TransportProtocol.GRPC: GRPCMockTransport,
    }
    cls = mapping.get(endpoint.protocol)
    if cls is None:
        raise ValueError(f"Unknown protocol: {endpoint.protocol}")
    return cls(endpoint)

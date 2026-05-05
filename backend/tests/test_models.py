"""Unit tests for Pydantic telemetry models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.telemetry import (
    BatchPayload,
    DeviceProfile,
    DeviceType,
    EndpointConfig,
    EndpointMode,
    HeartRateMetrics,
    ScenarioType,
    SendMode,
    SessionConfig,
    TelemetryEvent,
    TransportProtocol,
)


class TestHeartRateMetrics:
    def test_valid(self):
        m = HeartRateMetrics(bpm=72)
        assert m.bpm == 72
        assert m.hrv_ms is None

    def test_with_hrv(self):
        m = HeartRateMetrics(bpm=80, hrv_ms=45.3)
        assert m.hrv_ms == pytest.approx(45.3)

    def test_invalid_type(self):
        with pytest.raises(ValidationError):
            HeartRateMetrics(bpm="not-a-number")


class TestTelemetryEvent:
    def _make(self, **kwargs) -> TelemetryEvent:
        defaults = dict(
            device_id="dev-001",
            device_type=DeviceType.SMARTWATCH,
            user_id="user-001",
            scenario=ScenarioType.WORKOUT,
            protocol=TransportProtocol.HTTP,
        )
        return TelemetryEvent(**{**defaults, **kwargs})

    def test_defaults(self):
        ev = self._make()
        assert ev.event_id  # auto-generated UUID
        assert ev.timestamp  # auto-generated ISO timestamp
        assert ev.is_anomaly is False
        assert ev.firmware_version == "1.0.0"

    def test_event_ids_are_unique(self):
        a = self._make()
        b = self._make()
        assert a.event_id != b.event_id

    def test_anomaly_flag(self):
        ev = self._make(is_anomaly=True)
        assert ev.is_anomaly is True

    def test_with_heart_rate(self):
        ev = self._make(heart_rate=HeartRateMetrics(bpm=150, hrv_ms=30.0))
        assert ev.heart_rate is not None
        assert ev.heart_rate.bpm == 150

    def test_invalid_device_type(self):
        with pytest.raises(ValidationError):
            self._make(device_type="toaster")

    def test_invalid_protocol(self):
        with pytest.raises(ValidationError):
            self._make(protocol="carrier_pigeon")


class TestEndpointConfig:
    def test_defaults(self):
        ep = EndpointConfig(
            name="ingestion",
            url="http://example.com/ingest",
            protocol=TransportProtocol.HTTP,
        )
        assert ep.enabled is True
        assert ep.headers == {}

    def test_custom_headers(self):
        ep = EndpointConfig(
            name="secure",
            url="http://example.com/ingest",
            protocol=TransportProtocol.HTTP,
            headers={"X-API-Key": "secret"},
        )
        assert ep.headers["X-API-Key"] == "secret"


class TestSessionConfig:
    def _make(self, **kwargs) -> SessionConfig:
        defaults = dict(
            devices=[DeviceProfile(device_type=DeviceType.SMARTWATCH, count=5)],
            scenario=ScenarioType.RANDOM,
            send_mode=SendMode.IMMEDIATE,
            endpoints=[
                EndpointConfig(
                    name="test",
                    url="http://localhost/ingest",
                    protocol=TransportProtocol.HTTP,
                )
            ],
            protocols=[TransportProtocol.HTTP],
        )
        return SessionConfig(**{**defaults, **kwargs})

    def test_defaults(self):
        cfg = self._make()
        assert cfg.session_id  # auto-generated
        assert cfg.batch_size == 10
        assert cfg.interval_seconds == 5.0
        assert cfg.endpoint_mode == EndpointMode.FANOUT

    def test_total_events_none_by_default(self):
        cfg = self._make()
        assert cfg.total_events is None

    def test_anomaly_rate_bounds(self):
        cfg = self._make(anomaly_rate=0.0)
        assert cfg.anomaly_rate == 0.0
        cfg2 = self._make(anomaly_rate=1.0)
        assert cfg2.anomaly_rate == 1.0


class TestBatchPayload:
    def test_event_count_matches(self):
        events = [
            TelemetryEvent(
                device_id=f"dev-{i:03d}",
                device_type=DeviceType.SMARTPHONE,
                user_id=f"user-{i:03d}",
                scenario=ScenarioType.REST,
                protocol=TransportProtocol.HTTP,
            )
            for i in range(3)
        ]
        batch = BatchPayload(event_count=3, events=events)
        assert batch.event_count == 3
        assert len(batch.events) == 3
        assert batch.batch_id  # auto-generated

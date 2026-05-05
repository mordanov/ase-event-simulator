"""Unit tests for transport sender utilities."""

from __future__ import annotations

from app.models.telemetry import RegistrationEvent
from app.transports.sender import build_registration_payload


class TestBuildRegistrationPayload:
    def _event(self, **kwargs) -> RegistrationEvent:
        defaults = dict(device_id="dev-abc", status="registered", message="ok")
        return RegistrationEvent(**{**defaults, **kwargs})

    def test_required_fields_always_present(self):
        payload = build_registration_payload(self._event())
        assert payload["device_id"] == "dev-abc"

    def test_none_values_excluded(self):
        payload = build_registration_payload(self._event())
        # Biometric fields that are None should be stripped
        assert "height_cm" not in payload
        assert "weight_kg" not in payload

    def test_fallback_defaults_applied(self):
        # model/firmware/os/user_id fall back to sensible defaults when not set
        payload = build_registration_payload(self._event())
        assert payload["model"] == "SimDevice"
        assert payload["firmware_version"] == "1.0.0"
        assert payload["os"] == "SimOS"
        assert payload["user_id"] == "dev-abc"  # falls back to device_id

    def test_optional_fields_included_when_set(self):
        ev = self._event(
            device_type="smartwatch",
            model="SimWatch Pro",
            firmware_version="2.1.0",
            os="WatchOS-Sim 4.0",
            user_id="user-xyz",
            height_cm=175.0,
            weight_kg=72.5,
        )
        payload = build_registration_payload(ev)
        assert payload["device_type"] == "smartwatch"
        assert payload["model"] == "SimWatch Pro"
        assert payload["firmware_version"] == "2.1.0"
        assert payload["os"] == "WatchOS-Sim 4.0"
        assert payload["user_id"] == "user-xyz"
        assert payload["height_cm"] == 175.0
        assert payload["weight_kg"] == 72.5

    def test_event_type_not_in_payload(self):
        # The registration payload sent to the ingestion API should not include
        # internal simulation fields like event_type or status.
        payload = build_registration_payload(self._event())
        assert "event_type" not in payload
        assert "status" not in payload
        assert "message" not in payload

"""Pydantic models for telemetry payloads, sessions, and API request/response shapes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field

JsonMap = dict[str, object]
EndpointResponse = dict[str, object]


def _iso_timestamp() -> str:
    """Return an ISO-8601 UTC timestamp string."""
    return datetime.now(UTC).isoformat()


def _uuid_str() -> str:
    """Return a random UUID string."""
    return str(uuid.uuid4())


# ─── Enums ────────────────────────────────────────────────────────────────────


class DeviceType(str, Enum):
    SMARTWATCH = "smartwatch"
    FITNESS_TRACKER = "fitness_tracker"
    SMARTPHONE = "smartphone"
    LAPTOP = "laptop"
    RANDOM = "random"


class TransportProtocol(str, Enum):
    HTTP = "http"
    MQTT = "mqtt"
    WEBSOCKET = "websocket"
    GRPC = "grpc"


class ScenarioType(str, Enum):
    WORKOUT = "workout"
    SLEEP = "sleep"
    REST = "rest"
    EMERGENCY = "emergency"
    RANDOM = "random"


class SendMode(str, Enum):
    IMMEDIATE = "immediate"
    BATCH = "batch"


class EndpointMode(str, Enum):
    FANOUT = "fanout"  # same event to every endpoint (dedup by event_id)
    ROUND_ROBIN = "round_robin"  # distribute events evenly across endpoints


# ─── Metrics per device capability ────────────────────────────────────────────


class HeartRateMetrics(BaseModel):
    bpm: int
    hrv_ms: float | None = None  # Heart Rate Variability


class StepsMetrics(BaseModel):
    count: int
    distance_m: float
    calories_kcal: float


class SpO2Metrics(BaseModel):
    percentage: float


class SleepMetrics(BaseModel):
    duration_minutes: int
    deep_sleep_minutes: int
    rem_sleep_minutes: int
    sleep_score: int  # 0-100


class BloodPressureMetrics(BaseModel):
    systolic_mmhg: int
    diastolic_mmhg: int


class TemperatureMetrics(BaseModel):
    celsius: float


class GPSMetrics(BaseModel):
    latitude: float
    longitude: float
    altitude_m: float
    accuracy_m: float


class StressMetrics(BaseModel):
    score: int  # 0-100


class HydrationMetrics(BaseModel):
    level_percent: float


# ─── Core telemetry event ─────────────────────────────────────────────────────


class RegistrationEvent(BaseModel):
    """Emitted during the JITR registration phase (not telemetry)."""

    event_type: str = "registration"
    device_id: str
    status: str  # pending | rejected | registered
    message: str
    timestamp: str = Field(default_factory=_iso_timestamp)
    # Device profile — required by POST /api/v1/devices on the ingestion pipeline.
    device_type: str | None = None
    model: str | None = None
    firmware_version: str | None = None
    os: str | None = None
    user_id: str | None = None
    # User biometrics — included so downstream services (e.g. recommendation
    # aggregator) receive the profile data needed to call health-tip providers.
    height_cm: float | None = None
    weight_kg: float | None = None
    gender: str | None = None
    birth_date: str | None = None  # YYYY-MM-DD
    # Request/response inspection — populated after the event is sent to endpoints.
    request_payload: JsonMap | None = None
    endpoint_responses: list[EndpointResponse] | None = None


class TelemetryEvent(BaseModel):
    event_id: str = Field(default_factory=_uuid_str)
    device_id: str
    device_type: DeviceType
    user_id: str
    timestamp: str = Field(default_factory=_iso_timestamp)
    scenario: ScenarioType
    is_anomaly: bool = False
    protocol: TransportProtocol
    cert_fingerprint: str | None = None  # SHA-256 of device X.509 cert

    # Metrics (device-type-dependent)
    heart_rate: HeartRateMetrics | None = None
    steps: StepsMetrics | None = None
    spo2: SpO2Metrics | None = None
    sleep: SleepMetrics | None = None
    blood_pressure: BloodPressureMetrics | None = None
    temperature: TemperatureMetrics | None = None
    gps: GPSMetrics | None = None
    stress: StressMetrics | None = None
    hydration: HydrationMetrics | None = None
    battery_pct: int | None = None
    firmware_version: str = "1.0.0"


class BatchPayload(BaseModel):
    batch_id: str = Field(default_factory=_uuid_str)
    sent_at: str = Field(default_factory=_iso_timestamp)
    event_count: int
    events: list[TelemetryEvent]


# ─── Session / config models ──────────────────────────────────────────────────


class EndpointConfig(BaseModel):
    name: str
    url: str
    protocol: TransportProtocol
    enabled: bool = True
    headers: dict[str, str] = Field(default_factory=dict)


class DeviceProfile(BaseModel):
    device_type: DeviceType
    count: int = 1


class SessionConfig(BaseModel):
    session_id: str = Field(default_factory=_uuid_str)
    devices: list[DeviceProfile]
    scenario: ScenarioType
    send_mode: SendMode
    endpoint_mode: EndpointMode = EndpointMode.FANOUT
    batch_size: int = 10  # events per batch
    interval_seconds: float = 5.0
    anomaly_rate: float = 0.1  # 0.0 – 1.0
    total_events: int | None = None  # None = run until stopped
    endpoints: list[EndpointConfig]
    protocols: list[TransportProtocol]
    recommendation_handicap: int = 500  # call recommendations when balance >= this (0 = disabled)


# ─── Runtime status ───────────────────────────────────────────────────────────


class EndpointError(BaseModel):
    timestamp: str
    message: str
    status_code: int | None = None


class EndpointStatus(BaseModel):
    name: str
    url: str
    protocol: TransportProtocol
    success_count: int = 0
    error_count: int = 0
    last_status_code: int | None = None
    last_error: str | None = None
    avg_latency_ms: float = 0.0
    recent_errors: list[EndpointError] = Field(default_factory=list)


class ActivityEvent(BaseModel):
    """Unified log entry for every backend call made during a session."""

    timestamp: str
    event_type: (
        str  # workout|sleep|rest|emergency|random|registration|authorisation|rewards|recommendation
    )
    device_id: str
    status: str  # ok|error|anomaly|registered|rejected|pending
    data: JsonMap = Field(default_factory=dict)


class RecommendationLog(BaseModel):
    timestamp: str
    device_id: str
    reward_tier: str
    balance_before: int
    balance_after: int
    credits_spent: int
    request: JsonMap
    response: JsonMap | None = None
    error: str | None = None


class SessionStatus(BaseModel):
    session_id: str
    running: bool
    events_generated: int = 0
    events_sent: int = 0
    events_failed: int = 0
    batches_sent: int = 0
    anomalies_generated: int = 0
    started_at: str | None = None
    elapsed_seconds: float = 0.0
    endpoints: list[EndpointStatus] = Field(default_factory=list)
    recent_events: list[TelemetryEvent] = Field(default_factory=list)
    # Registration phase
    registration_phase: bool = False
    devices_total: int = 0
    devices_registered: int = 0
    devices_pending: int = 0
    registration_log: list[RegistrationEvent] = Field(default_factory=list)
    recommendation_log: list[RecommendationLog] = Field(default_factory=list)
    activity_log: list[ActivityEvent] = Field(default_factory=list)


# ─── API request/response shapes ─────────────────────────────────────────────


class StartSessionRequest(BaseModel):
    config: SessionConfig


class StartSessionResponse(BaseModel):
    session_id: str
    message: str


class StopSessionResponse(BaseModel):
    session_id: str
    message: str
    final_stats: SessionStatus


# ─── Device registry ──────────────────────────────────────────────────────────


class DeviceRecord(BaseModel):
    id: int
    device_id: str
    device_type: DeviceType
    user_id: str
    firmware_version: str
    gps_lat: float | None = None
    gps_lon: float | None = None
    is_active: bool
    created_at: str


class DeviceTypeStats(BaseModel):
    device_type: DeviceType
    count: int


class DeviceStats(BaseModel):
    total: int
    by_type: dict[str, int]


class SeedDevicesRequest(BaseModel):
    device_type: DeviceType
    count: int = Field(ge=1, le=1000)


class SeedDevicesResponse(BaseModel):
    created: int
    total: int
    message: str

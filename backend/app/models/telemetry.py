from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


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
    FANOUT = "fanout"          # same event to every endpoint (dedup by event_id)
    ROUND_ROBIN = "round_robin"  # distribute events evenly across endpoints


# ─── Metrics per device capability ────────────────────────────────────────────

class HeartRateMetrics(BaseModel):
    bpm: int
    hrv_ms: Optional[float] = None  # Heart Rate Variability


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
    status: str   # pending | rejected | registered
    message: str
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    # Device profile — required by POST /api/v1/devices on the ingestion pipeline.
    device_type: Optional[str] = None
    model: Optional[str] = None
    firmware_version: Optional[str] = None
    os: Optional[str] = None
    user_id: Optional[str] = None
    # User biometrics — included so downstream services (e.g. recommendation
    # aggregator) receive the profile data needed to call health-tip providers.
    height_cm: Optional[float] = None
    weight_kg: Optional[float] = None
    gender: Optional[str] = None
    birth_date: Optional[str] = None  # YYYY-MM-DD
    # Request/response inspection — populated after the event is sent to endpoints.
    request_payload: Optional[dict] = None
    endpoint_responses: Optional[list[dict]] = None  # [{"name", "url", "status_code", "body"}]


class TelemetryEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    device_id: str
    device_type: DeviceType
    user_id: str
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    scenario: ScenarioType
    is_anomaly: bool = False
    protocol: TransportProtocol
    cert_fingerprint: Optional[str] = None  # SHA-256 of device X.509 cert

    # Metrics (device-type-dependent)
    heart_rate: Optional[HeartRateMetrics] = None
    steps: Optional[StepsMetrics] = None
    spo2: Optional[SpO2Metrics] = None
    sleep: Optional[SleepMetrics] = None
    blood_pressure: Optional[BloodPressureMetrics] = None
    temperature: Optional[TemperatureMetrics] = None
    gps: Optional[GPSMetrics] = None
    stress: Optional[StressMetrics] = None
    hydration: Optional[HydrationMetrics] = None
    battery_pct: Optional[int] = None
    firmware_version: str = "1.0.0"


class BatchPayload(BaseModel):
    batch_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    sent_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
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
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    devices: list[DeviceProfile]
    scenario: ScenarioType
    send_mode: SendMode
    endpoint_mode: EndpointMode = EndpointMode.FANOUT
    batch_size: int = 10          # events per batch
    interval_seconds: float = 5.0
    anomaly_rate: float = 0.1     # 0.0 – 1.0
    total_events: Optional[int] = None  # None = run until stopped
    endpoints: list[EndpointConfig]
    protocols: list[TransportProtocol]


# ─── Runtime status ───────────────────────────────────────────────────────────

class EndpointError(BaseModel):
    timestamp: str
    message: str
    status_code: Optional[int] = None


class EndpointStatus(BaseModel):
    name: str
    url: str
    protocol: TransportProtocol
    success_count: int = 0
    error_count: int = 0
    last_status_code: Optional[int] = None
    last_error: Optional[str] = None
    avg_latency_ms: float = 0.0
    recent_errors: list[EndpointError] = Field(default_factory=list)


class SessionStatus(BaseModel):
    session_id: str
    running: bool
    events_generated: int = 0
    events_sent: int = 0
    events_failed: int = 0
    batches_sent: int = 0
    anomalies_generated: int = 0
    started_at: Optional[str] = None
    elapsed_seconds: float = 0.0
    endpoints: list[EndpointStatus] = Field(default_factory=list)
    recent_events: list[TelemetryEvent] = Field(default_factory=list)
    # Registration phase
    registration_phase: bool = False
    devices_total: int = 0
    devices_registered: int = 0
    devices_pending: int = 0
    registration_log: list[RegistrationEvent] = Field(default_factory=list)


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
    gps_lat: Optional[float] = None
    gps_lon: Optional[float] = None
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

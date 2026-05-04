"""
Telemetry data generators.
Each device type has a capability profile — only the metrics that
device physically supports are populated.
Scenarios shape the *range* of generated values.
"""
from __future__ import annotations

import random
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.models.telemetry import (
    BatchPayload,
    BloodPressureMetrics,
    DeviceProfile,
    DeviceType,
    GPSMetrics,
    HeartRateMetrics,
    HydrationMetrics,
    ScenarioType,
    SleepMetrics,
    SpO2Metrics,
    StepsMetrics,
    StressMetrics,
    TelemetryEvent,
    TemperatureMetrics,
    TransportProtocol,
)


# ─── Scenario value ranges ────────────────────────────────────────────────────

SCENARIO_RANGES: dict[ScenarioType, dict] = {
    ScenarioType.WORKOUT: {
        "heart_rate": (120, 175),
        "hrv_ms": (20, 50),
        "spo2": (96, 99),
        "stress": (40, 70),
        "steps_per_event": (200, 800),
        "calories_per_step": 0.055,
        "temperature": (36.8, 37.8),
        "systolic": (130, 155),
        "diastolic": (80, 95),
        "hydration": (55, 75),
    },
    ScenarioType.SLEEP: {
        "heart_rate": (45, 65),
        "hrv_ms": (60, 120),
        "spo2": (95, 99),
        "stress": (5, 20),
        "steps_per_event": (0, 10),
        "calories_per_step": 0.04,
        "temperature": (36.0, 36.6),
        "systolic": (100, 120),
        "diastolic": (60, 75),
        "hydration": (60, 80),
    },
    ScenarioType.REST: {
        "heart_rate": (60, 85),
        "hrv_ms": (40, 80),
        "spo2": (97, 100),
        "stress": (10, 35),
        "steps_per_event": (0, 100),
        "calories_per_step": 0.04,
        "temperature": (36.3, 36.9),
        "systolic": (110, 130),
        "diastolic": (65, 85),
        "hydration": (65, 85),
    },
    ScenarioType.EMERGENCY: {
        "heart_rate": (35, 220),     # wide — can be tachycardia or bradycardia
        "hrv_ms": (5, 15),
        "spo2": (80, 94),            # dangerously low
        "stress": (80, 100),
        "steps_per_event": (0, 50),
        "calories_per_step": 0.04,
        "temperature": (38.5, 41.0), # fever / hyperthermia
        "systolic": (80, 200),
        "diastolic": (50, 120),
        "hydration": (20, 45),       # severely dehydrated
    },
    ScenarioType.RANDOM: {
        "heart_rate": (45, 195),
        "hrv_ms": (15, 120),
        "spo2": (88, 100),
        "stress": (0, 100),
        "steps_per_event": (0, 900),
        "calories_per_step": 0.05,
        "temperature": (35.5, 40.0),
        "systolic": (85, 195),
        "diastolic": (50, 115),
        "hydration": (20, 90),
    },
}

# ─── Device capability map ────────────────────────────────────────────────────

DEVICE_CAPABILITIES: dict[DeviceType, set[str]] = {
    DeviceType.SMARTWATCH: {
        "heart_rate", "hrv", "steps", "spo2",
        "stress", "temperature", "gps", "hydration", "battery",
    },
    DeviceType.FITNESS_TRACKER: {
        "heart_rate", "steps", "spo2",
        "sleep", "calories", "battery",
    },
    DeviceType.SMARTPHONE: {
        "steps", "gps", "stress", "battery",
    },
    DeviceType.LAPTOP: {
        "stress",
    },
}

FIRMWARE_VERSIONS: dict[DeviceType, list[str]] = {
    DeviceType.SMARTWATCH:      ["2.1.0", "2.2.3", "3.0.0"],
    DeviceType.FITNESS_TRACKER: ["1.5.1", "1.6.0"],
    DeviceType.SMARTPHONE:      ["14.0", "14.4", "15.0"],
    DeviceType.LAPTOP:          ["11.0", "12.0"],
}

# Approximate GPS bounding box (Europe + NA)
GPS_BOUNDS = {
    "lat": (40.0, 55.0),
    "lon": (-10.0, 40.0),
    "alt": (0.0, 2500.0),
}


def _ri(lo: float, hi: float) -> int:
    return random.randint(int(lo), int(hi))


def _rf(lo: float, hi: float, decimals: int = 2) -> float:
    return round(random.uniform(lo, hi), decimals)


class TelemetryGenerator:
    """
    Generates realistic TelemetryEvent objects for any device type + scenario.
    Each generator instance owns a pool of virtual devices with stable IDs.

    Pass ``preloaded_devices`` (list of dicts with keys: device_id, user_id,
    device_type, firmware, gps_lat, gps_lon) to use DB-backed device records
    instead of generating ephemeral IDs.
    """

    def __init__(
        self,
        device_profiles: list[DeviceProfile] | None = None,
        preloaded_devices: list[dict] | None = None,
        anomaly_rate: float = 0.1,
    ):
        self.anomaly_rate = anomaly_rate
        self._devices: list[dict] = []

        if preloaded_devices:
            self._devices = list(preloaded_devices)
        elif device_profiles:
            _concrete = [
                DeviceType.SMARTWATCH, DeviceType.FITNESS_TRACKER,
                DeviceType.SMARTPHONE, DeviceType.LAPTOP,
            ]
            for profile in device_profiles:
                for _ in range(profile.count):
                    d_type = (
                        random.choice(_concrete)
                        if profile.device_type == DeviceType.RANDOM
                        else profile.device_type
                    )
                    self._devices.append({
                        "device_id": f"{d_type.value}-{uuid.uuid4().hex[:8]}",
                        "user_id":   f"user-{uuid.uuid4().hex[:6]}",
                        "device_type": d_type,
                        "firmware": random.choice(FIRMWARE_VERSIONS[d_type]),
                        "gps_lat": _rf(*GPS_BOUNDS["lat"], 5),
                        "gps_lon": _rf(*GPS_BOUNDS["lon"], 5),
                    })

    @property
    def device_count(self) -> int:
        return len(self._devices)

    @property
    def devices(self) -> list[dict]:
        return self._devices

    # ── public API ──────────────────────────────────────────────────────────

    def generate_event(
        self,
        scenario: ScenarioType,
        protocol: TransportProtocol,
        device_index: Optional[int] = None,
        force_anomaly: bool = False,
    ) -> TelemetryEvent:
        device = (
            self._devices[device_index % len(self._devices)]
            if device_index is not None
            else random.choice(self._devices)
        )
        is_anomaly = force_anomaly or (random.random() < self.anomaly_rate)
        # Use emergency ranges for anomalous events, but keep the configured scenario label.
        # is_anomaly already signals the anomaly; overriding scenario creates mixed event types
        # in a single session (e.g. a "random" session emitting "emergency" events).
        data_scenario = ScenarioType.EMERGENCY if is_anomaly else scenario

        caps = DEVICE_CAPABILITIES[device["device_type"]]
        ranges = SCENARIO_RANGES[data_scenario]

        return TelemetryEvent(
            device_id=device["device_id"],
            device_type=device["device_type"],
            user_id=device["user_id"],
            timestamp=datetime.now(timezone.utc).isoformat(),
            scenario=scenario,
            is_anomaly=is_anomaly,
            protocol=protocol,
            firmware_version=device["firmware"],
            cert_fingerprint=device.get("cert_fingerprint") or None,
            heart_rate=self._heart_rate(ranges, caps),
            steps=self._steps(ranges, caps),
            spo2=self._spo2(ranges, caps),
            sleep=self._sleep(ranges, caps, data_scenario),
            blood_pressure=self._bp(ranges, caps),
            temperature=self._temperature(ranges, caps),
            gps=self._gps(caps, device),
            stress=self._stress(ranges, caps),
            hydration=self._hydration(ranges, caps),
            battery_pct=_ri(5, 100) if "battery" in caps else None,
        )

    def generate_batch(
        self,
        scenario: ScenarioType,
        protocol: TransportProtocol,
    ) -> BatchPayload:
        """Generate one event per device (use session manager to chunk by batch_size)."""
        events = [
            self.generate_event(scenario, protocol, device_index=i)
            for i in range(self.device_count)
        ]
        return BatchPayload(event_count=len(events), events=events)

    # ── private metric builders ──────────────────────────────────────────────

    def _heart_rate(self, r: dict, caps: set) -> Optional[HeartRateMetrics]:
        if "heart_rate" not in caps:
            return None
        return HeartRateMetrics(
            bpm=_ri(*r["heart_rate"]),
            hrv_ms=_rf(*r["hrv_ms"]) if "hrv" in caps else None,
        )

    def _steps(self, r: dict, caps: set) -> Optional[StepsMetrics]:
        if "steps" not in caps:
            return None
        count = _ri(*r["steps_per_event"])
        dist = round(count * 0.762, 2)  # avg stride ~76.2 cm
        cal = round(count * r["calories_per_step"], 2)
        return StepsMetrics(count=count, distance_m=dist, calories_kcal=cal)

    def _spo2(self, r: dict, caps: set) -> Optional[SpO2Metrics]:
        if "spo2" not in caps:
            return None
        return SpO2Metrics(percentage=_rf(*r["spo2"], 1))

    def _sleep(self, r: dict, caps: set, scenario: ScenarioType) -> Optional[SleepMetrics]:
        if "sleep" not in caps or scenario not in (ScenarioType.SLEEP, ScenarioType.RANDOM):
            return None
        total = _ri(240, 540)
        deep = _ri(60, min(120, total // 3))
        rem = _ri(60, min(100, total // 4))
        score = _ri(40, 100) if scenario != ScenarioType.EMERGENCY else _ri(10, 40)
        return SleepMetrics(
            duration_minutes=total,
            deep_sleep_minutes=deep,
            rem_sleep_minutes=rem,
            sleep_score=score,
        )

    def _bp(self, r: dict, caps: set) -> Optional[BloodPressureMetrics]:
        if "heart_rate" not in caps:  # only devices with HR sensor get BP
            return None
        return BloodPressureMetrics(
            systolic_mmhg=_ri(*r["systolic"]),
            diastolic_mmhg=_ri(*r["diastolic"]),
        )

    def _temperature(self, r: dict, caps: set) -> Optional[TemperatureMetrics]:
        if "temperature" not in caps:
            return None
        return TemperatureMetrics(celsius=_rf(*r["temperature"], 1))

    def _gps(self, caps: set, device: dict) -> Optional[GPSMetrics]:
        if "gps" not in caps:
            return None
        jitter_lat = _rf(-0.005, 0.005, 6)
        jitter_lon = _rf(-0.005, 0.005, 6)
        return GPSMetrics(
            latitude=round(device["gps_lat"] + jitter_lat, 6),
            longitude=round(device["gps_lon"] + jitter_lon, 6),
            altitude_m=_rf(*GPS_BOUNDS["alt"], 1),
            accuracy_m=_rf(1.0, 15.0, 1),
        )

    def _stress(self, r: dict, caps: set) -> Optional[StressMetrics]:
        if "stress" not in caps:
            return None
        return StressMetrics(score=_ri(*r["stress"]))

    def _hydration(self, r: dict, caps: set) -> Optional[HydrationMetrics]:
        if "hydration" not in caps:
            return None
        return HydrationMetrics(level_percent=_rf(*r["hydration"], 1))

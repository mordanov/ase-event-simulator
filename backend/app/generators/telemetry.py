"""
Telemetry data generation utilities.

Each device type has a capability profile, so only supported metrics are populated.
Scenarios control the value ranges for generated telemetry.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime

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

MetricRanges = dict[str, tuple[float, float] | float]
DeviceRecord = dict[str, object]

# ─── Scenario value ranges ────────────────────────────────────────────────────

SCENARIO_RANGES: dict[ScenarioType, MetricRanges] = {
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
        "heart_rate": (35, 220),  # wide — can be tachycardia or bradycardia
        "hrv_ms": (5, 15),
        "spo2": (80, 94),  # dangerously low
        "stress": (80, 100),
        "steps_per_event": (0, 50),
        "calories_per_step": 0.04,
        "temperature": (38.5, 41.0),  # fever / hyperthermia
        "systolic": (80, 200),
        "diastolic": (50, 120),
        "hydration": (20, 45),  # severely dehydrated
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
        "heart_rate",
        "hrv",
        "steps",
        "spo2",
        "stress",
        "temperature",
        "gps",
        "hydration",
        "battery",
    },
    DeviceType.FITNESS_TRACKER: {
        "heart_rate",
        "steps",
        "spo2",
        "sleep",
        "calories",
        "battery",
    },
    DeviceType.SMARTPHONE: {
        "steps",
        "gps",
        "stress",
        "battery",
    },
    DeviceType.LAPTOP: {
        "stress",
    },
}

CONCRETE_DEVICE_TYPES = [
    DeviceType.SMARTWATCH,
    DeviceType.FITNESS_TRACKER,
    DeviceType.SMARTPHONE,
    DeviceType.LAPTOP,
]

FIRMWARE_VERSIONS: dict[DeviceType, list[str]] = {
    DeviceType.SMARTWATCH: ["2.1.0", "2.2.3", "3.0.0"],
    DeviceType.FITNESS_TRACKER: ["1.5.1", "1.6.0"],
    DeviceType.SMARTPHONE: ["14.0", "14.4", "15.0"],
    DeviceType.LAPTOP: ["11.0", "12.0"],
}

# Approximate GPS bounding box (Europe and North America).
GPS_BOUNDS = {
    "lat": (40.0, 55.0),
    "lon": (-10.0, 40.0),
    "alt": (0.0, 2500.0),
}


def _ri(lo: float, hi: float) -> int:
    return random.randint(int(lo), int(hi))


def _rf(lo: float, hi: float, decimals: int = 2) -> float:
    return round(random.uniform(lo, hi), decimals)


def _biased_ri(lo: float, hi: float, bias: float, noise: float = 0.18) -> int:
    """Triangular-sample an integer in [lo, hi] with mode driven by bias."""
    noisy = max(0.0, min(1.0, bias + random.gauss(0.0, noise)))
    return round(random.triangular(lo, hi, lo + (hi - lo) * noisy))


def _biased_rf(lo: float, hi: float, bias: float, decimals: int = 2, noise: float = 0.18) -> float:
    """Triangular-sample a float in [lo, hi] with mode driven by bias."""
    noisy = max(0.0, min(1.0, bias + random.gauss(0.0, noise)))
    return round(random.triangular(lo, hi, lo + (hi - lo) * noisy), decimals)


def _compute_health_bias(device: DeviceRecord) -> float:
    """
    Stable [0.0, 1.0] health bias for a device.
    0.0  → healthy end of every metric range
    1.0  → concerning end of every metric range

    Derived from biometrics (BMI, age) when available so DB-backed devices get
    a deterministic tendency. Ephemeral devices draw from Beta(0.4, 0.4) which
    is U-shaped — most land near 0 or 1, few cluster at 0.5.
    """
    components: list[float] = []

    weight = device.get("weight_kg")
    height = device.get("height_cm")
    if weight and height and height > 0:
        bmi = weight / (height / 100.0) ** 2
        # BMI 18.5 (healthy lower bound) → 0.0,  BMI 35 (obese) → 1.0
        components.append(max(0.0, min(1.0, (bmi - 18.5) / 16.5)))

    birth_date = device.get("birth_date")
    if birth_date:
        try:
            from datetime import date as _date

            age = _date.today().year - int(birth_date[:4])
            # Age 20 → 0.0,  age 75 → 1.0
            components.append(max(0.0, min(1.0, (age - 20) / 55.0)))
        except Exception:
            pass

    base = (sum(components) / len(components)) if components else random.betavariate(0.4, 0.4)
    # Small per-device jitter so identical biometrics still produce variety
    return max(0.0, min(1.0, base + random.gauss(0.0, 0.07)))


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
        preloaded_devices: list[DeviceRecord] | None = None,
        anomaly_rate: float = 0.1,
    ):
        self.anomaly_rate = anomaly_rate
        self._devices: list[DeviceRecord] = []

        if preloaded_devices:
            self._devices = list(preloaded_devices)
        elif device_profiles:
            for profile in device_profiles:
                for _ in range(profile.count):
                    d_type = self._resolve_device_type(profile.device_type)
                    self._devices.append(
                        {
                            "device_id": f"{d_type.value}-{uuid.uuid4().hex[:8]}",
                            "user_id": f"user-{uuid.uuid4().hex[:6]}",
                            "device_type": d_type,
                            "firmware": random.choice(FIRMWARE_VERSIONS[d_type]),
                            "gps_lat": _rf(*GPS_BOUNDS["lat"], 5),
                            "gps_lon": _rf(*GPS_BOUNDS["lon"], 5),
                        }
                    )

        # Assign a stable health bias to every device that doesn't have one yet.
        # DB-backed devices may carry biometrics; ephemeral devices draw from a
        # U-shaped Beta so they cluster near healthy (0) or concerning (1).
        for d in self._devices:
            if "health_bias" not in d:
                d["health_bias"] = _compute_health_bias(d)

    @property
    def device_count(self) -> int:
        return len(self._devices)

    @property
    def devices(self) -> list[DeviceRecord]:
        return self._devices

    def _resolve_device_type(self, requested_type: DeviceType) -> DeviceType:
        if requested_type == DeviceType.RANDOM:
            return random.choice(CONCRETE_DEVICE_TYPES)
        return requested_type

    # ── public API ──────────────────────────────────────────────────────────

    def generate_event(
        self,
        scenario: ScenarioType,
        protocol: TransportProtocol,
        device_index: int | None = None,
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

        # For EMERGENCY events use full-range uniform sampling — anything can happen.
        # For all other scenarios apply the device's stable health bias so each
        # device consistently leans healthy or concerning.
        bias: float | None = (
            None if data_scenario == ScenarioType.EMERGENCY else device.get("health_bias")
        )

        return TelemetryEvent(
            device_id=device["device_id"],
            device_type=device["device_type"],
            user_id=device["user_id"],
            timestamp=datetime.now(UTC).isoformat(),
            scenario=scenario,
            is_anomaly=is_anomaly,
            protocol=protocol,
            firmware_version=device["firmware"],
            cert_fingerprint=device.get("cert_fingerprint") or None,
            heart_rate=self._heart_rate(ranges, caps, bias),
            steps=self._steps(ranges, caps, bias),
            spo2=self._spo2(ranges, caps, bias),
            sleep=self._sleep(ranges, caps, data_scenario, bias),
            blood_pressure=self._bp(ranges, caps, bias),
            temperature=self._temperature(ranges, caps, bias),
            gps=self._gps(caps, device),
            stress=self._stress(ranges, caps, bias),
            hydration=self._hydration(ranges, caps, bias),
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

    def _heart_rate(self, r: dict, caps: set, bias: float | None) -> HeartRateMetrics | None:
        if "heart_rate" not in caps:
            return None
        if bias is not None:
            # High bias → high BPM (concerning); low HRV is also concerning → 1-bias
            bpm = _biased_ri(*r["heart_rate"], bias)
            hrv = _biased_rf(*r["hrv_ms"], 1.0 - bias) if "hrv" in caps else None
        else:
            bpm = _ri(*r["heart_rate"])
            hrv = _rf(*r["hrv_ms"]) if "hrv" in caps else None
        return HeartRateMetrics(bpm=bpm, hrv_ms=hrv)

    def _steps(self, r: dict, caps: set, bias: float | None) -> StepsMetrics | None:
        if "steps" not in caps:
            return None
        # High bias → fewer steps (sedentary / unhealthy)
        count = (
            _biased_ri(*r["steps_per_event"], 1.0 - bias)
            if bias is not None
            else _ri(*r["steps_per_event"])
        )
        dist = round(count * 0.762, 2)
        cal = round(count * r["calories_per_step"], 2)
        return StepsMetrics(count=count, distance_m=dist, calories_kcal=cal)

    def _spo2(self, r: dict, caps: set, bias: float | None) -> SpO2Metrics | None:
        if "spo2" not in caps:
            return None
        # High bias → low SpO2 (concerning)
        pct = _biased_rf(*r["spo2"], 1.0 - bias, 1) if bias is not None else _rf(*r["spo2"], 1)
        return SpO2Metrics(percentage=pct)

    def _sleep(
        self, r: dict, caps: set, scenario: ScenarioType, bias: float | None
    ) -> SleepMetrics | None:
        if "sleep" not in caps or scenario not in (ScenarioType.SLEEP, ScenarioType.RANDOM):
            return None
        total = _ri(240, 540)
        deep = _ri(60, min(120, total // 3))
        rem = _ri(60, min(100, total // 4))
        # High bias → low sleep score (poor sleep quality)
        if bias is not None:
            score = _biased_ri(40, 100, 1.0 - bias)
        else:
            score = _ri(10, 40)  # EMERGENCY
        return SleepMetrics(
            duration_minutes=total,
            deep_sleep_minutes=deep,
            rem_sleep_minutes=rem,
            sleep_score=score,
        )

    def _bp(self, r: dict, caps: set, bias: float | None) -> BloodPressureMetrics | None:
        if "heart_rate" not in caps:
            return None
        # High bias → high BP (concerning)
        if bias is not None:
            sys = _biased_ri(*r["systolic"], bias)
            dia = _biased_ri(*r["diastolic"], bias)
        else:
            sys = _ri(*r["systolic"])
            dia = _ri(*r["diastolic"])
        return BloodPressureMetrics(systolic_mmhg=sys, diastolic_mmhg=dia)

    def _temperature(self, r: dict, caps: set, bias: float | None) -> TemperatureMetrics | None:
        if "temperature" not in caps:
            return None
        # High bias → high temperature (fever / concerning)
        c = (
            _biased_rf(*r["temperature"], bias, 1)
            if bias is not None
            else _rf(*r["temperature"], 1)
        )
        return TemperatureMetrics(celsius=c)

    def _gps(self, caps: set, device: dict) -> GPSMetrics | None:
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

    def _stress(self, r: dict, caps: set, bias: float | None) -> StressMetrics | None:
        if "stress" not in caps:
            return None
        # High bias → high stress score (concerning)
        score = _biased_ri(*r["stress"], bias) if bias is not None else _ri(*r["stress"])
        return StressMetrics(score=score)

    def _hydration(self, r: dict, caps: set, bias: float | None) -> HydrationMetrics | None:
        if "hydration" not in caps:
            return None
        # High bias → low hydration (dehydrated / concerning)
        lvl = (
            _biased_rf(*r["hydration"], 1.0 - bias, 1)
            if bias is not None
            else _rf(*r["hydration"], 1)
        )
        return HydrationMetrics(level_percent=lvl)

"""
Idempotent seed script: populates the devices table if it is empty.

Usage (from /app directory inside the container or local venv):
    python -m scripts.seed_devices
    SEED_DEVICE_COUNT=500 python -m scripts.seed_devices
"""
from __future__ import annotations

import os
import random
import uuid

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from app.env_loader import bootstrap_environment

bootstrap_environment()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SEED_COUNT = int(os.getenv("SEED_DEVICE_COUNT", "1000"))

_raw_url = os.getenv("DATABASE_URL", "sqlite+aiosqlite:////app/simulator.db")
DATABASE_URL = _raw_url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg2")

# Device type distribution (must sum to 1.0)
TYPE_DISTRIBUTION = {
    "smartwatch":      0.35,
    "fitness_tracker": 0.30,
    "smartphone":      0.25,
    "laptop":          0.10,
}

FIRMWARE_VERSIONS = {
    "smartwatch":      ["2.1.0", "2.2.3", "3.0.0"],
    "fitness_tracker": ["1.5.1", "1.6.0"],
    "smartphone":      ["14.0", "14.4", "15.0"],
    "laptop":          ["11.0", "12.0"],
}

DEVICE_MODELS = {
    "smartwatch":      "SimWatch Pro",
    "fitness_tracker": "SimBand Ultra",
    "smartphone":      "SimPhone X",
    "laptop":          "SimBook Air",
}

DEVICE_OS = {
    "smartwatch":      "WatchOS-Sim 4.0",
    "fitness_tracker": "FitOS-Sim 2.1",
    "smartphone":      "AndroidOS-Sim 14",
    "laptop":          "SimOS 15.0",
}

GPS_CAPABLE = {"smartwatch", "smartphone"}
GPS_BOUNDS = {"lat": (40.0, 55.0), "lon": (-10.0, 40.0)}

GENDERS = ["male", "female"]
HEIGHT_RANGE = {"male": (165.0, 195.0), "female": (155.0, 180.0)}
WEIGHT_RANGE = {"male": (65.0, 100.0), "female": (50.0,  85.0)}
BIRTH_YEAR_RANGE = (1955, 2005)


def _rf(lo: float, hi: float, decimals: int = 5) -> float:
    return round(random.uniform(lo, hi), decimals)


def _make_user_pool(size: int) -> list[str]:
    return [f"user-{uuid.uuid4().hex[:6]}" for _ in range(size)]


def _make_device(device_type: str, user_id: str) -> dict:
    short = uuid.uuid4().hex[:8]
    has_gps = device_type in GPS_CAPABLE
    gender = random.choice(GENDERS)
    birth_year = random.randint(*BIRTH_YEAR_RANGE)
    birth_month = random.randint(1, 12)
    birth_day = random.randint(1, 28)
    return {
        "device_id": f"{device_type}-{short}",
        "device_type": device_type,
        "model": DEVICE_MODELS[device_type],
        "os": DEVICE_OS[device_type],
        "user_id": user_id,
        "firmware_version": random.choice(FIRMWARE_VERSIONS[device_type]),
        "gps_lat": _rf(*GPS_BOUNDS["lat"]) if has_gps else None,
        "gps_lon": _rf(*GPS_BOUNDS["lon"]) if has_gps else None,
        "height_cm": round(random.uniform(*HEIGHT_RANGE[gender]), 1),
        "weight_kg": round(random.uniform(*WEIGHT_RANGE[gender]), 1),
        "gender": gender,
        "birth_date": f"{birth_year:04d}-{birth_month:02d}-{birth_day:02d}",
        "is_active": True,
    }


def seed(count: int = SEED_COUNT) -> int:
    engine = create_engine(DATABASE_URL, echo=False)

    # Defer import so this script works without FastAPI context
    from app.models.device_orm import Device  # noqa: PLC0415
    from app.db import Base  # noqa: PLC0415

    Base.metadata.create_all(engine)  # safety net (app lifespan also runs create_all)

    with Session(engine) as session:
        existing = session.execute(select(func.count()).select_from(Device)).scalar_one()
        if existing > 0:
            print(f"[seed] Devices table already has {existing} rows — skipping.")
            return existing

        user_count = max(1, count // 4)
        users = _make_user_pool(user_count)

        records: list[dict] = []
        for dtype, fraction in TYPE_DISTRIBUTION.items():
            n = round(count * fraction)
            for _ in range(n):
                records.append(_make_device(dtype, random.choice(users)))

        # Fill remainder (rounding artefacts)
        while len(records) < count:
            dtype = random.choice(list(TYPE_DISTRIBUTION.keys()))
            records.append(_make_device(dtype, random.choice(users)))

        random.shuffle(records)

        session.bulk_insert_mappings(Device, records)  # type: ignore[arg-type]
        session.commit()

    print(f"[seed] Inserted {len(records)} devices.")
    return len(records)


if __name__ == "__main__":
    seed()

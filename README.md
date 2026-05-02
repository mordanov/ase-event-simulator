# Health Telemetry Simulator

Multi-protocol device data generator for testing the Health Recommender Platform.

## Quick Start

```bash
# 1. Clone / copy this directory
# 2. Configure (optional — defaults work out of the box)
cp .env .env.local   # edit if needed

# 3. Start everything (Postgres + backend + frontend)
docker compose up --build

# On first boot the backend will:
#   1. Run Alembic migration (create devices table)
#   2. Seed 1 000 devices (idempotent — skipped on restart)
#   3. Start the API server

# UI  → http://localhost:3000
# API → http://localhost:8000/docs
```

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│              React UI  :3000                    │
│  ConfigPanel │ StatsBar │ EndpointStatus │ Log  │
└──────────────────────┬──────────────────────────┘
                       │ REST (polling every 1.5s)
┌──────────────────────▼──────────────────────────┐
│           FastAPI Backend  :8000                │
│                                                  │
│  SessionManager                                  │
│    └── Session (asyncio Task)                   │
│          ├── TelemetryGenerator                 │
│          │     └── device profiles × scenarios  │
│          └── Transports (per endpoint)          │
│                ├── HTTPTransport   (real)        │
│                ├── MQTTMockTransport             │
│                ├── WebSocketMockTransport        │
│                └── GRPCMockTransport             │
└─────────────────────────────────────────────────┘
```

---

## Device Types & Capabilities

| Device | HR | SpO₂ | Steps | Sleep | Temp | GPS | Stress | Hydration | Battery |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Smartwatch      | ✓ | ✓ | ✓ |   | ✓ | ✓ | ✓ | ✓ | ✓ |
| Fitness Tracker | ✓ | ✓ | ✓ | ✓ |   |   |   |   | ✓ |
| Smartphone      |   |   | ✓ |   |   | ✓ | ✓ |   | ✓ |
| Laptop          |   |   |   |   |   |   | ✓ |   |   |

---

## Scenarios

| Scenario | Description | Typical HR | SpO₂ |
|---|---|---|---|
| **REST**      | Normal resting vitals             | 60–85 bpm  | 97–100% |
| **WORKOUT**   | Elevated HR, high steps, high temp | 120–175 bpm | 96–99% |
| **SLEEP**     | Low HR, high HRV, sleep metrics   | 45–65 bpm  | 95–99% |
| **EMERGENCY** | Out-of-range: tachycardia/bradycardia, low SpO₂, fever | 35–220 bpm | 80–94% |
| **RANDOM**    | Fully random across all ranges    | 45–195 bpm | 88–100% |

---

## Device Registry (PostgreSQL)

All simulated events use **stable device identities** stored in a PostgreSQL database.  
On first start the container seeds **1 000 devices** automatically.  
The UI panel **Device Registry** shows counts per type and lets you add more:

| Action | Details |
|---|---|
| Auto-seed on startup | 1 000 devices, idempotent |
| Add via UI | 1–1 000 devices, any type |
| Add via API | `POST /api/v1/devices/seed` |
| Scale target | ~1 000 000 devices supported |

### Devices table

| Column | Type | Example |
|---|---|---|
| `id` | `SERIAL` PK | `1` |
| `device_id` | `VARCHAR(50)` unique | `smartwatch-a3f9b2c1` |
| `device_type` | `VARCHAR(50)` | `smartwatch` |
| `user_id` | `VARCHAR(50)` | `user-4f8e2a` |
| `firmware_version` | `VARCHAR(20)` | `2.2.3` |
| `gps_lat` | `FLOAT` nullable | `52.37456` |
| `gps_lon` | `FLOAT` nullable | `4.89512` |
| `is_active` | `BOOLEAN` | `true` |
| `created_at` | `TIMESTAMPTZ` | `2026-05-02T10:00:00Z` |

Each user owns ~4 devices on average.  
Devices without GPS capability (`fitness_tracker`, `laptop`) have `NULL` lat/lon.

### Add devices — API

```bash
# Add 500 smartwatches
curl -X POST http://localhost:8000/api/v1/devices/seed \
  -H 'Content-Type: application/json' \
  -d '{"device_type": "smartwatch", "count": 500}'

# Response
{
  "created": 500,
  "total": 1500,
  "message": "Created 500 smartwatch devices. Total active: 1500."
}
```

### Device stats — API

```bash
curl http://localhost:8000/api/v1/devices/stats
# Response
{
  "total": 1000,
  "by_type": {
    "smartwatch": 350,
    "fitness_tracker": 300,
    "smartphone": 250,
    "laptop": 100
  }
}
```

> **Fallback:** if the database is unavailable (e.g. running without Docker), the simulator
> falls back to ephemeral random device IDs — all other features work normally.

---

## Payload Formats

### Single-event payload (Immediate mode)

Sent as the body of a `POST` request (HTTP transport) or serialised bytes (MQTT / WS / gRPC).

```json
{
  "event_id": "3f8a1c2d-4e5f-6789-abcd-ef0123456789",
  "device_id": "smartwatch-a3f9b2c1",
  "device_type": "smartwatch",
  "user_id": "user-4f8e2a",
  "timestamp": "2026-05-02T10:15:30.123456+00:00",
  "scenario": "workout",
  "is_anomaly": false,
  "protocol": "http",
  "firmware_version": "2.2.3",
  "battery_pct": 82,

  "heart_rate": {
    "bpm": 148,
    "hrv_ms": 32.5
  },
  "spo2": {
    "percentage": 98.2
  },
  "steps": {
    "count": 412,
    "distance_m": 313.94,
    "calories_kcal": 22.66
  },
  "temperature": {
    "celsius": 37.3
  },
  "blood_pressure": {
    "systolic_mmhg": 142,
    "diastolic_mmhg": 88
  },
  "gps": {
    "latitude": 52.374560,
    "longitude": 4.895120,
    "altitude_m": 12.5,
    "accuracy_m": 4.2
  },
  "stress": {
    "score": 54
  },
  "hydration": {
    "level_percent": 63.8
  },

  "sleep": null
}
```

#### Metric presence per device type

| Metric | Smartwatch | Fitness Tracker | Smartphone | Laptop |
|---|:---:|:---:|:---:|:---:|
| `heart_rate` | ✓ | ✓ | — | — |
| `heart_rate.hrv_ms` | ✓ | — | — | — |
| `spo2` | ✓ | ✓ | — | — |
| `steps` | ✓ | ✓ | ✓ | — |
| `sleep` | — | ✓ (SLEEP/RANDOM) | — | — |
| `blood_pressure` | ✓ | ✓ | — | — |
| `temperature` | ✓ | — | — | — |
| `gps` | ✓ | — | ✓ | — |
| `stress` | ✓ | — | ✓ | ✓ |
| `hydration` | ✓ | — | — | — |
| `battery_pct` | ✓ | ✓ | ✓ | — |

Fields absent for a given device type are sent as `null`.

#### Field reference

| Field | Type | Notes |
|---|---|---|
| `event_id` | `string` (UUID4) | Unique per event |
| `device_id` | `string` | `{type}-{hex8}`, stable from DB |
| `device_type` | `enum` | `smartwatch` \| `fitness_tracker` \| `smartphone` \| `laptop` |
| `user_id` | `string` | `user-{hex6}`, ~4 devices per user |
| `timestamp` | `string` (ISO 8601 UTC) | Event generation time |
| `scenario` | `enum` | `workout` \| `sleep` \| `rest` \| `emergency` \| `random` |
| `is_anomaly` | `boolean` | `true` when scenario was escalated to EMERGENCY |
| `protocol` | `enum` | `http` \| `mqtt` \| `websocket` \| `grpc` |
| `firmware_version` | `string` | Stable per device |
| `battery_pct` | `integer` \| `null` | 5–100 |
| `heart_rate.bpm` | `integer` | Beats per minute |
| `heart_rate.hrv_ms` | `float` \| `null` | Heart rate variability (ms) |
| `spo2.percentage` | `float` | Blood oxygen saturation (%) |
| `steps.count` | `integer` | Steps since last event |
| `steps.distance_m` | `float` | Estimated distance (m) |
| `steps.calories_kcal` | `float` | Estimated calories burned |
| `sleep.duration_minutes` | `integer` | Total sleep (min) |
| `sleep.deep_sleep_minutes` | `integer` | Deep-sleep portion (min) |
| `sleep.rem_sleep_minutes` | `integer` | REM portion (min) |
| `sleep.sleep_score` | `integer` | 0–100 quality score |
| `blood_pressure.systolic_mmhg` | `integer` | Systolic (mmHg) |
| `blood_pressure.diastolic_mmhg` | `integer` | Diastolic (mmHg) |
| `temperature.celsius` | `float` | Body temperature (°C) |
| `gps.latitude` | `float` | Degrees |
| `gps.longitude` | `float` | Degrees |
| `gps.altitude_m` | `float` | Meters above sea level |
| `gps.accuracy_m` | `float` | GPS accuracy radius (m) |
| `stress.score` | `integer` | 0–100 |
| `hydration.level_percent` | `float` | Hydration level (%) |

---

### Batch payload (Batch mode)

```json
{
  "batch_id": "7a1b2c3d-0000-1111-2222-333344445555",
  "sent_at":  "2026-05-02T10:15:30.000000+00:00",
  "event_count": 10,
  "events": [
    { /* TelemetryEvent — see above */ },
    { /* TelemetryEvent */ }
  ]
}
```

| Field | Type | Notes |
|---|---|---|
| `batch_id` | `string` (UUID4) | Unique per batch |
| `sent_at` | `string` (ISO 8601 UTC) | Batch send time |
| `event_count` | `integer` | Length of `events` array |
| `events` | `TelemetryEvent[]` | Same schema as single-event above |

---

## Protocols

| Protocol | Type | Notes |
|---|---|---|
| **HTTP**      | Real   | Fires actual POST requests to your endpoint URL |
| **MQTT**      | Mock   | Simulates publish latency (1–8ms), 99.5% reliability |
| **WebSocket** | Mock   | Simulates frame send (2–12ms), 99.7% reliability |
| **gRPC**      | Mock   | Simulates unary call (3–15ms), 99.8% reliability |

> Mock transports serialise the payload exactly as production would (JSON bytes for MQTT/WS, simulated protobuf for gRPC). No real broker needed.

---

## Send Modes

### Immediate
Each event is sent right after generation. One event per active transport per interval.

### Batch
Accumulate `batch_size` events, then send as a single JSON payload:
```json
{
  "batch_id": "uuid",
  "sent_at": "2025-01-01T12:00:00Z",
  "event_count": 10,
  "events": [ ... ]
}
```
Your endpoint receives one POST with the full batch body.

---

## API Reference

Full OpenAPI docs at **http://localhost:8000/docs**

### Key endpoints

```
POST /api/v1/sessions/start        Start a new simulation session
POST /api/v1/sessions/{id}/stop    Stop a session
GET  /api/v1/sessions/{id}/status  Poll session stats + recent events
GET  /api/v1/sessions              List all sessions
POST /api/v1/sessions/stop-all     Stop everything

POST /api/v1/preview               Generate one event without sending
GET  /api/v1/meta/device-types     Available device types
GET  /api/v1/meta/scenarios        Available scenarios
GET  /api/v1/meta/protocols        Available protocols
```

### Example: start a session via curl

```bash
curl -X POST http://localhost:8000/api/v1/sessions/start \
  -H 'Content-Type: application/json' \
  -d '{
    "config": {
      "devices": [
        {"device_type": "smartwatch", "count": 3},
        {"device_type": "fitness_tracker", "count": 2}
      ],
      "scenario": "workout",
      "send_mode": "batch",
      "batch_size": 20,
      "interval_seconds": 5,
      "anomaly_rate": 0.15,
      "total_events": null,
      "protocols": ["http", "mqtt"],
      "endpoints": [
        {
          "name": "My Backend",
          "url": "http://host.docker.internal:9000/ingest/batch",
          "protocol": "http",
          "enabled": true,
          "headers": {"Authorization": "Bearer my-token"}
        }
      ]
    }
  }'
```

### Example: receive a batch (Python)

```python
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

class BatchPayload(BaseModel):
    batch_id: str
    sent_at: str
    event_count: int
    events: list[dict]

@app.post("/ingest/batch")
async def ingest(batch: BatchPayload):
    print(f"Received {batch.event_count} events")
    for event in batch.events:
        print(event["device_id"], event["heart_rate"])
    return {"received": batch.event_count}
```

---

## Configuration (.env)

```env
BACKEND_PORT=8000                          # API server port

DEFAULT_BATCH_SIZE=10                      # Default events per batch
DEFAULT_INTERVAL_SECONDS=5                 # Default send interval
DEFAULT_MAX_DEVICES=200                    # UI max device count
DEFAULT_ANOMALY_RATE=0.02                  # Default anomaly rate (2%)

# Pre-fill UI with these endpoints (name::url pairs)
DEFAULT_HTTP_ENDPOINTS=local-api::http://host.docker.internal:9000/ingest

VITE_API_BASE_URL=http://localhost:8000    # For local dev (npm run dev)
```

---

## Local Development (without Docker)

```bash
# Start Postgres separately (or point DATABASE_URL at an existing instance)
docker run -d --name sim-db -e POSTGRES_USER=simulator \
  -e POSTGRES_PASSWORD=simulator -e POSTGRES_DB=simulator \
  -p 5432:5432 postgres:16-alpine

# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export DATABASE_URL=postgresql+asyncpg://simulator:simulator@localhost:5432/simulator
alembic upgrade head          # create schema
python -m scripts.seed_devices  # seed 1 000 devices
uvicorn app.main:app --reload --port 8000

# Frontend (separate terminal)
cd frontend
npm install
npm run dev     # → http://localhost:3000
```

---

## Stress Testing

For 100+ device stress tests, use the API directly:

```bash
# 100 smartwatches, emergency scenario, 1s interval, HTTP
curl -X POST http://localhost:8000/api/v1/sessions/start \
  -H 'Content-Type: application/json' \
  -d '{
    "config": {
      "devices": [{"device_type": "smartwatch", "count": 100}],
      "scenario": "emergency",
      "send_mode": "batch",
      "batch_size": 100,
      "interval_seconds": 1,
      "anomaly_rate": 1.0,
      "total_events": 10000,
      "protocols": ["http"],
      "endpoints": [{"name":"stress","url":"http://host.docker.internal:9000/ingest","protocol":"http","enabled":true,"headers":{}}]
    }
  }'
```

---

## Project Structure

```
simulator/
├── .env                       # Configuration (incl. DATABASE_URL)
├── docker-compose.yml         # backend + frontend + postgres
├── backend/
│   ├── Dockerfile
│   ├── entrypoint.sh          # migrate → seed → uvicorn
│   ├── alembic.ini
│   ├── alembic/
│   │   ├── env.py
│   │   └── versions/
│   │       └── 001_create_devices.py
│   ├── scripts/
│   │   └── seed_devices.py    # idempotent seed (1 000 devices)
│   ├── requirements.txt
│   └── app/
│       ├── main.py            # FastAPI app + lifespan
│       ├── db.py              # Async SQLAlchemy engine
│       ├── models/
│       │   ├── telemetry.py   # All Pydantic models
│       │   └── device_orm.py  # SQLAlchemy ORM Device model
│       ├── generators/
│       │   └── telemetry.py   # Data generation (DB-backed or ephemeral)
│       ├── transports/
│       │   └── sender.py      # HTTP / MQTT / WS / gRPC transports
│       └── api/
│           ├── routes.py      # All API endpoints (incl. /devices/*)
│           └── session.py     # Session lifecycle manager
└── frontend/
    ├── Dockerfile
    ├── nginx.conf
    ├── package.json
    └── src/
        ├── App.tsx
        ├── api/client.ts      # Typed API calls
        ├── hooks/useSimulator.ts
        ├── types/index.ts
        └── components/
            ├── DeviceRegistry.tsx  # Device count + add-devices form
            ├── ConfigPanel.tsx
            ├── StatsBar.tsx
            ├── EndpointStatusPanel.tsx
            └── EventLog.tsx
```

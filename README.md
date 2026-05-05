# Health Telemetry Simulator

Multi-protocol device data generator for testing the Health Recommender Platform.

## Quick Start

```bash
# 1. Clone / copy this directory
# 2. Configure (optional — defaults work out of the box)
cp .env .env.local   # edit if needed

# 3. Start everything (backend + frontend)
docker compose up --build

# On first boot the backend will:
#   1. Create the SQLite schema (idempotent)
#   2. Seed 1 000 devices
#   3. Start the API server

# UI  → http://localhost:3002
# API → http://localhost:8000/docs
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    React UI  :3002                          │
│  ConfigPanel │ StatsBar │ DeviceRegistry │ RegistrationLog  │
│  EndpointStatus │ RecommendationLog │ ActivityLog           │
└──────────────────────────┬──────────────────────────────────┘
                           │ REST (polling every 1.5s)
┌──────────────────────────▼──────────────────────────────────┐
│                  FastAPI Backend  :8000                     │
│                                                             │
│  SessionManager                                             │
│    └── Session (asyncio Task)                               │
│          ├── RegistrationPhase (JITR / X.509 certs)        │
│          ├── TelemetryGenerator                             │
│          │     └── device profiles × scenarios              │
│          ├── Transports (per endpoint)                      │
│          │     ├── HTTPTransport   (real, supports mTLS)    │
│          │     ├── AWSIoTMQTTTransport (real MQTT5 / mock) │
│          │     ├── WebSocketMockTransport                   │
│          │     └── GRPCMockTransport                        │
│          └── RecommendationClient (optional)                │
│                                                             │
│  SQLite DB  (devices table + X.509 cert store)             │
└─────────────────────────────────────────────────────────────┘
```

---

## Device Types & Capabilities

| Device | HR | SpO₂ | Steps | Sleep | Temp | BP | GPS | Stress | Hydration | Battery |
|---|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Smartwatch      | ✓ | ✓ | ✓ |   | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Fitness Tracker | ✓ | ✓ | ✓ | ✓ |   | ✓ |   |   |   | ✓ |
| Smartphone      |   |   | ✓ |   |   |   | ✓ | ✓ |   | ✓ |
| Laptop          |   |   |   |   |   |   |   | ✓ |   |   |

Blood pressure is generated for any device that has a heart-rate sensor.

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

## Device Registry (SQLite)

All simulated events use **stable device identities** stored in SQLite.  
On first start the container seeds **1 000 devices** automatically.  
The UI panel **Device Registry** shows counts per type and lets you add more.

| Action | Details |
|---|---|
| Auto-seed on startup | 1 000 devices, idempotent |
| Add via UI | 1–1 000 devices, any type |
| Add via API | `POST /api/v1/devices/seed` |

### Devices table

| Column | Type | Notes |
|---|---|---|
| `id` | `INTEGER` PK | Auto-increment |
| `device_id` | `VARCHAR(50)` unique | `{type}-{hex8}` |
| `device_type` | `VARCHAR(50)` | `smartwatch` \| `fitness_tracker` \| `smartphone` \| `laptop` |
| `model` | `VARCHAR(80)` | e.g. `SimWatch Pro` |
| `os` | `VARCHAR(80)` | e.g. `WatchOS-Sim 4.0` |
| `user_id` | `VARCHAR(50)` | ~4 devices per user |
| `firmware_version` | `VARCHAR(20)` | Stable per device |
| `gps_lat` | `FLOAT` nullable | GPS-capable devices only |
| `gps_lon` | `FLOAT` nullable | GPS-capable devices only |
| `height_cm` | `FLOAT` nullable | User biometric |
| `weight_kg` | `FLOAT` nullable | User biometric |
| `gender` | `VARCHAR(10)` nullable | `female` \| `male` \| `not_defined` |
| `birth_date` | `VARCHAR(10)` nullable | `YYYY-MM-DD` |
| `is_active` | `BOOLEAN` | `true` |
| `created_at` | `TIMESTAMPTZ` | Row creation time |
| `cert_pem` | `TEXT` nullable | X.509 device cert (PEM) |
| `cert_key_pem` | `TEXT` nullable | Device private key (PEM) |
| `cert_serial` | `VARCHAR(64)` nullable | Certificate serial number |
| `cert_fingerprint` | `VARCHAR(128)` nullable | SHA-256 fingerprint |
| `registration_status` | `VARCHAR(20)` | `unregistered` \| `pending` \| `registered` |
| `registered_at` | `TIMESTAMPTZ` nullable | Time of successful registration |

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

> **Fallback:** if the database is unavailable the simulator falls back to ephemeral random device IDs — all other features work normally.

---

## Device Registration & X.509 / JITR

Before any telemetry is sent, every session runs a **registration phase** that simulates AWS IoT Core Just-In-Time Registration (JITR).

### Local / dev mode (default)

1. **Cert generation** — RSA-2048 X.509 cert signed by the CA is generated for each device and stored in the DB.
2. **JITR simulation** — First connection is rejected (mimicking AWS IoT behaviour). After `CERT_APPROVAL_DELAY_SECONDS` (default 5 s) the cert is activated.
3. **Device profiles sent** — Each device's profile and biometrics are POST-ed to `/api/v1/devices` on every configured HTTP endpoint.
4. **Telemetry starts** — mTLS certs are loaded into the HTTP / MQTT transports; events include `cert_fingerprint`.

### Cloud mode (`SIMULATOR_CLOUD_MODE=true`)

Set `AWS_IOT_REGISTRATION_MODE` to control how devices are registered:

| Mode | Behaviour |
|---|---|
| `direct` | Calls `RegisterCertificateWithoutCA` + creates Thing + attaches policy via boto3 |
| `jitr` | Generates/stores cert only; first MQTT mTLS connect triggers IoT JITR rule + Lambda |

### CA configuration

| Priority | Source |
|---|---|
| 1 | `CA_CERT_PEM` / `CA_KEY_PEM` env vars (inline PEM, `\n` escaped) |
| 2 | `CA_CERT_FILE` / `CA_KEY_FILE` file paths |
| 3 | Auto-generated self-signed CA (local testing only) |

---

## Payload Formats

### Single-event payload (Immediate mode)

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
  "cert_fingerprint": "a1b2c3d4...",
  "battery_pct": 82,

  "heart_rate": { "bpm": 148, "hrv_ms": 32.5 },
  "spo2": { "percentage": 98.2 },
  "steps": { "count": 412, "distance_m": 313.94, "calories_kcal": 22.66 },
  "temperature": { "celsius": 37.3 },
  "blood_pressure": { "systolic_mmhg": 142, "diastolic_mmhg": 88 },
  "gps": { "latitude": 52.374560, "longitude": 4.895120, "altitude_m": 12.5, "accuracy_m": 4.2 },
  "stress": { "score": 54 },
  "hydration": { "level_percent": 63.8 },
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
| `cert_fingerprint` | `string` \| `null` | SHA-256 of device X.509 cert |
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
    { /* TelemetryEvent — see above */ }
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
| **HTTP**      | Real   | Fires actual POST requests; supports per-device mTLS certs |
| **MQTT**      | Real / Mock | Real MQTT5 to AWS IoT Core when certs + `awsiotsdk` are available; falls back to latency-realistic mock (1–8 ms, 99.5% reliability) |
| **WebSocket** | Mock   | Simulates frame send (2–12 ms, 99.7% reliability) |
| **gRPC**      | Mock   | Simulates unary call (3–15 ms, 99.8% reliability) |

> Mock transports serialise the payload exactly as production would (JSON bytes for MQTT/WS, simulated protobuf for gRPC). No real broker or server needed.

---

## Send Modes

### Immediate
Each event is sent right after generation. One event per active device per interval, fanned out to all transports.

### Batch
Accumulate `batch_size` events, then send as a single JSON payload:
```json
{
  "batch_id": "uuid",
  "sent_at": "2026-05-02T12:00:00Z",
  "event_count": 10,
  "events": [ ... ]
}
```

---

## Endpoint Modes

| Mode | Behaviour |
|---|---|
| **Fanout** (default) | Same event (same `event_id`) is delivered to every enabled endpoint. Backends can deduplicate by `event_id`. |
| **Round-robin** | Events are distributed evenly across endpoints — each event goes to exactly one transport. |

---

## Recommendation Integration

When `recommendation_handicap > 0` (default: 500), the simulator automatically calls the recommendation API after each telemetry cycle for any device whose credit balance meets the threshold.

The recommendation URL is derived from the first configured HTTP endpoint's host:
```
POST {scheme}://{host}/api/v1/devices/{device_id}/recommendations
```

Results (recommendations, credits spent, reward tier) appear in the **Recommendation Log** UI panel and the unified **Activity Log**.

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
GET  /api/v1/meta/send-modes       Available send modes
GET  /api/v1/meta/defaults         Defaults from .env

GET  /api/v1/devices/stats         Device counts by type
POST /api/v1/devices/seed          Add devices to the registry
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
      "endpoint_mode": "fanout",
      "batch_size": 20,
      "interval_seconds": 5,
      "anomaly_rate": 0.15,
      "total_events": null,
      "recommendation_handicap": 500,
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
# ── Backend ───────────────────────────────────────────────────────────────────
BACKEND_HOST=0.0.0.0
BACKEND_PORT=8000
LOG_LEVEL=info

# ── Database ──────────────────────────────────────────────────────────────────
DATABASE_URL=sqlite+aiosqlite:////app/simulator.db
SEED_DEVICE_COUNT=1000

# ── Default target endpoints (pre-fill UI) ────────────────────────────────────
# Comma-separated name::url pairs
DEFAULT_HTTP_ENDPOINTS=local-api::http://host.docker.internal:9000/ingest
DEFAULT_MQTT_BROKER_URL=mqtt://your-endpoint.iot.us-west-2.amazonaws.com
DEFAULT_MQTT_TOPIC=health/telemetry
DEFAULT_WS_URL=ws://host.docker.internal:9002/ws/ingest
DEFAULT_GRPC_HOST=host.docker.internal
DEFAULT_GRPC_PORT=50051

# ── Generator defaults ────────────────────────────────────────────────────────
DEFAULT_BATCH_SIZE=10
DEFAULT_INTERVAL_SECONDS=5
DEFAULT_MAX_DEVICES=200
DEFAULT_ANOMALY_RATE=0.02

# ── X.509 / JITR ─────────────────────────────────────────────────────────────
# Leave CA_CERT_PEM / CA_KEY_PEM empty to auto-generate a throwaway self-signed CA.
CA_CERT_PEM=
CA_KEY_PEM=
# Or point at PEM files mounted into the container:
CA_CERT_FILE=/path/to/root-ca.pem
CA_KEY_FILE=/path/to/root-ca.key
CERT_APPROVAL_DELAY_SECONDS=5   # JITR simulation wait time
CERT_VALIDITY_DAYS=365
DEVICE_CERT_COUNTRY=US
DEVICE_CERT_ORG=HealthSimulator

# ── AWS IoT Core ──────────────────────────────────────────────────────────────
AWS_IOT_CERT_FILE=/path/to/device.cert.pem   # fallback cert (no JITR)
AWS_IOT_KEY_FILE=/path/to/device.private.key
AWS_IOT_CA_FILE=/path/to/root-CA.crt

# ── Cloud mode ────────────────────────────────────────────────────────────────
# SIMULATOR_CLOUD_MODE=true         # force cloud behaviour
# AWS_IOT_REGISTRATION_MODE=direct  # direct | jitr

# ── Recommendation API ────────────────────────────────────────────────────────
INGESTION_API_KEY=poc-dev-key

# ── Frontend (local dev only) ─────────────────────────────────────────────────
VITE_API_BASE_URL=http://localhost:8000
```

---

## Local Development (without Docker)

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export DATABASE_URL=sqlite+aiosqlite:////tmp/simulator.db
python -m scripts.seed_devices     # seed 1 000 devices (idempotent)
uvicorn app.main:app --reload --port 8000
# Schema is created automatically on startup via Base.metadata.create_all

# Frontend (separate terminal)
cd frontend
npm install
npm run dev     # → http://localhost:3000 (Vite dev server)
```

---

## Stress Testing

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
      "recommendation_handicap": 0,
      "protocols": ["http"],
      "endpoints": [{"name":"stress","url":"http://host.docker.internal:9000/ingest","protocol":"http","enabled":true,"headers":{}}]
    }
  }'
```

---

## AWS Deployment

CloudFormation templates are in `aws/cloudformation/` and the bootstrap script in `aws/scripts/bootstrap.sh`.

| Stack | File | Purpose |
|---|---|---|
| `00-buckets` | S3 buckets for artifacts | |
| `01-jitr` | JITR Lambda + IoT rule | Activates devices on first MQTT connect |
| `02-network` | VPC / subnets / SGs | |
| `03-persistent` | RDS / secrets | |
| `04-platform` | ECS cluster / ECR / IAM | |
| `05-service` | ECS service + ALB | |
| `06-acm` | ACM certificate | |
| `07-cdn` | CloudFront distribution | |

### JITR Lambda (`aws/scripts/lambda/jitr_handler.py`)

Triggered by AWS IoT Core on `certificateStatus = PENDING_ACTIVATION`:
1. Activates the certificate
2. Creates an IoT Thing (`thingName = device_id`)
3. Creates the device policy (if absent)
4. Attaches policy → certificate → Thing

---

## Project Structure

```
simulator/
├── .env                       # Configuration
├── docker-compose.yml         # backend + frontend (SQLite — no separate DB container)
├── certificates/              # Local CA cert files (for dev)
├── aws/
│   ├── cloudformation/        # 8 CloudFormation stacks (00–07)
│   └── scripts/
│       ├── bootstrap.sh       # Deploy all stacks
│       ├── teardown.sh        # Tear down all stacks
│       └── lambda/
│           └── jitr_handler.py  # IoT JITR activation Lambda
├── backend/
│   ├── Dockerfile
│   ├── entrypoint.sh          # seed → uvicorn (no migrations; schema auto-created)
│   ├── requirements.txt
│   ├── scripts/
│   │   └── seed_devices.py    # Idempotent seed (1 000 devices + biometrics)
│   └── app/
│       ├── main.py            # FastAPI app + lifespan (schema create_all)
│       ├── db.py              # Async SQLAlchemy engine (SQLite / PostgreSQL)
│       ├── env_loader.py      # .env bootstrap
│       ├── models/
│       │   ├── telemetry.py   # All Pydantic models
│       │   └── device_orm.py  # SQLAlchemy Device model (incl. cert + biometric cols)
│       ├── generators/
│       │   └── telemetry.py   # Data generation (DB-backed or ephemeral)
│       ├── services/
│       │   ├── cert_service.py        # X.509 cert generation (RSA-2048, CA-signed)
│       │   ├── registration_service.py # JITR simulation + AWS IoT provisioning
│       │   └── runtime_mode.py        # Cloud vs local mode detection
│       ├── transports/
│       │   └── sender.py      # HTTP (mTLS) / MQTT5 (AWS IoT) / WS mock / gRPC mock
│       └── api/
│           ├── routes.py      # All API endpoints (sessions, devices, meta, preview)
│           └── session.py     # Session lifecycle + recommendation client
└── frontend/
    ├── Dockerfile
    ├── nginx.conf
    ├── package.json           # React 18, TypeScript, Vite
    └── src/
        ├── App.tsx
        ├── api/client.ts
        ├── hooks/useSimulator.ts
        ├── types/index.ts
        └── components/
            ├── ConfigPanel.tsx        # Session configuration form
            ├── StatsBar.tsx           # Live counters
            ├── DeviceRegistry.tsx     # Device counts + add-devices form
            ├── RegistrationLog.tsx    # JITR phase log + biometrics
            ├── EndpointStatusPanel.tsx
            ├── RecommendationLog.tsx  # Credit balance + recommendation results
            └── EventLog.tsx           # Unified activity log (all backend calls)
```

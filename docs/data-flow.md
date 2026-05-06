# Health Telemetry Simulator — Data Flow

## Architecture Overview

```
┌─ Frontend (React, :3002) ──────────────────────────────────────────────┐
│  ConfigPanel → StatsBar → EndpointStatusPanel → EventLog               │
│  DeviceRegistry → RegistrationLog → RecommendationLog                 │
└───────────────────────────── REST polling every 1.5s ──────────────────┘
                                        │
┌─ Backend (FastAPI, :8000) ─────────────────────────────────────────────┐
│                                                                         │
│  POST /api/v1/sessions/start                                           │
│         │                                                               │
│         ▼                                                               │
│  SessionManager ──► Session (asyncio.Task)                             │
│                           │                                             │
│          ┌────────────────┼────────────────┐                           │
│          ▼                ▼                ▼                            │
│   Phase 1: JITR   Phase 2: Telemetry   Phase 3: Recommendations        │
└────────────────────────────────────────────────────────────────────────┘
```

## Phase 1: Device Registration (JITR)

```
SQLite devices table
        │
        ▼
Load device profiles (or create ephemeral IDs)
        │
        ▼
cert_service.py → Generate RSA-2048 X.509 cert per device
  • Signed by CA (env CA_CERT_PEM or auto-generated)
  • Cert + fingerprint stored back to SQLite
        │
        ▼
Simulate JITR rejection → wait CERT_APPROVAL_DELAY_SECONDS → mark registered
        │
        ▼
HTTPTransport → POST {endpoint}/api/v1/devices
  Body: { device_id, device_type, model, firmware_version,
          user_id, height_cm, weight_kg, gender, birth_date }
```

## Phase 2: Telemetry Loop

```
Every interval_seconds:
        │
        ▼
TelemetryGenerator.generate_event()
  • Pick device from pool (round-robin)
  • Apply device type capabilities filter
    (smartwatch: HR+GPS+HRV, laptop: stress only, ...)
  • Apply scenario ranges:
      WORKOUT   → HR 120-175, high steps, temp 36.8-37.8°C
      SLEEP     → HR 45-65, high HRV, sleep metrics
      REST      → normal vitals, low activity
      EMERGENCY → out-of-range vitals (tachycardia, low SpO₂)
      RANDOM    → full range
  • Flip anomaly coin (anomaly_rate %) → use EMERGENCY ranges
  • Attach cert_fingerprint (for mTLS)
        │
        ▼
Event Routing
  FANOUT      → every event to ALL endpoints
  ROUND_ROBIN → every event to ONE endpoint (cycling)
        │
        ▼
Send Mode
  IMMEDIATE → send each event as generated
  BATCH     → buffer N events → send BatchPayload {batch_id, events[]}
        │
        ▼
Transport dispatch (make_transport factory)
```

| Transport | Behaviour |
|---|---|
| HTTP | Real POST to `{url}/ingest`; mTLS via per-device `AsyncClient` pool; parses `credit_results` in response |
| AWS IoT MQTT | MQTT5 to `{acct}.iot.{region}.amazonaws.com`; topic `{prefix}/{device_id}`; per-device X.509 cert |
| Local MQTT | Plain MQTT to `localhost:1883` via `aiomqtt` |
| WebSocket | Mock only — 2–12 ms simulated latency, 99.7% success |
| gRPC | Mock only — 3–15 ms simulated latency, 99.8% success |

```
        │
        ▼
BaseTransport._record() → updates success/error counts, rolling avg latency
```

## Phase 3: Rules & Recommendations

```
After registration:
  GET {ingestion}/api/v1/rules/disabled-devices
    → marks devices to skip in telemetry loop

When device credit balance ≥ recommendation_handicap:
  POST {ingestion}/api/v1/devices/{device_id}/recommendations
    Body: { "min_confidence": 0.2 }
    Response: recommendations[], credits_remaining, reward_tier
    On 403 → device added to disabled set
```

## Data Stores

| Store | Contents |
|---|---|
| SQLite `devices` | `device_id`, type, biometrics, GPS, X.509 cert PEM, `registration_status` |
| In-memory (Session) | event counts, per-endpoint stats, activity log, recommendation log |

## External Integrations

| Service | Call | Auth |
|---|---|---|
| Ingestion Pipeline | `POST /ingest` (telemetry), `POST /api/v1/devices` (registration) | X-API-Key |
| Rules Engine | `GET /api/v1/rules/disabled-devices` | X-API-Key |
| Recommendation API | `POST /api/v1/devices/{id}/recommendations` | X-API-Key |

## Key Config Knobs

`scenario` · `send_mode` (immediate/batch) · `endpoint_mode` (fanout/round-robin) · `anomaly_rate` · `interval_seconds` · `batch_size` · `recommendation_handicap` · `endpoints[]` (protocol + URL + headers)

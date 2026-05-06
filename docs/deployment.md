# Health Telemetry Simulator — Deployment

## Environments

| | Local (docker-compose) | Production (AWS ECS Fargate) |
|---|---|---|
| **Frontend served by** | Nginx in container (:3000, exposed :3002) | CloudFront CDN → S3 (static) |
| **API routing** | Nginx proxies `/api/*` → backend:8000 | CloudFront proxies `/api/*` → ALB:80 → ECS:8000 |
| **API base URL** | `http://localhost:8000` | Empty string (relative paths via CloudFront) |
| **TLS** | None | ACM cert on CloudFront (HTTP/2) |
| **Database** | SQLite in container (ephemeral) | SQLite in ECS task storage (ephemeral, reseeded on restart) |
| **CA certificates** | Auto-generated throwaway | Persisted in AWS Secrets Manager (survives redeployments) |
| **Secrets** | `.env` file | AWS Secrets Manager + task environment vars |
| **Logs** | Container stdout | CloudWatch `/ecs/{project}-backend` (14-day retention) |
| **Ingestion endpoint** | `host.docker.internal:9000` / `:1883` | `origin.ingestion-pipeline.{domain}:9000` / `:1883` |

---

## Local Development

```
docker-compose up
```

```
simulator-net
  ├── simulator-backend  (:8000)  python:3.12-slim, uvicorn, hot-reload via volume mount
  └── simulator-frontend (:3002→3000)  node:20 build → nginx:alpine
          └── /api/* → http://backend:8000  (nginx proxy)
```

Startup sequence:
1. `entrypoint.sh` runs `seed_devices.py` (idempotent, 1000 devices)
2. Uvicorn starts on `0.0.0.0:8000`
3. Frontend container waits for backend healthcheck (`/health`)

**Key env vars** (`.env`):

```
DATABASE_URL=sqlite+aiosqlite:////app/simulator.db
SEED_DEVICE_COUNT=1000
DEFAULT_HTTP_ENDPOINTS=ingestion::http://host.docker.internal:9000/ingest
DEFAULT_MQTT_BROKER_URL=mqtt://host.docker.internal:1883
INGESTION_API_KEY=poc-dev-key
CA_CERT_PEM=          # auto-generated if blank
CA_KEY_PEM=           # auto-generated if blank
```

---

## Production Architecture (AWS)

```
Users
  │
  ▼
CloudFront  (event-simulator.{domain})
  ├── /api/*  ──► ALB :80  ──► ECS Fargate :8000  (FastAPI backend)
  ├── /health ──► ALB :80  ──► ECS Fargate :8000
  └── /*      ──► S3 bucket  (React SPA, cached)

origin.{domain}  (direct A record → EC2 Elastic IP, bypasses CloudFront)
  ├── :9000  ──► Ingestion HTTP API
  └── :1883  ──► Mosquitto MQTT broker
```

### CloudFormation Stacks (deploy order)

| # | Stack | What it creates |
|---|---|---|
| 00 | `buckets` | S3: deploy bucket (versioned) + frontend SPA bucket (private, OAC) |
| 01 | `jitr` | IoT Thing Type, IoT Policy, JITR Lambda (auto-activates device certs) |
| 02 | `network` | VPC `10.0.0.0/16`, 2× public + 2× private subnets, IGW, security groups |
| 03 | `persistent` | ECR repo (`{project}-backend`), image scan on push, keep last 10 |
| 04 | `platform` | ECS cluster (FARGATE + FARGATE_SPOT), ALB, target group, task definition |
| 05 | `service` | ECS Fargate service (desired count: 1), circuit breaker enabled |
| 06 | `acm` | 3× ACM certs: simulator domain, ingestion domain, `*.{domain}` wildcard |
| 07 | `cdn` | 2× CloudFront distributions + Route 53 CNAME/A records |

### ECS Task

- **Image**: ECR `{project}-backend:latest`
- **CPU / Memory**: 1024 / 2048 MB (configurable)
- **Platform**: FARGATE, `awsvpc` network mode, public IP enabled (for ECR pulls)
- **Healthcheck**: `curl -sf http://localhost:8000/health` — start-period 90 s (device seeding)
- **Secrets from Secrets Manager**: `CA_CERT_PEM`, `CA_KEY_PEM`

### Security Groups

| Group | Inbound |
|---|---|
| ALB | 80, 443 from `0.0.0.0/0` |
| ECS tasks | 8000 from ALB SG only |

---

## Bootstrap Deployment (`aws/scripts/bootstrap.sh`)

Deploys all stacks in dependency order with a single script:

```bash
./aws/scripts/bootstrap.sh \
  --profile  my-aws-profile \
  --region   us-west-2 \
  --domain   event-simulator.example.com \
  --hosted-zone-id  Z1234567890 \
  --ingestion-host  origin.ingestion.example.com \
  --api-key  my-api-key
```

**Steps executed:**
1. Preflight: verify `aws`, `docker`, `npm`, `jq`, `openssl`
2. Deploy stacks 00 → 03 → 04 (network before platform, ECR before service)
3. Generate CA cert pair → store in Secrets Manager (idempotent)
4. `docker build` → `docker push` to ECR
5. Deploy stacks 05 → 06 → 07

**Skip flags** for incremental deploys: `SKIP_BUCKETS`, `SKIP_NETWORK`, `SKIP_PERSISTENT`, `SKIP_PLATFORM`, `SKIP_SERVICE`, `SKIP_ACM`, `SKIP_CDN`, `SKIP_DOCKER`

---

## AWS IoT / JITR Flow

```
Simulator generates RSA-2048 X.509 cert per device (CN = device_id)
        │
        ▼
Device connects to AWS IoT Core MQTT endpoint
  → cert status: PENDING_ACTIVATION
        │
        ▼
IoT Core triggers JITR Lambda
  1. UpdateCertificate → ACTIVE
  2. CreateThing (name = device_id)
  3. Attach policy HealthSimulatorDevicePolicy
     (publish: health/telemetry/{device_id},
      subscribe: $aws/things/{thingName}/shadow/update/accepted)
        │
        ▼
Device publishes telemetry to health/telemetry/{device_id}
```

---

## CI/CD (GitHub Actions)

**Triggers**: push or PR to `main`

| Job | Steps |
|---|---|
| `pre-commit` | Ruff lint + format (Python 3.12), ESLint (Node 24) |
| `backend-tests` | `pytest` with coverage (Python 3.12) |
| `frontend-tests` | TypeScript type-check + `npm test` (Node 24) |

> CI does **not** auto-deploy. Production deploy is triggered manually via `bootstrap.sh`.

---

## Ports Reference

| Port | Where | Purpose |
|---|---|---|
| 8000 | Backend container / ECS task | FastAPI (uvicorn) |
| 3000 | Nginx container (internal) | React SPA |
| 3002 | Host (local dev) | Nginx exposed port |
| 80 | ALB (production) | HTTP → backend target group |
| 443 | CloudFront (production) | HTTPS (ACM TLS) |
| 9000 | `origin.*` subdomain | Ingestion HTTP API (direct, bypasses CDN) |
| 1883 | `origin.*` subdomain | Mosquitto MQTT (direct, bypasses CDN) |

---

## Key Files

| Path | Purpose |
|---|---|
| `docker-compose.yml` | Local orchestration |
| `backend/Dockerfile` | `python:3.12-slim` image, uvicorn |
| `frontend/Dockerfile` | Multi-stage Node 20 → `nginx:alpine` |
| `frontend/nginx.conf` | SPA fallback + `/api` proxy |
| `.env` | Local dev config (gitignored) |
| `.env.production` | `VITE_API_BASE_URL=` (empty for CloudFront) |
| `.github/workflows/ci.yml` | Lint + test pipeline |
| `aws/cloudformation/` | 8 ordered CloudFormation stacks |
| `aws/scripts/bootstrap.sh` | Full deploy orchestration script |
| `backend/entrypoint.sh` | Seed devices → start uvicorn |
| `backend/scripts/seed_devices.py` | Idempotent 1000-device seed |

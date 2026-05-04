#!/bin/bash
set -e

echo "[entrypoint] Seeding device registry (idempotent)..."
python -m scripts.seed_devices

echo "[entrypoint] Starting uvicorn..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000

FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY alembic/ ./alembic/
COPY alembic.ini .
COPY scripts/ ./scripts/
COPY entrypoint.sh .

RUN chmod +x entrypoint.sh

ENV PYTHONUNBUFFERED=1

HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
  CMD curl -sf http://localhost:8000/health || exit 1

ENTRYPOINT ["./entrypoint.sh"]

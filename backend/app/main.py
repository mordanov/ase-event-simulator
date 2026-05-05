"""FastAPI application entrypoint for the health telemetry simulator."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func, select

from app.api.routes import router
from app.api.session import session_manager
from app.db import Base, async_session_factory, engine
from app.env_loader import bootstrap_environment
from app.models import device_orm as _device_orm  # noqa: F401  # register ORM table metadata

bootstrap_environment()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "info").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)


async def _prepare_schema() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _log_device_count() -> None:
    from app.models.device_orm import Device

    async with async_session_factory() as db:
        count = (await db.execute(select(func.count(Device.id)))).scalar_one()
        logger.info("DB connected - %d devices in registry", count)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Create all tables (idempotent for SQLite ephemeral storage).
    try:
        await _prepare_schema()
        logger.info("DB schema ready")
    except Exception as exc:
        logger.warning("DB schema creation failed (ephemeral IDs will be used): %s", exc)

    # Log initial device count.
    try:
        await _log_device_count()
    except Exception as exc:
        logger.warning("DB not available at startup: %s", exc)

    yield

    await session_manager.stop_all()


app = FastAPI(
    title="Health Telemetry Simulator",
    description="Generates and sends realistic health device telemetry for testing.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.get("/health")
async def health():
    return {"status": "ok", "active_sessions": len(session_manager.list_sessions())}

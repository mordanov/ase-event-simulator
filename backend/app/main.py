from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.api.session import session_manager

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "info").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Verify DB connectivity on startup (non-fatal — simulator works without DB)
    try:
        from app.db import async_session_factory
        from app.models.device_orm import Device
        from sqlalchemy import func, select

        async with async_session_factory() as db:
            count = (await db.execute(select(func.count(Device.id)))).scalar_one()
            logger.info("DB connected — %d devices in registry", count)
    except Exception as exc:
        logger.warning("DB not available at startup (ephemeral IDs will be used): %s", exc)

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

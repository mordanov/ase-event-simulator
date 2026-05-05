"""Database engine and session factory configuration for the backend."""

from __future__ import annotations

import os
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.env_loader import bootstrap_environment

bootstrap_environment()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:////app/simulator.db")


def _build_engine_kwargs(database_url: str) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"echo": False}
    if database_url.startswith("sqlite"):
        # SQLite requires check_same_thread=False for async access.
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs["pool_pre_ping"] = True
    return kwargs


engine = create_async_engine(DATABASE_URL, **_build_engine_kwargs(DATABASE_URL))
async_session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass

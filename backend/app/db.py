from __future__ import annotations

import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.env_loader import bootstrap_environment

bootstrap_environment()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:////app/simulator.db")

_engine_kwargs: dict = {"echo": False}
if DATABASE_URL.startswith("sqlite"):
    # SQLite requires check_same_thread=False for async use
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    _engine_kwargs["pool_pre_ping"] = True

engine = create_async_engine(DATABASE_URL, **_engine_kwargs)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass

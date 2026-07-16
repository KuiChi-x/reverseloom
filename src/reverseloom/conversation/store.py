"""Session metadata store (switchable sqlite / postgres).

The LangGraph checkpointer (see ``runtime/checkpoints.py``) stores *run state* keyed by
thread_id, but not the business metadata a UI needs: the list of sessions, their
titles. Conversation and execution history live in the LangGraph checkpointer.
This module uses async SQLAlchemy Core so the same code targets SQLite (default, desktop) or PostgreSQL
by only changing ``config.DB_BACKEND`` / ``config.DB_URL`` — the "one env var
flips it" property, applied to the session tables too.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    Column, DateTime, MetaData, String, Table, func, insert, select, update, delete,
)
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from reverseloom.runtime import config

_metadata = MetaData()

sessions = Table(
    "sessions", _metadata,
    Column("id", String(64), primary_key=True),
    Column("title", String(256), nullable=False, default=""),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), server_default=func.now(), onupdate=func.now()),
)


def _async_dsn() -> str:
    """Build a SQLAlchemy async DSN from the configured backend."""
    backend = (config.DB_BACKEND or "sqlite").strip().lower()
    if backend == "postgres":
        if not config.DB_URL:
            raise RuntimeError("postgres backend requires REVERSELOOM_DB_URL")
        # Normalise to the async psycopg3 dialect.
        dsn = config.DB_URL
        if dsn.startswith("postgresql://"):
            dsn = "postgresql+psycopg://" + dsn[len("postgresql://"):]
        elif dsn.startswith("postgres://"):
            dsn = "postgresql+psycopg://" + dsn[len("postgres://"):]
        return dsn
    # SQLite also stores session metadata when the graph uses an in-memory checkpointer.
    os.makedirs(os.path.dirname(config.DB_SQLITE_PATH), exist_ok=True)
    return f"sqlite+aiosqlite:///{config.DB_SQLITE_PATH}"


class SessionStore:
    """Async CRUD over session metadata. One instance per app lifetime."""

    def __init__(self) -> None:
        self._engine: Optional[AsyncEngine] = None

    async def open(self) -> "SessionStore":
        self._engine = create_async_engine(_async_dsn(), future=True)
        async with self._engine.begin() as conn:
            await conn.run_sync(_metadata.create_all)
        return self

    async def close(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None

    async def create_session(self, session_id: str, title: str = "") -> None:
        async with self._engine.begin() as conn:
            await conn.execute(insert(sessions).values(id=session_id, title=title or "New session"))

    async def rename_session(self, session_id: str, title: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(update(sessions).where(sessions.c.id == session_id).values(title=title))

    async def touch_session(self, session_id: str, title_if_empty: str = "") -> None:
        """Ensure a row exists (create-if-missing), bumping updated_at."""
        async with self._engine.begin() as conn:
            row = (await conn.execute(select(sessions.c.id).where(sessions.c.id == session_id))).first()
            if row is None:
                await conn.execute(insert(sessions).values(id=session_id, title=title_if_empty or "New session"))
            else:
                await conn.execute(update(sessions).where(sessions.c.id == session_id).values(updated_at=func.now()))

    async def delete_session(self, session_id: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(delete(sessions).where(sessions.c.id == session_id))

    async def list_sessions(self) -> List[Dict[str, Any]]:
        async with self._engine.connect() as conn:
            rows = (await conn.execute(
                select(sessions).order_by(sessions.c.updated_at.desc())
            )).mappings().all()
        out: List[Dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            # datetime columns aren't JSON-serializable by the default encoder;
            # emit ISO strings so the row can go straight into JSONResponse.
            for k in ("created_at", "updated_at"):
                if d.get(k) is not None:
                    d[k] = d[k].isoformat()
            out.append(d)
        return out

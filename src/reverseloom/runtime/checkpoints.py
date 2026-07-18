"""Switchable persistence layer for reverseloom.

Provides a LangGraph checkpointer chosen at runtime by ``config.DB_BACKEND``:

    sqlite   (default) — local file, zero external deps beyond the bundled
                         ``langgraph-checkpoint-sqlite``. Good for the desktop build.
    postgres           — requires the ``postgres`` extra
                         (``pip install "reverseloom[postgres]"``) and
                         ``REVERSELOOM_DB_URL``. One env var flips the whole app over.
    memory             — no checkpointer (state lost on restart).

Unlike the earlier service implementation (which required postgres and raised NotImplementedError
otherwise), the backend here is a real switch: the same code path builds either
saver, so a local SQLite run and a PostgreSQL deployment differ only by env vars.

The saver is a long-lived resource held for the whole application lifetime, so we
manage the underlying connection explicitly (open on startup, close on shutdown)
rather than through a one-shot ``async with`` context.
"""
from __future__ import annotations

import os
from typing import Any, Optional

from reverseloom.runtime import config


class CheckpointerManager:
    """Owns the checkpointer's underlying connection for the app lifetime.

    Usage (FastAPI lifespan):
        mgr = CheckpointerManager()
        checkpointer = await mgr.open()
        ...
        await mgr.close()
    """

    def __init__(self, backend: Optional[str] = None) -> None:
        self.backend = (backend or config.DB_BACKEND or "sqlite").strip().lower()
        self._conn: Any = None
        self._pool: Any = None
        self.checkpointer: Any = None

    async def open(self) -> Any:
        if self.backend == "memory":
            from langgraph.checkpoint.memory import InMemorySaver

            self.checkpointer = InMemorySaver()
            return self.checkpointer
        if self.backend == "sqlite":
            self.checkpointer = await self._open_sqlite()
            return self.checkpointer
        if self.backend == "postgres":
            self.checkpointer = await self._open_postgres()
            return self.checkpointer
        raise ValueError(
            f"Unknown REVERSELOOM_DB_BACKEND={self.backend!r}; expected sqlite | postgres | memory"
        )

    async def _open_sqlite(self) -> Any:
        try:
            import aiosqlite
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise RuntimeError(
                "sqlite backend needs 'langgraph-checkpoint-sqlite' (declared in "
                "pyproject dependencies). Run: pip install langgraph-checkpoint-sqlite"
            ) from exc
        os.makedirs(os.path.dirname(config.DB_SQLITE_PATH), exist_ok=True)
        self._conn = await aiosqlite.connect(config.DB_SQLITE_PATH)
        # Desktop DB can accumulate freelist after prune; WAL + busy_timeout keep
        # UI history loads from stalling while another write is in flight.
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.execute("PRAGMA temp_store=MEMORY")
        saver = AsyncSqliteSaver(self._conn)
        await saver.setup()
        return saver

    async def _open_postgres(self) -> Any:
        if not config.DB_URL:
            raise RuntimeError(
                "postgres backend requires REVERSELOOM_DB_URL "
                "(e.g. postgresql://user:pass@host:5432/reverseloom)"
            )
        try:
            from psycopg_pool import AsyncConnectionPool
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise RuntimeError(
                "postgres backend needs the 'postgres' extra. Run: "
                'pip install "reverseloom[postgres]"'
            ) from exc
        self._pool = AsyncConnectionPool(
            conninfo=config.DB_URL, max_size=10, kwargs={"autocommit": True}, open=False
        )
        await self._pool.open()
        saver = AsyncPostgresSaver(self._pool)
        await saver.setup()
        return saver

    async def prune_thread(self, thread_id: str) -> None:
        """Keep only the latest checkpoint per namespace for this thread
        """
        if self.checkpointer is None:
            return
        try:
            await self.checkpointer.aprune([thread_id], strategy="keep_latest")
        except Exception:
            pass

    async def close(self) -> None:
        if self._conn is not None:
            try:
                await self._conn.close()
            finally:
                self._conn = None
        if self._pool is not None:
            try:
                await self._pool.close()
            finally:
                self._pool = None
        self.checkpointer = None

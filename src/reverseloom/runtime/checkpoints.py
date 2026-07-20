"""Switchable persistence layer for reverseloom.

Provides a LangGraph checkpointer chosen at runtime by ``config.DB_BACKEND``:

    sqlite   (default) — local file, zero external deps beyond the bundled
                         ``langgraph-checkpoint-sqlite``. Good for the desktop build.
    postgres           — requires the ``postgres`` extra
                         (``pip install "reverseloom[postgres]"``) and
                         ``REVERSELOOM_DB_URL``. One env var flips the whole app over.

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
        if self.backend == "sqlite":
            self.checkpointer = await self._open_sqlite()
            return self.checkpointer
        if self.backend == "postgres":
            self.checkpointer = await self._open_postgres()
            return self.checkpointer
        raise ValueError(
            f"Unknown REVERSELOOM_DB_BACKEND={self.backend!r}; expected sqlite | postgres"
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
        # Desktop-oriented SQLite tuning: WAL for concurrent read/write,
        # NORMAL fsync for much lower checkpoint latency than FULL, and a
        # larger cache/mmap so history loads stay in memory after first open.
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute("PRAGMA temp_store=MEMORY")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.execute("PRAGMA cache_size=-65536")
        await self._conn.execute("PRAGMA mmap_size=268435456")
        await self._conn.execute("PRAGMA wal_autocheckpoint=1000")
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
        """Keep only the latest checkpoint per namespace (official aprune is NotImplemented)."""
        if not thread_id or self.checkpointer is None:
            return
        try:
            await self.checkpointer.aprune([thread_id], strategy="keep_latest")
            return
        except Exception:
            pass  # fall through to SQL below

        # checkpoint_id is time-sortable UUIDv6; keep MAX per ns, drop orphan writes/blobs.
        if self.backend == "sqlite" and self._conn is not None:
            await self._conn.execute(
                """
                DELETE FROM checkpoints
                WHERE thread_id = ?
                  AND (checkpoint_ns, checkpoint_id) NOT IN (
                    SELECT checkpoint_ns, checkpoint_id FROM (
                      SELECT checkpoint_ns, MAX(checkpoint_id) AS checkpoint_id
                      FROM checkpoints WHERE thread_id = ? GROUP BY checkpoint_ns
                    )
                  )
                """,
                (thread_id, thread_id),
            )
            await self._conn.execute(
                """
                DELETE FROM writes
                WHERE thread_id = ?
                  AND NOT EXISTS (
                    SELECT 1 FROM checkpoints c
                    WHERE c.thread_id = writes.thread_id
                      AND c.checkpoint_ns = writes.checkpoint_ns
                      AND c.checkpoint_id = writes.checkpoint_id
                  )
                """,
                (thread_id,),
            )
            await self._conn.commit()
        elif self.backend == "postgres" and self._pool is not None:
            async with self._pool.connection() as conn:
                async with conn.cursor() as cur:
                    await cur.execute(
                        """
                        DELETE FROM checkpoints
                        WHERE thread_id = %s
                          AND (checkpoint_ns, checkpoint_id) NOT IN (
                            SELECT checkpoint_ns, MAX(checkpoint_id)
                            FROM checkpoints WHERE thread_id = %s GROUP BY checkpoint_ns
                          )
                        """,
                        (thread_id, thread_id),
                    )
                    await cur.execute(
                        """
                        DELETE FROM checkpoint_writes w
                        WHERE w.thread_id = %s
                          AND NOT EXISTS (
                            SELECT 1 FROM checkpoints c
                            WHERE c.thread_id = w.thread_id
                              AND c.checkpoint_ns = w.checkpoint_ns
                              AND c.checkpoint_id = w.checkpoint_id
                          )
                        """,
                        (thread_id,),
                    )
                    await cur.execute(
                        """
                        DELETE FROM checkpoint_blobs b
                        WHERE b.thread_id = %s
                          AND NOT EXISTS (
                            SELECT 1 FROM checkpoints c
                            WHERE c.thread_id = b.thread_id
                              AND c.checkpoint_ns = b.checkpoint_ns
                          )
                        """,
                        (thread_id,),
                    )

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

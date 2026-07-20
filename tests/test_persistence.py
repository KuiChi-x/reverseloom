"""Switchable persistence: sqlite checkpointer opens, roundtrips, and closes."""
import uuid

import pytest
from langgraph.checkpoint.base import empty_checkpoint

from reverseloom.runtime.checkpoints import CheckpointerManager


async def test_sqlite_checkpointer_roundtrip():
    mgr = CheckpointerManager(backend="sqlite")
    cp = await mgr.open()
    try:
        assert type(cp).__name__ == "AsyncSqliteSaver"
        cfg = {"configurable": {"thread_id": "t1", "checkpoint_ns": ""}}
        saved = await cp.aput(cfg, empty_checkpoint(), {}, {})
        got = await cp.aget_tuple(saved)
        assert got is not None and got.checkpoint["id"]
    finally:
        await mgr.close()


async def test_sqlite_prune_keeps_only_latest():
    """Official aprune is NotImplemented; our SQL keep_latest must actually trim."""
    mgr = CheckpointerManager(backend="sqlite")
    cp = await mgr.open()
    try:
        thread_id = f"prune-{uuid.uuid4().hex}"
        cfg = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
        for _ in range(3):
            cfg = await cp.aput(cfg, empty_checkpoint(), {}, {})
        rows = await mgr._conn.execute_fetchall(
            "SELECT checkpoint_id FROM checkpoints WHERE thread_id = ? ORDER BY checkpoint_id",
            (thread_id,),
        )
        assert len(rows) == 3
        latest_id = rows[-1][0]

        await mgr.prune_thread(thread_id)

        rows_after = await mgr._conn.execute_fetchall(
            "SELECT checkpoint_id FROM checkpoints WHERE thread_id = ?",
            (thread_id,),
        )
        assert len(rows_after) == 1
        assert rows_after[0][0] == latest_id
        tip = await cp.aget_tuple({"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}})
        assert tip is not None and tip.checkpoint["id"] == latest_id
    finally:
        await mgr.close()


async def test_unknown_backend_raises():
    mgr = CheckpointerManager(backend="mysql")
    with pytest.raises(ValueError, match="sqlite | postgres"):
        await mgr.open()


async def test_memory_backend_is_rejected():
    mgr = CheckpointerManager(backend="memory")
    with pytest.raises(ValueError, match="sqlite | postgres"):
        await mgr.open()


async def test_postgres_backend_requires_url(monkeypatch):
    from reverseloom.runtime import config
    monkeypatch.setattr(config, "DB_URL", "")
    mgr = CheckpointerManager(backend="postgres")
    with pytest.raises(RuntimeError):
        await mgr.open()

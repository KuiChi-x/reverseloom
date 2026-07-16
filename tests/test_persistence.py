"""Switchable persistence: sqlite checkpointer opens, roundtrips, and closes."""
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


async def test_memory_backend_uses_in_process_checkpointer():
    mgr = CheckpointerManager(backend="memory")
    cp = await mgr.open()
    assert type(cp).__name__ == "InMemorySaver"
    await mgr.close()


async def test_unknown_backend_raises():
    mgr = CheckpointerManager(backend="mysql")
    with pytest.raises(ValueError):
        await mgr.open()


async def test_postgres_backend_requires_url(monkeypatch):
    # No DB_URL configured -> clear, early failure (not a silent wrong default).
    from reverseloom.runtime import config
    monkeypatch.setattr(config, "DB_URL", "")
    mgr = CheckpointerManager(backend="postgres")
    with pytest.raises(RuntimeError):
        await mgr.open()

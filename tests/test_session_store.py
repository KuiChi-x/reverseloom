"""Session store CRUD (async SQLAlchemy, sqlite) — the metadata layer behind the
session sidebar & history."""
from reverseloom.conversation.store import SessionStore


async def _store():
    return await SessionStore().open()


async def test_create_and_list_sessions():
    st = await _store()
    try:
        await st.create_session("s1", "first task")
        sessions = await st.list_sessions()
        assert [s["id"] for s in sessions] == ["s1"]
        assert sessions[0]["title"] == "first task"
    finally:
        await st.close()


async def test_timestamps_are_iso_serializable():
    st = await _store()
    try:
        await st.touch_session("s1", "t")
        rows = await st.list_sessions()
        # must be strings (JSONResponse-safe), not datetime objects
        assert isinstance(rows[0]["updated_at"], str)
    finally:
        await st.close()


async def test_touch_creates_then_updates():
    st = await _store()
    try:
        await st.touch_session("s2", "created via touch")
        await st.touch_session("s2", "ignored on second call")
        rows = await st.list_sessions()
        assert len(rows) == 1 and rows[0]["title"] == "created via touch"
    finally:
        await st.close()


async def test_rename_and_delete():
    st = await _store()
    try:
        await st.create_session("s1", "orig")
        await st.rename_session("s1", "renamed")
        assert (await st.list_sessions())[0]["title"] == "renamed"
        await st.delete_session("s1")
        assert await st.list_sessions() == []
    finally:
        await st.close()

"""API layer: app boots with lifespan, checkpointer injected, REST endpoints work,
and the agent graph compiles with the find_fault node wired in."""
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient


def test_artifact_review_excludes_browser_observation_messages():
    import asyncio

    from reverseloom.agent.review import create_artifact_review_node

    captured = {}

    async def reviewer(state):
        captured.update(state)
        return {"end_tag": True}

    def node_factory(system_prompt, llm):
        assert system_prompt == "review prompt"
        assert llm == "model"
        return reviewer

    node = create_artifact_review_node("review prompt", "model", node_factory)
    result = asyncio.get_event_loop().run_until_complete(
        node({
            "observer_message_parts": ["browser screenshot"],
            "current_delivery_manifest": [{"path": "artifact.py"}],
            "past_steps": [{"result": "verified"}],
            "input_query": "build a scraper",
        })
    )

    assert result == {"end_tag": True}
    assert captured["observer_message_parts"] == []
    assert captured["current_delivery_manifest"] == [{"path": "artifact.py"}]
    assert captured["past_steps"] == [{"result": "verified"}]
    assert captured["input_query"] == "build a scraper"


def test_app_boots_and_injects_checkpointer():
    from reverseloom.web.server import app
    with TestClient(app) as c:
        assert c.get("/").status_code == 200
        assert type(app.state.checkpointer).__name__ == "AsyncSqliteSaver"
        assert app.state.store_ready is True


def test_app_shutdown_closes_browser_manager(monkeypatch):
    from reverseloom.web import server

    closed = []

    async def close_browser_manager():
        closed.append(True)

    monkeypatch.setattr(server.browser_manager, "close", close_browser_manager)
    with TestClient(server.create_app()) as client:
        assert client.get("/").status_code == 200

    assert closed == [True]


def test_session_and_history_endpoints():
    import asyncio

    from reverseloom.web.server import app

    state = {"events": [{
        "id": "user-1", "type": "message", "role": "user",
        "content": "hi", "created_at": "2026-07-13T00:00:00+00:00",
    }]}

    class FakeCheckpointer:
        async def aget_tuple(self, _config):
            return SimpleNamespace(checkpoint={"channel_values": state})

        async def adelete_thread(self, thread_id):
            assert thread_id == "demo"

    with TestClient(app) as c:
        app.state.checkpointer = FakeCheckpointer()
        assert c.get("/api/sessions").json() == []
        asyncio.run(app.state.store.touch_session("demo", "demo task"))
        ss = c.get("/api/sessions").json()
        assert [s["id"] for s in ss] == ["demo"]
        hist = c.get("/api/sessions/demo/history").json()
        assert hist["messages"][0]["role"] == "user"
        assert hist["messages"][0]["content"] == "hi"
        assert isinstance(hist["messages"][0]["created_at"], str)
        assert hist["past_steps"] == []
        assert hist["delivered_artifacts"] == []
        assert c.post("/api/sessions/demo/rename", json={"title": "x"}).json() == {"ok": True}
        assert c.delete("/api/sessions/demo").json() == {"ok": True}
        assert c.get("/api/sessions").json() == []


def test_delete_session_cancels_run_and_closes_browser(monkeypatch):
    import asyncio

    from reverseloom.web import server

    closed_sessions = []

    async def close_session(session_id):
        closed_sessions.append(session_id)

    monkeypatch.setattr(server.browser_manager, "close_session", close_session)

    app = server.create_app()
    with TestClient(app) as client:
        cancel_event = asyncio.Event()
        app.state.cancels["demo"] = cancel_event

        assert client.delete("/api/sessions/demo").json() == {"ok": True}

        assert cancel_event.is_set()
        assert "demo" not in app.state.cancels
        assert closed_sessions == ["demo"]

def test_history_endpoint_restores_checkpoint_steps():
    from reverseloom.web.server import app

    step = {
        "step_id": "step-1",
        "timestamp": "2026-07-10T06:13:01+00:00",
        "think": "????",
        "content": "????",
        "tool_calls": [],
        "result": "??",
        "has_error": False,
    }

    class FakeCheckpointer:
        async def aget_tuple(self, config):
            assert config == {
                "configurable": {"thread_id": "demo", "checkpoint_ns": ""}
            }
            return SimpleNamespace(
                checkpoint={"channel_values": {"events": [{"type": "step", "step": step}]}}
            )

    with TestClient(app) as client:
        app.state.checkpointer = FakeCheckpointer()
        history = client.get("/api/sessions/demo/history").json()

    assert history["past_steps"][0]["step_id"] == "step-1"
    assert history["timeline"][0]["type"] == "step"


def test_history_endpoint_reads_conversation_from_checkpoint():
    from reverseloom.web.server import app

    events = [
        {"id": "user-1", "type": "message", "role": "user", "content": "hi", "created_at": "2026-07-13T00:00:00+00:00"},
        {"id": "assistant-1", "type": "message", "role": "assistant", "content": "checkpoint reply", "created_at": "2026-07-13T00:00:01+00:00"},
    ]

    class FakeCheckpointer:
        async def aget_tuple(self, _config):
            return SimpleNamespace(checkpoint={"channel_values": {"events": events}})

    with TestClient(app) as client:
        app.state.checkpointer = FakeCheckpointer()
        history = client.get("/api/sessions/recover/history").json()

    assert [message["content"] for message in history["messages"]] == ["hi", "checkpoint reply"]
    assert history["timeline"] == events


def test_artifact_path_traversal_is_blocked():
    from reverseloom.web.server import app
    with TestClient(app) as c:
        # a path escaping the session's artifact dir must 404, not read arbitrary files
        r = c.get("/api/sessions/demo/artifact", params={"path": "../../../etc/hosts"})
        assert r.status_code == 404


def test_artifact_preview_supports_text_and_inline_metadata(tmp_path):
    from reverseloom.runtime import config
    from reverseloom.web.server import app

    artifact_dir = Path(config.artifact_dir("preview-session"))
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact = artifact_dir / "report.md"
    artifact.write_text("# Report\n\nUseful result", encoding="utf-8")

    with TestClient(app) as client:
        # Listing only surfaces deliver_artifact handoffs; bare files stay hidden.
        assert client.get("/api/sessions/preview-session/artifacts").json() == []
        preview = client.get(
            "/api/sessions/preview-session/artifact/preview",
            params={"path": "report.md"},
        ).json()

    assert preview["previewable"] is True
    assert preview["content_type"] == "markdown"
    assert preview["artifact_content"].startswith("# Report")
    assert preview["truncated"] is False


def test_list_artifacts_only_shows_delivered_manifest(monkeypatch, tmp_path):
    """Chat product rail must use deliver_artifact handoff, not delivery_status
    drafts / system captures such as browser_fingerprint.json."""
    from reverseloom.runtime import config
    from reverseloom.web.server import app

    session_id = "delivered-session"
    monkeypatch.setattr(config, "artifact_dir", lambda sid: str(tmp_path / sid))
    artifact_dir = Path(config.artifact_dir(session_id))
    artifact_dir.mkdir(parents=True, exist_ok=True)

    crawler = artifact_dir / "crawler.py"
    crawler.write_text("print('ok')\n", encoding="utf-8")
    fingerprint = artifact_dir / "browser_fingerprint.json"
    fingerprint.write_text('{"fingerprint": {}}', encoding="utf-8")

    class FakeCheckpointer:
        async def aget_tuple(self, config):
            assert config == {
                "configurable": {"thread_id": session_id, "checkpoint_ns": ""}
            }
            return SimpleNamespace(checkpoint={
                "channel_values": {
                    "current_delivery_manifest": [{
                        "path": str(crawler.resolve()),
                        "summary": "Standalone crawler",
                        "tags": ["kind:deliverable"],
                        "producer": "agent",
                    }],
                    # System captures may still sit in delivery_status, but must not
                    # surface through the user-facing artifacts list.
                }
            })

    with TestClient(app) as client:
        client.app.state.checkpointer = FakeCheckpointer()
        rows = client.get(f"/api/sessions/{session_id}/artifacts").json()
        history = client.get(f"/api/sessions/{session_id}/history").json()

    assert [row["path"] for row in rows] == ["crawler.py"]
    assert all(row["name"] != "browser_fingerprint.json" for row in rows)
    assert [row["path"] for row in history["delivered_artifacts"]] == ["crawler.py"]


def test_artifact_raw_returns_inline_content_with_detected_media_type(monkeypatch, tmp_path):
    from reverseloom.runtime import config
    from reverseloom.web.server import app

    monkeypatch.setattr(config, "artifact_dir", lambda session_id: str(tmp_path / session_id))
    artifact_dir = Path(config.artifact_dir("raw-preview-session"))
    artifact_dir.mkdir(parents=True, exist_ok=True)
    artifact = artifact_dir / "diagram.svg"
    artifact.write_text('<svg xmlns="http://www.w3.org/2000/svg"></svg>', encoding="utf-8")

    with TestClient(app) as client:
        response = client.get(
            "/api/sessions/raw-preview-session/artifact/raw",
            params={"path": "diagram.svg"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert "attachment" not in response.headers.get("content-disposition", "").lower()
    assert response.content == artifact.read_bytes()


def test_artifact_preview_path_traversal_is_blocked():
    from reverseloom.web.server import app

    with TestClient(app) as client:
        response = client.get(
            "/api/sessions/demo/artifact/preview",
            params={"path": "../../../etc/hosts"},
        )

    assert response.status_code == 404


def test_settings_read_and_write(monkeypatch, tmp_path):
    import os

    from reverseloom.runtime import settings as settings_io
    from reverseloom.web.server import app

    monkeypatch.setattr(settings_io, "_env_path", lambda: str(tmp_path / ".env"))
    with TestClient(app) as client:
        data = client.get("/api/settings").json()
        assert [group["id"] for group in data["groups"]] == [
            "model", "browser", "context", "storage"
        ]
        fields = [field for group in data["groups"] for field in group["fields"]]
        human_copy = "".join(
            str(group.get("label", "")) + str(group.get("description", ""))
            for group in data["groups"]
        ) + "".join(
            str(field.get("label", "")) + str(field.get("description", ""))
            for field in fields
        )
        assert "?" not in human_copy
        keys = {field["key"] for field in fields}
        assert {
            "MODEL_PROTOCOL",
            "MODEL_REASONING_EFFORT",
            "BASE_URL",
            "OPENAI_API_KEY",
            "MODEL",
            "REVERSELOOM_BROWSER_PATH",
            "REVERSELOOM_PROXY_HOST",
            "REVERSELOOM_PROXY_PORT",
            "REVERSELOOM_PROXY_USERNAME",
            "REVERSELOOM_PROXY_PASSWORD",
            "GRAPHLOOM_MODEL_CONTEXT_WINDOW",
            "REVERSELOOM_DB_BACKEND",
        } <= keys
        assert not {
            "MAX_TOKENS",
            "TEMPERATURE",
            "REVERSELOOM_PROXY_URL",
            "REVERSELOOM_REPLAY_USE_PROXY",
            "REVERSELOOM_REPLAY_PROXY_MAX_RETRIES",
            "REVERSELOOM_REPLAY_PROBE_TIMEOUT_SEC",
            "REVERSELOOM_REPLAY_PROBE_CONCURRENCY",
            "GRAPHLOOM_COMPACT_TRIGGER_RATIO",
            "GRAPHLOOM_COMPACT_TARGET_RATIO",
            "GRAPHLOOM_COMPACT_KEEP_RECENT",
            "GRAPHLOOM_COMPACT_MAX_RETRY",
            "GRAPHLOOM_COMPACT_EMERGENCY_TRUNC_CHARS",
            "SUBAGENT_MAX_CONCURRENCY",
        } & keys

        assert "MODEL_PROVIDER" not in keys
        protocol_field = next(field for field in fields if field["key"] == "MODEL_PROTOCOL")
        reasoning_field = next(
            field for field in fields if field["key"] == "MODEL_REASONING_EFFORT"
        )
        assert protocol_field["type"] == "select"
        assert protocol_field["default"] == "openai"
        protocol_values = [option["value"] for option in protocol_field["options"]]
        assert "openai" in protocol_values
        assert "openai/responses" in protocol_values
        assert "anthropic" in protocol_values
        assert "vertex_ai" in protocol_values
        assert "ollama" in protocol_values
        assert reasoning_field["type"] == "text"
        assert reasoning_field["placeholder"].endswith("max")

        api_key = next(field for field in fields if field["key"] == "OPENAI_API_KEY")
        proxy_password = next(
            field for field in fields if field["key"] == "REVERSELOOM_PROXY_PASSWORD"
        )
        assert api_key["secret"] is True
        assert proxy_password["secret"] is True
        assert "sk-test-dummy" not in str(api_key["value"])

        response = client.post("/api/settings", json={"MODEL": "gpt-4o"})
        assert response.status_code == 200
        assert response.json() == {
            "ok": True,
            "changed": ["MODEL"],
            "reconnect_required": True,
            "restart_required": False,
        }
        assert os.environ["MODEL"] == "gpt-4o"
        assert "MODEL=gpt-4o" in (tmp_path / ".env").read_text(encoding="utf-8")

        invalid = client.post(
            "/api/settings",
            json={"REVERSELOOM_PROXY_PORT": "70000"},
        )
        assert invalid.status_code == 400
        assert "65535" in invalid.json()["error"]


def test_build_llm_builds_litellm_model_from_selected_protocol(monkeypatch):
    from reverseloom.agent import build as build_module

    captured = {}

    class FakeChatModel:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("MODEL_PROTOCOL", "anthropic")
    monkeypatch.setenv("BASE_URL", "https://example.invalid")
    monkeypatch.setenv("OPENAI_API_KEY", "provider-key")
    monkeypatch.setenv("MODEL", "claude-sonnet-test")
    monkeypatch.setattr(build_module, "ChatLiteLLM", FakeChatModel)

    build_module.build_llm()

    assert captured == {
        "model": "anthropic/claude-sonnet-test",
        "api_base": "https://example.invalid",
        "api_key": "provider-key",
        "streaming": True,
    }


def test_build_llm_defaults_to_openai_compatible_protocol(monkeypatch):
    from reverseloom.agent import build as build_module

    captured = {}

    class FakeChatModel:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.delenv("MODEL_PROTOCOL", raising=False)
    monkeypatch.setenv("MODEL", "legacy-internal-model")
    monkeypatch.setattr(build_module, "ChatLiteLLM", FakeChatModel)

    build_module.build_llm()

    assert captured["model"] == "openai/legacy-internal-model"


def test_build_llm_passes_configured_reasoning_effort(monkeypatch):
    from reverseloom.agent import build as build_module

    captured = {}

    class FakeChatModel:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setenv("MODEL_PROTOCOL", "xai")
    monkeypatch.setenv("MODEL", "grok-test")
    monkeypatch.setenv("MODEL_REASONING_EFFORT", "max")
    monkeypatch.setattr(build_module, "ChatLiteLLM", FakeChatModel)

    build_module.build_llm()

    assert captured["model"] == "xai/grok-test"
    assert captured["model_kwargs"] == {"reasoning_effort": "max"}



def test_build_llm_enables_litellm_unsupported_parameter_dropping():
    import litellm

    from reverseloom.agent.build import build_llm

    assert litellm.drop_params is True
    assert build_llm is not None



def test_litellm_chunks_preserve_reasoning_and_tool_calls_for_graphloom():
    from langchain_core.messages import AIMessageChunk
    from langchain_litellm.chat_models.litellm import _convert_delta_to_message_chunk

    reasoning_chunk = _convert_delta_to_message_chunk(
        {
            "role": "assistant",
            "content": "",
            "reasoning_content": "inspect the page",
        },
        AIMessageChunk,
    )
    tool_chunk = _convert_delta_to_message_chunk(
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "index": 0,
                    "id": "call_probe",
                    "function": {
                        "name": "protocol_probe",
                        "arguments": '{"value":"OK"}',
                    },
                }
            ],
        },
        AIMessageChunk,
    )

    assert reasoning_chunk.additional_kwargs["reasoning_content"] == "inspect the page"
    assert tool_chunk.tool_call_chunks[0]["name"] == "protocol_probe"
    assert tool_chunk.tool_call_chunks[0]["args"] == '{"value":"OK"}'


def test_authenticated_proxy_settings_feed_local_tunnel(monkeypatch):
    import base64

    from reverseloom.runtime import config
    from reverseloom.browser.proxy import ProxyTunnel

    monkeypatch.setenv("REVERSELOOM_PROXY_HOST", "proxy.example.com")
    monkeypatch.setenv("REVERSELOOM_PROXY_PORT", "18080")
    monkeypatch.setenv("REVERSELOOM_PROXY_USERNAME", "team@tenant")
    monkeypatch.setenv("REVERSELOOM_PROXY_PASSWORD", "p:a/ss")

    proxy_url = config.build_proxy_url_from_env()
    assert proxy_url == "http://team%40tenant:p%3Aa%2Fss@proxy.example.com:18080"

    tunnel = ProxyTunnel(21000, proxy_url)
    assert tunnel.upstream_host == "proxy.example.com"
    assert tunnel.upstream_port == 18080
    assert base64.b64decode(tunnel.upstream_auth).decode() == "team@tenant:p:a/ss"


def test_agent_graph_compiles_with_find_fault():
    from reverseloom.agent.build import build_agent
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage

    class FakeLLM(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kw):
            return self
        def with_structured_output(self, schema, **kw):
            return self

    g = build_agent(llm=FakeLLM(responses=[AIMessage(content="ok")]))
    nodes = list(g.get_graph().nodes.keys())
    assert "find_fault" in nodes
    assert "observer" in nodes and "ai" in nodes and "tool" in nodes

def test_websocket_broadcasts_model_chunks(monkeypatch):
    from types import SimpleNamespace

    from reverseloom.web import server

    class Chunk:
        def __init__(self, content="", reasoning=""):
            self.content = content
            self.additional_kwargs = {"reasoning_content": reasoning} if reasoning else {}

    class FakeAgent:
        async def astream_events(self, _input, config, version):
            emitter = config["configurable"]["event_emitter"]
            assert emitter
            assert version == "v2"
            await emitter("ai_delta", {"reasoning": "??", "content": "??"})
            yield {"event": "on_chat_model_stream", "data": {"chunk": Chunk(reasoning="??")}}
            yield {"event": "on_chat_model_stream", "data": {"chunk": Chunk(content="??")}}
            yield {
                "event": "on_chain_end",
                "name": "LangGraph",
                "data": {"output": {"final_reply": "??"}},
            }

        async def aget_state(self, _config):
            return SimpleNamespace(tasks=(), values={"final_reply": "??"})

    monkeypatch.setattr(server, "build_llm", lambda: object())
    monkeypatch.setattr(server, "build_agent", lambda **_kwargs: FakeAgent())

    async def ignore_store(*_args, **_kwargs):
        return None

    with TestClient(server.app) as client:
        monkeypatch.setattr(server.app.state.store, "touch_session", ignore_store)
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"task": "test stream"})
            messages = [ws.receive_json() for _ in range(4)]

    session_id = messages[0]["session_id"]
    assert messages == [
        {"type": "run_start", "session_id": session_id},
        {"type": "reasoning", "text": "??", "session_id": session_id},
        {"type": "token", "text": "??", "session_id": session_id},
        {"type": "final", "text": "??", "session_id": session_id},
    ]


def test_websocket_does_not_broadcast_review_model_json(monkeypatch):
    from types import SimpleNamespace

    from reverseloom.web import server

    class Chunk:
        content = '{"is_acceptable": true}'
        additional_kwargs = {"reasoning_content": "review thought"}

    class FakeAgent:
        async def astream_events(self, _input, config, version):
            assert version == "v2"
            emitter = config["configurable"]["event_emitter"]
            await emitter("ai_delta", {"reasoning": "thinking", "content": "answer"})
            yield {
                "event": "on_chat_model_stream",
                "metadata": {"langgraph_node": "find_fault"},
                "data": {"chunk": Chunk()},
            }

        async def aget_state(self, _config):
            return SimpleNamespace(tasks=(), values={"final_reply": "answer"})

    async def ignore_store(*_args, **_kwargs):
        return None

    monkeypatch.setattr(server, "build_llm", lambda: object())
    monkeypatch.setattr(server, "build_agent", lambda **_kwargs: FakeAgent())

    with TestClient(server.app) as client:
        monkeypatch.setattr(server.app.state.store, "touch_session", ignore_store)
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"task": "test review filtering"})
            messages = [ws.receive_json() for _ in range(4)]

    assert [message["type"] for message in messages] == [
        "run_start", "reasoning", "token", "final"
    ]
    assert all("is_acceptable" not in message.get("text", "") for message in messages)

def test_websocket_announces_main_ai_turn_before_its_deltas(monkeypatch):
    from types import SimpleNamespace

    from reverseloom.web import server

    class FakeAgent:
        async def astream_events(self, _input, config, version):
            assert version == "v2"
            yield {
                "event": "on_chain_start",
                "name": "ai",
                "metadata": {"langgraph_node": "ai"},
            }
            yield {
                "event": "on_chain_start",
                "name": "RunnableSequence",
                "metadata": {"langgraph_node": "ai"},
            }
            emitter = config["configurable"]["event_emitter"]
            await emitter("ai_delta", {"reasoning": "next thought", "content": ""})
            yield {
                "event": "on_chain_start",
                "name": "find_fault",
                "metadata": {"langgraph_node": "find_fault"},
            }

        async def aget_state(self, _config):
            return SimpleNamespace(tasks=(), values={"final_reply": "done"})

    async def ignore_store(*_args, **_kwargs):
        return None

    monkeypatch.setattr(server, "build_llm", lambda: object())
    monkeypatch.setattr(server, "build_agent", lambda **_kwargs: FakeAgent())

    with TestClient(server.app) as client:
        monkeypatch.setattr(server.app.state.store, "touch_session", ignore_store)
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"task": "test turn boundary"})
            messages = []
            while len(messages) < 5:
                messages.append(ws.receive_json())
                if messages[-1]["type"] == "final":
                    break

    assert [message["type"] for message in messages] == [
        "run_start", "ai_turn_start", "reasoning", "final"
    ]

def test_websocket_stop_targets_the_requested_session(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from reverseloom.web import server


    class FakeAgent:
        async def astream_events(self, _input, config, version):
            assert version == "v2"
            assert config["configurable"]["thread_id"] == "running-one"
            emitter = config["configurable"]["event_emitter"]
            await emitter("ai_delta", {"content": "working", "reasoning": ""})
            yield {"event": "on_chain_start"}
            await asyncio.Event().wait()

        async def aget_state(self, _config):
            return SimpleNamespace(tasks=(), values={})

    async def ignore_store(*_args, **_kwargs):
        return None

    monkeypatch.setattr(server, "build_llm", lambda: object())
    monkeypatch.setattr(server, "build_agent", lambda **_kwargs: FakeAgent())

    with TestClient(server.app) as client:
        monkeypatch.setattr(server.app.state.store, "touch_session", ignore_store)
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"task": "long task", "session_id": "running-one"})
            assert ws.receive_json() == {"type": "run_start", "session_id": "running-one"}
            assert ws.receive_json() == {
                "type": "token",
                "text": "working",
                "session_id": "running-one",
            }
            ws.send_json({"stop": True, "session_id": "running-one"})
            assert ws.receive_json() == {
                "type": "paused",
                "text": "已暂停",
                "session_id": "running-one",
            }


def test_websocket_returns_streamed_direct_reply_without_message_store(monkeypatch):
    from reverseloom.web import server

    reply = "streamed reply"

    class FakeAgent:
        async def astream_events(self, _input, config, version):
            assert version == "v2"
            emitter = config["configurable"]["event_emitter"]
            await emitter("ai_delta", {"content": reply, "reasoning": ""})
            yield {
                "event": "on_chain_end",
                "name": "LangGraph",
                "data": {"output": {}},
            }

        async def aget_state(self, _config):
            return SimpleNamespace(tasks=(), values={"final_reply": ""})

    async def touch_session(*_args, **_kwargs):
        return None

    monkeypatch.setattr(server, "build_llm", lambda: object())
    monkeypatch.setattr(server, "build_agent", lambda **_kwargs: FakeAgent())

    with TestClient(server.app) as client:
        monkeypatch.setattr(server.app.state.store, "touch_session", touch_session)
        with client.websocket_connect("/ws") as ws:
            ws.send_json({"task": "hi"})
            messages = [ws.receive_json() for _ in range(3)]

    assert messages[-1] == {
        "type": "final",
        "text": reply,
        "session_id": messages[0]["session_id"],
    }


async def test_direct_reply_skips_artifact_review_and_finishes():
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage

    from graphloom import build_agent_graph

    class FakeLLM(FakeMessagesListChatModel):
        def bind_tools(self, _tools, **_kwargs):
            return self

    async def artifact_reviewer(_state):
        raise AssertionError("pure text replies must not enter artifact review")

    graph = build_agent_graph(
        custom_system_prompt="Reply directly.",
        tools=[],
        llm=FakeLLM(responses=[AIMessage(content="hello")]),
        custom_find_fault=artifact_reviewer,
        allow_direct_reply=True,
    )

    result = await graph.ainvoke(
        {"input_query": "hello", "session_id": "direct"},
        config={"recursion_limit": 5},
    )

    assert result["final_reply"] == "hello"
    assert result["agent_status"] == "done"


def test_agent_allows_direct_reply_for_non_execution_messages(monkeypatch):
    from reverseloom.agent import build as build_module

    captured = {}

    def fake_build_agent_graph(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(build_module, "build_agent_graph", fake_build_agent_graph)
    monkeypatch.setattr(build_module, "create_browser_observer_node", lambda tools: object())
    build_module.build_agent(llm=object())

    assert captured["allow_direct_reply"] is True


def test_agent_discovers_builtin_and_user_skills(monkeypatch):
    from reverseloom.agent import build as build_module

    captured = {}
    monkeypatch.setattr(
        build_module,
        "build_agent_graph",
        lambda **kwargs: captured.update(kwargs) or object(),
    )
    monkeypatch.setattr(build_module, "create_browser_observer_node", lambda tools: object())

    build_module.build_agent(llm=object())

    assert captured["available_skills"] == ["*"]
    assert captured["skills_dirs"] == build_module._SKILLS_DIRS
    assert captured["skills_dirs"][0] == build_module._SKILLS_DIR
    assert captured["skills_dirs"][1].endswith(".reverseloom\\skills") or captured["skills_dirs"][1].endswith(".reverseloom/skills")


def test_agent_always_exposes_all_tools(monkeypatch):
    from reverseloom.agent import build as build_module

    captured = {}
    monkeypatch.setattr(
        build_module,
        "build_agent_graph",
        lambda **kwargs: captured.update(kwargs) or object(),
    )
    monkeypatch.setattr(build_module, "create_browser_observer_node", lambda tools: object())

    build_module.build_agent(llm=object())

    assert captured["tools"] == list(build_module.ALL_TOOLS)
    enabled_names = {tool.name for tool in captured["tools"]}
    assert {"read_file", "write_file", "edit_file", "list_dir", "search_code", "run_shell"} <= enabled_names


def test_index_isolates_live_events_when_switching_sessions():
    from reverseloom.web.server import app

    with TestClient(app) as client:
        html = client.get("/").text

    assert "function confirmRunningSessionSwitch" in html
    assert "确认终止当前任务并切换会话" in html
    assert "ignoredLiveSessions" in html
    assert "m.session_id" in html
    assert "eventSessionId !== sessionId" in html
    assert "{stop:true, session_id:stoppingSessionId}" in html
    assert "let sessionLoadVersion = 0" in html


def test_index_has_immediate_feedback_and_step_scoped_status():
    from reverseloom.web.server import app

    with TestClient(app) as client:
        html = client.get("/").text

    assert "showPendingAgent" in html
    assert "findStepRail" in html
    assert "\u6b63\u5728\u7406\u89e3\u4efb\u52a1" in html
    assert "renderHistoricalStep" in html
    assert "past_steps" in html
    assert "???" not in html
    assert '<span class="ti">\u258c</span>' in html
    assert "\u7b49\u5f85\u56de\u590d" in html
    assert "\u5df2\u6682\u505c" in html
    assert "finish(); loadSessions(); void loadInlineArtifacts(b); down();" in html
    assert "sendBtn.classList.remove('stop')" in html
    assert "sendBtn.textContent='\u27a4'" in html
    assert "settings-shell" in html
    assert "renderSettingsGroup" in html
    assert "setSave').onclick = saveSettings" in html


def test_index_combines_step_review_and_next_action():
    from reverseloom.web.server import app

    with TestClient(app) as client:
        html = client.get("/").text

    assert "function renderStepContext" in html
    assert "[review, action].filter" in html
    assert ".join(' ')" in html
    assert "agent-step-review" not in html
    assert "?????" not in html


def test_long_external_links_are_compacted_without_changing_the_target():
    from reverseloom.web.server import app

    with TestClient(app) as client:
        html = client.get("/").text

    assert "function compactExternalLinkLabel" in html
    assert "function decorateRenderedLinks" in html
    assert "link.dataset.fullUrl=target.href" in html
    assert "link.textContent=compactExternalLinkLabel(target)" in html
    assert ".agent-text a.external-link" in html
    assert "renderMarkdown(curText" in html


def test_index_ignores_reasoning_that_arrives_after_visible_answer_content():
    from reverseloom.web.server import app

    with TestClient(app) as client:
        html = client.get("/").text

    assert "function hasStartedVisibleReply" in html
    assert "visibleReplyStarted || Boolean" in html
    assert "if (hasStartedVisibleReply()) return;" in html
    assert "} else if (m.type === 'final') {\n    visibleReplyStarted = true;" in html


def test_index_starts_next_step_in_a_provisional_turn():
    from reverseloom.web.server import app

    with TestClient(app) as client:
        html = client.get("/").text

    assert "function beginAgentTurn()" in html
    assert "m.type === 'ai_turn_start'" in html
    assert "if (!pendingAgent && running && !activeStepId) showPendingAgent();" in html
    assert "if (!m.step_id || m.step_id === activeStepId)" not in html


def test_attachment_upload_and_image_prompt_parts():
    from reverseloom.runtime import config
    from reverseloom.web import server

    png = b"\x89PNG\r\n\x1a\n" + b"image-bytes"
    with TestClient(server.app) as client:
        response = client.post(
            "/api/sessions/upload-demo/attachments",
            params={"filename": "screen.png"},
            headers={"Content-Type": "image/png"},
            content=png,
        )

    assert response.status_code == 200
    metadata = response.json()
    assert metadata["name"] == "screen.png"
    assert metadata["content_type"] == "image"
    assert (Path(config.attachment_dir("upload-demo")) / metadata["path"]).read_bytes() == png

    parts, manifest, names = server._prepare_attachment_inputs("upload-demo", [metadata])
    assert names == ["screen.png"]
    assert parts[0]["type"] == "image_url"
    assert parts[0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert parts[0]["image_url"]["detail"] == "high"
    assert manifest == []


def test_attachment_upload_rejects_unsupported_files():
    from reverseloom.web import server

    with TestClient(server.app) as client:
        response = client.post(
            "/api/sessions/upload-demo/attachments",
            params={"filename": "payload.exe"},
            headers={"Content-Type": "application/octet-stream"},
            content=b"not-allowed",
        )

    assert response.status_code == 400
    assert "PDF" in response.json()["error"]


def test_index_supports_image_and_pdf_attachments():
    from reverseloom.web.server import app

    with TestClient(app) as client:
        html = client.get("/").text

    assert 'id="attachInput"' in html
    assert "application/pdf" in html
    assert "async function uploadFiles" in html
    assert "pendingAttachments" in html
    assert "attachments, session_id" in html
    assert "collectClipboardFiles" in html
    assert "composerBox').addEventListener('paste'" in html or 'composerBox").addEventListener("paste"' in html or "$('composerBox').addEventListener('paste'" in html


def test_pdf_attachment_is_sent_as_native_file_content():
    from reverseloom.web import server

    pdf_bytes = b"%PDF-1.4\n% native file payload regression\n"

    with TestClient(server.app) as client:
        response = client.post(
            "/api/sessions/pdf-demo/attachments",
            params={"filename": "reference.pdf"},
            headers={"Content-Type": "application/pdf"},
            content=pdf_bytes,
        )

    metadata = response.json()
    parts, manifest, names = server._prepare_attachment_inputs("pdf-demo", [metadata])
    assert response.status_code == 200
    assert names == ["reference.pdf"]
    assert parts[0] == {
        "type": "file",
        "file": {
            "filename": "reference.pdf",
            "file_data": parts[0]["file"]["file_data"],
        },
    }
    assert parts[0]["file"]["file_data"].startswith("data:application/pdf;base64,")
    assert manifest == []


def test_missing_credentials_uses_single_configuration_notice(monkeypatch):
    from reverseloom.web import server

    monkeypatch.setattr(
        server,
        "build_llm",
        lambda: (_ for _ in ()).throw(RuntimeError("Missing credentials")),
    )
    with TestClient(server.create_app()) as client:
        with client.websocket_connect("/ws") as ws:
            assert ws.receive_json() == {
                "type": "config_required",
                "text": "请先在配置中心填写模型服务地址、API Key 和模型。",
            }


def test_index_deduplicates_missing_credentials_and_stops_reconnecting():
    from reverseloom.web.server import app

    with TestClient(app) as client:
        html = client.get("/").text

    assert "function showConfigNotice" in html
    assert "clearConfigNotice" in html
    assert "m.type === 'config_required'" in html
    assert "event.code === 4001" in html
    assert "if (configRequired || reconnectTimer) return" in html
    assert "showConfigNotice(m.text)" in html
    assert "configRequired = false; clearConfigNotice();" in html

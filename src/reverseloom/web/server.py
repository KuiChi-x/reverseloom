"""FastAPI + WebSocket backend for the reverseloom web UI.

Streams the agent loop to the browser: main-agent deltas come through the
graph event emitter, while LangGraph's `astream_events` supplies lifecycle and
final-state events. Internal reviewer/model streams are intentionally hidden.
"""
import asyncio
import base64
import json
import mimetypes
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict
from uuid import uuid4

from langgraph.types import Command

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles


from reverseloom.runtime import config
from reverseloom.runtime import settings as settings_io
from reverseloom.runtime.checkpoints import CheckpointerManager
from reverseloom.conversation.store import SessionStore

# static/ lives at the package root (reverseloom/static), one level up from web/.
_STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")


class _LazyBrowserManager:
    async def close(self):
        from reverseloom.browser import browser_manager

        return await browser_manager.close()

    async def close_session(self, session_id: str):
        from reverseloom.browser import browser_manager

        return await browser_manager.close_session(session_id)


browser_manager = _LazyBrowserManager()


async def _send(ws: WebSocket, payload: Dict[str, Any]) -> None:
    try:
        await ws.send_text(json.dumps(payload, ensure_ascii=False))
    except Exception:
        pass


def _log_ws_fatal(context: str, exc: BaseException) -> None:
    """Persist a full traceback to the log dir. Frozen builds run with
    console=False, so an uncaught error is otherwise invisible; this is how a
    packaged WebSocket failure becomes diagnosable."""
    import traceback

    try:
        from reverseloom.runtime.paths import default_log_dir

        log_dir = default_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        with open(log_dir / "ws-errors.log", "a", encoding="utf-8") as handle:
            handle.write(f"\n=== {datetime.now(timezone.utc).isoformat()} {context} ===\n")
            handle.write("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
    except Exception:
        # Diagnostics must never mask the original failure.
        pass


async def _delete_checkpoint_thread(checkpointer: Any, session_id: str) -> None:
    if checkpointer is None or not hasattr(checkpointer, "adelete_thread"):
        return
    try:
        await checkpointer.adelete_thread(session_id)
    except Exception:
        pass


_ATTACHMENT_MAX_BYTES = 20 * 1024 * 1024
_IMAGE_ATTACHMENT_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def _safe_attachment_name(filename: str) -> str:
    name = os.path.basename(str(filename or "").replace("\\", "/")).strip()
    name = re.sub(r"[^A-Za-z0-9._()一-鿿 -]+", "_", name)
    return name[:180] or "attachment"


def _attachment_target(session_id: str, relative_path: str) -> str | None:
    base = os.path.realpath(config.attachment_dir(session_id))
    target = os.path.realpath(os.path.join(base, str(relative_path or "")))
    try:
        inside = os.path.commonpath([base, target]) == base
    except ValueError:
        inside = False
    return target if inside and os.path.isfile(target) else None


def _save_attachment(session_id: str, filename: str, mime_type: str, content: bytes) -> Dict[str, Any]:
    if not content:
        raise ValueError("附件为空")
    if len(content) > _ATTACHMENT_MAX_BYTES:
        raise ValueError("单个附件不能超过 20 MB")

    safe_name = _safe_attachment_name(filename)
    extension = os.path.splitext(safe_name)[1].lower()
    normalized_mime = str(mime_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream").lower()
    if extension in _IMAGE_ATTACHMENT_EXTENSIONS and normalized_mime.startswith("image/"):
        kind = "image"
    elif extension == ".pdf" and normalized_mime in {"application/pdf", "application/octet-stream"}:
        kind = "pdf"
        normalized_mime = "application/pdf"
    else:
        raise ValueError("仅支持 PNG、JPEG、GIF、WebP 图片和 PDF 文件")

    base = config.attachment_dir(session_id)
    os.makedirs(base, exist_ok=True)
    stored_name = f"{uuid4().hex[:10]}-{safe_name}"
    target = os.path.join(base, stored_name)
    with open(target, "wb") as handle:
        handle.write(content)
    return {
        "path": stored_name,
        "name": safe_name,
        "size": len(content),
        "mime_type": normalized_mime,
        "content_type": kind,
    }


def _prepare_attachment_inputs(session_id: str, attachments: Any) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]], list[str]]:
    message_parts: list[Dict[str, Any]] = []
    names: list[str] = []
    for item in attachments if isinstance(attachments, list) else []:
        if not isinstance(item, dict):
            continue
        target = _attachment_target(session_id, str(item.get("path") or ""))
        if target is None:
            raise ValueError("附件不存在或不属于当前会话")
        name = _safe_attachment_name(str(item.get("name") or os.path.basename(target)))
        extension = os.path.splitext(target)[1].lower()
        mime_type = str(item.get("mime_type") or mimetypes.guess_type(name)[0] or "application/octet-stream")
        with open(target, "rb") as handle:
            encoded = base64.b64encode(handle.read()).decode("ascii")
        data_url = f"data:{mime_type};base64,{encoded}"
        names.append(name)

        if extension in _IMAGE_ATTACHMENT_EXTENSIONS:
            message_parts.append({
                "type": "image_url",
                "image_url": {"url": data_url, "detail": "high"},
            })
        elif extension == ".pdf":
            message_parts.append({
                "type": "file",
                "file": {
                    "filename": name,
                    "file_data": data_url,
                },
            })

    # Binary user attachments are sent directly to the model. Do not expose
    # their local paths through input_artifact_manifest: read_artifact is a text
    # tool and would otherwise be selected for PNG/PDF files.
    return message_parts, [], names


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.event_loop = asyncio.get_running_loop()
        # Open the switchable checkpointer (sqlite by default) for the app's
        # lifetime and compile one shared agent wired to it. Conversation state
        # is keyed per-connection by thread_id, so one agent serves all sessions.
        app.state.ckpt = CheckpointerManager()
        try:
            checkpointer = await app.state.ckpt.open()
        except Exception as exc:
            # Persistence must not take the whole server down; fall back to
            # memory (no resume) and surface the reason in logs.
            print(f"[reverseloom] checkpointer disabled: {type(exc).__name__}: {exc}")
            checkpointer = None
        app.state.checkpointer = checkpointer
        # Per-session asyncio.Event for the stop button (co-op cancellation).
        app.state.cancels = {}
        # Session/message metadata store (sqlite/postgres, same switch).
        app.state.store = SessionStore()
        try:
            await app.state.store.open()
            app.state.store_ready = True
        except Exception as exc:
            print(f"[reverseloom] session store disabled: {type(exc).__name__}: {exc}")
            app.state.store_ready = False
        try:
            yield
        finally:
            try:
                await browser_manager.close()
            finally:
                app.state.event_loop = None
                await app.state.ckpt.close()
                if getattr(app.state, "store_ready", False):
                    await app.state.store.close()

    app = FastAPI(title="reverseloom", lifespan=lifespan)

    @app.get("/")
    async def index() -> HTMLResponse:
        with open(os.path.join(_STATIC_DIR, "index.html"), "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())

    if os.path.isdir(_STATIC_DIR):
        app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    # --- session / history REST -------------------------------------------------
    @app.get("/api/sessions")
    async def list_sessions(request: Request):
        if not getattr(request.app.state, "store_ready", False):
            return JSONResponse([])
        return JSONResponse(await request.app.state.store.list_sessions())

    @app.get("/api/sessions/{session_id}/history")
    async def session_history(session_id: str, request: Request):
        checkpointer = getattr(request.app.state, "checkpointer", None)
        if checkpointer is None:
            return JSONResponse({
                "messages": [],
                "past_steps": [],
                "timeline": [],
                "delivered_artifacts": [],
            })
        checkpoint = await checkpointer.aget_tuple({
            "configurable": {"thread_id": session_id, "checkpoint_ns": ""}
        })
        values = (checkpoint.checkpoint.get("channel_values") or {}) if checkpoint else {}
        timeline = list(values.get("events", []) or [])
        # Latest agent delivery only (finish node copies current_delivery_manifest
        # into approved_artifact_manifest). Attach to the last assistant reply in UI.
        delivered = _manifest_artifacts(
            session_id,
            list(values.get("current_delivery_manifest") or values.get("approved_artifact_manifest") or []),
        )
        return JSONResponse({
            "messages": [event for event in timeline if event.get("type") == "message"],
            "past_steps": [event.get("step") for event in timeline if event.get("type") == "step"],
            "timeline": timeline,
            "delivered_artifacts": delivered,
        })

    @app.post("/api/sessions/{session_id}/rename")
    async def rename_session(session_id: str, request: Request):
        body = await request.json()
        if getattr(request.app.state, "store_ready", False):
            await request.app.state.store.rename_session(session_id, str(body.get("title") or ""))
        return JSONResponse({"ok": True})

    @app.delete("/api/sessions/{session_id}")
    async def delete_session(session_id: str, request: Request):
        cancel_event = request.app.state.cancels.pop(session_id, None)
        if cancel_event is not None:
            cancel_event.set()
        try:
            await browser_manager.close_session(session_id)
        finally:
            await _delete_checkpoint_thread(
                getattr(request.app.state, "checkpointer", None),
                session_id,
            )
            if getattr(request.app.state, "store_ready", False):
                await request.app.state.store.delete_session(session_id)
        return JSONResponse({"ok": True})

    # --- user attachments (per-session) ---------------------------------------
    @app.post("/api/sessions/{session_id}/attachments")
    async def upload_attachment(session_id: str, request: Request, filename: str):
        try:
            metadata = _save_attachment(
                session_id,
                filename,
                request.headers.get("content-type", ""),
                await request.body(),
            )
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse(metadata)

    # --- artifact panel (per-session) ------------------------------------------
    text_preview_extensions = {
        ".txt", ".log", ".json", ".jsonl", ".csv", ".tsv", ".xml",
        ".html", ".htm", ".css", ".js", ".mjs", ".cjs", ".ts", ".tsx",
        ".jsx", ".py", ".java", ".go", ".rs", ".c", ".h", ".cpp",
        ".hpp", ".sh", ".ps1", ".sql", ".yaml", ".yml", ".toml",
        ".ini", ".cfg", ".env",
    }
    image_preview_extensions = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
    preview_limit = 512 * 1024

    def artifact_target(session_id: str, relative_path: str) -> tuple[str, str] | None:
        base = os.path.realpath(config.artifact_dir(session_id))
        target = os.path.realpath(os.path.join(base, relative_path))
        try:
            inside = os.path.commonpath([base, target]) == base
        except ValueError:
            inside = False
        if not inside or not os.path.isfile(target):
            return None
        return base, target

    def artifact_metadata(base: str, target: str) -> Dict[str, Any]:
        relative_path = os.path.relpath(target, base).replace("\\", "/")
        extension = os.path.splitext(target)[1].lower()
        mime_type = mimetypes.guess_type(target)[0] or "application/octet-stream"
        if extension in {".md", ".markdown"}:
            content_type = "markdown"
            mime_type = "text/markdown"
        elif extension in image_preview_extensions:
            content_type = "image"
        elif extension == ".pdf":
            content_type = "pdf"
            mime_type = "application/pdf"
        elif mime_type.startswith("text/") or extension in text_preview_extensions:
            content_type = "text"
        else:
            content_type = "binary"
        stat = os.stat(target)
        return {
            "path": relative_path,
            "name": os.path.basename(target),
            "size": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            "content_type": content_type,
            "mime_type": mime_type,
            "previewable": content_type != "binary",
        }

    def _manifest_artifacts(session_id: str, manifest: list | None) -> list[Dict[str, Any]]:
        """Normalize a delivery/approved manifest into UI artifact cards.

        Only files the agent actually handed off via deliver_artifact should reach
        the chat rail — never the full delivery_status registry (which also holds
        system captures like browser_fingerprint.json and draft dumps).
        """
        base = os.path.abspath(config.artifact_dir(session_id))
        out: list[Dict[str, Any]] = []
        seen: set[str] = set()
        for raw in list(manifest or []):
            if not isinstance(raw, dict):
                continue
            raw_path = str(raw.get("path") or "").strip()
            if not raw_path:
                continue
            target = os.path.realpath(raw_path)
            if target in seen or not os.path.isfile(target):
                continue
            try:
                if os.path.commonpath([base, target]) != base:
                    continue
            except ValueError:
                continue
            seen.add(target)
            try:
                meta = artifact_metadata(base, target)
            except OSError:
                continue
            meta["summary"] = str(raw.get("summary") or "")
            meta["tags"] = [str(tag) for tag in (raw.get("tags") or [])]
            meta["producer"] = str(raw.get("producer") or "")
            out.append(meta)
        out.sort(key=lambda item: (item.get("modified_at") or "", item.get("path") or ""))
        return out

    @app.get("/api/sessions/{session_id}/artifacts")
    async def list_artifacts(session_id: str, request: Request):
        """List only artifacts the agent delivered via deliver_artifact.

        Does not expose the full delivery_status registry (drafts, dumps, and
        system runtime files such as browser_fingerprint.json).
        """
        checkpointer = getattr(request.app.state, "checkpointer", None)
        if checkpointer is None:
            return JSONResponse([])
        checkpoint = await checkpointer.aget_tuple({
            "configurable": {"thread_id": session_id, "checkpoint_ns": ""}
        })
        values = (checkpoint.checkpoint.get("channel_values") or {}) if checkpoint else {}
        return JSONResponse(_manifest_artifacts(
            session_id,
            list(values.get("current_delivery_manifest") or values.get("approved_artifact_manifest") or []),
        ))

    @app.get("/api/sessions/{session_id}/artifact/preview")
    async def preview_artifact(session_id: str, path: str):
        resolved = artifact_target(session_id, path)
        if resolved is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        base, target = resolved
        metadata = artifact_metadata(base, target)
        if metadata["content_type"] in {"image", "pdf"}:
            return JSONResponse({**metadata, "artifact_content": "", "truncated": False})
        if not metadata["previewable"]:
            return JSONResponse({**metadata, "artifact_content": "", "truncated": False})
        with open(target, "rb") as handle:
            raw = handle.read(preview_limit + 1)
        truncated = len(raw) > preview_limit
        content = raw[:preview_limit].decode("utf-8", errors="replace")
        return JSONResponse({**metadata, "artifact_content": content, "truncated": truncated})

    @app.get("/api/sessions/{session_id}/artifact/raw")
    async def raw_artifact(session_id: str, path: str):
        resolved = artifact_target(session_id, path)
        if resolved is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        _base, target = resolved
        mime_type = mimetypes.guess_type(target)[0] or "application/octet-stream"
        return FileResponse(target, media_type=mime_type)

    @app.get("/api/sessions/{session_id}/artifact")
    async def read_artifact(session_id: str, path: str):
        """Download one artifact; path is confined to the session directory."""
        resolved = artifact_target(session_id, path)
        if resolved is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        _base, target = resolved
        return FileResponse(target, filename=os.path.basename(target))

    # --- settings (gear panel; reads/writes .env) -------------------------------
    @app.get("/api/settings")
    async def get_settings():
        return JSONResponse(settings_io.read_settings())

    @app.post("/api/settings")
    async def save_settings(request: Request):
        body = await request.json()
        try:
            result = settings_io.write_settings(body or {})
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return JSONResponse({"ok": True, **result})

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        # Importing the agent stack pulls in graphloom/langgraph/litellm lazily.
        # In a frozen build a missing hidden import raises here; without this
        # guard the exception escapes ws_endpoint, the socket is torn down, and
        # the client reconnect-loops with no diagnosis. Log it and tell the UI.
        try:
            from reverseloom.agent.build import build_llm, build_agent
        except Exception as exc:
            _log_ws_fatal("agent import failed", exc)
            await _send(ws, {
                "type": "error",
                "text": f"引擎加载失败：{type(exc).__name__}: {exc}",
            })
            await ws.close(code=1011)
            return

        try:
            llm = build_llm()
        except Exception:
            await ws.send_text(json.dumps({
                "type": "config_required",
                "text": "请先在配置中心填写模型服务地址、API Key 和模型。",
            }, ensure_ascii=False))
            await ws.close(code=4001)
            return

        try:
            agent = build_agent(llm=llm, checkpointer=ws.app.state.checkpointer)
        except Exception as exc:
            _log_ws_fatal("build_agent failed", exc)
            await _send(ws, {
                "type": "error",
                "text": f"引擎初始化失败：{type(exc).__name__}: {exc}",
            })
            await ws.close(code=1011)
            return
        store = ws.app.state.store if getattr(ws.app.state, "store_ready", False) else None
        session_id = uuid4().hex[:12]
        run_task: asyncio.Task | None = None
        active_run_session_id: str | None = None
        send_lock = asyncio.Lock()

        async def _send_run(run_session_id: str, payload: Dict[str, Any]) -> None:
            async with send_lock:
                await _send(ws, {**payload, "session_id": run_session_id})

        async def _run_agent(msg: dict, run_session_id: str):
            """Run one session's agent graph and tag every streamed event."""
            raw_attachments = msg.get("attachments") or []
            try:
                attachment_parts, input_manifest, attachment_names = _prepare_attachment_inputs(
                    run_session_id, raw_attachments
                )
            except ValueError as exc:
                await _send_run(run_session_id, {"type": "error", "text": str(exc)})
                return
            task_text = str(msg.get("task") or "").strip() or ("请分析这些附件。" if attachment_names else "")
            display_text = task_text + ("\n\n附件：" + "、".join(attachment_names) if attachment_names else "")
            artifact_directory = config.artifact_dir(run_session_id)
            os.makedirs(artifact_directory, exist_ok=True)

            if store is not None and task_text:
                await store.touch_session(run_session_id, title_if_empty=task_text[:80])

            await _send_run(run_session_id, {"type": "run_start"})

            cancel_event = ws.app.state.cancels.setdefault(run_session_id, asyncio.Event())
            cancel_event.clear()

            is_resume = "resume" in msg
            if is_resume:
                invoke_input = Command(
                    resume=msg["resume"],
                    update={
                        "attach_message_parts": attachment_parts or None,
                        "input_artifact_manifest": input_manifest,
                    },
                )
            else:
                invoke_input = {
                    "input_query": display_text,
                    "session_id": run_session_id,
                    "attach_message_parts": attachment_parts or None,
                    "input_artifact_manifest": input_manifest,
                }

            streamed_reply_parts: list[str] = []

            async def _emit(event_type, payload):
                if event_type == "ai_delta":
                    reasoning = str(payload.get("reasoning") or "")
                    content = str(payload.get("content") or "")
                    if reasoning:
                        await _send_run(run_session_id, {"type": "reasoning", "text": reasoning})
                    if content:
                        streamed_reply_parts.append(content)
                        await _send_run(run_session_id, {"type": "token", "text": content})
                elif event_type == "step_planned":
                    await _send_run(run_session_id, {"type": "step_start", **payload})
                elif event_type == "tool_start":
                    await _send_run(run_session_id, {"type": "tool_start", **payload})
                elif event_type == "tool_end":
                    await _send_run(run_session_id, {"type": "tool_end", **payload})
                elif event_type == "step_done":
                    await _send_run(run_session_id, {"type": "step_done", **payload})

            run_config = {
                "recursion_limit": 1000,
                "configurable": {
                    "thread_id": run_session_id,
                    "checkpoint_ns": "",
                    "cancel_event": cancel_event,
                    "event_emitter": _emit,
                    "runtime_context": {
                        "artifact_dir": artifact_directory,
                        "session_id": run_session_id,
                        "user_id": config.cookie_user_id(run_session_id),
                    },
                },
            }
            try:
                final_reply = None
                async for ev in agent.astream_events(invoke_input, config=run_config, version="v2"):
                    kind = ev["event"]
                    if kind == "on_chain_start" and ev.get("name") == "ai":
                        await _send_run(run_session_id, {"type": "ai_turn_start"})
                    elif kind == "on_chain_end" and ev.get("name") == "LangGraph":
                        out = (ev.get("data") or {}).get("output") or {}
                        if isinstance(out, dict) and out.get("final_reply"):
                            final_reply = out["final_reply"]
                # Check for interrupts (HITL or stop-button pause).
                snapshot = await agent.aget_state(run_config)
                interrupts = []
                for t in (snapshot.tasks or ()):
                    for intr in (getattr(t, "interrupts", None) or ()):
                        interrupts.append(intr)
                if interrupts:
                    intr = interrupts[0]
                    payload = getattr(intr, "value", None) or {}
                    if isinstance(payload, tuple):
                        payload = {"reason": str(payload)}
                    await _send_run(run_session_id, {
                        "type": "user_interaction",
                        "interaction_id": getattr(intr, "id", ""),
                        **(payload if isinstance(payload, dict) else {"reason": str(payload)}),
                    })
                else:
                    vals = snapshot.values or {}
                    final_reply = str(
                        vals.get("final_reply")
                        or final_reply
                        or "".join(streamed_reply_parts)
                    ).strip()
                    delivered_artifacts = _manifest_artifacts(
                        run_session_id,
                        list(
                            vals.get("current_delivery_manifest")
                            or vals.get("approved_artifact_manifest")
                            or []
                        ),
                    )
                    await _send_run(run_session_id, {
                        "type": "final",
                        "text": final_reply or "(no final reply)",
                        "artifacts": delivered_artifacts,
                    })
            except asyncio.CancelledError:
                await _send_run(run_session_id, {"type": "paused", "text": "已暂停"})
            except Exception as exc:
                await _send_run(run_session_id, {"type": "error", "text": f"{type(exc).__name__}: {exc}"})
            finally:
                ws.app.state.cancels.pop(run_session_id, None)
                # Trim this thread's checkpoint history so the DB doesn't grow without bound.
                await ws.app.state.ckpt.prune_thread(run_session_id)

        try:
            while True:
                raw = await ws.receive_text()
                msg = json.loads(raw)

                # Stop button: cancel the running task immediately.
                if msg.get("stop"):
                    stopping_session_id = str(msg.get("session_id") or session_id)
                    ce = ws.app.state.cancels.get(stopping_session_id)
                    if ce is not None:
                        ce.set()
                    if (
                        run_task
                        and not run_task.done()
                        and active_run_session_id == stopping_session_id
                    ):
                        run_task.cancel()
                    continue

                if msg.get("session_id"):
                    session_id = str(msg["session_id"])

                task_text = str(msg.get("task") or "").strip()
                if not task_text and not msg.get("resume") and not msg.get("attachments"):
                    continue

                # If a previous run is still going, cancel it first.
                if run_task and not run_task.done():
                    run_task.cancel()
                    try:
                        await run_task
                    except (asyncio.CancelledError, Exception):
                        pass

                # Launch the agent run as a background task so the WS loop
                # stays responsive to stop/resume messages.
                active_run_session_id = session_id
                run_task = asyncio.create_task(_run_agent(msg, active_run_session_id))

        except WebSocketDisconnect:
            if run_task and not run_task.done():
                run_task.cancel()
            return

    return app


app = create_app()

"""Browser observer node.

Runs before `ai` every turn. Captures a FRESH browser snapshot (URL, DOM
digest, tabs, debugger/breakpoint state, screenshot) from the live session and
returns it as `observer_message_parts` — graphloom writes this field with
overwrite semantics and never persists it into `past_steps`. That's exactly what
a browser agent needs: the page state is huge and changes every turn, so only
the latest snapshot is injected, keeping the agent grounded without exploding
its memory.
"""
import logging
from typing import Any, Dict, List

from langchain_core.messages import HumanMessage
from langchain_core.runnables.config import RunnableConfig

from reverseloom.browser.browser_manager import browser_manager
from reverseloom.browser.browser_snapshot import capture_browser_snapshot
from reverseloom.runtime import config as config_module

# Reverse-engineering tools that make the observer include debugger state.
_DEBUGGER_TOOL_NAMES = {
    "search_in_network_payloads",
    "inspect_network_request",
    "break_on_request",
    "set_line_breakpoint",
    "get_paused_state",
    "evaluate_in_call_frame",
    "step_execution",
    "get_script_source",
    "search_in_js_codes",
    "extract_webpack_loader",
    "dump_runtime_asset",
}


def _tool_name_set(tools: List[Any]) -> set:
    names: set = set()
    for tool in tools:
        tool_name = str(getattr(tool, "name", "") or "").strip()
        if tool_name:
            names.add(tool_name)
    return names


async def _build_cdp_state_async(session) -> str:
    cdp = session.cdp_handler
    if not cdp.is_paused:
        return "Debugger state: RUNNING (not paused)"

    event = cdp.last_paused_event
    if not event:
        return "Debugger state: RUNNING (not paused)"

    from reverseloom.tools.browser.investigation import build_paused_state_report
    cdp_client = await browser_manager.get_cdp_client(session.session_id)
    report = await build_paused_state_report(session, cdp_client, event, frame_index=0)
    return f"Debugger state: PAUSED\n{report}"


def _build_browser_state_text(snapshot: Dict[str, Any]) -> str:
    parts = [
        f"Current URL: {snapshot.get('url', 'about:blank')}",
        f"Current Page Title: {snapshot.get('current_title', '')}",
        f"Active Tab Index: {snapshot.get('active_tab_index')}",
        "Open Tabs Summary:",
        str(snapshot.get("tabs_info", "")),
        "Page elements:",
        str(snapshot.get("dom_content", "")),
    ]

    debugger_state = str(snapshot.get("cdp_state") or "").strip()
    if debugger_state:
        parts.append(f"Browser Debugger/Breakpoint State: {debugger_state}")

    return "\n".join(parts)


def _build_observer_message(snapshot: Dict[str, Any]) -> List[HumanMessage]:
    observer_messages: List[HumanMessage] = []

    browser_state_text = _build_browser_state_text(snapshot)
    if browser_state_text.strip():
        observer_messages.append(HumanMessage(content=browser_state_text))

    screenshot = str(snapshot.get("screenshot") or "").strip()
    if screenshot:
        observer_messages.append(HumanMessage(content=[
            {"type": "text", "text": "<browser_vision>"},
            {
                "type": "image_url",
                "image_url": {"url": screenshot, "detail": "high"},
            },
            {"type": "text", "text": "</browser_vision>"},
        ]))

    return observer_messages


def create_browser_observer_node(*, tools: List[Any]):
    tool_names = _tool_name_set(tools)
    include_debugger = bool(tool_names & _DEBUGGER_TOOL_NAMES)

    async def browser_observer_node(state: Dict[str, Any], config: RunnableConfig) -> Dict[str, Any]:
        session_id = str(state.get("session_id") or "default")
        # user_id is injected under configurable.runtime_context (see server.py)
        configurable = (config or {}).get("configurable", {}) or {}
        runtime_context = configurable.get("runtime_context", {}) or {}
        user_id = str(
            runtime_context.get("user_id")
            or configurable.get("user_id")
            or config_module.cookie_user_id(session_id)
        )
        logging.info(
            "[BrowserObserverNode] Capturing state for session: %s (include_debugger=%s)",
            session_id, include_debugger,
        )

        session = await browser_manager.get_or_create_session(session_id, user_id)

        snapshot = await capture_browser_snapshot(session)
        snapshot["cdp_state"] = (
            await _build_cdp_state_async(session)
            if include_debugger else "Debugger monitoring is disabled."
        )

        return {"observer_message_parts": _build_observer_message(snapshot)}

    return browser_observer_node

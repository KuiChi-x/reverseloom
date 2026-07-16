import asyncio
import json
import logging
import math
import random
from typing import Optional, List, Literal

from pydantic import Field
from graphloom import StandardThoughtInput
from langchain_core.tools import tool
from reverseloom.browser.browser_manager import browser_manager
from reverseloom.browser.element_geometry import get_center as _element_center
from reverseloom.tools.browser.assistance import request_user_interaction
from reverseloom.tools.browser.result_handler import handle_tool_result
from reverseloom.browser.element_mapping_service import element_mapping_service
from reverseloom.tools.browser.visual import visual_locate


# ==========================================
# Navigation Tools
# ==========================================

class NavigateInput(StandardThoughtInput):
    url: str = Field(description="Full URL to navigate to, e.g., https://www.google.com")


class ResetBrowserStateInput(StandardThoughtInput):
    cookies: bool = Field(
        default=False,
        description="Clear browser cookies. Use when replaying a clean unauthenticated/session-less visit.",
    )
    local_storage: bool = Field(
        default=False,
        description="Clear window.localStorage for the current page origin.",
    )
    session_storage: bool = Field(
        default=False,
        description="Clear window.sessionStorage for the current page origin.",
    )
    cache: bool = Field(
        default=True,
        description="Clear the browser HTTP cache. Defaults to true to avoid stale protocol analysis observations.",
    )
    permissions: bool = Field(
        default=False,
        description="Clear browser permission grants for the context.",
    )
    reload_page: bool = Field(
        default=True,
        description="Reload the current page after clearing selected state and internal observations.",
    )
    new_fingerprint: bool = Field(
        default=False,
        description=(
            "Close and recreate the browser session with a fresh fingerprint, proxy/IP session, "
            "and profile. This is a full reset; selected clear options are unnecessary when enabled."
        ),
    )


@tool("browser_navigate", args_schema=NavigateInput)
async def browser_navigate(url: str, **kwargs) -> str:
    """Navigate to the specified URL in the browser and wait for page load to complete."""
    session_id = kwargs.get("session_id")

    async def _action():
        page = browser_manager.get_page(session_id)
        await page.goto(url, wait_until="networkidle", timeout=45000)
        return f"Success"

    try:
        return await handle_tool_result(_action, session_id=session_id)
    except Exception as e:
        return f"Failed: {str(e)}"


@tool("navigate_back", args_schema=StandardThoughtInput)
async def navigate_back(**kwargs) -> str:
    """Go back to the previous page (browser back button)."""
    session_id = kwargs.get("session_id")

    async def _action():
        page = browser_manager.get_page(session_id)
        await page.go_back(wait_until="networkidle", timeout=10000)
        return f"Success. Current URL: {page.url}"

    try:
        return await handle_tool_result(_action, session_id=session_id)
    except Exception as e:
        return f"Failed: {str(e)}"


@tool("reset_browser_state", args_schema=ResetBrowserStateInput)
async def reset_browser_state(
        cookies: bool = False,
        local_storage: bool = False,
        session_storage: bool = False,
        cache: bool = True,
        permissions: bool = False,
        reload_page: bool = True,
        new_fingerprint: bool = False,
        **kwargs,
) -> str:
    """
    Reset selected browser state and clear all internal observation/debug data.

    Browser-state options are explicit (cookies, storage, cache, permissions).
    new_fingerprint performs a full browser restart with fresh proxy/profile/fingerprint.
    Internal observer caches, captured scripts/assets, breakpoints, temporary
    headers, and AST logs are always reset so the next observation is clean.
    """
    session_id = kwargs.get("session_id")
    user_id = str(kwargs.get("runtime_context", {}).get("user_id") or session_id)

    async def _action():
        if new_fingerprint:
            current_url = "about:blank"
            try:
                current_url = browser_manager.get_page(session_id).url
            except Exception:
                pass

            from reverseloom.browser.fingerprint import FingerprintManager
            FingerprintManager.clear_session_state(session_id)

            await browser_manager.close_session(session_id, user_id)
            await browser_manager.get_or_create_session(session_id, user_id)
            new_page = browser_manager.get_page(session_id)

            if reload_page and current_url and current_url != "about:blank":
                try:
                    await new_page.goto(current_url, wait_until="domcontentloaded", timeout=30000)
                    return (
                        "Full reset complete (new fingerprint, new proxy/IP session, new profile); "
                        f"navigated back to: {current_url}"
                    )
                except Exception as exc:
                    return (
                        "Full reset complete (new fingerprint, new proxy/IP session, new profile), "
                        f"but failed to navigate back to {current_url}: {str(exc)}"
                    )

            return "Full reset complete (new fingerprint, new proxy/IP session, new profile)."

        session = browser_manager.get_session(session_id)
        page = browser_manager.get_page(session_id)
        context = browser_manager.get_context(session_id)
        client = await browser_manager.get_cdp_client(session_id)
        handler = getattr(session, "cdp_handler", None)

        cleared = ["observations"]

        if handler:
            # Remove active CDP breakpoints before clearing local bookkeeping.
            for breakpoint_id in list(getattr(handler, "line_breakpoint_ids", []) or []):
                try:
                    await client.send("Debugger.removeBreakpoint", {"breakpointId": breakpoint_id})
                except Exception as exc:
                    logging.debug(f"Failed to remove breakpoint {breakpoint_id}: {exc}")
            for pattern in list(getattr(handler, "xhr_breakpoint_patterns", []) or []):
                try:
                    await client.send("DOMDebugger.removeXHRBreakpoint", {"url": pattern})
                except Exception as exc:
                    logging.debug(f"Failed to remove XHR breakpoint {pattern}: {exc}")
            handler.reset_observation_state()

        try:
            await client.send("Runtime.evaluate", {
                "expression": (
                    "try { window.__loom_vmp_log = []; } catch (e) {};"
                    "try { window.__loom_ast_logs = []; } catch (e) {}"
                ),
                "awaitPromise": False,
            })
        except Exception as exc:
            logging.debug(f"Failed to clear AST runtime logs: {exc}")

        if cookies:
            try:
                await context.clear_cookies()
            except Exception:
                await client.send("Network.clearBrowserCookies")
            cleared.append("cookies")

        if cache:
            await client.send("Network.clearBrowserCache")
            cleared.append("cache")

        if permissions:
            clear_permissions = getattr(context, "clear_permissions", None)
            if clear_permissions:
                await clear_permissions()
            else:
                await client.send("Browser.resetPermissions")
            cleared.append("permissions")

        storage_scripts = []
        if local_storage:
            storage_scripts.append("try { window.localStorage.clear(); } catch (e) {}")
            cleared.append("local_storage")
        if session_storage:
            storage_scripts.append("try { window.sessionStorage.clear(); } catch (e) {}")
            cleared.append("session_storage")
        if storage_scripts:
            await page.evaluate("() => { " + " ".join(storage_scripts) + " }")

        if reload_page:
            await page.reload(wait_until="networkidle", timeout=45000)

        action = "reloaded" if reload_page else "kept current page"
        return f"Reset complete ({', '.join(cleared)}); {action}. Current URL: {page.url}"

    try:
        if new_fingerprint:
            return await _action()
        return await handle_tool_result(_action, session_id=session_id)
    except Exception as e:
        return f"Failed: {str(e)}"


# ==========================================
# Element Interaction Tools
# ==========================================

def _format_element_info(element_info: dict) -> str:
    """Format element metadata for tool return values."""
    return json.dumps({
        "tag": element_info.get("node_name", ""),
        "text": element_info.get("text", ""),
        # "attrs": element_info.get("attributes", {}),
        # "xpath": element_info.get("xpath", ""),
    }, ensure_ascii=False)


def _rect_center(rect: dict) -> tuple[float, float]:
    """Return (cx, cy) in viewport CSS pixels from a cached DOM rect."""
    return (
        rect["x"] + rect["width"] / 2,
        rect["y"] + rect["height"] / 2,
    )


def _require_cached_rect(element_info: dict, target_id: str) -> dict:
    rect = element_info.get("rect") or {}
    if not rect or rect.get("width", 0) <= 0 or rect.get("height", 0) <= 0:
        raise ValueError(f"No cached bbox for ocId {target_id}; re-observe the page")
    return rect


async def _resolve_click_coords(session_id: str, element_info: dict, target_id: str) -> tuple[float, float]:
    """Primary: backendNodeId + CDP getContentQuads (live, correct after
    scroll/transform, pierces shadow DOM, covers OOPIFs).
    Fallback: cached rect center captured at tree-build time."""
    coords = await _element_center(session_id, element_info)
    if coords is not None:
        return coords
    rect = _require_cached_rect(element_info, target_id)
    return _rect_center(rect)


class ClickInput(StandardThoughtInput):
    target_id: str = Field(
        default="",
        description=(
            "ocId of a target element listed in browser_state, e.g., 'o_12'. "
            "Use this whenever the target is an enumerated DOM element."
        ),
    )
    x: Optional[int] = Field(
        default=None,
        description=(
            "Viewport x coordinate in CSS pixels. Use ONLY when the target is "
            "not enumerated in browser_state"
        ),
    )
    y: Optional[int] = Field(
        default=None,
        description="Viewport y coordinate in CSS pixels. See x.",
    )


@tool("browser_click", args_schema=ClickInput)
async def browser_click(target_id: str = "", x: Optional[int] = None, y: Optional[int] = None, **kwargs) -> str:
    """Click a DOM element by ocId, or click a pixel coordinate when the target is not enumerable in browser_state (captcha widgets, canvas controls, about:blank/srcdoc/blob iframes)."""
    session_id = kwargs.get("session_id")

    has_target = bool(str(target_id or "").strip())
    has_coords = x is not None and y is not None
    if has_target and has_coords:
        return "Failed: provide either target_id or (x, y), not both."
    if not has_target and not has_coords:
        return "Failed: must provide either target_id or (x, y)."

    async def _action():
        page = browser_manager.get_page(session_id)

        if has_coords:
            await asyncio.wait_for(page.mouse.click(int(x), int(y)), timeout=5)
            element_mapping_service.set_last_action(
                session_id, f"browser_click(x={int(x)},y={int(y)})"
            )
            return f"Success. Clicked viewport pixel ({int(x)}, {int(y)})."

        element_info = element_mapping_service.get_mapping(session_id, target_id)
        if not element_info:
            raise ValueError(f"No element mapping found for ocId {target_id}")

        cx, cy = await _resolve_click_coords(session_id, element_info, target_id)
        await asyncio.wait_for(page.mouse.click(cx, cy), timeout=5)

        element_mapping_service.track_element(session_id, target_id)
        element_mapping_service.set_last_action(session_id, f"browser_click({target_id})")
        return f"Success. Element info: {_format_element_info(element_info)}"

    try:
        return await handle_tool_result(_action, session_id=session_id)
    except Exception as e:
        return f"Failed: {str(e)}"


class DragInput(StandardThoughtInput):
    from_x: int = Field(description="Viewport x coordinate in CSS pixels where the drag starts.")
    from_y: int = Field(description="Viewport y coordinate in CSS pixels where the drag starts.")
    to_x: int = Field(description="Viewport x coordinate in CSS pixels where the drag ends.")
    to_y: int = Field(description="Viewport y coordinate in CSS pixels where the drag ends.")
    duration_ms: int = Field(
        default=800,
        description=(
            "Total drag duration in milliseconds. 600-1200ms is typical human "
            "speed for slider captchas; scale longer for longer distances."
        ),
    )


def _windmouse_path(
        fx: float,
        fy: float,
        tx: float,
        ty: float,
        *,
        G: float = 9.0,  # gravity toward target
        W: float = 3.0,  # wind / noise magnitude
        M: float = 15.0,  # max step
        D: float = 12.0,  # distance at which wind/grav start decaying
) -> list:
    """BenLand100's WindMouse (2007). Physics model: wind noise + gravity
    toward target + inertia. Produces non-symmetric, slightly-overshooting
    trajectories whose velocity profile matches real human mouse-drag
    statistics. No Bezier, no cosine ease — just a force accumulator.

    Returns list of (x, y) integer pixel positions. Timing is applied by
    the caller so the same path can be replayed at different speeds."""
    x, y = fx, fy
    v_x = v_y = w_x = w_y = 0.0
    points: list = []

    while True:
        dist = math.hypot(tx - x, ty - y)
        if dist < 1.0:
            break

        w_mag = min(W, dist)
        if dist >= D:
            w_x = w_x / math.sqrt(3) + (2 * random.random() - 1) * w_mag / math.sqrt(5)
            w_y = w_y / math.sqrt(3) + (2 * random.random() - 1) * w_mag / math.sqrt(5)
        else:
            w_x /= math.sqrt(3)
            w_y /= math.sqrt(3)
            if M < 3:
                M = random.random() * 3 + 3
            else:
                M /= math.sqrt(5)

        v_x += w_x + G * (tx - x) / dist
        v_y += w_y + G * (ty - y) / dist

        v_mag = math.hypot(v_x, v_y)
        if v_mag > M:
            v_clip = M / 2 + random.random() * M / 2
            v_x = (v_x / v_mag) * v_clip
            v_y = (v_y / v_mag) * v_clip

        x += v_x
        y += v_y
        ix, iy = int(round(x)), int(round(y))
        if not points or points[-1] != (ix, iy):
            points.append((ix, iy))

        if len(points) > 5000:
            break  # safety

    # Ensure path terminates exactly on target.
    if not points or points[-1] != (int(round(tx)), int(round(ty))):
        points.append((int(round(tx)), int(round(ty))))
    return points


async def _cdp_mouse_event(cdp, event_type: str, x: float, y: float, button: str = "none", buttons: int = 0):
    """Send one mouse event via raw CDP. Bypasses Playwright's per-step
    await overhead so event frequency can reach ~100 Hz."""
    await cdp.send("Input.dispatchMouseEvent", {
        "type": event_type,  # mouseMoved / mousePressed / mouseReleased
        "x": float(x),
        "y": float(y),
        "button": button,  # left / none
        "buttons": buttons,  # bitfield: 1 = left down
        "clickCount": 1 if event_type in ("mousePressed", "mouseReleased") else 0,
    })


async def _replay_path_cdp(cdp, path, total_ms: int, buttons: int):
    """Replay a precomputed path via CDP mouseMoved events at ~100 Hz with
    per-step jitter on both position (±0.7 px) and timing (±20%)."""
    if not path:
        return
    base_dt = (total_ms / 1000.0) / max(1, len(path))
    # Ease-in-out weighting so motion feels less uniform.
    for i, (px, py) in enumerate(path):
        u = (i + 1) / len(path)
        ease = 0.5 - 0.5 * math.cos(math.pi * u)  # 0..1
        # Slow near the endpoints, fast in middle — multiply dt by inverse of ease derivative.
        speed_factor = 0.6 + 0.8 * (1 - abs(2 * ease - 1))  # 0.6..1.4
        dt = base_dt / max(0.5, speed_factor) * random.uniform(0.85, 1.15)
        jx = px + random.gauss(0, 0.7)
        jy = py + random.gauss(0, 0.7)
        await _cdp_mouse_event(cdp, "mouseMoved", jx, jy, button="left" if buttons else "none", buttons=buttons)
        await asyncio.sleep(max(0.004, dt))


@tool("browser_drag", args_schema=DragInput)
async def browser_drag(from_x: int, from_y: int, to_x: int, to_y: int, duration_ms: int = 800, **kwargs) -> str:
    """Drag mouse (from_x, from_y) → (to_x, to_y) using WindMouse trajectory
    (physics: wind + gravity + inertia) delivered over raw CDP at ~100 Hz.
    Includes pre-move approach from a random nearby origin, press/release
    dwell, and a tiny post-release drift — the full profile expected by
    slider-captcha challenge models (standard sliders / visual puzzles)."""
    session_id = kwargs.get("session_id")

    async def _action():
        page = browser_manager.get_page(session_id)
        cdp = await page.context.new_cdp_session(page)

        # 1) Pre-move: approach (from_x, from_y) from a random nearby origin
        #    so the mouse doesn't teleport into the slider handle.
        distance = math.hypot(to_x - from_x, to_y - from_y)
        approach_offset = max(40.0, min(200.0, 0.3 * distance))
        theta = random.uniform(0, 2 * math.pi)
        origin_x = from_x + math.cos(theta) * approach_offset
        origin_y = from_y + math.sin(theta) * approach_offset
        approach_path = _windmouse_path(origin_x, origin_y, from_x, from_y)
        await _cdp_mouse_event(cdp, "mouseMoved", origin_x, origin_y)
        await _replay_path_cdp(cdp, approach_path, total_ms=random.randint(250, 450), buttons=0)

        # 2) Press + dwell: humans pause slightly after mousedown before moving.
        await _cdp_mouse_event(cdp, "mousePressed", from_x, from_y, button="left", buttons=1)
        await asyncio.sleep(random.uniform(0.06, 0.16))

        try:
            # 3) Main drag: WindMouse path, buttons=1 so every move is a real drag event.
            main_path = _windmouse_path(from_x, from_y, to_x, to_y)
            await _replay_path_cdp(cdp, main_path, total_ms=duration_ms, buttons=1)

            # 4) Pre-release dwell: pause on target so release isn't "land+fly".
            await asyncio.sleep(random.uniform(0.05, 0.12))
        finally:
            # 5) Release.
            await _cdp_mouse_event(cdp, "mouseReleased", to_x, to_y, button="left", buttons=0)

        # 6) Post-release drift: small idle motion so mouse doesn't freeze.
        drift_x = to_x + random.uniform(-15, 15)
        drift_y = to_y + random.uniform(-15, 15)
        drift_path = _windmouse_path(to_x, to_y, drift_x, drift_y)
        await _replay_path_cdp(cdp, drift_path, total_ms=random.randint(150, 300), buttons=0)

        try:
            await cdp.detach()
        except Exception:
            pass

        element_mapping_service.set_last_action(
            session_id,
            f"browser_drag(({from_x},{from_y})->({to_x},{to_y}),{duration_ms}ms)",
        )
        return f"Dragged ({from_x},{from_y}) -> ({to_x},{to_y}) over {duration_ms}ms"

    try:
        return await handle_tool_result(_action, session_id=session_id)
    except Exception as e:
        return f"Failed: {str(e)}"


class TypeInput(StandardThoughtInput):
    target_id: str = Field(description="Unique identifier (ocId) of the target input field, e.g., 'o_5'")
    text: str = Field(description="Text content to type")


@tool("browser_type", args_schema=TypeInput)
async def browser_type(target_id: str, text: str, **kwargs) -> str:
    """Type text into the specified input field on the page. Must provide the ocId obtained from the observed state."""
    session_id = kwargs.get("session_id")

    async def _action():
        element_info = element_mapping_service.get_mapping(session_id, target_id)
        if not element_info:
            raise ValueError(f"No element mapping found for ocId {target_id}")

        page = browser_manager.get_page(session_id)
        cx, cy = await _resolve_click_coords(session_id, element_info, target_id)
        # Triple-click to select any existing value so keyboard.type overwrites.
        await page.mouse.click(cx, cy, click_count=3)
        await page.keyboard.type(text)

        element_mapping_service.track_element(session_id, target_id)
        element_mapping_service.set_last_action(session_id, f"browser_type({target_id})")
        return f"Success. Element info: {_format_element_info(element_info)}"

    try:
        return await handle_tool_result(_action, session_id=session_id)
    except Exception as e:
        return f"Failed: {str(e)}"


class SelectOptionInput(StandardThoughtInput):
    target_id: str = Field(description="ocId of the dropdown element, e.g., 'o_3'")
    values: List[str] = Field(description="List of option values to select, matching the option's value or label text")


@tool("select_option", args_schema=SelectOptionInput)
async def select_option(target_id: str, values: List[str], **kwargs) -> str:
    """Select options from a dropdown (<select>) element. Supports single and multi-select."""
    session_id = kwargs.get("session_id")

    async def _action():
        element_info = element_mapping_service.get_mapping(session_id, target_id)
        if not element_info:
            raise ValueError(f"No element mapping found for ocId {target_id}")

        xpath = element_info.get("xpath")
        frame_id = element_info.get("frame_id")
        page = browser_manager.get_page(session_id)

        target = page.frame_locator(frame_id) if frame_id else page
        await target.locator(xpath).first.select_option(values, timeout=5000)

        element_mapping_service.track_element(session_id, target_id)
        element_mapping_service.set_last_action(session_id, f"select_option({target_id})")
        return f"Success. Element info: {_format_element_info(element_info)}"

    try:
        return await handle_tool_result(_action, session_id=session_id)
    except Exception as e:
        return f"Failed: {str(e)}"


class PressKeyInput(StandardThoughtInput):
    target_id: Optional[str] = Field(default=None,
                                     description="ocId of the target element (optional; if omitted, triggers on the currently focused element)")
    keys: str = Field(
        description="Key to press, e.g., 'Enter', 'Escape', 'Tab', 'ArrowDown'. Use '+' for combos, e.g., 'Control+a', 'Shift+Enter'")


@tool("press_key", args_schema=PressKeyInput)
async def press_key(keys: str, target_id: Optional[str] = None, **kwargs) -> str:
    """Simulate keyboard key press. Supports Enter, Escape, Tab, arrow keys, and key combos (e.g., Control+a)."""
    session_id = kwargs.get("session_id")

    async def _action():
        page = browser_manager.get_page(session_id)
        element_info = None

        if target_id:
            element_info = element_mapping_service.get_mapping(session_id, target_id)
            if element_info:
                try:
                    cx, cy = await _resolve_click_coords(session_id, element_info, target_id)
                    await page.mouse.click(cx, cy)
                except Exception as exc:
                    logging.debug(f"press_key focus click skipped for {target_id}: {exc}")

        await page.keyboard.press(keys)
        if target_id:
            element_mapping_service.track_element(session_id, target_id)
            element_mapping_service.set_last_action(session_id, f"press_key({target_id})")
        if element_info:
            return f"Success. Element info: {_format_element_info(element_info)}"
        return f"Success"

    try:
        return await handle_tool_result(_action, session_id=session_id)
    except Exception as e:
        return f"Failed: {str(e)}"


# ==========================================
# Scroll Tools
# ==========================================

class ScrollPageInput(StandardThoughtInput):
    direction: Literal["up", "down", "top", "bottom"] = Field(description="Scroll direction: up/down/top/bottom")
    element_id: Optional[str] = Field(default=None,
                                      description="Optional: ocId of an element whose nearest scrollable parent container will be scrolled")


@tool("scroll_page", args_schema=ScrollPageInput)
async def scroll_page(direction: str, element_id: Optional[str] = None, **kwargs) -> str:
    """Scroll the page or a specific container. Direction options: up, down, top (to top), bottom (to bottom)."""
    session_id = kwargs.get("session_id")

    async def _action():
        page = browser_manager.get_page(session_id)

        xpath = None
        frame_id = None
        if element_id:
            element_info = element_mapping_service.get_mapping(session_id, element_id)
            if element_info and element_info.get("xpath"):
                xpath = element_info["xpath"]
                frame_id = element_info.get("frame_id")

        viewport = page.viewport_size
        distance = viewport["height"] * 0.7 if viewport else 600

        # Hover over the target scroll area
        if xpath:
            try:
                target = page
                if frame_id:
                    target = page.frame_locator(frame_id)
                await target.locator(xpath).first.hover(timeout=5000)
            except Exception:
                pass
        else:
            if viewport:
                await page.mouse.move(viewport["width"] / 2, viewport["height"] / 2)

        # Send native mouse wheel events
        if direction == "down":
            await page.mouse.wheel(0, distance)
        elif direction == "up":
            await page.mouse.wheel(0, -distance)
        elif direction == "bottom":
            await page.mouse.wheel(0, distance * 20)
        elif direction == "top":
            await page.mouse.wheel(0, -distance * 20)

        import asyncio
        await asyncio.sleep(0.5)

        target_desc = f" (container: {element_id})" if element_id else ""
        return f"Success"

    try:
        return await handle_tool_result(_action, session_id=session_id)
    except Exception as e:
        return f"Failed: {str(e)}"


# ==========================================
# Tab Management Tools
# ==========================================

class CreateTabInput(StandardThoughtInput):
    url: Optional[str] = Field(default=None,
                               description="URL to open in the new tab (optional; opens blank page if omitted)")


@tool("create_tab", args_schema=CreateTabInput)
async def create_tab(url: Optional[str] = None, **kwargs) -> str:
    """Create a new browser tab and automatically switch to it."""
    session_id = kwargs.get("session_id")
    try:
        context = browser_manager.get_context(session_id)
        if not context:
            return "Browser not initialized"

        new_page = await context.new_page()
        await browser_manager.set_page(new_page, session_id)

        if url:
            await new_page.goto(url, wait_until="domcontentloaded", timeout=45000)
            return f"Success"
        return "Created new blank tab"
    except Exception as e:
        return f"Failed: {str(e)}"


@tool("list_tabs", args_schema=StandardThoughtInput)
async def list_tabs(**kwargs) -> str:
    """List all open browser tabs (title, URL) and mark the currently active tab."""
    session_id = kwargs.get("session_id")
    try:
        context = browser_manager.get_context(session_id)
        if not context:
            return "Browser not initialized"

        current_page = browser_manager.get_page(session_id)
        tabs_info = []
        for i, page in enumerate(context.pages):
            tabs_info.append({
                "index": i,
                "title": await page.title(),
                "url": page.url,
                "is_active": page == current_page
            })

        return json.dumps(tabs_info, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Failed: {str(e)}"


class SwitchTabInput(StandardThoughtInput):
    tab_index: int = Field(description="Index of the tab to switch to (0-based, see list_tabs for available indices)")


@tool("switch_tab", args_schema=SwitchTabInput)
async def switch_tab(tab_index: int, **kwargs) -> str:
    """Switch to the tab at the specified index."""
    session_id = kwargs.get("session_id")
    try:
        context = browser_manager.get_context(session_id)
        if not context:
            return "Browser not initialized"

        pages = context.pages
        if tab_index < 0 or tab_index >= len(pages):
            return f"Invalid tab index {tab_index}, currently {len(pages)} tabs open (indices 0-{len(pages) - 1})"

        target_page = pages[tab_index]
        await target_page.bring_to_front()
        await browser_manager.set_page(target_page, session_id)

        title = await target_page.title()
        return f"Switched to tab {tab_index}: {title} ({target_page.url})"
    except Exception as e:
        return f"Failed: {str(e)}"


class QueryElementInfoInput(StandardThoughtInput):
    oc_ids: List[str] = Field(description="List of element ocIds, e.g., ['o_1', 'o_2']")


@tool("query_element_info", args_schema=QueryElementInfoInput)
async def query_element_info(oc_ids: List[str], **kwargs) -> str:
    """
    Get detailed element attributes (like href, placeholder, checked state...) by their ocId list.
    """
    session_id = kwargs.get("session_id")
    results = {}
    for oc_id in oc_ids:
        info = element_mapping_service.get_mapping(session_id, oc_id)
        if not info:
            results[oc_id] = None
            continue

        element_mapping_service.track_element(session_id, oc_id)
        element_mapping_service.set_last_action(session_id, f"query_element_info({oc_id})")

        results[oc_id] = {
            "tag": info.get("node_name", ""),
            "text": info.get("text", ""),
            "attrs": info.get("attributes", {}),
            "xpath": info.get("xpath", ""),
            "rect": info.get("rect", {}),
        }

    return json.dumps(results, ensure_ascii=False)


class WaitInput(StandardThoughtInput):
    seconds: int = Field(description="Number of seconds to wait (minimum 10, maximum 60).", ge=10, le=60)


@tool("wait_for_seconds", args_schema=WaitInput)
async def wait_for_seconds(seconds: int, **kwargs) -> str:
    """Wait for a specified number of seconds (between 10 and 60). Use this when the page needs time to load, an animation to finish, or a background process to complete."""
    seconds = max(10, min(60, seconds))
    await asyncio.sleep(seconds)
    return f"Waited {seconds} seconds."


class EvaluateInput(StandardThoughtInput):
    expression: str = Field(description="JavaScript expression or script to execute in the page context.")


@tool("browser_evaluate", args_schema=EvaluateInput)
async def browser_evaluate(expression: str, **kwargs) -> str:
    """
    Execute a JavaScript expression or script in the current page context.
    """
    session_id = kwargs.get("session_id")

    async def _action():
        page = browser_manager.get_page(session_id)
        result = await page.evaluate(expression)
        return f"JS execution result:\n{json.dumps(result, ensure_ascii=False, indent=2) if result is not None else 'None'}"

    try:
        return await handle_tool_result(_action, session_id=session_id)
    except Exception as e:
        return f"JS execution failed: {str(e)}"


AUTOMATION_TOOLS = [
    browser_navigate,
    navigate_back,
    reset_browser_state,
    browser_click,
    browser_drag,
    browser_type,
    select_option,
    press_key,
    scroll_page,
    create_tab,
    list_tabs,
    switch_tab,
    query_element_info,
    wait_for_seconds,
    browser_evaluate,
    visual_locate,
    request_user_interaction,
]

import asyncio
import logging
import base64
from typing import Dict, Any
from reverseloom.browser.image_utils import draw_bounding_boxes
from reverseloom.browser.get_interactive_elements import execute_get_interactive_elements


async def capture_browser_snapshot(session) -> Dict[str, Any]:
    """
    Reusable browser snapshot utility.
    Captures current page DOM, screenshot, URL and tab info.
    Handles paused debugger state to avoid deadlocks.
    """
    url = ""
    dom_content = ""
    screenshot = ""
    tabs_info = ""
    current_title = ""
    active_tab_index = None
    tabs_detail = []

    try:
        if not session or not session.page:
            return {
                "url": "about:blank",
                "current_title": "",
                "active_tab_index": None,
                "dom_content": "Browser session or page object is missing.",
                "screenshot": "",
                "tabs_info": "No Active Session",
                "tabs_detail": [],
            }

        # If page is paused at a breakpoint, skip all page interaction to avoid deadlock
        if hasattr(session, "cdp_handler") and session.cdp_handler.is_paused:
            logging.info("[BrowserSnapshot] Page is paused, skipping screenshot and DOM scan")
            return {
                "url": url,
                "current_title": current_title,
                "active_tab_index": active_tab_index,
                "dom_content": "Page is currently PAUSED at a breakpoint. Cannot scan interactive elements. Analyze the call stack or resume execution first.",
                "screenshot": "",
                "tabs_info": tabs_info or "Paused State",
                "tabs_detail": tabs_detail,
            }

        page = session.page
        url = page.url
        tabs_info, current_title, active_tab_index, tabs_detail = await _collect_tabs_snapshot(session, page)

        # Yield to event loop so pending CDP events (Debugger.paused) can fire
        await asyncio.sleep(0.1)

        # 2. Get DOM and coordinates (with timeout to prevent deadlock)
        try:
            dom_content, bboxes, viewport_info = await execute_get_interactive_elements(page, session.session_id)
            dpr = viewport_info.get("dpr", 1.0)
        except asyncio.TimeoutError:
            logging.warning("[BrowserSnapshot] DOM extraction timed out (possible debugger pause)")
            dom_content = "DOM extraction timed out — page may be frozen or paused."
            bboxes = []
            dpr = 1.0
        except Exception as e:
            logging.warning(f"[BrowserSnapshot] DOM extraction failed: {e}")
            dom_content = f"DOM extraction failed: {e}"
            bboxes = []
            dpr = 1.0

        # 3. Capture screenshot (with timeout)
        try:
            screenshot_bytes = await page.screenshot(type="jpeg", quality=90, full_page=False, timeout=3_000)
            screenshot = f"data:image/jpeg;base64,{base64.b64encode(screenshot_bytes).decode('utf-8')}"

            # 4. Draw bounding boxes on screenshot using Python (Pillow)
            # PIL operations are CPU-bound; run in thread pool to keep the event loop free.
            if bboxes:
                loop = asyncio.get_running_loop()
                screenshot = await loop.run_in_executor(
                    None, draw_bounding_boxes, screenshot, bboxes, dpr
                )

        except asyncio.TimeoutError:
            logging.warning("[BrowserSnapshot] Screenshot timed out (possible debugger pause)")
        except Exception as e:
            logging.warning(f"[BrowserSnapshot] Failed to capture screenshot: {e}")

    except Exception as e:
        logging.error(f"[BrowserSnapshot] Error capturing state: {e}")
        dom_content = "Error capturing state."

    return {
        "url": url,
        "current_title": current_title,
        "active_tab_index": active_tab_index,
        "dom_content": dom_content,
        "screenshot": screenshot,
        "tabs_info": tabs_info,
        "tabs_detail": tabs_detail,
    }


async def _collect_tabs_snapshot(session, current_page) -> tuple[str, str, int | None, list[dict[str, Any]]]:
    local_tabs_info = ""
    local_current_title = ""
    local_active_tab_index = None
    local_tabs_detail: list[dict[str, Any]] = []

    try:
        context = session.context
        if not context:
            return local_tabs_info, local_current_title, local_active_tab_index, local_tabs_detail

        for i, p in enumerate(context.pages):
            is_active = p == current_page
            title = await asyncio.wait_for(p.title(), timeout=3)

            tab_record = {
                "index": i,
                "title": title,
                "url": p.url,
                "is_active": is_active,
            }
            local_tabs_detail.append(tab_record)

            if is_active:
                local_active_tab_index = i
                local_current_title = title

        if local_tabs_detail:
            local_tabs_info = "\n".join(
                [
                    f"[{tab['index']}] {tab['title']} | {tab['url']}" + (" (active)" if tab["is_active"] else "")
                    for tab in local_tabs_detail
                ]
            )
    except Exception as e:
        logging.warning(f"[BrowserSnapshot] Failed to get tabs info: {e}")

    return local_tabs_info, local_current_title, local_active_tab_index, local_tabs_detail

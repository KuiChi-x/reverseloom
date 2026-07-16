import asyncio
import logging
from patchright.async_api import Page
from reverseloom.browser.browser_manager import browser_manager


async def wait_for_page_stable(page: Page, session_id: str, timeout_ms: int = 45000):
    """
    Wait for page stability.
    Safely handles paused debugger state to avoid deadlocks.
    """
    await asyncio.sleep(3)

    if browser_manager.is_paused(session_id):
        logging.info("[wait_for_page_stable] Page is paused, skipping stability check")
        return

    try:
        await page.wait_for_load_state("load", timeout=15000)
    except Exception as e:
        logging.warning(f"[wait_for_page_stable] Load state wait failed: {e}")

    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception as e:
        logging.warning(f"[wait_for_page_stable] Network idle wait failed: {e}")


async def handle_tool_result(action_func, session_id: str = "default", *args, **kwargs):
    """
    Wrapper for browser tool execution. Handles debugger pause state to avoid
    deadlocks. New tabs are no longer auto-switched here: the observer node
    surfaces tab changes to the LLM, and the LLM calls `switch_tab` when it
    wants to move `session.page`. This keeps the "active tab" pointer under
    explicit model control and guarantees per-page CdpHandler routing stays
    consistent with what the LLM believes it's looking at.
    """
    context = browser_manager.get_context(session_id)
    if not context:
        return await action_func(*args, **kwargs)

    result = await action_func(*args, **kwargs)
    await browser_manager.sync_session_cookies(session_id)
    await asyncio.sleep(2)

    if browser_manager.is_paused(session_id):
        logging.info("[handle_tool_result] Page paused after action, skipping stability wait")
        return result

    return result

import os
import asyncio
import logging
from typing import Dict, Any, Optional, List, Tuple
from patchright.async_api import BrowserContext, Page, Playwright, Browser

from reverseloom.browser.cdp_handler import CdpHandler



class BrowserSession:
    """
    One browser context + many pages. Each page owns its own CdpHandler so
    network/debugger state never bleeds across tabs. The agent's active-tab
    pointer is `self.page`; tools route through `self.cdp_handler` which is a
    property that always returns the handler for the currently-active page.

    Lossless network capture is guaranteed by driving the browser-level CDP
    session with Target.setAutoAttach(waitForDebuggerOnStart=True): every new
    page target is suspended before running any code, we attach, then call
    Runtime.runIfWaitingForDebugger to release it. Non-page targets (service
    workers, dedicated/shared workers, OOPIFs, …) are released immediately
    without attachment — the agent does not inspect them today.
    """

    def __init__(
        self,
        session_id: str,
        user_id: str,
        context: BrowserContext,
        page: Page,
    ):
        self.session_id = session_id
        self.user_id = user_id
        self.context = context
        self.page = page
        self.browser: Any = None
        # One CdpHandler per Page. Populated eagerly for the initial page and
        # on-demand (or via Target.setAutoAttach) for every subsequent tab.
        self.cdp_handlers: Dict[Page, CdpHandler] = {}
        # Browser-level CDP session owning the Target auto-attach stream. Kept
        # alive for the lifetime of the session; torn down in close().
        self._root_cdp: Any = None
        # Guards concurrent ensure_cdp_attached calls for the same page so
        # switch_tab + context.on("page") + Target.attachedToTarget can't
        # race into duplicate handlers.
        self._attach_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Per-page handler access
    # ------------------------------------------------------------------
    @property
    def cdp_handler(self) -> CdpHandler:
        """Handler for the agent's currently-active page (`self.page`).

        `self.page` is only moved by `switch_tab` (browser_manager.set_page).
        New tabs opened by clicks do NOT pull the pointer automatically; the
        observer surfaces them and the LLM decides when to switch.
        """
        handler = self.cdp_handlers.get(self.page)
        if handler is None:
            raise RuntimeError(
                f"No CdpHandler attached for current page (session={self.session_id}). "
                "ensure_cdp_attached should have been invoked during init_cdp / set_page."
            )
        return handler

    @property
    def network_logs(self) -> List[Dict]:
        return self.cdp_handler.network_logs

    @property
    def js_codes(self) -> Dict[str, str]:
        return self.cdp_handler.js_codes

    @property
    def runtime_assets(self) -> Dict[str, Dict[str, Any]]:
        return self.cdp_handler.runtime_assets

    @property
    def script_registry(self) -> Dict[str, Dict[str, Any]]:
        return self.cdp_handler.script_registry

    @property
    def frame_cdp_sessions(self) -> Dict[str, Any]:
        """Backwards-compatible alias; lives on the per-page CdpHandler now."""
        return self.cdp_handler.frame_cdp_sessions

    @property
    def frame_offsets(self) -> Dict[str, Tuple[float, float]]:
        return self.cdp_handler.frame_offsets

    async def reset_frame_cdp_sessions(self):
        """Delegates to the active page's handler — dom_service rebuilds call this."""
        await self.cdp_handler.reset_frame_cdp_sessions()

    # ------------------------------------------------------------------
    # CDP lifecycle
    # ------------------------------------------------------------------
    async def init_cdp(self):
        """Install new-page listeners and attach handlers to any pages that
        already exist in the context.

        We rely on `context.on('page')` (fires as soon as Playwright has a
        Page wrapper built) + `Target.setAutoAttach` WITHOUT
        waitForDebuggerOnStart. The suspend-then-release variant was attempted
        but flatten-mode child sessions can't be released by
        `Runtime.runIfWaitingForDebugger` via the root CDPSession (sessionId
        envelope isn't exposed by patchright's CDPSession.send) — the target
        stayed suspended forever and new tabs hung on a spinner. The
        non-suspending setup loses at most the first few microseconds of the
        very first request on a brand-new tab, which is acceptable.
        """
        browser = getattr(self.context, "browser", None)
        if browser is not None:
            try:
                self._root_cdp = await browser.new_browser_cdp_session()
                await self._root_cdp.send("Target.setAutoAttach", {
                    "autoAttach": True,
                    "waitForDebuggerOnStart": False,
                    "flatten": True,
                })
                self._root_cdp.on(
                    "Target.attachedToTarget",
                    lambda e: asyncio.create_task(self._on_target_attached(e)),
                )
            except Exception as exc:
                logging.warning(
                    f"[BrowserSession:{self.session_id}] Target.setAutoAttach failed; "
                    f"falling back to context.on('page'): {exc}"
                )
                self._root_cdp = None
        else:
            logging.warning(
                f"[BrowserSession:{self.session_id}] context.browser is None "
                "(persistent context edge case); falling back to context.on('page')"
            )

        # Primary path for Page wrappers: Playwright fires this as soon as the
        # wrapper exists, which is before the new tab's first business request
        # completes in practice.
        self.context.on(
            "page",
            lambda p: asyncio.create_task(self._on_new_page(p)),
        )

        # Attach to any pages that existed before we installed the hooks.
        for existing in list(self.context.pages):
            await self.ensure_cdp_attached(existing)

    async def _on_target_attached(self, event: Dict[str, Any]) -> None:
        """Install a CdpHandler when a new page target attaches. We no longer
        release targets via `Runtime.runIfWaitingForDebugger` because
        waitForDebuggerOnStart is off — the target is already running. For
        non-page targets (workers, OOPIFs, service workers) there's nothing
        to do: the agent doesn't inspect them and they aren't suspended.
        """
        info = event.get("targetInfo") or {}
        target_type = info.get("type", "")
        if target_type != "page":
            return

        target_id = info.get("targetId")
        page = await self._resolve_page_for_target(target_id)
        if page is not None:
            try:
                await self.ensure_cdp_attached(page)
            except Exception as exc:
                logging.warning(
                    f"[BrowserSession:{self.session_id}] attach for new page "
                    f"target {target_id} failed: {exc}"
                )
        else:
            logging.debug(
                f"[BrowserSession:{self.session_id}] target {target_id} attached "
                "but no matching Playwright Page resolved; context.on('page') "
                "will pick it up."
            )

    async def _resolve_page_for_target(
        self, target_id: Optional[str], timeout_s: float = 2.0
    ) -> Optional[Page]:
        """Map a CDP targetId to a Playwright Page. Playwright builds its Page
        wrapper asynchronously after Target.targetCreated, so we poll briefly."""
        if not target_id:
            return None
        deadline = asyncio.get_event_loop().time() + timeout_s
        while True:
            for page in self.context.pages:
                # Patchright/Playwright exposes the target id via a private
                # attribute on the main frame's page impl. We best-effort it
                # and fall back to "just match whatever new page shows up".
                pg_target_id = None
                try:
                    pg_target_id = page._impl_obj._target._target_id  # type: ignore[attr-defined]
                except Exception:
                    pg_target_id = None
                if pg_target_id == target_id:
                    return page
                if page not in self.cdp_handlers:
                    # Good enough: a not-yet-attached page is almost certainly
                    # the one we just got notified about.
                    return page
            if asyncio.get_event_loop().time() >= deadline:
                return None
            await asyncio.sleep(0.05)

    async def _on_new_page(self, page: Page) -> None:
        try:
            await self.ensure_cdp_attached(page)
        except Exception as exc:
            logging.warning(
                f"[BrowserSession:{self.session_id}] ensure_cdp_attached from "
                f"context.on('page') failed: {exc}"
            )

    async def ensure_cdp_attached(self, page: Page) -> CdpHandler:
        """Idempotently attach a CdpHandler to `page`. Safe to call from
        multiple paths: init_cdp, context.on('page'), Target.attachedToTarget,
        and set_page fallback."""
        async with self._attach_lock:
            handler = self.cdp_handlers.get(page)
            if handler is not None:
                return handler
            handler = CdpHandler()
            await handler.attach(self.context, page)
            self.cdp_handlers[page] = handler
            page.on("close", lambda p=page: asyncio.create_task(self._on_page_close(p)))
            return handler

    async def _on_page_close(self, page: Page) -> None:
        handler = self.cdp_handlers.pop(page, None)
        if handler is not None:
            try:
                await handler.detach()
            except Exception as exc:
                logging.debug(f"handler.detach on page close failed: {exc}")
        # If the closed page was the agent's active pointer, pick any still-
        # open page as the new default so `self.cdp_handler` doesn't throw.
        if self.page is page:
            open_pages = [p for p in self.context.pages if p is not page]
            if open_pages:
                self.page = open_pages[-1]
                # No need to re-attach — context.on('page') / autoAttach did it.

    async def close(self):
        # Detach all per-page handlers first.
        for page, handler in list(self.cdp_handlers.items()):
            try:
                await handler.detach()
            except Exception as exc:
                logging.debug(f"handler.detach on close failed: {exc}")
        self.cdp_handlers.clear()
        if self._root_cdp is not None:
            try:
                await self._root_cdp.detach()
            except Exception as exc:
                logging.debug(f"root_cdp.detach on close failed: {exc}")
            self._root_cdp = None
        await self.context.close()
        if self.browser:
            await self.browser.close()
        logging.info(f"Session {self.session_id} closed.")


class SessionManager:
    """
    Registry for BrowserSession objects. Manages the mapping of session_id to session resources.
    """
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super(SessionManager, cls).__new__(cls)
            cls._instance.sessions: Dict[str, BrowserSession] = {}
            cls._instance._create_lock = asyncio.Lock()
        return cls._instance

    async def get_or_create_session(self, session_id: str, user_id: str = "") -> BrowserSession:
        """
        Orchestrates session creation by delegating to BrowserManager for the process,
        but manages the mapping and lifecycle here.
        """
        async with self._create_lock:
            if session_id in self.sessions:
                return self.sessions[session_id]

            logging.info(f"[SessionManager] 正在为会话 '{session_id}' 创建新浏览器实例...")
            from reverseloom.browser.browser_manager import browser_manager
            session = await browser_manager.create_browser_session(session_id, user_id)
            self.sessions[session_id] = session
            return session

    def get_session(self, session_id: str) -> Optional[BrowserSession]:
        return self.sessions.get(session_id)

    async def close_session(self, session_id: str):
        session = self.sessions.pop(session_id, None)
        if session:
            await session.close()

    async def close_all(self):
        for session_id in list(self.sessions.keys()):
            await self.close_session(session_id)

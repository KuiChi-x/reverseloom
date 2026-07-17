import logging
import asyncio
import os
from typing import Dict, Any, Optional, List
from patchright.async_api import async_playwright, Page, BrowserContext, Playwright, ProxySettings

from reverseloom.runtime.config import SESSION_BASE_DIR, BROWSER_EXECUTABLE_PATH, UPSTREAM_PROXY_URL, cookie_user_id
from reverseloom.browser.discovery import resolve_browser_executable
from reverseloom.browser.fingerprint import FingerprintManager
from reverseloom.browser.proxy import ProxyManager
from reverseloom.browser.session_manager import SessionManager, BrowserSession


class BrowserManager:
    """
    Browser factory. Launches browser processes (via patchright, pointed at a
    Chromium-based executable such as radar-browser) with the right fingerprint
    launch args and an optional local proxy tunnel. Does NOT store session
    state; delegates that to SessionManager. Requires an explicit session_id
    for all operations to keep sessions strictly isolated.
    """
    _instance = None
    playwright: Playwright = None
    proxy_manager: Optional[ProxyManager] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(BrowserManager, cls).__new__(cls)
            cls._instance.playwright = None
            cls._instance.proxy_manager = None
            cls._instance._init_lock = asyncio.Lock()
        return cls._instance

    async def init_browser(self):
        if self.playwright:
            return

        async with self._init_lock:
            if self.playwright:
                return

            logging.info("[BrowserManager] Starting Playwright...")
            self.playwright = await async_playwright().start()
            self.proxy_manager = ProxyManager()
            # SessionManager is a pure registry; per-session paths derive from
            # SESSION_BASE_DIR/<session_id>/profile, not from a shared base.
            SessionManager()

    async def create_browser_session(self, session_id: str, user_id: str = "") -> BrowserSession:
        """
        FACTORY METHOD: Orchestrates the creation of a new fingerprinted/proxied browser session.
        Does NOT register it in SessionManager (SessionManager handles that caller-side).
        """
        if not self.playwright:
            await self.init_browser()

        # 1. Fingerprint generation is isolated per conversation. Cookie identity
        # follows COOKIE_SCOPE when the caller didn't pass one explicitly.
        user_id = user_id or cookie_user_id(session_id)
        fingerprint = FingerprintManager.load_session_state(session_id)
        if fingerprint is None:
            probe_proxy = UPSTREAM_PROXY_URL or None
            fingerprint = await FingerprintManager.generate(proxy=probe_proxy)
            FingerprintManager.save_session_state(session_id, fingerprint)

        # 2. Optional authenticated upstream proxy assembled from tunnel settings.
        proxy_config = None
        if UPSTREAM_PROXY_URL:
            # Route through a local tunnel so credentialed upstream proxies work
            # (Chromium's --proxy-server can't carry user:pass in the URL).
            tunnel = await self.proxy_manager.get_or_create_tunnel(session_id, UPSTREAM_PROXY_URL)
            proxy_config = ProxySettings(server=f"http://127.0.0.1:{tunnel.local_port}")
            logging.info(f"session_id:{session_id}, using proxy tunnel on port {tunnel.local_port}")

        # 3. Launch browser with a PERSISTENT context for per-session isolation.
        executable_path = resolve_browser_executable(BROWSER_EXECUTABLE_PATH)
        trace_dir = os.path.join(SESSION_BASE_DIR, session_id, "_native_trace")
        launch_args = FingerprintManager.get_launch_args(fingerprint, trace_dir=trace_dir)
        user_data_dir = os.path.join(SESSION_BASE_DIR, session_id, "profile")
        os.makedirs(user_data_dir, exist_ok=True)

        logging.info(f"[BrowserManager] Launching persistent context for session {session_id}...")
        logging.info(f"User data dir: {user_data_dir}")
        logging.info(f"Executable path: {executable_path}")
        logging.info(f"Launch args: {launch_args}")

        launch_kwargs: Dict[str, Any] = {
            "user_data_dir": user_data_dir,
            "headless": False,
            "args": launch_args,
            "no_viewport": True,
        }
        launch_kwargs["executable_path"] = executable_path
        if proxy_config:
            launch_kwargs["proxy"] = proxy_config

        try:
            context = await self.playwright.chromium.launch_persistent_context(**launch_kwargs)
            logging.info(f"[BrowserManager] Browser launched successfully for session {session_id}.")
        except Exception as e:
            logging.error(f"[BrowserManager] Failed to launch browser: {e}")
            raise

        # A persistent context usually opens one page by default
        if context.pages:
            page = context.pages[0]
        else:
            page = await context.new_page()

        # Capture login changes from already-open conversations before starting
        # another isolated browser profile for the same desktop user.
        await self.sync_user_cookies(user_id, inject=False)

        # Inject this user's persisted login state so the session starts authenticated.
        global_cookies = FingerprintManager.load_global_cookies(user_id)
        if global_cookies:
            await context.add_cookies(global_cookies)

        # 4. Build Session Object
        session = BrowserSession(session_id, user_id, context, page)

        logging.info(f"[BrowserManager] Initializing CDP for session {session_id}...")
        await session.init_cdp()
        logging.info(f"[BrowserManager] CDP initialized for session {session_id}.")
        try:
            fingerprint_path = await FingerprintManager.persist_live_fingerprint(
                session_id=session_id,
                page=page,
            )
            logging.info(f"[BrowserManager] Live browser fingerprint saved: {fingerprint_path}")
        except Exception as exc:
            logging.warning(f"[BrowserManager] Failed to capture live browser fingerprint: {exc}")
        return session

    # --- Facade Methods for Tools (Delegate to SessionManager) ---

    async def get_or_create_session(self, session_id: str, user_id: str = "") -> BrowserSession:
        return await SessionManager().get_or_create_session(session_id, user_id)

    def get_session(self, session_id: str) -> BrowserSession:
        session = SessionManager().get_session(session_id)
        if not session:
            raise RuntimeError(f"Session '{session_id}' not found. Call get_or_create_session first.")
        return session

    def get_page(self, session_id: str) -> Page:
        return self.get_session(session_id).page

    def get_context(self, session_id: str) -> BrowserContext:
        return self.get_session(session_id).context

    def get_network_logs(self, session_id: str) -> List[Dict]:
        return self.get_session(session_id).network_logs

    def get_js_codes(self, session_id: str) -> Dict[str, str]:
        return self.get_session(session_id).js_codes

    def get_runtime_assets(self, session_id: str) -> Dict[str, Dict]:
        return self.get_session(session_id).runtime_assets

    def is_paused(self, session_id: str) -> bool:
        try:
            return self.get_session(session_id).cdp_handler.is_paused
        except Exception:
            return False

    def get_last_paused_event(self, session_id: str) -> Optional[Dict]:
        try:
            return self.get_session(session_id).cdp_handler.last_paused_event
        except Exception:
            return None

    async def get_cdp_client(self, session_id: str):
        session = self.get_session(session_id)
        return session.cdp_handler.cdp_session

    async def get_cdp_client_for_frame(self, session_id: str, frame_id: Optional[str] = None):
        """Return the CDP session that owns backendNodeIds for the given frame."""
        session = self.get_session(session_id)
        if frame_id:
            client = session.frame_cdp_sessions.get(frame_id)
            if client is not None:
                return client
        return session.cdp_handler.cdp_session

    async def set_page(self, page: Page, session_id: str):
        session = self.get_session(session_id)
        session.page = page
        await session.ensure_cdp_attached(page)

    async def sync_user_cookies(self, user_id: str, inject: bool = True) -> List[Dict[str, Any]]:
        """Merge cookies from active sessions and optionally refresh their contexts."""
        sessions = [
            session
            for session in SessionManager().sessions.values()
            if getattr(session, "user_id", session.session_id) == user_id
        ]
        for session in sessions:
            try:
                FingerprintManager.save_global_cookies(user_id, await session.context.cookies())
            except Exception as exc:
                logging.warning(f"[BrowserManager] Failed to read cookies from session {session.session_id}: {exc}")

        shared_cookies = FingerprintManager.load_global_cookies(user_id)
        if inject and shared_cookies:
            for session in sessions:
                try:
                    await session.context.add_cookies(shared_cookies)
                except Exception as exc:
                    logging.warning(f"[BrowserManager] Failed to sync cookies to session {session.session_id}: {exc}")
        return shared_cookies

    async def sync_session_cookies(self, session_id: str) -> None:
        """Publish one session's latest cookies to other sessions for its user."""
        session = SessionManager().get_session(session_id)
        if session is None:
            return

        user_id = getattr(session, "user_id", session_id)
        try:
            cookies = await session.context.cookies()
            FingerprintManager.save_global_cookies(user_id, cookies)
            shared_cookies = FingerprintManager.load_global_cookies(user_id)
        except Exception as exc:
            logging.warning(f"[BrowserManager] Failed to persist cookies for session {session_id}: {exc}")
            return

        if not shared_cookies:
            return
        for other in SessionManager().sessions.values():
            if other is session or getattr(other, "user_id", other.session_id) != user_id:
                continue
            try:
                await other.context.add_cookies(shared_cookies)
            except Exception as exc:
                logging.warning(f"[BrowserManager] Failed to sync cookies to session {other.session_id}: {exc}")

    async def close_session(self, session_id: str, user_id: str = ""):
        """Close a browser session after persisting its user-level login state."""
        session = SessionManager().get_session(session_id)
        if session is not None:
            try:
                resolved_user_id = user_id or getattr(session, "user_id", session_id)
                cookies = await session.context.cookies()
                FingerprintManager.save_global_cookies(resolved_user_id, cookies)
                logging.info(f"[BrowserManager] Persisted {len(cookies)} cookies for user {resolved_user_id} from session {session_id}.")
            except Exception as exc:
                logging.warning(f"[BrowserManager] Failed to persist cookies for session {session_id}: {exc}")

        try:
            await SessionManager().close_session(session_id)
        finally:
            if self.proxy_manager:
                await self.proxy_manager.stop_tunnel(session_id)

    async def close(self):
        """Close all sessions and stop the manager."""
        session_manager = SessionManager()
        async with session_manager._create_lock:
            for session_id in list(session_manager.sessions):
                try:
                    await self.close_session(session_id)
                except Exception as exc:
                    logging.warning(f"[BrowserManager] Failed to close session {session_id}: {exc}")
        if self.proxy_manager:
            try:
                await self.proxy_manager.stop_all()
            except Exception as exc:
                logging.warning(f"[BrowserManager] Failed to stop proxy tunnels: {exc}")
            finally:
                self.proxy_manager = None
        if self.playwright:
            try:
                await self.playwright.stop()
            except Exception as exc:
                logging.warning(f"[BrowserManager] Failed to stop Playwright: {exc}")
            finally:
                self.playwright = None
        logging.info("[BrowserManager] Environment closed.")


browser_manager = BrowserManager()

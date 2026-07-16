"""reverseloom entry point.

Default: launch a native desktop window (pywebview) wrapping the local UI.
    python -m reverseloom
Fallback: serve only, open in your system browser.
    python -m reverseloom --web
"""
import argparse
import asyncio
import threading
import time
from urllib.parse import urlparse

from dotenv import load_dotenv

from reverseloom.runtime.paths import settings_env_path

_APP = "reverseloom.web.server:app"


class DesktopApi:
    """Small native bridge for actions that must not replace the app window."""

    def open_external(self, url: str) -> bool:
        value = str(url or "").strip()
        if urlparse(value).scheme.lower() not in {"http", "https", "mailto"}:
            return False
        try:
            import webbrowser

            return bool(webbrowser.open(value))
        except Exception:
            return False


def _serve(host: str, port: int) -> None:
    import uvicorn
    uvicorn.run(_APP, host=host, port=port, log_level="warning")


def _create_server(host: str, port: int):
    import uvicorn
    return uvicorn.Server(uvicorn.Config(
        _APP,
        host=host,
        port=port,
        log_level="warning",
        timeout_graceful_shutdown=5,
    ))


def _wait_until_up(url: str, timeout: float = 60.0, is_alive=None) -> bool:
    import urllib.request
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if is_alive is not None and not is_alive():
            return False
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.2)
    return False


def _create_desktop_window(webview_module, url: str):
    """Create the native shell with normal browser text-selection semantics."""
    return webview_module.create_window(
        "reverseloom",
        url,
        width=1280,
        height=860,
        text_select=True,
        js_api=DesktopApi(),
    )


def _close_runtime_resources(timeout: float = 15.0) -> bool:
    try:
        from reverseloom.browser import browser_manager
        from reverseloom.web.server import app

        loop = getattr(app.state, "event_loop", None)
        if loop is None or not loop.is_running():
            return False
        for cancel_event in list(getattr(app.state, "cancels", {}).values()):
            loop.call_soon_threadsafe(cancel_event.set)
        future = asyncio.run_coroutine_threadsafe(browser_manager.close(), loop)
        future.result(timeout=timeout)
        return True
    except Exception:
        return False


def _run_desktop_window(
    webview_module,
    url: str,
    server,
    wait_until_up=_wait_until_up,
    close_runtime=_close_runtime_resources,
) -> bool:
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()
    try:
        server_ready = wait_until_up(url, is_alive=server_thread.is_alive)
    except TypeError:
        server_ready = wait_until_up(url)
    if not server_ready:
        server.should_exit = True
        server_thread.join(timeout=5)
        return False

    window = _create_desktop_window(webview_module, url)
    shutdown_lock = threading.Lock()
    shutdown_complete = threading.Event()
    shutdown_started = False

    def stop_server(*_args) -> None:
        nonlocal shutdown_started
        with shutdown_lock:
            owns_shutdown = not shutdown_started
            shutdown_started = True
        if not owns_shutdown:
            shutdown_complete.wait(timeout=20)
            return
        try:
            close_runtime()
        finally:
            server.should_exit = True
            shutdown_complete.set()

    window.events.closed += stop_server
    try:
        webview_module.start()
    finally:
        stop_server()
        server_thread.join(timeout=15)
        if server_thread.is_alive():
            server.force_exit = True
            server_thread.join(timeout=5)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(prog="reverseloom", description="Local deep browser reverse-engineering agent.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8973)
    parser.add_argument("--web", action="store_true", help="Serve only and open in the system browser (no desktop window).")
    args = parser.parse_args()

    load_dotenv(settings_env_path())  # BASE_URL / OPENAI_API_KEY / MODEL / REVERSELOOM_*
    url = f"http://{args.host}:{args.port}"

    if args.web:
        import webbrowser
        try:
            webbrowser.open(url)
        except Exception:
            pass
        print(f"reverseloom → {url}")
        _serve(args.host, args.port)
        return

    # Desktop mode: run the server in a background thread, show a native window.
    try:
        import webview  # pywebview
    except ImportError:
        print("pywebview not installed; falling back to browser mode. `pip install pywebview` for the desktop app.")
        import webbrowser
        try:
            webbrowser.open(url)
        except Exception:
            pass
        print(f"reverseloom → {url}")
        _serve(args.host, args.port)
        return

    server = _create_server(args.host, args.port)
    if not _run_desktop_window(webview, url, server):
        print(f"reverseloom server did not come up at {url}; check logs.")


if __name__ == "__main__":
    main()

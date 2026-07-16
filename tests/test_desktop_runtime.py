import tomllib
import time
from pathlib import Path


def test_native_window_enables_text_selection():
    from reverseloom.__main__ import _create_desktop_window

    captured = {}

    class FakeWebview:
        @staticmethod
        def create_window(title, url, **kwargs):
            captured.update({"title": title, "url": url, **kwargs})
            return object()

    _create_desktop_window(FakeWebview, "http://127.0.0.1:8973")

    assert captured["text_select"] is True
    assert captured["width"] == 1280
    assert captured["height"] == 860


def test_public_suffix_dependency_is_declared():
    project_file = Path(__file__).parents[1] / "pyproject.toml"
    project = tomllib.loads(project_file.read_text(encoding="utf-8"))

    dependencies = project["project"]["dependencies"]
    assert any(item.startswith("tldextract") for item in dependencies)


def test_desktop_packager_is_declared_as_dev_dependency():
    project_file = Path(__file__).parents[1] / "pyproject.toml"
    project = tomllib.loads(project_file.read_text(encoding="utf-8"))

    dev_dependencies = project["project"]["optional-dependencies"]["dev"]
    assert any(item.startswith("pyinstaller") for item in dev_dependencies)

def test_same_site_uses_registered_domain():
    from reverseloom.tools.browser.investigation import _is_same_site

    assert _is_same_site("api.example.co.uk", "static.example.co.uk")
    assert not _is_same_site("example.co.uk", "example.com")


def test_native_window_exposes_safe_external_link_bridge(monkeypatch):
    from reverseloom.__main__ import DesktopApi, _create_desktop_window

    opened = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url) or True)

    captured = {}

    class FakeWebview:
        @staticmethod
        def create_window(title, url, **kwargs):
            captured.update(kwargs)
            return object()

    _create_desktop_window(FakeWebview, "http://127.0.0.1:8973")
    assert isinstance(captured["js_api"], DesktopApi)
    assert captured["js_api"].open_external("https://example.com") is True
    assert captured["js_api"].open_external("javascript:alert(1)") is False
    assert opened == ["https://example.com"]


def test_closing_native_window_stops_embedded_server():
    from reverseloom.__main__ import _run_desktop_window

    callbacks = []

    class ClosedEvent:
        def __iadd__(self, callback):
            callbacks.append(callback)
            return self

    class FakeWindow:
        events = type("Events", (), {"closed": ClosedEvent()})()

    class FakeWebview:
        @staticmethod
        def create_window(*_args, **_kwargs):
            return FakeWindow()

        @staticmethod
        def start():
            callbacks[0]()

    class FakeServer:
        should_exit = False
        force_exit = False
        stopped = False

        def run(self):
            while not self.should_exit:
                time.sleep(0.001)
            self.stopped = True

    server = FakeServer()
    runtime_closed = []
    assert _run_desktop_window(
        FakeWebview,
        "http://127.0.0.1:8973",
        server,
        wait_until_up=lambda _url: True,
        close_runtime=lambda: runtime_closed.append(True),
    )
    assert runtime_closed == [True]
    assert server.should_exit is True
    assert server.stopped is True


def test_runtime_cleanup_runs_on_server_event_loop(monkeypatch):
    from fastapi.testclient import TestClient
    from reverseloom.__main__ import _close_runtime_resources
    from reverseloom.web import server

    closed = []

    async def close_browser_manager():
        closed.append(True)

    monkeypatch.setattr(server.browser_manager, "close", close_browser_manager)
    with TestClient(server.app):
        assert _close_runtime_resources(timeout=2)

    assert closed


def test_desktop_bundle_uses_real_package_entrypoint():
    spec_file = Path(__file__).parents[1] / "reverseloom.spec"
    spec = spec_file.read_text(encoding="utf-8")

    assert '["src/reverseloom/__main__.py"]' in spec
    assert '["run.py"]' not in spec


def test_desktop_bundle_collects_patchright_driver_without_env_file():
    spec_file = Path(__file__).parents[1] / "reverseloom.spec"
    spec = spec_file.read_text(encoding="utf-8")

    assert 'collect_data_files("patchright")' in spec
    assert '(".env"' not in spec
    assert '(".env.example"' not in spec


def test_desktop_bundle_collects_model_backends():
    project_root = Path(__file__).parents[1]
    for spec_name in ("reverseloom.spec", "reverseloom-macos.spec"):
        spec = (project_root / spec_name).read_text(encoding="utf-8")
        assert 'collect_submodules("langchain_litellm")' in spec
        assert 'collect_submodules("litellm")' in spec


def test_desktop_bundle_collects_tiktoken_encoding_plugins():
    spec_file = Path(__file__).parents[1] / "reverseloom.spec"
    spec = spec_file.read_text(encoding="utf-8")

    assert 'collect_submodules("tiktoken_ext")' in spec
    assert '"tiktoken_ext.openai_public"' in spec


def test_desktop_bundle_and_web_ui_use_branded_icon():
    project_root = Path(__file__).parents[1]
    spec = (project_root / "reverseloom.spec").read_text(encoding="utf-8")
    page = (project_root / "src/reverseloom/static/index.html").read_text(encoding="utf-8")

    assert 'icon="assets/reverseloom.ico"' in spec
    assert 'href="/static/app-icon.png"' in page
    assert (project_root / "assets/reverseloom.ico").is_file()
    assert (project_root / "src/reverseloom/static/app-icon.png").is_file()


def test_streaming_scroll_only_follows_when_user_is_near_bottom():
    page_file = Path(__file__).parents[1] / "src/reverseloom/static/index.html"
    page = page_file.read_text(encoding="utf-8")

    assert "let followLatest = true" in page
    assert "if (!force && !followLatest) return;" in page
    assert "scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 72" in page
    assert "jumpLatest.onclick = () => down(true);" in page


def test_packaging_requires_shared_jsdom_runtime():
    project_root = Path(__file__).parents[1]
    for spec_name in ("reverseloom.spec", "reverseloom-macos.spec"):
        spec = (project_root / spec_name).read_text(encoding="utf-8")
        assert "sandbox_jsdom_manifest" in spec
        assert "node_modules" in spec
        assert "jsdom" in spec
        assert "Shared jsdom runtime is missing" in spec


def test_sandbox_bundle_keeps_jsdom_external():
    project_root = Path(__file__).parents[1]
    webpack_config = (
        project_root / "src/reverseloom/browser/sandbox_env/webpack.config.js"
    ).read_text(encoding="utf-8")
    bundle = (
        project_root / "src/reverseloom/browser/sandbox_env/reverseloom-sandbox.bundle.js"
    )
    assert "jsdom: 'commonjs jsdom'" in webpack_config
    assert bundle.stat().st_size < 1024 * 1024


def test_runtime_paths_use_hidden_home_directory():
    from reverseloom.runtime.paths import (
        default_db_path,
        default_log_dir,
        default_session_dir,
        default_skills_dir,
        settings_env_path,
    )

    home = Path("D:/Users/tester")
    assert default_session_dir(home=home, environ={}) == home / ".reverseloom" / "sessions"
    assert default_skills_dir(home=home, environ={}) == home / ".reverseloom" / "skills"
    assert default_log_dir(home=home, environ={}) == home / ".reverseloom" / "logs"
    assert default_db_path(home=home, environ={}) == home / ".reverseloom" / "reverseloom.sqlite3"
    assert settings_env_path(home=home, environ={}) == home / ".reverseloom" / ".env"


def test_settings_do_not_expose_custom_session_directory():
    from reverseloom.runtime.settings import FIELDS

    assert "REVERSELOOM_SESSION_DIR" not in FIELDS

from pathlib import Path


def test_domain_packages_are_primary_imports():
    from reverseloom.conversation.store import SessionStore
    from reverseloom.runtime.checkpoints import CheckpointerManager
    from reverseloom.tools.browser import AUTOMATION_TOOLS, REVERSE_TOOLS

    assert SessionStore.__module__ == "reverseloom.conversation.store"
    assert CheckpointerManager.__module__ == "reverseloom.runtime.checkpoints"
    assert AUTOMATION_TOOLS
    assert REVERSE_TOOLS


def test_legacy_imports_remain_compatible():
    from reverseloom.persistence import CheckpointerManager as LegacyCheckpointer
    from reverseloom.runtime.checkpoints import CheckpointerManager
    from reverseloom.session_store import SessionStore as LegacySessionStore
    from reverseloom.conversation.store import SessionStore

    assert LegacyCheckpointer is CheckpointerManager
    assert LegacySessionStore is SessionStore


def test_bundled_sandbox_runtime_path():
    package_root = Path(__file__).parents[1] / "src/reverseloom"
    sandbox_engine = package_root / "browser/sandbox_env/reverseloom-sandbox.bundle.js"

    assert sandbox_engine.is_file()


def test_tools_are_grouped_by_function():
    from reverseloom.tools import ALL_TOOLS, BROWSER_TOOLS, FILESYSTEM_TOOLS

    package_root = Path(__file__).parents[1] / "src/reverseloom"
    assert ALL_TOOLS == BROWSER_TOOLS + FILESYSTEM_TOOLS
    assert (package_root / "tools/filesystem.py").is_file()
    assert (package_root / "tools/browser/investigation.py").is_file()
    assert not (package_root / "workspace").exists()
    assert not (package_root / "browser/tools").exists()
    assert not (package_root / "browser/automation_tools.py").exists()
    assert not (package_root / "browser/reverse_tools.py").exists()
    assert (package_root / "tools/browser/result_handler.py").is_file()

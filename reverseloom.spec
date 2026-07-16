# PyInstaller spec for reverseloom — builds a single desktop executable.
#
# Build (run at release time, in the project venv):
#     pip install pyinstaller
#     pyinstaller reverseloom.spec
#
# Output: dist/reverseloom(.exe). Runtime configuration is external. The build
# must never include a developer .env or API key; each user configures their own
# local .env through the configuration center after launch. Never ship a configured .env. No browser is bundled or downloaded;
# the app discovers an installed Chrome/Chromium browser or uses REVERSELOOM_BROWSER_PATH.
import os
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

sandbox_env_dir = os.path.join("src", "reverseloom", "browser", "sandbox_env")
sandbox_bundle = os.path.join(sandbox_env_dir, "reverseloom-sandbox.bundle.js")
sandbox_jsdom_manifest = os.path.join(sandbox_env_dir, "node_modules", "jsdom", "package.json")
if not os.path.isfile(sandbox_bundle):
    raise SystemExit("Sandbox bundle is missing; run npm ci && npm run build in src/reverseloom/browser/sandbox_env")
if not os.path.isfile(sandbox_jsdom_manifest):
    raise SystemExit("Shared jsdom runtime is missing; run npm ci --omit=dev --ignore-scripts in src/reverseloom/browser/sandbox_env before packaging")

datas = [
    # the static web UI must ship inside the binary
    ("src/reverseloom/static", "reverseloom/static"),
    # the reverse-engineering skill + node sandbox bundle
    ("src/reverseloom/skills", "reverseloom/skills"),
    ("src/reverseloom/browser/sandbox_env", "reverseloom/browser/sandbox_env"),
    # patchright needs its packaged Node driver at runtime; this is the driver, not a browser.
    *collect_data_files("patchright"),
]
# graphloom / langgraph ship data files and many lazily-imported submodules.
hiddenimports = (
    collect_submodules("graphloom")
    + collect_submodules("langgraph")
    + collect_submodules("langchain_litellm")
    + collect_submodules("litellm")
    + collect_submodules("tiktoken")
    + collect_submodules("tiktoken_ext")
    + ["tiktoken_ext.openai_public", "aiosqlite", "sqlalchemy.dialects.sqlite.aiosqlite"]
)

a = Analysis(
    ["src/reverseloom/__main__.py"],
    pathex=["src"],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name="reverseloom",
    debug=False,
    strip=False,
    upx=True,
    icon="assets/reverseloom.ico",
    console=False,       # windowed desktop app (pywebview owns the window)
    disable_windowed_traceback=False,
)

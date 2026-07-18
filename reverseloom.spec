# PyInstaller spec for reverseloom - builds the desktop app (onedir).
#
# Build (run at release time, in the project venv):
#     pip install pyinstaller
#     pyinstaller reverseloom.spec
#
# Output: dist/reverseloom/ (onedir). Distribute the whole folder (zip it or
# wrap it in an installer); the launcher is dist/reverseloom/reverseloom(.exe).
# onedir avoids re-extracting ~113MB to a temp dir on every launch.
# Runtime configuration is external. The build
# must never include a developer .env or API key; each user configures their own
# local .env through the configuration center after launch. Never ship a configured .env. No browser is bundled or downloaded;
# the app discovers an installed Chrome/Chromium browser or uses REVERSELOOM_BROWSER_PATH.
import os
import subprocess
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# NOTE: curl_cffi is NOT bundled into the app itself - the app never imports it.
# The crawlers the agent generates import it, and they run against the separate
# CPython under _internal/pybin/ (see the pybin block below), which has
# curl_cffi + its full dependency tree installed by scripts/prepare_pybin.py.

sandbox_env_dir = os.path.join("src", "reverseloom", "browser", "sandbox_env")
sandbox_bundle = os.path.join(sandbox_env_dir, "reverseloom-sandbox.bundle.js")
sandbox_jsdom_manifest = os.path.join(sandbox_env_dir, "node_modules", "jsdom", "package.json")
if not os.path.isfile(sandbox_bundle):
    raise SystemExit("Sandbox bundle is missing; run npm ci && npm run build in src/reverseloom/browser/sandbox_env")
if not os.path.isfile(sandbox_jsdom_manifest):
    raise SystemExit("Shared jsdom runtime is missing; run npm ci --omit=dev --ignore-scripts in src/reverseloom/browser/sandbox_env before packaging")

# --- Bundled Python runtime for agent crawlers -----------------------------
# run_shell must expose a REAL `python` so agent-generated crawlers run with
# zero user setup. sys.executable in a frozen build is the windowed app, so we
# ship a self-contained CPython under _internal/pybin/ and put it on PATH at
# runtime. `scripts/prepare_pybin.py` builds that directory (embeddable CPython
# + patched ._pth + `pip install --target` of curl_cffi and all its deps, so
# the whole dependency tree is resolved by pip, not hand-copied). The spec just
# packages the prepared directory verbatim. Point REVERSELOOM_PYBIN_DIR at it.
_pybin_src = os.environ.get("REVERSELOOM_PYBIN_DIR", "").strip()
if not _pybin_src:
    raise SystemExit(
        "REVERSELOOM_PYBIN_DIR is required: prepare the bundled crawler runtime "
        "with scripts/prepare_pybin.py before building the production package."
    )
_pybin_exe = os.path.join(_pybin_src, "python.exe")
if not os.path.isfile(_pybin_exe):
    raise SystemExit(
        f"REVERSELOOM_PYBIN_DIR={_pybin_src!r} has no python.exe; "
        "run `python scripts/prepare_pybin.py <dir>` first."
    )
_crawler_probe = subprocess.run(
    [
        _pybin_exe,
        "-c",
        "import bs4, curl_cffi, parsel, Crypto, dateutil",
    ],
    capture_output=True,
    text=True,
)
if _crawler_probe.returncode != 0:
    raise SystemExit(
        "The bundled crawler runtime is missing a required dependency; "
        "run scripts/prepare_pybin.py again.\n"
        f"{_crawler_probe.stderr.strip()}"
    )
pybin_datas = []
for _root, _dirs, _files in os.walk(_pybin_src):
    for _f in _files:
        _abs = os.path.join(_root, _f)
        _rel = os.path.relpath(_root, _pybin_src)
        pybin_datas.append((_abs, os.path.join("pybin", _rel)))

datas = [
    # the static web UI must ship inside the binary
    ("src/reverseloom/static", "reverseloom/static"),
    # the reverse-engineering skill + node sandbox bundle
    ("src/reverseloom/skills", "reverseloom/skills"),
    ("src/reverseloom/browser/sandbox_env", "reverseloom/browser/sandbox_env"),
    # patchright needs its packaged Node driver at runtime; this is the driver, not a browser.
    *collect_data_files("patchright"),
    # litellm ships JSON data files (e.g. model_prices_and_context_window_backup.json)
    # that collect_submodules does NOT gather; without them build_llm() raises
    # FileNotFoundError at import time. Code is already covered by
    # collect_submodules("litellm"); this adds the data files it omits.
    *collect_data_files("litellm"),
    # self-contained CPython for agent crawlers (empty unless prepared via
    # scripts/prepare_pybin.py and pointed to by REVERSELOOM_PYBIN_DIR).
    *pybin_datas,
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
# onedir: the EXE is just the bootloader; COLLECT lays dependencies out on disk
# next to it. This avoids re-extracting ~113MB (node.exe alone is 92MB) to a
# temp dir on every launch, which onefile mode does. Distribute the dist/
# reverseloom/ folder (zip it or wrap it in an installer).
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="reverseloom",
    debug=False,
    strip=False,
    upx=True,
    icon="assets/reverseloom.ico",
    console=False,       # windowed desktop app (pywebview owns the window)
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="reverseloom",
)

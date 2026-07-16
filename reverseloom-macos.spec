# PyInstaller spec for a native macOS ReverseLoom.app bundle.
# Build this file on macOS; PyInstaller does not cross-compile from Windows.
import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


block_cipher = None
target_arch = os.environ.get("REVERSELOOM_MAC_ARCH") or None
codesign_identity = os.environ.get("REVERSELOOM_CODESIGN_IDENTITY") or None
entitlements_file = os.environ.get("REVERSELOOM_ENTITLEMENTS_FILE") or None

sandbox_env_dir = os.path.join("src", "reverseloom", "browser", "sandbox_env")
sandbox_bundle = os.path.join(sandbox_env_dir, "reverseloom-sandbox.bundle.js")
sandbox_jsdom_manifest = os.path.join(sandbox_env_dir, "node_modules", "jsdom", "package.json")
if not os.path.isfile(sandbox_bundle):
    raise SystemExit("Sandbox bundle is missing; run npm ci && npm run build in src/reverseloom/browser/sandbox_env")
if not os.path.isfile(sandbox_jsdom_manifest):
    raise SystemExit("Shared jsdom runtime is missing; run npm ci --omit=dev --ignore-scripts in src/reverseloom/browser/sandbox_env before packaging")

datas = [
    ("src/reverseloom/static", "reverseloom/static"),
    ("src/reverseloom/skills", "reverseloom/skills"),
    ("src/reverseloom/browser/sandbox_env", "reverseloom/browser/sandbox_env"),
    *collect_data_files("patchright"),
]

hiddenimports = (
    collect_submodules("graphloom")
    + collect_submodules("langgraph")
    + collect_submodules("langchain_litellm")
    + collect_submodules("litellm")
    + collect_submodules("tiktoken")
    + collect_submodules("tiktoken_ext")
    + collect_submodules("webview")
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
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ReverseLoom",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    target_arch=target_arch,
    codesign_identity=codesign_identity,
    entitlements_file=entitlements_file,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="ReverseLoom",
)

app = BUNDLE(
    coll,
    name="ReverseLoom.app",
    icon="assets/reverseloom.icns",
    bundle_identifier="com.reverseloom.desktop",
    info_plist={
        "CFBundleDisplayName": "ReverseLoom",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "1",
        "LSMinimumSystemVersion": "11.0",
        "NSHighResolutionCapable": True,
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
    },
)

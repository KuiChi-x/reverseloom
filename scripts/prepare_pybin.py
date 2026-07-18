"""Prepare a self-contained CPython runtime for agent-generated crawlers.

The frozen reverseloom app can't act as a `python` interpreter (its
sys.executable is the windowed shell), so `run_shell` needs a real, portable
Python on PATH with the crawler dependencies (curl_cffi and its whole tree)
already installed. This script builds that directory; `reverseloom.spec` then
packages it verbatim under _internal/pybin/ when REVERSELOOM_PYBIN_DIR points
at the output.

Why not a venv: a venv shares stdlib with the base interpreter and hard-codes
an absolute `home` in pyvenv.cfg, so it breaks the moment it's copied to a
machine without that base Python. Windows uses python.org's embeddable package;
macOS uses a pinned python-build-standalone runtime. Both are relocatable.

Why pip and not hand-copying: pip resolves the entire dependency tree
(curl_cffi -> cffi, certifi, rich, orjson, ... incl. C extensions) so nothing
is silently missed.

Usage (from the repo root, in the build venv):
    python scripts/prepare_pybin.py build/pybin
    # Set REVERSELOOM_PYBIN_DIR to build/pybin, then run the platform spec.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import quote

# Libraries the agent-generated crawlers import, staged into the bundled runtime
# so crawlers run with zero user setup. Keep this in sync with the core
# dependencies in pyproject.toml. pip resolves each package's
# full transitive tree (curl_cffi -> cffi/certifi/..., parsel -> lxml/w3lib/cssselect,
# etc.), so nothing is silently missed. The PyPI `datetime` package is
# intentionally excluded (it shadows the stdlib datetime module).
CRAWLER_DEPS = [
    "curl_cffi",
    "openpyxl",
    "tldextract",
    "beautifulsoup4",
    "parsel",
    "loguru",
    "pycryptodome",
    "tenacity",
    "brotli",
    "python-dateutil",
    "protobuf",
    "imap-tools",
    "xlrd",
    "x-client-transaction-id",
]

MACOS_PYTHON_VERSION = "3.11.15"
MACOS_STANDALONE_RELEASE = "20260623"
MACOS_STANDALONE_SHA256 = {
    "arm64": "d2324bfd1a7b9fc44ccd884c3a2505bcab6691dbfd4f8270e10c50aaa4e19506",
    "x86_64": "38f3c18a4ccbd6faa09243c45c85d8e09b5a7b345e02f174346cf72ebf901f87",
}


def _embeddable_url(version: str, arch: str) -> str:
    # e.g. https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip
    return f"https://www.python.org/ftp/python/{version}/python-{version}-embed-{arch}.zip"


def _download_embeddable(version: str, arch: str, dest: Path) -> None:
    url = _embeddable_url(version, arch)
    print(f"[prepare_pybin] downloading {url}")
    with urllib.request.urlopen(url, timeout=120) as resp:  # noqa: S310 (trusted python.org)
        data = resp.read()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(dest)
    print(f"[prepare_pybin] extracted embeddable -> {dest}")


def _standalone_url(version: str, arch: str) -> str:
    asset_arch = "aarch64" if arch == "arm64" else arch
    asset = (
        f"cpython-{version}+{MACOS_STANDALONE_RELEASE}-{asset_arch}-"
        "apple-darwin-install_only.tar.gz"
    )
    return (
        "https://github.com/astral-sh/python-build-standalone/releases/download/"
        f"{MACOS_STANDALONE_RELEASE}/{quote(asset, safe='')}"
    )


def _download_standalone(version: str, arch: str, dest: Path) -> None:
    if version != MACOS_PYTHON_VERSION:
        raise SystemExit(
            f"macOS runtime is pinned to Python {MACOS_PYTHON_VERSION}; "
            "update its release and checksums before changing the version."
        )
    expected_hash = MACOS_STANDALONE_SHA256[arch]
    url = _standalone_url(version, arch)
    print(f"[prepare_pybin] downloading {url}")
    with urllib.request.urlopen(url, timeout=120) as resp:  # noqa: S310 (pinned GitHub asset)
        data = resp.read()
    actual_hash = hashlib.sha256(data).hexdigest()
    if actual_hash != expected_hash:
        raise SystemExit(
            f"python-build-standalone checksum mismatch: {actual_hash}"
        )

    with tempfile.TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            archive.extractall(root)  # noqa: S202 (archive hash is pinned above)
        shutil.copytree(root / "python", dest, dirs_exist_ok=True, symlinks=True)
    print(f"[prepare_pybin] extracted standalone runtime -> {dest}")


def _patch_pth(dest: Path) -> None:
    """The embeddable ships a pythonNN._pth that isolates sys.path and disables
    site imports; without patching, packages in Lib/site-packages are invisible.
    Rewrite it to keep the stdlib zip and add Lib/site-packages + `import site`."""
    pth_files = list(dest.glob("python*._pth"))
    if not pth_files:
        raise SystemExit("no pythonNN._pth in embeddable; unexpected package layout")
    pth = pth_files[0]
    zip_name = next((p.name for p in dest.glob("python*.zip")), "python311.zip")
    pth.write_text(f"{zip_name}\n.\nLib\\site-packages\nimport site\n", encoding="ascii")
    print(f"[prepare_pybin] patched {pth.name} to enable site-packages")


def _bootstrap_pip(dest: Path) -> None:
    """Embeddable has no pip. Fetch get-pip.py and install into the embeddable."""
    exe = dest / "python.exe"
    getpip = dest / "get-pip.py"
    print("[prepare_pybin] bootstrapping pip")
    with urllib.request.urlopen("https://bootstrap.pypa.io/get-pip.py", timeout=120) as resp:  # noqa: S310
        getpip.write_bytes(resp.read())
    subprocess.run([str(exe), str(getpip), "--no-warn-script-location"], check=True)
    getpip.unlink(missing_ok=True)


def _runtime_exe(dest: Path) -> Path:
    return dest / "python.exe" if os.name == "nt" else dest / "bin" / "python3"


def _install_deps(dest: Path) -> None:
    exe = _runtime_exe(dest)
    print(f"[prepare_pybin] installing {CRAWLER_DEPS}")
    subprocess.run(
        [str(exe), "-m", "pip", "install", "--no-warn-script-location", *CRAWLER_DEPS],
        check=True,
    )


def _verify(dest: Path) -> None:
    exe = _runtime_exe(dest)
    out = subprocess.run(
        [
            str(exe),
            "-c",
            "import bs4, curl_cffi, parsel, Crypto, dateutil; "
            "print('crawler runtime verified')",
        ],
        check=True, capture_output=True, text=True,
    )
    print(f"[prepare_pybin] verify: {out.stdout.strip()}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a portable CPython for crawler execution.")
    parser.add_argument("dest", help="output directory (e.g. build/pybin)")
    parser.add_argument(
        "--version",
        help=(
            "CPython version to fetch (default: build interpreter on Windows; "
            f"{MACOS_PYTHON_VERSION} on macOS)"
        ),
    )
    parser.add_argument(
        "--arch",
        choices=["amd64", "win32", "arm64", "x86_64"],
        help="runtime architecture (default: current platform)",
    )
    args = parser.parse_args()

    dest = Path(args.dest).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        version = args.version or platform.python_version()
        arch = args.arch or "amd64"
        if arch not in {"amd64", "win32", "arm64"}:
            raise SystemExit(f"unsupported Windows architecture: {arch}")
        _download_embeddable(version, arch, dest)
        _patch_pth(dest)
        _bootstrap_pip(dest)
    elif sys.platform == "darwin":
        version = args.version or MACOS_PYTHON_VERSION
        machine = platform.machine().lower()
        arch = args.arch or ("arm64" if machine in {"arm64", "aarch64"} else "x86_64")
        if arch not in MACOS_STANDALONE_SHA256:
            raise SystemExit(f"unsupported macOS architecture: {arch}")
        _download_standalone(version, arch, dest)
    else:
        raise SystemExit("prepare_pybin supports Windows and macOS only")

    _install_deps(dest)
    _verify(dest)
    print(f"[prepare_pybin] done. Set REVERSELOOM_PYBIN_DIR={dest}")


if __name__ == "__main__":
    main()

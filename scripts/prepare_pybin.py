"""Prepare a self-contained CPython runtime for agent-generated crawlers.

The frozen reverseloom app can't act as a `python` interpreter (its
sys.executable is the windowed shell), so `run_shell` needs a real, portable
Python on PATH with the crawler dependencies (curl_cffi and its whole tree)
already installed. This script builds that directory; `reverseloom.spec` then
packages it verbatim under _internal/pybin/ when REVERSELOOM_PYBIN_DIR points
at the output.

Why the python.org *embeddable* and not a venv: a venv shares stdlib with the
base interpreter and hard-codes an absolute `home` in pyvenv.cfg, so it breaks
the moment it's copied to a machine without that base Python. The embeddable
package is fully self-contained and relocatable.

Why `pip install --target` and not hand-copying: pip resolves the entire
dependency tree (curl_cffi -> cffi, certifi, rich, orjson, ... incl. C
extensions) so nothing is silently missed.

Usage (Windows, from the repo root, in the build venv):
    python scripts/prepare_pybin.py build/pybin
    set REVERSELOOM_PYBIN_DIR=%CD%\build\pybin
    pyinstaller --clean --noconfirm reverseloom.spec

The embeddable's Python version MUST match the interpreter that built
curl_cffi's C extension ABI. This script targets the running interpreter's
major.minor by default.
"""
from __future__ import annotations

import argparse
import io
import os
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

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


def _embeddable_url(version: str, arch: str) -> str:
    # e.g. https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip
    return f"https://www.python.org/ftp/python/{version}/python-{version}-embed-{arch}.zip"


def _download_embeddable(version: str, arch: str, dest: Path) -> None:
    url = _embeddable_url(version, arch)
    print(f"[prepare_pybin] downloading {url}")
    with urllib.request.urlopen(url) as resp:  # noqa: S310 (trusted python.org)
        data = resp.read()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        zf.extractall(dest)
    print(f"[prepare_pybin] extracted embeddable -> {dest}")


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
    with urllib.request.urlopen("https://bootstrap.pypa.io/get-pip.py") as resp:  # noqa: S310
        getpip.write_bytes(resp.read())
    subprocess.run([str(exe), str(getpip), "--no-warn-script-location"], check=True)
    getpip.unlink(missing_ok=True)


def _install_deps(dest: Path) -> None:
    target = dest / "Lib" / "site-packages"
    target.mkdir(parents=True, exist_ok=True)
    exe = dest / "python.exe"
    print(f"[prepare_pybin] installing {CRAWLER_DEPS} -> {target}")
    subprocess.run(
        [str(exe), "-m", "pip", "install", "--no-warn-script-location", *CRAWLER_DEPS],
        check=True,
    )


def _verify(dest: Path) -> None:
    exe = dest / "python.exe"
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
        default=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        help="CPython version to fetch (default: this interpreter's version)",
    )
    parser.add_argument("--arch", default="amd64", choices=["amd64", "win32", "arm64"])
    args = parser.parse_args()

    if os.name != "nt":
        raise SystemExit(
            "prepare_pybin targets the Windows embeddable. On macOS/Linux the "
            "system Python is used, or adapt this script to python-build-standalone."
        )

    dest = Path(args.dest).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    _download_embeddable(args.version, args.arch, dest)
    _patch_pth(dest)
    _bootstrap_pip(dest)
    _install_deps(dest)
    _verify(dest)
    print(f"[prepare_pybin] done. Set REVERSELOOM_PYBIN_DIR={dest}")


if __name__ == "__main__":
    main()

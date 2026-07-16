"""Discover an installed Chromium-based browser without downloading one."""
from __future__ import annotations

import os
import platform
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Iterable, Mapping


def browser_candidates(
    platform_name: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> list[str]:
    """Return common browser executable locations for the current OS."""
    system = (platform_name or platform.system()).lower()
    env = dict(os.environ if environ is None else environ)
    if system == "windows":
        path_type = PureWindowsPath
        home = path_type(env.get("USERPROFILE") or str(Path.home()))
        candidates = []
        roots = [env.get("PROGRAMFILES"), env.get("PROGRAMFILES(X86)"), env.get("LOCALAPPDATA")]
        suffixes = [
            ("Google", "Chrome", "Application", "chrome.exe"),
            ("Microsoft", "Edge", "Application", "msedge.exe"),
            ("Chromium", "Application", "chrome.exe"),
            ("BraveSoftware", "Brave-Browser", "Application", "brave.exe"),
        ]
        for root in roots:
            if root:
                candidates.extend(path_type(root).joinpath(*suffix) for suffix in suffixes)
    elif system == "darwin":
        path_type = PurePosixPath
        home = path_type(env.get("HOME") or str(Path.home()))
        candidates = []
        app_roots = [path_type("/Applications"), home / "Applications"]
        app_paths = [
            ("Google Chrome.app", "Contents", "MacOS", "Google Chrome"),
            ("Microsoft Edge.app", "Contents", "MacOS", "Microsoft Edge"),
            ("Chromium.app", "Contents", "MacOS", "Chromium"),
            ("Brave Browser.app", "Contents", "MacOS", "Brave Browser"),
        ]
        for root in app_roots:
            candidates.extend(root.joinpath(*suffix) for suffix in app_paths)
    else:
        path_type = PurePosixPath
        candidates = []
        candidates.extend(path_type(item) for item in (
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
            "/usr/bin/microsoft-edge",
            "/usr/bin/brave-browser",
        ))

    return [str(candidate) for candidate in candidates]


def resolve_browser_executable(
    configured_path: str = "",
    *,
    candidates: Iterable[str] | None = None,
) -> str:
    """Resolve an explicit path or the first installed browser candidate."""
    configured = os.path.abspath(os.path.expanduser(configured_path.strip())) if configured_path.strip() else ""
    if configured:
        if os.path.isfile(configured):
            return configured
        raise FileNotFoundError(f"Configured browser executable does not exist: {configured}")

    for candidate in candidates if candidates is not None else browser_candidates():
        resolved = os.path.abspath(os.path.expanduser(str(candidate)))
        if os.path.isfile(resolved):
            return resolved

    raise FileNotFoundError(
        "No supported Chrome/Chromium browser was found. Install Chrome, Edge, Chromium, or Brave, "
        "or set REVERSELOOM_BROWSER_PATH to its executable. reverseloom does not download a browser."
    )

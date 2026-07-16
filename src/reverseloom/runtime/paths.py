from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Mapping


APP_DIRECTORY_NAME = ".reverseloom"


def is_frozen_runtime() -> bool:
    return bool(getattr(sys, "frozen", False))


def user_data_dir(
    platform_name: str | None = None,
    environ: Mapping[str, str] | None = None,
    *,
    home: str | Path | None = None,
) -> Path:
    home_dir = Path(home) if home is not None else Path.home()
    return home_dir / APP_DIRECTORY_NAME


def runtime_data_dir(
    platform_name: str | None = None,
    environ: Mapping[str, str] | None = None,
    *,
    home: str | Path | None = None,
    frozen: bool | None = None,
    cwd: str | Path | None = None,
) -> Path:
    env = dict(os.environ if environ is None else environ)
    return user_data_dir(platform_name, env, home=home)


def default_session_dir(
    platform_name: str | None = None,
    environ: Mapping[str, str] | None = None,
    *,
    home: str | Path | None = None,
    frozen: bool | None = None,
    cwd: str | Path | None = None,
) -> Path:
    base = runtime_data_dir(platform_name, environ, home=home, frozen=frozen, cwd=cwd)
    return base / "sessions"


def default_skills_dir(
    platform_name: str | None = None,
    environ: Mapping[str, str] | None = None,
    *,
    home: str | Path | None = None,
    frozen: bool | None = None,
    cwd: str | Path | None = None,
) -> Path:
    return runtime_data_dir(platform_name, environ, home=home, frozen=frozen, cwd=cwd) / "skills"


def default_log_dir(
    platform_name: str | None = None,
    environ: Mapping[str, str] | None = None,
    *,
    home: str | Path | None = None,
    frozen: bool | None = None,
    cwd: str | Path | None = None,
) -> Path:
    return runtime_data_dir(platform_name, environ, home=home, frozen=frozen, cwd=cwd) / "logs"


def default_db_path(
    platform_name: str | None = None,
    environ: Mapping[str, str] | None = None,
    *,
    home: str | Path | None = None,
    frozen: bool | None = None,
    cwd: str | Path | None = None,
) -> Path:
    return runtime_data_dir(platform_name, environ, home=home, frozen=frozen, cwd=cwd) / "reverseloom.sqlite3"


def settings_env_path(
    platform_name: str | None = None,
    environ: Mapping[str, str] | None = None,
    *,
    home: str | Path | None = None,
    frozen: bool | None = None,
    cwd: str | Path | None = None,
) -> Path:
    env = dict(os.environ if environ is None else environ)
    configured = str(env.get("REVERSELOOM_CONFIG_PATH") or "").strip()
    if configured:
        return Path(os.path.expandvars(os.path.expanduser(configured))).resolve()
    return runtime_data_dir(platform_name, env, home=home, frozen=frozen, cwd=cwd) / ".env"

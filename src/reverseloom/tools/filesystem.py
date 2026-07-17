"""Local filesystem and shell tools with explicit path parameters."""
import asyncio
import locale
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import List

from langchain_core.tools import tool
from pydantic import Field

from graphloom import StandardThoughtInput

_SANDBOX_ENV_DIR = Path(__file__).parents[1] / "browser" / "sandbox_env"
_BUNDLED_SANDBOX_ENGINE = _SANDBOX_ENV_DIR / "reverseloom-sandbox.bundle.js"


def _runtime_base(runtime_context: dict | None = None) -> str:
    context = dict(runtime_context or {})
    base = context.get("artifact_dir") or context.get("artifact_base_dir") or os.getcwd()
    return os.path.realpath(os.path.abspath(str(base)))


def _resolve_path(path: str, default: str = ".", runtime_context: dict | None = None) -> str:
    value = str(path or default).strip() or default
    expanded = os.path.expandvars(os.path.expanduser(value))
    if not os.path.isabs(expanded):
        expanded = os.path.join(_runtime_base(runtime_context), expanded)
    return os.path.realpath(os.path.abspath(expanded))


class ReadInput(StandardThoughtInput):
    path: str = Field(description="File path. Relative paths resolve from the current session artifact directory.")


class WriteInput(StandardThoughtInput):
    path: str = Field(description="Destination path. Relative paths resolve from the current session artifact directory.")
    content: str = Field(description="Full file content to write (overwrites).")


class EditInput(StandardThoughtInput):
    path: str = Field(description="File path. Relative paths resolve from the current session artifact directory.")
    old_str: str = Field(description="Exact text to find (must match uniquely).")
    new_str: str = Field(description="Replacement text (must differ from old_str).")


class ListInput(StandardThoughtInput):
    path: str = Field(default=".", description="Directory to list. Relative paths resolve from the current session artifact directory.")


class SearchInput(StandardThoughtInput):
    pattern: str = Field(description="Substring or regex to search for in file contents.")
    path: str = Field(default=".", description="Directory tree to search. Relative paths resolve from the current session artifact directory.")
    glob: str = Field(default="", description="Optional filename glob filter, e.g. '*.py'.")


class ShellInput(StandardThoughtInput):
    command: str = Field(description="Shell command to run.")
    cwd: str = Field(default=".", description="Working directory. Relative paths resolve from the current session artifact directory.")
    timeout_seconds: int = Field(default=180, ge=1, le=86400, description="Maximum execution time in seconds.")
    runtime_files: List[str] = Field(
        default_factory=list,
        description=(
            "Session-relative resource filenames to mount next to the script before "
            "running (copied into the working dir and registered for delivery). "
            "Examples: ['reverseloom-sandbox.bundle.js', 'xxx_bootstrap.js', "
            "'browser_fingerprint.json', 'module.wasm']. Use the exact name "
            "'reverseloom-sandbox.bundle.js' to mount the verified sandbox engine. "
            "Relative filenames only; absolute paths and '..' are rejected."
        ),
    )


def _decode_shell_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    encodings = ("utf-8", locale.getpreferredencoding(False))
    for encoding in dict.fromkeys(encodings):
        try:
            return value.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return value.decode("utf-8", errors="replace")


def _frozen_python_dir() -> str | None:
    """In a frozen build, sys.executable is the windowed app, not a Python
    interpreter. The build ships a self-contained CPython (python.org
    embeddable) under _internal/pybin/ with a real python.exe. Return that
    directory so it can be prepended to PATH; agent crawlers then call a real
    `python` that natively parses every flag and imports the bundled curl_cffi.
    Returns None if the runtime isn't present (falls back to system Python)."""
    meipass = getattr(sys, "_MEIPASS", None)
    if not meipass:
        return None
    pybin = Path(meipass) / "pybin"
    exe = pybin / ("python.exe" if os.name == "nt" else "bin/python3")
    return str(exe.parent) if exe.is_file() else None


def _shell_env(artifact_dir: str) -> dict[str, str]:
    env = os.environ.copy()
    env["REVERSELOOM_ARTIFACT_DIR"] = artifact_dir

    if not getattr(sys, "frozen", False):
        python_executable = str(Path(sys.executable).resolve())
        env["REVERSELOOM_PYTHON_PATH"] = python_executable
        current = env.get("PATH", "")
        python_dir = str(Path(python_executable).parent)
        env["PATH"] = python_dir + (os.pathsep + current if current else "")
    else:
        # Frozen: expose the bundled self-contained CPython on PATH.
        python_dir = _frozen_python_dir()
        if python_dir:
            exe_name = "python.exe" if os.name == "nt" else "python3"
            env["REVERSELOOM_PYTHON_PATH"] = str(Path(python_dir) / exe_name)
            current = env.get("PATH", "")
            env["PATH"] = python_dir + (os.pathsep + current if current else "")

    node_modules = _SANDBOX_ENV_DIR / "node_modules"
    if node_modules.is_dir():
        current = env.get("NODE_PATH", "")
        env["NODE_PATH"] = str(node_modules) + (os.pathsep + current if current else "")

    try:
        import patchright

        driver_dir = Path(patchright.__file__).resolve().parent / "driver"
        node = driver_dir / ("node.exe" if os.name == "nt" else "node")
        if node.is_file():
            env["REVERSELOOM_NODE_PATH"] = str(node)
            current = env.get("PATH", "")
            env["PATH"] = str(node.parent) + (os.pathsep + current if current else "")
    except (ImportError, OSError):
        pass
    return env


@tool("read_file", args_schema=ReadInput)
async def read_file(path: str, **kwargs) -> str:
    """Read a text file and return its contents with line numbers."""
    resolved = _resolve_path(path, runtime_context=kwargs.get("runtime_context"))
    if not os.path.isfile(resolved):
        return f"Error: file not found: {path}"
    try:
        with open(resolved, "r", encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
        numbered = "\n".join(f"{index + 1:>5}  {line}" for index, line in enumerate(lines))
        return f"{resolved} ({len(lines)} lines):\n{numbered}"
    except Exception as exc:
        return f"Error reading {resolved}: {exc}"


@tool("write_file", args_schema=WriteInput)
async def write_file(path: str, content: str, **kwargs) -> str:
    """Create or overwrite a file at an explicit path."""
    resolved = _resolve_path(path, runtime_context=kwargs.get("runtime_context"))
    try:
        os.makedirs(os.path.dirname(resolved) or ".", exist_ok=True)
        with open(resolved, "w", encoding="utf-8") as handle:
            handle.write(content)
        return f"Wrote {len(content)} chars to {resolved}."
    except Exception as exc:
        return f"Error writing {resolved}: {exc}"


@tool("edit_file", args_schema=EditInput)
async def edit_file(path: str, old_str: str, new_str: str, **kwargs) -> str:
    """Replace one exact text fragment in an existing file."""
    resolved = _resolve_path(path, runtime_context=kwargs.get("runtime_context"))
    if not os.path.isfile(resolved):
        return f"Error: file not found: {path}"
    if old_str == new_str:
        return "Error: old_str and new_str are identical."
    try:
        with open(resolved, "r", encoding="utf-8") as handle:
            content = handle.read()
        count = content.count(old_str)
        if count == 0:
            return f"Error: old_str not found in {resolved}."
        if count > 1:
            return f"Error: old_str matches {count} places in {resolved}; make it unique."
        with open(resolved, "w", encoding="utf-8") as handle:
            handle.write(content.replace(old_str, new_str))
        return f"Edited {resolved} (1 replacement)."
    except Exception as exc:
        return f"Error editing {resolved}: {exc}"


@tool("list_dir", args_schema=ListInput)
async def list_dir(path: str = ".", **kwargs) -> str:
    """List entries in an explicitly selected directory."""
    resolved = _resolve_path(path, runtime_context=kwargs.get("runtime_context"))
    if not os.path.isdir(resolved):
        return f"Error: not a directory: {path}"
    try:
        entries = []
        for name in sorted(os.listdir(resolved)):
            if name in {".git", "__pycache__", ".venv", "node_modules"}:
                continue
            full_path = os.path.join(resolved, name)
            entries.append(name + ("/" if os.path.isdir(full_path) else ""))
        return f"{resolved}:\n" + "\n".join(entries) if entries else f"{resolved}: (empty)"
    except Exception as exc:
        return f"Error listing {resolved}: {exc}"


@tool("search_code", args_schema=SearchInput)
async def search_code(pattern: str, path: str = ".", glob: str = "", **kwargs) -> str:
    """Search a selected directory tree for matching file content."""
    import fnmatch
    import re

    search_root = _resolve_path(path, runtime_context=kwargs.get("runtime_context"))
    if not os.path.isdir(search_root):
        return f"Error: not a directory: {path}"
    try:
        expression = re.compile(pattern)
    except re.error as exc:
        return f"Invalid pattern: {exc}"

    hits: List[str] = []
    for root, directories, files in os.walk(search_root):
        directories[:] = [name for name in directories if name not in {".git", "__pycache__", ".venv", "node_modules"}]
        for filename in files:
            if glob and not fnmatch.fnmatch(filename, glob):
                continue
            file_path = os.path.join(root, filename)
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as handle:
                    for line_number, line in enumerate(handle, 1):
                        if expression.search(line):
                            relative_path = os.path.relpath(file_path, search_root)
                            hits.append(f"{relative_path}:{line_number}: {line.strip()[:200]}")
                            if len(hits) >= 200:
                                return "\n".join(hits) + "\n... (truncated at 200 matches)"
            except OSError:
                continue
    return "\n".join(hits) if hits else "No matches."


async def _terminate_process_tree(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return

    if os.name == "nt":
        def _taskkill() -> None:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )

        try:
            await asyncio.to_thread(_taskkill)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except ProcessLookupError:
                pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                process.kill()
            except ProcessLookupError:
                pass

    try:
        await asyncio.wait_for(process.wait(), timeout=2)
    except asyncio.TimeoutError:
        pass


def _reject_unsafe_relative_name(name: str) -> str:
    value = str(name or "").strip().replace("\\", "/")
    if not value:
        raise ValueError("empty runtime file name is not allowed")
    if os.path.isabs(value) or any(part == ".." for part in value.split("/")):
        raise ValueError(f"runtime_files must be session-relative names, got: {name}")
    return os.path.basename(value)


def _mount_runtime_files(
    runtime_files: List[str],
    artifact_dir: str,
    execution_dir: str,
    runtime_context: dict | None,
) -> List[str]:
    """Ensure each requested runtime file exists next to the script and is
    registered for delivery. The bundled sandbox engine is materialized from
    the package on demand; every other name must already be a session artifact.
    Returns the list of mounted basenames. Raises ValueError on a bad name."""
    from reverseloom.tools.browser.investigation import _register_dumped_asset

    session_id = str((runtime_context or {}).get("session_id") or "default")
    producer = str((runtime_context or {}).get("current_agent_name") or "")
    mounted: List[str] = []
    for raw in runtime_files or []:
        name = _reject_unsafe_relative_name(raw)
        dest = os.path.join(execution_dir, name)
        if not os.path.isfile(dest):
            if name == _BUNDLED_SANDBOX_ENGINE.name and _BUNDLED_SANDBOX_ENGINE.is_file():
                shutil.copy2(str(_BUNDLED_SANDBOX_ENGINE), dest)
            else:
                src = os.path.join(artifact_dir, name)
                if os.path.abspath(src) != os.path.abspath(dest) and os.path.isfile(src):
                    shutil.copy2(src, dest)
        if not os.path.isfile(dest):
            raise FileNotFoundError(
                f"runtime file not found: {name} (write it as an artifact first, "
                f"or use the exact bundled engine name '{_BUNDLED_SANDBOX_ENGINE.name}')"
            )
        try:
            size = os.path.getsize(dest)
        except OSError:
            size = 0
        _register_dumped_asset(
            session_id, dest,
            f"Runtime dependency mounted for sandbox replay ({size} bytes)",
            producer=producer,
        )
        mounted.append(name)
    return mounted


@tool("run_shell", args_schema=ShellInput)
async def run_shell(
    command: str,
    cwd: str = ".",
    timeout_seconds: int = 180,
    runtime_files: List[str] = None,
    **kwargs,
) -> str:
    """Run a shell command in the current session artifact directory or an explicit directory.

    List any sandbox dependencies in `runtime_files` (session-relative names) and
    they are copied next to the script and registered so they travel with the
    delivery. Use the exact name 'reverseloom-sandbox.bundle.js' to mount the
    verified engine; reference every dependency by its relative filename so the
    same command works in-session and in the delivered crawler."""
    runtime_context = kwargs.get("runtime_context")
    execution_dir = _resolve_path(cwd, runtime_context=runtime_context)
    if not os.path.isdir(execution_dir):
        return f"Error: not a directory: {cwd}"

    if runtime_files:
        try:
            _mount_runtime_files(
                runtime_files, _runtime_base(runtime_context), execution_dir, runtime_context,
            )
        except (ValueError, FileNotFoundError, OSError) as exc:
            return f"Error mounting runtime_files: {exc}"

    process = None
    try:
        process_kwargs = {"start_new_session": True} if os.name != "nt" else {}
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=execution_dir,
            env=_shell_env(_runtime_base(runtime_context)),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            **process_kwargs,
        )
        output_bytes, _ = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        output = _decode_shell_output(output_bytes).strip()
        if len(output) > 15000:
            output = output[:7500] + "\n... (middle truncated) ...\n" + output[-7500:]
        return f"exit={process.returncode}\n{output}"
    except asyncio.TimeoutError:
        if process:
            await _terminate_process_tree(process)
        return f"Error: command timed out after {timeout_seconds}s."
    except asyncio.CancelledError:
        if process:
            await _terminate_process_tree(process)
        raise
    except Exception as exc:
        return f"Error running command in {execution_dir}: {exc}"


FILESYSTEM_TOOLS = [read_file, write_file, edit_file, list_dir, search_code, run_shell]

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from reverseloom.tools.filesystem import _resolve_path, run_shell


def _command(*args: str) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(args)
    return shlex.join(args)


def test_relative_paths_resolve_from_session_artifacts(tmp_path):
    resolved = _resolve_path(
        "crawler.py",
        runtime_context={"artifact_dir": str(tmp_path)},
    )

    assert resolved == str((tmp_path / "crawler.py").resolve())


@pytest.mark.asyncio
async def test_run_shell_uses_artifact_directory_and_environment(tmp_path):
    script = (
        "import os; "
        "from pathlib import Path; "
        "Path('cwd.txt').write_text(str(Path.cwd()), encoding='utf-8'); "
        "Path('env.txt').write_text(os.environ['REVERSELOOM_ARTIFACT_DIR'], encoding='utf-8'); "
        "Path('python.txt').write_text(os.environ['REVERSELOOM_PYTHON_PATH'], encoding='utf-8')"
    )

    result = await run_shell.coroutine(
        command=_command(sys.executable, "-c", script),
        cwd=".",
        runtime_context={"artifact_dir": str(tmp_path)},
    )

    assert result.startswith("exit=0")
    assert (tmp_path / "cwd.txt").read_text(encoding="utf-8") == str(tmp_path.resolve())
    assert (tmp_path / "env.txt").read_text(encoding="utf-8") == str(tmp_path.resolve())
    assert Path((tmp_path / "python.txt").read_text(encoding="utf-8")).resolve() == Path(sys.executable).resolve()


@pytest.mark.asyncio
async def test_run_shell_timeout_kills_child_processes(tmp_path):
    import time

    child_code = "import time; time.sleep(4)"
    parent_code = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        "time.sleep(4)"
    )
    started = time.monotonic()

    result = await run_shell.coroutine(
        command=_command(sys.executable, "-c", parent_code),
        cwd=".",
        timeout_seconds=1,
        runtime_context={"artifact_dir": str(tmp_path)},
    )

    elapsed = time.monotonic() - started
    assert result == "Error: command timed out after 1s."
    assert elapsed < 3

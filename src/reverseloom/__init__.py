"""reverseloom — a local browser-automation & reverse-engineering agent, built on graphloom."""
import logging
import os
import warnings
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv

from reverseloom.runtime.paths import default_log_dir, settings_env_path

# Structured output can emit a harmless PydanticSerializationUnexpectedValue warning on
# the `parsed` field. It doesn't affect the structured result — silence it.
warnings.filterwarnings("ignore", message="Pydantic serializer warnings")

__version__ = "0.1.0"

load_dotenv(settings_env_path())


def _resolve_log_dir() -> Path:
    """Log directory: REVERSELOOM_LOG_DIR, else ~/.reverseloom/logs."""
    configured = os.getenv("REVERSELOOM_LOG_DIR", "").strip()
    if configured:
        return Path(configured)
    return default_log_dir()


class _ColorFormatter(logging.Formatter):
    """Logging formatter that adds ANSI colors by level (console only)."""

    _COLORS = {
        logging.DEBUG:    "\033[36m",    # cyan
        logging.INFO:     "\033[32m",    # green
        logging.WARNING:  "\033[33m",    # yellow
        logging.ERROR:    "\033[31m",    # red
        logging.CRITICAL: "\033[1;31m",  # bold red
    }
    _RESET = "\033[0m"

    def format(self, record):
        color = self._COLORS.get(record.levelno, "")
        message = super().format(record)
        return f"{color}{message}{self._RESET}" if color else message


def _reconfigure_logging() -> None:
    """(Re-)apply the root logger config: rotating file + colored console.

    Safe to call repeatedly — clears existing handlers first. Called once at
    import time; can be re-invoked if a third-party library hijacks the root
    logger. Level via REVERSELOOM_LOG_LEVEL (default INFO).
    """
    level = getattr(logging, os.getenv("REVERSELOOM_LOG_LEVEL", "INFO").upper(), logging.INFO)
    logger = logging.getLogger()
    logger.setLevel(level)
    logger.handlers.clear()
    logger.filters.clear()

    format_str = "%(asctime)s - %(name)s - %(levelname)s - %(filename)s - %(lineno)d - %(message)s"

    log_dir = _resolve_log_dir()
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            str(log_dir / "reverseloom.log"),
            mode="a",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter(format_str))
        logger.addHandler(file_handler)
    except OSError:
        # A read-only or missing log dir must not stop the app from starting;
        # fall back to console-only logging.
        pass

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(_ColorFormatter(format_str))
    logger.addHandler(console_handler)


# Apply on first import.
_reconfigure_logging()

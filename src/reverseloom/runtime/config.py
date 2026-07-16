"""reverseloom runtime configuration (env-overridable)."""
import os
from urllib.parse import quote

from reverseloom.runtime.paths import default_db_path, default_session_dir

# Stable identity used only for data intentionally shared by the single-user
# desktop runtime, such as login cookies. Browser profiles and fingerprints stay
# isolated per conversation.
LOCAL_USER_ID: str = "local"

# Cookie/login-state sharing across conversations.
COOKIE_SCOPE: str = os.environ.get("REVERSELOOM_COOKIE_SCOPE", "shared").strip().lower()


def cookie_user_id(session_id: str) -> str:
    """Resolve the cookie-store identity for a session, honoring COOKIE_SCOPE.

    shared  -> LOCAL_USER_ID (one store for all conversations)
    isolated-> the session_id (a per-conversation store)
    """
    if COOKIE_SCOPE == "isolated":
        return session_id
    return LOCAL_USER_ID

# Root directory for per-session browser profiles, identities, and artifacts.
SESSION_BASE_DIR: str = os.path.abspath(str(default_session_dir()))

# Path to the Chromium-based browser executable (e.g. radar-browser). When set,
# patchright launches this instead of downloading its own Chromium.
BROWSER_EXECUTABLE_PATH: str = os.environ.get("REVERSELOOM_BROWSER_PATH", "")

def build_proxy_url_from_env() -> str:
    """Build the authenticated upstream URL consumed by the local tunnel."""
    host = os.environ.get("REVERSELOOM_PROXY_HOST", "").strip()
    if not host:
        return ""

    port = os.environ.get("REVERSELOOM_PROXY_PORT", "").strip() or "80"
    username = os.environ.get("REVERSELOOM_PROXY_USERNAME", "")
    password = os.environ.get("REVERSELOOM_PROXY_PASSWORD", "")
    auth = ""
    if username or password:
        auth = f"{quote(username, safe='')}:{quote(password, safe='')}@"
    return f"http://{auth}{host}:{port}"


# Browser traffic is always routed through a local per-session tunnel when an
# upstream proxy is configured. The tunnel, not Chromium, owns authentication.
UPSTREAM_PROXY_URL: str = build_proxy_url_from_env()

# --- persistence / checkpointer ------------------------------------------------
# Backend for the LangGraph checkpointer (conversation state + resume).
#   "sqlite"   (default) — local file, zero-config, good for the desktop build.
#   "postgres"           — set REVERSELOOM_DB_URL to a postgresql:// DSN.
#   "memory"             — no persistence (previous behaviour; state lost on restart).
DB_BACKEND: str = os.environ.get("REVERSELOOM_DB_BACKEND", "sqlite").strip().lower()

# SQLite checkpoint file (only used when DB_BACKEND == "sqlite").
DB_SQLITE_PATH: str = os.path.abspath(
    os.environ.get("REVERSELOOM_DB_PATH", str(default_db_path()))
)

# Full connection string for postgres (only used when DB_BACKEND == "postgres"),
# e.g. postgresql://user:pass@host:5432/reverseloom
DB_URL: str = os.environ.get("REVERSELOOM_DB_URL", "")


def session_dir(session_id: str) -> str:
    """Absolute path to a session's own folder under SESSION_BASE_DIR.

    Everything a session produces — browser profile, attachments, and the
    artifacts the agent writes — lives under here, so sessions never write to
    arbitrary disk locations and are isolated by subdirectory.
    """
    return os.path.join(SESSION_BASE_DIR, session_id)



def attachment_dir(session_id: str) -> str:
    """Files explicitly uploaded by the user for one conversation."""
    return os.path.join(session_dir(session_id), "attachments")


def artifact_dir(session_id: str) -> str:
    """Where the agent writes deliverable artifacts for a session."""
    return os.path.join(session_dir(session_id), "artifacts")

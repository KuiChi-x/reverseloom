"""Web layer: FastAPI + WebSocket server and the single-page UI."""
from reverseloom.web.server import create_app, app

__all__ = ["create_app", "app"]

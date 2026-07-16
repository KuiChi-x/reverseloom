"""Schema-driven settings persistence for the desktop configuration center."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict

from reverseloom.runtime.paths import settings_env_path

MASK_CHAR = "\u2022"

if sys.platform == "darwin":
    BROWSER_PATH_PLACEHOLDER = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    SQLITE_PATH_PLACEHOLDER = "~/.reverseloom/reverseloom.sqlite3"
elif sys.platform == "win32":
    BROWSER_PATH_PLACEHOLDER = "C:\\Program Files\\Chromium\\chrome.exe"
    SQLITE_PATH_PLACEHOLDER = "D:\\Users\\<user>\\.reverseloom\\reverseloom.sqlite3"
else:
    BROWSER_PATH_PLACEHOLDER = "/usr/bin/google-chrome"
    SQLITE_PATH_PLACEHOLDER = "~/.reverseloom/reverseloom.sqlite3"


def _field(
    key: str,
    label: str,
    description: str,
    *,
    field_type: str = "text",
    default: str = "",
    placeholder: str = "",
    secret: bool = False,
    minimum: float | None = None,
    maximum: float | None = None,
    step: float | None = None,
    options: list[dict[str, str]] | None = None,
    apply: str = "restart",
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "description": description,
        "type": field_type,
        "default": default,
        "placeholder": placeholder,
        "secret": secret,
        "min": minimum,
        "max": maximum,
        "step": step,
        "options": options or [],
        "apply": apply,
    }


SETTINGS_GROUPS = [
    {
        "id": "model",
        "label": "模型服务",
        "eyebrow": "MODEL RUNTIME",
        "description": "配置 OpenAI 兼容接口和多模态模型。",
        "fields": [
            _field("MODEL_PROTOCOL", "LiteLLM 协议", "选择 LiteLLM 供应商或 Responses 协议前缀。", field_type="select", default="openai", options=[{"value": "openai", "label": "OpenAI / 兼容网关"}, {"value": "openai/responses", "label": "OpenAI Responses"}, {"value": "anthropic", "label": "Anthropic Claude"}, {"value": "gemini", "label": "Google Gemini"}, {"value": "azure", "label": "Azure OpenAI"}, {"value": "azure/responses", "label": "Azure Responses"}, {"value": "openrouter", "label": "OpenRouter"}, {"value": "xai", "label": "xAI"}, {"value": "bedrock", "label": "AWS Bedrock"}, {"value": "vertex_ai", "label": "Vertex AI"}, {"value": "ollama", "label": "Ollama"}, {"value": "deepseek", "label": "DeepSeek"}, {"value": "groq", "label": "Groq"}, {"value": "mistral", "label": "Mistral"}, {"value": "together_ai", "label": "Together AI"}, {"value": "nvidia_nim", "label": "NVIDIA NIM"}], apply="reconnect"),
            _field("MODEL_REASONING_EFFORT", "思考强度", "直接填写 LiteLLM reasoning_effort，留空表示由模型决定。", default="", placeholder="low / medium / high / xhigh / max", apply="reconnect"),
            _field("BASE_URL", "接口地址", "OpenAI 兼容 API 的 base URL。", placeholder="https://api.openai.com/v1", apply="reconnect"),
            _field("OPENAI_API_KEY", "API Key", "只显示遮罩值，重新输入才会覆盖。", field_type="password", secret=True, apply="reconnect"),
            _field("MODEL", "模型", "必须支持图像输入和流式输出。", default="gpt-4o", placeholder="gpt-4o", apply="reconnect"),
        ],
    },
    {
        "id": "browser",
        "label": "浏览器与隧道代理",
        "eyebrow": "BROWSER / TUNNEL",
        "description": "指定 Chromium 内核，并通过本地认证隧道连接上游代理。",
        "fields": [
            _field("REVERSELOOM_BROWSER_PATH", "浏览器可执行文件", "留空时自动探测系统 Chrome、Edge、Chromium 或 Brave；也可填写可执行文件绝对路径。", placeholder=BROWSER_PATH_PLACEHOLDER),
            _field("REVERSELOOM_PROXY_HOST", "代理主机", "远程 HTTP 代理的域名或 IP；浏览器会通过本地隧道转发。", placeholder="proxy.example.com"),
            _field("REVERSELOOM_PROXY_PORT", "代理端口", "远程代理服务端口。", field_type="number", minimum=1, maximum=65535, step=1),
            _field("REVERSELOOM_PROXY_USERNAME", "代理用户名", "由本地隧道注入 Proxy-Authorization，不直接交给 Chromium。"),
            _field("REVERSELOOM_PROXY_PASSWORD", "代理密码", "仅保存在本地 .env，界面中始终遮罩。", field_type="password", secret=True),
        ],
    },
    {
        "id": "context",
        "label": "模型上下文",
        "eyebrow": "MODEL CONTEXT",
        "description": "仅配置当前模型的上下文容量；压缩策略由系统统一管理。",
        "fields": [
            _field("GRAPHLOOM_MODEL_CONTEXT_WINDOW", "模型上下文窗口", "应与当前模型实际支持的 context window 一致。", field_type="number", default="830000", minimum=8192, maximum=4_000_000, step=1024),
        ],
    },
    {
        "id": "storage",
        "label": "存储与运行",
        "eyebrow": "STORAGE / PROCESS",
        "description": "配置会话目录、数据库后端和日志级别。",
        "fields": [
            _field("REVERSELOOM_DB_BACKEND", "数据库后端", "SQLite 适合本地使用；PostgreSQL 适合多实例部署；Memory 不保留历史。", field_type="select", default="sqlite", options=[{"value": "sqlite", "label": "SQLite (推荐)"}, {"value": "postgres", "label": "PostgreSQL"}, {"value": "memory", "label": "Memory (不持久化)"}]),
            _field("REVERSELOOM_DB_PATH", "SQLite 文件", "留空时使用 ~/.reverseloom/reverseloom.sqlite3。", placeholder=SQLITE_PATH_PLACEHOLDER),
            _field("REVERSELOOM_DB_URL", "PostgreSQL URL", "仅在数据库后端为 PostgreSQL 时使用。", field_type="password", secret=True, placeholder="postgresql://user:pass@host/db"),
            _field("REVERSELOOM_LOG_LEVEL", "日志级别", "控制服务端日志详细程度。", field_type="select", default="INFO", options=[{"value": "DEBUG", "label": "DEBUG"}, {"value": "INFO", "label": "INFO"}, {"value": "WARNING", "label": "WARNING"}, {"value": "ERROR", "label": "ERROR"}]),
        ],
    },
]

FIELDS = {
    field["key"]: field
    for group in SETTINGS_GROUPS
    for field in group["fields"]
}
SECRET_KEYS = {key for key, field in FIELDS.items() if field["secret"]}


def _env_path() -> str:
    path = settings_env_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return MASK_CHAR * len(value)
    return value[:4] + MASK_CHAR * (len(value) - 8) + value[-4:]


def _switch_value(value: Any) -> bool:
    return str(value).strip().lower() not in {"", "0", "false", "off", "no"}


def read_settings() -> Dict[str, object]:
    groups = []
    for group in SETTINGS_GROUPS:
        fields = []
        for definition in group["fields"]:
            field = dict(definition)
            raw = os.environ.get(field["key"], "")
            effective = raw if raw != "" else field["default"]
            if field["type"] == "switch":
                value: Any = _switch_value(effective)
            elif field["secret"]:
                value = _mask(raw)
            else:
                value = effective
            field.update({
                "value": value,
                "set": bool(raw),
                "restart_required": field["apply"] == "restart",
                "reconnect_required": field["apply"] == "reconnect",
            })
            fields.append(field)
        groups.append({**{k: v for k, v in group.items() if k != "fields"}, "fields": fields})
    return {"groups": groups}


def _normalise_value(field: dict[str, Any], raw: Any) -> str:
    value = "" if raw is None else str(raw).strip()
    if field["secret"] and MASK_CHAR in value:
        raise LookupError
    if value == "":
        return ""
    if field["type"] == "switch":
        return "1" if _switch_value(value) else "0"
    if field["type"] == "select":
        allowed = {option["value"] for option in field["options"]}
        if value not in allowed:
            raise ValueError(f"{field['label']} must be one of: {', '.join(sorted(allowed))}")
        return value
    if field["type"] == "number":
        try:
            number = float(value)
        except ValueError as exc:
            raise ValueError(f"{field['label']} must be a number") from exc
        if field["min"] is not None and number < field["min"]:
            raise ValueError(f"{field['label']} must be >= {field['min']}")
        if field["max"] is not None and number > field["max"]:
            raise ValueError(f"{field['label']} must be <= {field['max']}")
        if float(number).is_integer() and float(field.get("step") or 0).is_integer():
            return str(int(number))
        return format(number, "g")
    return value


def _parse_env(text: str) -> list[tuple[str | None, str]]:
    parsed = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            parsed.append((None, line))
        else:
            parsed.append((line.split("=", 1)[0].strip(), line))
    return parsed


def write_settings(updates: Dict[str, Any]) -> Dict[str, object]:
    clean: dict[str, str] = {}
    changed: list[str] = []
    for key, raw in updates.items():
        field = FIELDS.get(key)
        if field is None:
            continue
        try:
            value = _normalise_value(field, raw)
        except LookupError:
            continue
        clean[key] = value
        if os.environ.get(key, "") != value:
            changed.append(key)

    path = _env_path()
    existing = Path(path).read_text(encoding="utf-8") if os.path.isfile(path) else ""
    parsed = _parse_env(existing)
    seen: set[str] = set()
    out_lines = []
    for key, line in parsed:
        if key is not None and key in clean:
            out_lines.append(f"{key}={clean[key]}")
            seen.add(key)
        else:
            out_lines.append(line)
    for key, value in clean.items():
        if key not in seen:
            out_lines.append(f"{key}={value}")
    Path(path).write_text("\n".join(out_lines) + "\n", encoding="utf-8")

    for key, value in clean.items():
        if value:
            os.environ[key] = value
        else:
            os.environ.pop(key, None)

    return {
        "changed": changed,
        "reconnect_required": any(FIELDS[key]["apply"] == "reconnect" for key in changed),
        "restart_required": any(FIELDS[key]["apply"] == "restart" for key in changed),
    }

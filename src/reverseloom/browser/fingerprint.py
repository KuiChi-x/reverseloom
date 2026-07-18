import os
import random
import logging
import json
from typing import Dict, Any, List, Optional
from dataclasses import dataclass

from patchright.async_api import Cookie

from reverseloom.runtime.config import SESSION_BASE_DIR
from graphloom.util.session_store import session_store

try:
    import httpx
except ImportError:
    httpx = None


@dataclass
class Fingerprint:
    platform: str
    timezone: str
    seed: int
    proxy_pid: str = ""


class FingerprintManager:
    BROWSER_FINGERPRINT_ARTIFACT = "browser_fingerprint.json"
    # Fingerprints are stored under each session; only cookies use this shared directory.
    _IDENTITY_DIR = os.path.join(SESSION_BASE_DIR, "_global_identity")
    OPERATING_SYSTEMS = ['windows', 'windows', 'windows', 'windows', 'windows', 'windows', 'macos', 'linux']

    @classmethod
    def _state_file(cls, session_id: str) -> str:
        return os.path.join(
            SESSION_BASE_DIR,
            session_id or "default",
            "browser_identity.json",
        )

    @classmethod
    def _cookies_file(cls, user_id: str) -> str:
        return os.path.join(cls._IDENTITY_DIR, f"{user_id or 'default'}_cookies.json")

    @classmethod
    async def get_timezone(cls, proxy: Optional[str] = None) -> str:
        if httpx is None:
            return 'Asia/Shanghai'
        try:
            async with httpx.AsyncClient(proxy=proxy, timeout=10.0, verify=False) as client:
                response = await client.get('http://ip-api.com/json', follow_redirects=True)
                if response.status_code == 200:
                    data = response.json()
                    return data.get('timezone', 'Asia/Shanghai')
        except Exception as e:
            logging.error(f"get_timezone failed (proxy={proxy}): {str(e)}")
        return 'Asia/Shanghai'

    @classmethod
    async def generate(cls, proxy: Optional[str] = None, proxy_pid: str = "") -> Fingerprint:
        rand = random.random()
        platform = cls.OPERATING_SYSTEMS[int(rand * len(cls.OPERATING_SYSTEMS))]
        timezone = await cls.get_timezone(proxy=proxy)

        return Fingerprint(
            platform=platform,
            timezone=timezone,
            seed=random.getrandbits(64),
            proxy_pid=proxy_pid,
        )

    # ------------------------------------------------------------------
    # Per-session fingerprint persistence (seed / proxy pid / timezone)
    # ------------------------------------------------------------------
    @classmethod
    def load_session_state(cls, session_id: str) -> Optional[Fingerprint]:
        """Load this conversation's persisted fingerprint."""
        try:
            with open(cls._state_file(session_id), "r", encoding="utf-8") as handle:
                data = json.load(handle)
            return Fingerprint(**data)
        except (FileNotFoundError, OSError, TypeError, ValueError, json.JSONDecodeError):
            return None

    @classmethod
    def save_session_state(cls, session_id: str, fingerprint: Fingerprint) -> None:
        path = cls._state_file(session_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(fingerprint.__dict__, handle, ensure_ascii=False, indent=2)

    @classmethod
    def clear_session_state(cls, session_id: str) -> None:
        """Drop only this conversation's persisted fingerprint."""
        try:
            os.remove(cls._state_file(session_id))
        except (FileNotFoundError, OSError):
            pass

    # ------------------------------------------------------------------
    # Per-user cookie store (login state shared across that user's sessions)
    # ------------------------------------------------------------------
    @classmethod
    def load_global_cookies(cls, user_id: str) -> List[Cookie]:
        try:
            with open(cls._cookies_file(user_id), "r", encoding="utf-8") as handle:
                cookies = json.load(handle)
            return cookies if isinstance(cookies, list) else []
        except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError):
            return []

    @classmethod
    def save_global_cookies(cls, user_id: str, cookies: List[Cookie]) -> None:
        """Merge `cookies` into this user's persisted cookie store."""
        if not cookies:
            return
        existing = cls.load_global_cookies(user_id)
        merged: Dict[Any, Dict[str, Any]] = {
            (c.get("name"), c.get("domain"), c.get("path")): c for c in existing
        }
        for cookie in cookies:
            merged[(cookie.get("name"), cookie.get("domain"), cookie.get("path"))] = cookie

        path = cls._cookies_file(user_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(list(merged.values()), handle, ensure_ascii=False, indent=2)

    @classmethod
    def clear_global_cookies(cls, user_id: str) -> None:
        try:
            os.remove(cls._cookies_file(user_id))
        except (FileNotFoundError, OSError):
            pass

    @classmethod
    def get_launch_args(cls, fingerprint: Fingerprint, trace_dir: str = "") -> List[str]:
        args = [
            f"--fp-seed={fingerprint.seed}",
            f"--fp-timezone={fingerprint.timezone}",
            f"--fp-platform={fingerprint.platform}",
            "--disable-infobars",
            "--start-maximized",
        ]
        if trace_dir:
            args.append(f"--fp-native-trace-dir={trace_dir}")
            args.append("--no-sandbox")
        return args

    @classmethod
    def get_live_fingerprint_js(cls) -> str:
        return r"""
async () => {
  const safe = (fn, fallback = null) => {
    try {
      const value = fn();
      return value === undefined ? fallback : value;
    } catch (e) {
      return fallback;
    }
  };
  const safeArray = (value) => {
    try {
      return Array.from(value || []);
    } catch (e) {
      return [];
    }
  };
  const hashString = async (text) => {
    try {
      if (!crypto || !crypto.subtle || !TextEncoder) return "";
      const bytes = new TextEncoder().encode(String(text || ""));
      const digest = await crypto.subtle.digest("SHA-256", bytes);
      return Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, "0")).join("");
    } catch (e) {
      return "";
    }
  };
  const getCanvasData = async () => {
    const canvas = safe(() => document.createElement("canvas"));
    if (!canvas) return {};
    canvas.width = 240;
    canvas.height = 80;
    const ctx = safe(() => canvas.getContext("2d"));
    if (!ctx) return {};
    safe(() => {
      ctx.textBaseline = "top";
      ctx.font = "16px Arial";
      ctx.fillStyle = "#f60";
      ctx.fillRect(4, 4, 120, 32);
      ctx.fillStyle = "#069";
      ctx.fillText("Fingerprint probe 123", 8, 12);
      ctx.strokeStyle = "rgba(120, 30, 200, 0.7)";
      ctx.beginPath();
      ctx.arc(180, 38, 24, 0, Math.PI * 2);
      ctx.stroke();
    });
    const dataUrl = safe(() => canvas.toDataURL(), "");
    return {
      canvas_data_url: dataUrl || "",
      canvas_hash: await hashString(dataUrl || ""),
      canvas_preview: dataUrl ? dataUrl.slice(0, 160) : "",
    };
  };
  const getWebglData = (type) => {
    const canvas = safe(() => document.createElement("canvas"));
    const gl = canvas ? safe(() => canvas.getContext(type)) : null;
    if (!gl) return {};
    const debugInfo = safe(() => gl.getExtension("WEBGL_debug_renderer_info"));
    const unmaskedVendor = debugInfo ? safe(() => gl.getParameter(debugInfo.UNMASKED_VENDOR_WEBGL), "") : "";
    const unmaskedRenderer = debugInfo ? safe(() => gl.getParameter(debugInfo.UNMASKED_RENDERER_WEBGL), "") : "";
    const extensions = safe(() => gl.getSupportedExtensions(), []) || [];
    return {
      context: type,
      vendor: safe(() => gl.getParameter(gl.VENDOR), ""),
      renderer: safe(() => gl.getParameter(gl.RENDERER), ""),
      version: safe(() => gl.getParameter(gl.VERSION), ""),
      shading_language_version: safe(() => gl.getParameter(gl.SHADING_LANGUAGE_VERSION), ""),
      unmasked_vendor: unmaskedVendor || "",
      unmasked_renderer: unmaskedRenderer || "",
      max_texture_size: safe(() => gl.getParameter(gl.MAX_TEXTURE_SIZE), 0),
      max_viewport_dims: safe(() => safeArray(gl.getParameter(gl.MAX_VIEWPORT_DIMS)), []),
      extensions,
      extension_count: extensions.length,
    };
  };
  const getPlugins = () => safeArray(navigator.plugins).map((plugin) => ({
    name: String(plugin.name || ""),
    filename: String(plugin.filename || ""),
    description: String(plugin.description || ""),
    mime_types: safeArray(plugin).map((mime) => ({
      type: String(mime.type || ""),
      suffixes: String(mime.suffixes || ""),
      description: String(mime.description || ""),
    })),
  }));
  const getMimeTypes = () => safeArray(navigator.mimeTypes).map((mime) => ({
    type: String(mime.type || ""),
    suffixes: String(mime.suffixes || ""),
    description: String(mime.description || ""),
    enabled_plugin: mime.enabledPlugin ? String(mime.enabledPlugin.name || "") : "",
  }));
  const getPermissions = async () => {
    const names = ["geolocation", "notifications", "camera", "microphone", "clipboard-read", "clipboard-write", "persistent-storage"];
    const out = {};
    for (const name of names) {
      out[name] = await safe(async () => {
        if (!navigator.permissions || !navigator.permissions.query) return "unsupported";
        const result = await navigator.permissions.query({ name });
        return result && result.state ? String(result.state) : "unknown";
      }, "error");
    }
    return out;
  };
  const getUaCh = async () => {
    const data = navigator.userAgentData;
    if (!data) return {};
    const out = {
      brands: safeArray(data.brands).map((item) => ({ brand: String(item.brand || ""), version: String(item.version || "") })),
      mobile: Boolean(data.mobile),
      platform: String(data.platform || ""),
    };
    if (data.getHighEntropyValues) {
      out.high_entropy = await safe(async () => data.getHighEntropyValues([
        "architecture",
        "bitness",
        "brands",
        "fullVersionList",
        "mobile",
        "model",
        "platform",
        "platformVersion",
        "uaFullVersion",
        "wow64",
      ]), {});
    }
    return out;
  };
  const connection = navigator.connection || navigator.mozConnection || navigator.webkitConnection || {};
  const webgl = getWebglData("webgl") || {};
  const webgl2 = getWebglData("webgl2") || {};
  const canvas = await getCanvasData();
  const fingerprint = {
    user_agent: String(navigator.userAgent || ""),
    app_version: String(navigator.appVersion || ""),
    app_name: String(navigator.appName || ""),
    platform: String(navigator.platform || ""),
    languages: safeArray(navigator.languages).map(String),
    language: String(navigator.language || ""),
    vendor: String(navigator.vendor || ""),
    product: String(navigator.product || ""),
    product_sub: String(navigator.productSub || ""),
    hardware_concurrency: Number(navigator.hardwareConcurrency || 0),
    device_memory: Number(navigator.deviceMemory || 0),
    max_touch_points: Number(navigator.maxTouchPoints || 0),
    cookie_enabled: Boolean(navigator.cookieEnabled),
    do_not_track: String(navigator.doNotTrack || ""),
    webdriver: Boolean(navigator.webdriver),
    timezone: safe(() => Intl.DateTimeFormat().resolvedOptions().timeZone, ""),
    timezone_offset: new Date().getTimezoneOffset(),
    screen_width: Number(screen.width || 0),
    screen_height: Number(screen.height || 0),
    screen_avail_width: Number(screen.availWidth || 0),
    screen_avail_height: Number(screen.availHeight || 0),
    screen_color_depth: Number(screen.colorDepth || 0),
    screen_pixel_depth: Number(screen.pixelDepth || 0),
    inner_width: Number(window.innerWidth || 0),
    inner_height: Number(window.innerHeight || 0),
    outer_width: Number(window.outerWidth || 0),
    outer_height: Number(window.outerHeight || 0),
    page_x_offset: Number(window.pageXOffset || 0),
    page_y_offset: Number(window.pageYOffset || 0),
    device_pixel_ratio: Number(window.devicePixelRatio || 0),
    connection_effective_type: String(connection.effectiveType || ""),
    connection_downlink: Number(connection.downlink || 0),
    connection_rtt: Number(connection.rtt || 0),
    connection_save_data: Boolean(connection.saveData),
    webgl_vendor: String(webgl.unmasked_vendor || webgl.vendor || ""),
    webgl_renderer: String(webgl.unmasked_renderer || webgl.renderer || ""),
    webgl,
    webgl2,
    canvas_data_url: String(canvas.canvas_data_url || ""),
    canvas_hash: String(canvas.canvas_hash || ""),
    canvas_preview: String(canvas.canvas_preview || ""),
    ua_ch: await getUaCh(),
    plugins: getPlugins(),
    mime_types: getMimeTypes(),
    permissions: await getPermissions(),
    storage_estimate: await safe(async () => navigator.storage && navigator.storage.estimate ? navigator.storage.estimate() : {}, {}),
    media_devices: {
      has_media_devices: Boolean(navigator.mediaDevices),
      has_enumerate_devices: Boolean(navigator.mediaDevices && navigator.mediaDevices.enumerateDevices),
      supported_constraints: safe(() => navigator.mediaDevices.getSupportedConstraints(), {}),
    },
    document: {
      visibility_state: String(document.visibilityState || ""),
      hidden: Boolean(document.hidden),
      has_focus: safe(() => document.hasFocus(), false),
      referrer: String(document.referrer || ""),
      character_set: String(document.characterSet || ""),
      compat_mode: String(document.compatMode || ""),
    },
    performance: {
      time_origin: Number(performance.timeOrigin || 0),
      now: Number(performance.now ? performance.now() : 0),
      memory: safe(() => performance.memory ? {
        js_heap_size_limit: Number(performance.memory.jsHeapSizeLimit || 0),
        total_js_heap_size: Number(performance.memory.totalJSHeapSize || 0),
        used_js_heap_size: Number(performance.memory.usedJSHeapSize || 0),
      } : {}, {}),
    },
    touch_pointer: {
      ontouchstart: "ontouchstart" in window,
      pointer_event: typeof PointerEvent,
      touch_event: typeof TouchEvent,
      max_touch_points: Number(navigator.maxTouchPoints || 0),
    },
    chrome: {
      has_chrome: Boolean(window.chrome),
      keys: window.chrome ? Object.keys(window.chrome).sort() : [],
      runtime: Boolean(window.chrome && window.chrome.runtime),
      load_times: typeof window.chrome?.loadTimes,
      csi: typeof window.chrome?.csi,
      app: Boolean(window.chrome && window.chrome.app),
    },
  };
  return fingerprint;
}
"""

    @classmethod
    async def collect_live_fingerprint(cls, page: Any) -> Dict[str, Any]:
        result = await page.evaluate(cls.get_live_fingerprint_js())
        return result if isinstance(result, dict) else {}

    @classmethod
    def save_live_fingerprint_artifact(cls, session_id: str, fingerprint: Dict[str, Any]) -> str:
        from reverseloom.runtime.config import artifact_dir as _artifact_dir
        session_dir = _artifact_dir(session_id or "default")
        os.makedirs(session_dir, exist_ok=True)
        artifact_path = os.path.join(session_dir, cls.BROWSER_FINGERPRINT_ARTIFACT)
        payload = {"fingerprint": dict(fingerprint or {})}
        with open(artifact_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)

        delivery_status = session_store.get(session_id or "default", "delivery_status", {}) or {}
        delivery_status[cls.BROWSER_FINGERPRINT_ARTIFACT] = {
            "path": os.path.abspath(artifact_path),
            "status": "SYSTEM",
            "fatal_gaps": [],
            "recommended_rework": [],
            "summary": "Live browser fingerprint captured automatically for sandbox replay.",
            "tags": ["system:browser", "kind:fingerprint", "runtime:run_shell"],
            "producer": "browser_session",
        }
        session_store.set(session_id or "default", "delivery_status", delivery_status)
        return os.path.abspath(artifact_path)

    @classmethod
    async def persist_live_fingerprint(cls, session_id: str, page: Any) -> str:
        fingerprint = await cls.collect_live_fingerprint(page)
        return cls.save_live_fingerprint_artifact(session_id, fingerprint)

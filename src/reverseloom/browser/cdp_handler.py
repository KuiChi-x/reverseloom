import logging
import asyncio
import time
from typing import Dict, List, Any, Optional, Tuple

class CdpHandler:
    """
    Handles CDP communication, network interception, and resource extraction for a specific page.

    One CdpHandler per Page. Owned by BrowserSession.cdp_handlers[page]. All
    per-page state (cdp_session, main_frame_id, network_logs, script_registry,
    OOPIF iframe caches, debugger pause state, …) lives here so that multi-tab
    sessions are naturally isolated: switching tabs is just picking a different
    handler out of the dict — no state bleeds between pages.
    """
    def __init__(self):
        self.network_logs: List[Dict] = []
        self.runtime_assets: Dict[str, Dict[str, Any]] = {}
        self.script_registry: Dict[str, Dict[str, Any]] = {}
        self.active_script_ids: set = set()
        self.url_to_script_ids: Dict[str, List[str]] = {}
        self.xhr_breakpoint_patterns: List[str] = []
        self.line_breakpoint_ids: List[str] = []
        self.pending_runtime_asset_requests: Dict[str, str] = {}
        self.cdp_session: Any = None
        self.is_paused: bool = False
        self.last_paused_event: Optional[Dict] = None
        self._temp_extra_headers: Dict[str, Dict] = {}
        self._temp_response_extra_headers: Dict[str, Dict] = {}
        self.page: Any = None
        self.page_guid: Optional[str] = None
        self.main_frame_id: Optional[str] = None
        self.last_loader_id: Optional[str] = None
        self.last_marked_loader_id: Optional[str] = None
        self.last_marked_url: Optional[str] = None
        self.last_marker_time: float = 0.0
        self.navigation_epoch: int = 0
        self.nav_marker_counter: int = 0
        self._max_network_logs: int = 5000
        self._max_script_registry: int = 5000
        self._max_runtime_assets: int = 5000
        # CDP sessions for this page's cross-origin iframes, keyed by the
        # iframe xpath in the parent document. Previously held on BrowserSession
        # but the cache is per-page: switching tabs must not expose another
        # page's stale OOPIF clients. dom_service rebuilds fill this on demand.
        self.frame_cdp_sessions: Dict[str, Any] = {}
        # Cumulative top-level viewport offset (x, y) of each OOPIF keyed by
        # the iframe xpath — see BrowserSession docstring (moved from there).
        self.frame_offsets: Dict[str, Tuple[float, float]] = {}

    async def reset_frame_cdp_sessions(self) -> None:
        """Detach and clear all iframe CDP sessions. Called at the start of
        each DOM tree rebuild because iframe frames and their backendNodeIds
        become stale after navigation or DOM mutation."""
        for frame_id, client in list(self.frame_cdp_sessions.items()):
            try:
                await client.detach()
            except Exception as exc:
                logging.debug(f"detach frame session {frame_id} failed: {exc}")
        self.frame_cdp_sessions.clear()
        self.frame_offsets.clear()

    async def detach(self) -> None:
        """Tear down this handler: release OOPIF clients and the page cdp session.
        Called by BrowserSession when the owning Page closes."""
        try:
            await self.reset_frame_cdp_sessions()
        except Exception as exc:
            logging.debug(f"reset_frame_cdp_sessions on detach failed: {exc}")
        if self.cdp_session is not None:
            try:
                await self.cdp_session.detach()
            except Exception as exc:
                logging.debug(f"cdp_session.detach failed: {exc}")
            self.cdp_session = None

    def _trim_network_logs(self) -> None:
        while len(self.network_logs) > self._max_network_logs:
            self.network_logs.pop(0)

    def _trim_script_registry(self) -> None:
        while len(self.script_registry) > self._max_script_registry:
            oldest_script_id = next(iter(self.script_registry))
            meta = self.script_registry.pop(oldest_script_id, {})
            self.active_script_ids.discard(oldest_script_id)
            url = str(meta.get("url") or "")
            if url in self.url_to_script_ids:
                self.url_to_script_ids[url] = [
                    sid for sid in self.url_to_script_ids[url]
                    if sid != oldest_script_id
                ]
                if not self.url_to_script_ids[url]:
                    self.url_to_script_ids.pop(url, None)

    def _trim_runtime_assets(self) -> None:
        while len(self.runtime_assets) > self._max_runtime_assets:
            oldest_url = next(iter(self.runtime_assets))
            self.runtime_assets.pop(oldest_url, None)

    def reset_observation_state(self) -> None:
        """Clear internal observation/debug caches while keeping browser storage intact."""
        self.network_logs.clear()
        self.runtime_assets.clear()
        self.script_registry.clear()
        self.active_script_ids.clear()
        self.url_to_script_ids.clear()
        self.xhr_breakpoint_patterns = []
        self.line_breakpoint_ids = []
        self.pending_runtime_asset_requests.clear()
        self._temp_extra_headers.clear()
        self._temp_response_extra_headers.clear()
        self.last_loader_id = None
        self.last_marked_loader_id = None
        self.last_marked_url = None
        self.last_marker_time = 0.0
        self.navigation_epoch = 0
        self.nav_marker_counter = 0
        self.is_paused = False
        self.last_paused_event = None

    @property
    def js_codes(self) -> Dict[str, str]:
        text_assets: Dict[str, str] = {}
        for url, asset in self.runtime_assets.items():
            if asset.get("base64Encoded"):
                continue
            text_assets[url] = str(asset.get("body", "") or "")
        return text_assets

    @staticmethod
    def _is_runtime_asset_url(url: str) -> bool:
        lower_url = (url or "").split("?", 1)[0].lower()
        return lower_url.endswith((".js", ".mjs", ".wasm"))

    @staticmethod
    def _is_runtime_asset_mime(mime_type: str) -> bool:
        lower_mime = (mime_type or "").lower()
        return (
            "javascript" in lower_mime
            or "ecmascript" in lower_mime
            or "application/x-javascript" in lower_mime
            or "application/wasm" in lower_mime
        )
    
    def _extract_stack_frames(self, stack, max_frames=30):
        """Recursively extract call frames from a StackTrace (including async parents)."""
        frames = []
        curr = stack
        while curr and len(frames) < max_frames:
            # 如果有异步描述（如 "Promise.then"），加上标记
            desc = curr.get("description")
            if desc and frames: # 第一个 stack 不需要分界线
                frames.append({
                    "functionName": f"--- {desc} ---",
                    "url": "", "lineNumber": 0, "columnNumber": 0, "scriptId": ""
                })

            for f in curr.get("callFrames", []):
                if len(frames) >= max_frames: break
                frames.append({
                    "functionName": f.get("functionName") or "(anonymous)",
                    "scriptId": f.get("scriptId", ""),
                    "url": f.get("url", "")[:1200],
                    "lineNumber": f.get("lineNumber", 0),
                    "columnNumber": f.get("columnNumber", 0),
                })
            curr = curr.get("parent")
        return frames

    async def attach(self, context: Any, page: Any):
        """Initialize CDP session and event listeners."""
        self.page = page
        self.page_guid = getattr(page, "guid", None) or str(id(page))
        try:
            self.cdp_session = await context.new_cdp_session(page)
            
            # 1. 获取主 Frame ID
            tree = await self.cdp_session.send("Page.getFrameTree")
            self.main_frame_id = tree.get("frameTree", {}).get("frame", {}).get("id")
            logging.info(f"Attached to page. Main Frame ID: {self.main_frame_id}")

            await self.cdp_session.send("Network.enable")
            await self.cdp_session.send("Debugger.enable")
            await self.cdp_session.send("Debugger.setAsyncCallStackDepth", {"maxDepth": 32})
            await self.cdp_session.send("Debugger.setBlackboxPatterns", {
                "patterns": ["node_modules", "bower_components", r"jquery.*\.js", r"react.*\.js", r"vue.*\.js"]
            })
            
            self.cdp_session.on("Network.requestWillBeSent", self._handle_network_request)
            self.cdp_session.on("Network.requestWillBeSentExtraInfo", self._handle_request_extra_info)
            self.cdp_session.on("Network.responseReceived", self._handle_response_received)
            self.cdp_session.on("Network.responseReceivedExtraInfo", self._handle_response_extra_info)
            self.cdp_session.on("Network.loadingFinished", 
                               lambda e: asyncio.create_task(self._handle_network_finished(e)))
            
            self.cdp_session.on("Debugger.paused", self._handle_paused)
            self.cdp_session.on("Debugger.resumed", self._handle_resumed)
            self.cdp_session.on("Debugger.scriptParsed", self._handle_script_parsed)

            # 监听主页面导航，自动清理日志
            page.on("framenavigated", self._handle_frame_navigation)
        except Exception as e:
            logging.debug(f"Failed to attach CDP handler: {e}")

    def _handle_paused(self, event):
        self.is_paused = True
        self.last_paused_event = event
        logging.info("Debugger PAUSED.")

    def _handle_resumed(self, event=None):
        self.is_paused = False
        logging.info("Debugger RESUMED.")

    def _handle_script_parsed(self, event):
        script_id = event.get("scriptId", "")
        if not script_id:
            return

        url = event.get("url", "") or ""
        self.script_registry[script_id] = {
            "scriptId": script_id,
            "url": url,
            "load_time": time.time(),
            "startLine": event.get("startLine", 0),
            "startColumn": event.get("startColumn", 0),
            "endLine": event.get("endLine", 0),
            "endColumn": event.get("endColumn", 0),
            "executionContextId": event.get("executionContextId"),
            "hash": event.get("hash", ""),
            "length": event.get("length"),
        }

        if url:
            script_ids = self.url_to_script_ids.setdefault(url, [])
            if script_id not in script_ids:
                script_ids.append(script_id)

        self.active_script_ids.add(script_id)
        self._trim_script_registry()
        
        async def fetch_source():
            try:
                res = await self.cdp_session.send("Debugger.getScriptSource", {"scriptId": script_id})
                if script_id in self.script_registry:
                    self.script_registry[script_id]["source"] = res.get("scriptSource", "")
            except Exception as e:
                logging.debug(f"Proactive getScriptSource failed for {script_id}: {e}")
                if script_id in self.script_registry:
                    self.script_registry[script_id]["source_unavailable"] = True
                    self.script_registry[script_id]["source_error"] = str(e)
                    
        asyncio.create_task(fetch_source())

    def _add_navigation_marker(self, url, loader_id=None):
        """向日志流中插入一个高度去重且智能合并的导航标记（DevTools 风格）。"""
        now = time.time()
        
        # 1. 重定向链合并：如果这个 loader_id 之前已经打过标了，尝试原地更新 URL 而非新增
        if loader_id:
            # 在最近的 5 条里找是否有同 loader 的标记
            for log in reversed(self.network_logs[-5:]):
                if log.get("type") == "navigation_event" and log.get("loaderId") == loader_id:
                    log["url"] = url
                    log["responseBody"] = f"--- ⚡️ NAVIGATED TO {url} ⚡️ ---"
                    self.last_marked_url = url
                    self.last_marker_time = now
                    return
            # 如果没找到但 loaderId 一致，说明已经打过标但被挤远了，也跳过
            if loader_id == self.last_marked_loader_id:
                return

        # 2. SPA/事件信号判重：同一个 URL 在 2 秒内不重复打标 (针对无 loaderId 信号)
        if not loader_id:
            if url == self.last_marked_url and (now - self.last_marker_time) < 2.0:
                return

        # 3. 极速信号合并：如果最后一条已经是标记，且时间极短，直接覆盖（防止并发信号堆叠）
        if self.network_logs and self.network_logs[-1].get("type") == "navigation_event":
            if (now - self.last_marker_time) < 1.0:
                self.network_logs[-1]["url"] = url
                self.network_logs[-1]["responseBody"] = f"--- ⚡️ NAVIGATED TO {url} ⚡️ ---"
                if loader_id: self.network_logs[-1]["loaderId"] = loader_id
                self.last_marked_url = url
                self.last_marked_loader_id = loader_id or self.last_marked_loader_id
                self.last_marker_time = now
                return

        self.nav_marker_counter += 1
        marker = {
            "requestId": f"NAV-{self.nav_marker_counter}",
            "url": url,
            "method": "NAVIGATE",
            "type": "navigation_event",
            "load_time": now,
            "navigation_epoch": self.navigation_epoch,
            "loaderId": loader_id,
            "status": 200,
            "headers": {},
            "response_headers": {},
            "postData": "",
            "initiator_stack": [],
            "responseBody": f"--- ⚡️ NAVIGATED TO {url} ⚡️ ---"
        }
        self.network_logs.append(marker)
        self.last_marked_loader_id = loader_id
        self.last_marked_url = url
        self.last_marker_time = now
        self._trim_network_logs()

    def _handle_frame_navigation(self, frame):
        """记录主框架导航事件（针对 SPA 路由变化或浏览器后退进行兜底）。"""
        if self.page and frame == self.page.main_frame:
            url = frame.url
            if url == "about:blank":
                return
            
            # 这里是事件驱动的，通常比 loaderId 逻辑慢，所以大概率会被去重拦掉
            if url != self.last_marked_url:
                self.navigation_epoch += 1
            self._add_navigation_marker(url)
            self._temp_extra_headers.clear()
            self._temp_response_extra_headers.clear()
            self.active_script_ids.clear()

    def _handle_request_extra_info(self, event):
        req_id = event.get("requestId", "")
        headers = event.get("headers", {})
        # 更新已有的日志
        target = next((log for log in self.network_logs if log["requestId"] == req_id), None)
        if target:
            target["headers"].update(headers)
        else:
            self._temp_extra_headers[req_id] = headers

    def _handle_response_extra_info(self, event):
        req_id = event.get("requestId", "")
        headers = event.get("headers", {})
        # 更新已有的日志（响应 Headers）
        target = next((log for log in self.network_logs if log["requestId"] == req_id), None)
        if target:
            if "response_headers" not in target: target["response_headers"] = {}
            target["response_headers"].update(headers)
        else:
            self._temp_response_extra_headers[req_id] = headers

    def _handle_network_request(self, event):
        req = event.get("request", {})
        url = req.get("url", "")
        req_type = event.get("type", "")
        req_id = event.get("requestId", "")
        loader_id = event.get("loaderId", "")
        frame_id = event.get("frameId", "")
        method = req.get("method", "")
        load_time = event.get("wallTime") or time.time()
        
        # 核心：监测主页面的硬刷新/主跳转
        if req_type == "Document" and frame_id == self.main_frame_id:
            # 只要 loaderId 是新的，或者这是第一次加载，就打标 (首笔请求领先)
            if not self.last_loader_id or loader_id != self.last_loader_id:
                logging.info(f"Detected new navigation (LoaderId: {loader_id}). Inserting marker...")
                self.navigation_epoch += 1
                self._add_navigation_marker(url, loader_id=loader_id)
            self.last_loader_id = loader_id

        if req_type not in ["Stylesheet", "Preflight"] and method in ["GET", "POST"]:
             if not url.lower().endswith(".css"):
                initiator = event.get("initiator", {})
                initiator_stack = []
                if initiator.get("type") == "script":
                    stack = initiator.get("stack")
                    if stack:
                        frames = self._extract_stack_frames(stack)
                        for i, f in enumerate(frames):
                            f["index"] = i
                            initiator_stack.append(f)
                    elif initiator.get("url"):
                        # 如果没有 stack 但是有直接位置信息
                        initiator_stack.append({
                            "index": 0, "functionName": "(initiator)",
                            "url": initiator.get("url", "")[:1200],
                            "lineNumber": initiator.get("lineNumber", 0),
                            "columnNumber": initiator.get("columnNumber", 0),
                            "scriptId": "",
                        })

                new_entry = {
                    "requestId": req_id,
                    "url": url,
                    "method": method,
                    "type": req_type, # 记录资源类型 (如 Document, XHR, Fetch)
                    "load_time": load_time,
                    "navigation_epoch": self.navigation_epoch,
                    "loaderId": loader_id,
                    "frameId": frame_id,
                    "status": None, # 这里拿不到状态码，需要在 responseReceived 更新
                    "mimeType": "",
                    "headers": req.get("headers", {}),
                    "response_headers": {},
                    "postData": req.get("postData", ""),
                    "initiator_stack": initiator_stack,
                    "responseBody": '',
                    "responseBodyIsBase64": False,
                }
                
                # 合并可能已经到达的 ExtraInfo (Cookie / Extra Response Headers)
                if req_id in self._temp_extra_headers:
                    new_entry["headers"].update(self._temp_extra_headers.pop(req_id))
                if req_id in self._temp_response_extra_headers:
                    new_entry["response_headers"].update(self._temp_response_extra_headers.pop(req_id))

                self.network_logs.append(new_entry)
                self._trim_network_logs()

        if self._is_runtime_asset_url(url):
            self.pending_runtime_asset_requests[req_id] = url

    def _handle_response_received(self, event):
        req_id = event.get("requestId", "")
        response = event.get("response", {})
        status = response.get("status")
        headers = response.get("headers", {})
        
        target = next((log for log in self.network_logs if log["requestId"] == req_id), None)
        if target:
            target["status"] = status
            mime_type = response.get("mimeType", "") or target.get("mimeType", "")
            target["mimeType"] = mime_type
            # 合并 Headers，优先信任 Response 里的，但保留 ExtraInfo 里的新字段
            current_headers = target.get("response_headers", {})
            if isinstance(current_headers, dict):
                current_headers.update(headers)
                target["response_headers"] = current_headers

            if self._is_runtime_asset_mime(mime_type):
                self.pending_runtime_asset_requests[req_id] = target.get("url", "")
            
            if "type" not in target or not target["type"]:
                target["type"] = event.get("type", "")

    async def _handle_network_finished(self, event):
        req_id = event.get("requestId", "")
        target = next((log for log in self.network_logs if log["requestId"] == req_id), None)
        if target and self.cdp_session:
            try:
                res = await self.cdp_session.send("Network.getResponseBody", {"requestId": req_id})
                target["responseBody"] = res.get("body", "")
                target["responseBodyIsBase64"] = bool(res.get("base64Encoded"))
            except:
                pass

        asset_url = self.pending_runtime_asset_requests.get(req_id)
        if asset_url and self.cdp_session:
            try:
                res = await self.cdp_session.send("Network.getResponseBody", {"requestId": req_id})
                body = res.get("body", "")
                base64_encoded = bool(res.get("base64Encoded"))
                target_entry = next((log for log in self.network_logs if log.get("requestId") == req_id), None) or {}
                self.runtime_assets[asset_url] = {
                    "url": asset_url,
                    "requestId": req_id,
                    "load_time": target_entry.get("load_time", time.time()),
                    "mimeType": target_entry.get("mimeType", ""),
                    "body": body,
                    "base64Encoded": base64_encoded,
                }
                self._trim_runtime_assets()
                if not base64_encoded:
                    for script_id in self.url_to_script_ids.get(asset_url, []):
                        meta = self.script_registry.get(script_id)
                        if meta is not None:
                            meta["source"] = str(body or "")
            except:
                pass
            finally:
                self.pending_runtime_asset_requests.pop(req_id, None)

import asyncio
import logging
from typing import Dict, Any, Optional, List

from patchright.async_api import CDPSession, Page
from dataclasses import dataclass
from collections import defaultdict


@dataclass(frozen=True, slots=True)
class Rect:
    """Closed axis-aligned rectangle with (x1,y1) bottom-left, (x2,y2) top-right."""
    x1: float
    y1: float
    x2: float
    y2: float

    def __post_init__(self):
        if not (self.x1 <= self.x2 and self.y1 <= self.y2):
            pass

    def area(self) -> float:
        return (self.x2 - self.x1) * (self.y2 - self.y1)

    def intersects(self, other: 'Rect') -> bool:
        return not (self.x2 <= other.x1 or other.x2 <= self.x1 or self.y2 <= other.y1 or other.y2 <= self.y1)

    def contains(self, other: 'Rect') -> bool:
        return self.x1 <= other.x1 and self.y1 <= other.y1 and self.x2 >= other.x2 and self.y2 >= other.y2


class RectUnionPure:
    __slots__ = ('_rects',)
    _MAX_RECTS = 5000

    def __init__(self):
        self._rects: List[Rect] = []

    def _split_diff(self, a: Rect, b: Rect) -> List[Rect]:
        parts = []
        if a.y1 < b.y1:
            parts.append(Rect(a.x1, a.y1, a.x2, b.y1))
        if b.y2 < a.y2:
            parts.append(Rect(a.x1, b.y2, a.x2, a.y2))
        y_lo = max(a.y1, b.y1)
        y_hi = min(a.y2, b.y2)
        if a.x1 < b.x1:
            parts.append(Rect(a.x1, y_lo, b.x1, y_hi))
        if b.x2 < a.x2:
            parts.append(Rect(b.x2, y_lo, a.x2, y_hi))
        return parts

    def contains(self, r: Rect) -> bool:
        if not self._rects:
            return False
        stack = [r]
        for s in self._rects:
            new_stack = []
            for piece in stack:
                if s.contains(piece):
                    continue
                if piece.intersects(s):
                    new_stack.extend(self._split_diff(piece, s))
                else:
                    new_stack.append(piece)
            if not new_stack:
                return True
            stack = new_stack
        return False

    def add(self, r: Rect) -> bool:
        if len(self._rects) >= self._MAX_RECTS:
            return False
        if self.contains(r):
            return False
        pending = [r]
        i = 0
        while i < len(self._rects):
            s = self._rects[i]
            new_pending = []
            changed = False
            for piece in pending:
                if piece.intersects(s):
                    new_pending.extend(self._split_diff(piece, s))
                    changed = True
                else:
                    new_pending.append(piece)
            pending = new_pending
            if changed:
                i += 1
            else:
                i += 1
        self._rects.extend(pending)
        return True


from reverseloom.browser.dom.views import (
    DOMRect, NodeType, EnhancedAXProperty, EnhancedAXNode,
    EnhancedSnapshotNode, EnhancedDOMTreeNode
)


class DomService:
    def __init__(self, client: CDPSession, page: Page = None):
        self.client = client
        self.page = page

    async def get_dom_tree(self, offset_x: float = 0, offset_y: float = 0, viewport_width: float = 1920,
                           viewport_height: float = 1080, cross_origin_iframes: bool = True,
                           device_pixel_ratio: float = 1.0) -> EnhancedDOMTreeNode:
        # Detach any iframe CDP sessions kept from a previous build — their
        # backendNodeIds are about to be invalidated by this rebuild.
        if self.page:
            try:
                from reverseloom.browser.browser_manager import browser_manager
                ctx = getattr(self.page, "context", None)
                session_id = None
                # We cannot recover session_id from page alone; the caller
                # resets by rebuilding, so detach is best-effort via any
                # BrowserSession matching this context.
                from reverseloom.browser.session_manager import SessionManager
                for sid, bs in SessionManager().sessions.items():
                    if bs.context is ctx:
                        await bs.reset_frame_cdp_sessions()
                        break
            except Exception as exc:
                logging.debug(f"reset_frame_cdp_sessions pre-build failed: {exc}")

        if self.page:
            cdp_session = await self.page.context.new_cdp_session(self.page)
            try:
                return await self._build_tree_for_session(
                    cdp_session, self.page, offset_x=offset_x, offset_y=offset_y,
                    viewport_width=viewport_width, viewport_height=viewport_height,
                    cross_origin_iframes=cross_origin_iframes,
                    device_pixel_ratio=device_pixel_ratio
                )
            finally:
                await cdp_session.detach()
        else:
            return await self._build_tree_for_session(
                self.client, self.page, offset_x=offset_x, offset_y=offset_y,
                viewport_width=viewport_width, viewport_height=viewport_height,
                cross_origin_iframes=cross_origin_iframes,
                device_pixel_ratio=device_pixel_ratio
            )

    async def _build_tree_for_session(self, client: CDPSession, page: Page = None, offset_x: float = 0,
                                      offset_y: float = 0, viewport_width: float = 1920, viewport_height: float = 1080,
                                      cross_origin_iframes: bool = True,
                                      device_pixel_ratio: float = 1.0) -> EnhancedDOMTreeNode:
        await client.send("DOM.enable")
        await client.send("Accessibility.enable")
        await client.send("DOMSnapshot.enable")

        # 1. Capture DOM Snapshot, DOM tree, and Accessibility tree concurrently
        snapshot_task = client.send("DOMSnapshot.captureSnapshot", {
            "computedStyles": [
                "display", "visibility", "opacity", "cursor", "pointer-events",
                "position", "background-color", "background-image"
            ],
            "includePaintOrder": True,
            "includeDOMRects": True,
            'includeBlendedBackgroundColors': False,
            'includeTextColorOpacities': False,
        })

        doc_task = client.send("DOM.getDocument", {"pierce": True, "depth": -1})

        ax_task = client.send("Accessibility.getFullAXTree")

        import asyncio
        # Wait for all tasks to complete concurrently with a timeout
        try:
            snapshot_response, doc_response, ax_response = await asyncio.gather(snapshot_task, doc_task, ax_task)
        except asyncio.TimeoutError:
            logging.warning("Timeout waiting for CDP commands (captureSnapshot/getDocument/getFullAXTree)")
            return EnhancedDOMTreeNode(
                node_id=-1, backend_node_id=-1, node_type=1, node_name="HTML", node_value="",
                attributes={}, is_visible=False, absolute_position=None, ax_node=None, snapshot_node=None,
                parent_node=None, frame_id=None
            )

        logging.info("captureSnapshot and getDocument and getDocument finish")
        root_node_data = doc_response.get("root", {})
        ax_nodes_data = ax_response.get("nodes", [])

        # 4. Build lookups — run CPU-intensive work in threads to avoid blocking the event loop
        #    (blocking the loop causes aiohttp heartbeat PONG timeout → WebSocket disconnect)
        loop = asyncio.get_running_loop()
        snapshot_lookup, ax_lookup = await asyncio.gather(
            loop.run_in_executor(None, self._build_snapshot_lookup, snapshot_response, device_pixel_ratio),
            loop.run_in_executor(None, self._build_ax_lookup, ax_nodes_data),
        )

        # 5. Build tree in thread pool to keep the event loop free for heartbeat PONGs
        pending_iframes = []
        root_node = await loop.run_in_executor(None, lambda: self._build_node_sync(
            root_node_data, snapshot_lookup, ax_lookup,
            offset_x, offset_y, viewport_width, viewport_height,
            cross_origin_iframes, pending_iframes,
            has_page=page is not None,
        ))

        # 6. Resolve cross-origin iframes (requires async CDP calls, cannot run in thread)
        if pending_iframes and page:
            await self._resolve_pending_iframes(pending_iframes, page)

        logging.info("build_tree_for_session finish")
        return root_node

    def _build_snapshot_lookup(self, snapshot_response: Dict[str, Any], device_pixel_ratio: float = 1.0) -> Dict[
        int, EnhancedSnapshotNode]:
        lookup = {}
        documents = snapshot_response.get("documents", [])
        strings = snapshot_response.get("strings", [])

        style_names = [
            "display", "visibility", "opacity", "cursor", "pointer-events",
            "position", "background-color", "background-image"
        ]

        for doc in documents:
            nodes = doc.get("nodes", {})
            backend_node_ids = nodes.get("backendNodeId", [])

            layout = doc.get("layout", {})
            node_indices = layout.get("nodeIndex", [])
            bounds = layout.get("bounds", [])
            styles = layout.get("styles", [])
            paint_orders = layout.get("paintOrders", [])

            # Parse isClickable
            is_clickable_data = nodes.get("isClickable", {})
            clickable_indices = set(is_clickable_data.get("index", []))

            for i, backend_id in enumerate(backend_node_ids):
                if backend_id not in lookup:
                    lookup[backend_id] = EnhancedSnapshotNode(
                        is_clickable=(i in clickable_indices),
                        bounds=None,
                        computed_styles={},
                        paint_order=None
                    )

            for i, node_idx in enumerate(node_indices):
                if node_idx < len(backend_node_ids):
                    backend_id = backend_node_ids[node_idx]

                    rect = None
                    if i < len(bounds):
                        b = bounds[i]
                        # CDP DOMSnapshot bounds are in device pixels — convert to CSS pixels
                        dpr = device_pixel_ratio if device_pixel_ratio > 0 else 1.0
                        rect = DOMRect(
                            x=b[0] / dpr,
                            y=b[1] / dpr,
                            width=b[2] / dpr,
                            height=b[3] / dpr
                        )

                    paint_order = None
                    if i < len(paint_orders):
                        paint_order = paint_orders[i]

                    computed_styles = {}
                    cursor_style = None
                    if i < len(styles):
                        node_styles = styles[i]
                        for j, style_str_idx in enumerate(node_styles):
                            if j < len(style_names) and style_str_idx >= 0 and style_str_idx < len(strings):
                                style_name = style_names[j]
                                style_val = strings[style_str_idx]
                                computed_styles[style_name] = style_val
                                if style_name == "cursor":
                                    cursor_style = style_val

                    if backend_id in lookup:
                        lookup[backend_id].bounds = rect
                        lookup[backend_id].computed_styles = computed_styles
                        lookup[backend_id].cursor_style = cursor_style
                        if paint_order is not None:
                            lookup[backend_id].paint_order = paint_order

        return lookup

    def _build_ax_lookup(self, ax_nodes_data: List[Dict[str, Any]]) -> Dict[int, EnhancedAXNode]:
        lookup = {}
        for ax_node in ax_nodes_data:
            backend_id = ax_node.get("backendDOMNodeId")
            if not backend_id:
                continue

            properties = []
            for prop in ax_node.get("properties", []):
                properties.append(EnhancedAXProperty(
                    name=prop.get("name", ""),
                    value=prop.get("value", {}).get("value")
                ))

            lookup[backend_id] = EnhancedAXNode(
                ax_node_id=ax_node.get("nodeId", ""),
                ignored=ax_node.get("ignored", False),
                role=ax_node.get("role", {}).get("value"),
                name=ax_node.get("name", {}).get("value"),
                description=ax_node.get("description", {}).get("value"),
                properties=properties,
                child_ids=ax_node.get("childIds")
            )
        return lookup

    def _build_node_sync(
            self,
            node_data: Dict[str, Any],
            snapshot_lookup: Dict[int, EnhancedSnapshotNode],
            ax_lookup: Dict[int, EnhancedAXNode],
            offset_x: float,
            offset_y: float,
            viewport_width: float,
            viewport_height: float,
            cross_origin_iframes: bool,
            pending_iframes: list,
            parent: Optional[EnhancedDOMTreeNode] = None,
            current_iframe_xpath: Optional[str] = None,
            device_pixel_ratio: float = 1.0,
            has_page: bool = False
    ) -> EnhancedDOMTreeNode:
        """Synchronous DOM tree builder — runs in a thread pool to keep the event loop free."""

        backend_id = node_data.get("backendNodeId", 0)
        snapshot_node = snapshot_lookup.get(backend_id)
        ax_node = ax_lookup.get(backend_id)

        # Parse attributes
        attrs_list = node_data.get("attributes", [])
        attributes = {attrs_list[i]: attrs_list[i + 1] for i in range(0, len(attrs_list), 2)}

        # Calculate absolute position
        abs_pos = None
        is_visible = False
        if snapshot_node and snapshot_node.bounds:
            b = snapshot_node.bounds
            abs_pos = DOMRect(
                x=b.x + offset_x,
                y=b.y + offset_y,
                width=b.width,
                height=b.height
            )

            # Visibility logic
            styles = snapshot_node.computed_styles
            if (styles.get("display") != "none" and
                    styles.get("visibility") != "hidden" and
                    styles.get("opacity") != "0" and
                    b.width > 0 and b.height > 0):

                # Check if element is at least partially in the viewport
                if (abs_pos.x < viewport_width and
                        abs_pos.y < viewport_height and
                        abs_pos.x + abs_pos.width > 0 and
                        abs_pos.y + abs_pos.height > 0):
                    is_visible = True

                    # Note: parent-overflow clipping was intentionally removed.
                    # The naive "if any ancestor has overflow:hidden and child's
                    # rect falls outside the ancestor's content box -> hidden"
                    # breaks for position:fixed / position:sticky elements,
                    # which escape their DOM parent's layout and are frequently
                    # used for sticky headers / nav bars. On sites with an
                    # `overflow-x: hidden` wrapper (common anti-horizontal-scroll
                    # pattern) every fixed/sticky header under it was wrongly
                    # flagged invisible. Paint-order filtering in the serializer
                    # already handles real occlusion, so we rely on that.

        node = EnhancedDOMTreeNode(
            node_id=node_data.get("nodeId", 0),
            backend_node_id=backend_id,
            node_type=node_data.get("nodeType", 0),
            node_name=node_data.get("nodeName", ""),
            node_value=node_data.get("nodeValue", ""),
            attributes=attributes,
            is_visible=is_visible,
            absolute_position=abs_pos,
            ax_node=ax_node,
            snapshot_node=snapshot_node,
            parent_node=parent,
            frame_id=current_iframe_xpath
        )

        # Handle children
        if node_data.get("children"):
            children = []
            for child_data in node_data["children"]:
                children.append(self._build_node_sync(
                    child_data, snapshot_lookup, ax_lookup, offset_x, offset_y, viewport_width,
                    viewport_height, cross_origin_iframes, pending_iframes, node,
                    current_iframe_xpath, device_pixel_ratio, has_page
                ))
            node.children_nodes = children

        # Handle shadow roots
        if node_data.get("shadowRoots"):
            shadow_roots = []
            for shadow_data in node_data["shadowRoots"]:
                shadow_roots.append(self._build_node_sync(
                    shadow_data, snapshot_lookup, ax_lookup, offset_x, offset_y, viewport_width,
                    viewport_height, cross_origin_iframes, pending_iframes, node,
                    current_iframe_xpath, device_pixel_ratio, has_page
                ))
            node.shadow_roots = shadow_roots

        # Handle iframes
        content_doc_data = node_data.get("contentDocument")
        if content_doc_data:
            # Same-origin iframe: contentDocument is in the snapshot, build synchronously
            node.content_document = self._build_node_sync(
                content_doc_data, snapshot_lookup, ax_lookup, offset_x, offset_y, viewport_width,
                viewport_height, cross_origin_iframes, pending_iframes, node,
                node.xpath or current_iframe_xpath, device_pixel_ratio, has_page
            )
        elif node.node_name == "IFRAME" and cross_origin_iframes and has_page and node.is_visible:
            # Cross-origin iframe: requires async CDP calls — defer to post-processing
            frame_id = node_data.get("frameId")
            if frame_id:
                pending_iframes.append({
                    'node': node,
                    'frame_id': frame_id,
                    'attributes': attributes,
                    'abs_pos': abs_pos,
                    'offset_x': offset_x,
                    'offset_y': offset_y,
                    'viewport_width': viewport_width,
                    'viewport_height': viewport_height,
                    'current_iframe_xpath': current_iframe_xpath,
                    'device_pixel_ratio': device_pixel_ratio,
                })

        return node

    async def _resolve_pending_iframes(self, pending_iframes: list, page: Page):
        """Resolve cross-origin iframes collected during sync tree building."""
        # Look up the BrowserSession so we can stash iframe CDP sessions on it.
        browser_session = None
        try:
            from reverseloom.browser.session_manager import SessionManager
            for bs in SessionManager().sessions.values():
                if bs.context is page.context:
                    browser_session = bs
                    break
        except Exception as exc:
            logging.debug(f"locate BrowserSession for iframe registration failed: {exc}")

        for iframe_info in pending_iframes:
            node = iframe_info['node']
            frame_id = iframe_info['frame_id']
            attributes = iframe_info['attributes']
            abs_pos = iframe_info['abs_pos']
            offset_x = iframe_info['offset_x']
            offset_y = iframe_info['offset_y']
            viewport_width = iframe_info['viewport_width']
            viewport_height = iframe_info['viewport_height']
            current_iframe_xpath = iframe_info['current_iframe_xpath']
            device_pixel_ratio = iframe_info['device_pixel_ratio']

            try:
                target_frame = None
                for f in page.frames:
                    src = attributes.get('src', '').split('#')[0].split('?')[0]
                    if src and src in f.url:
                        target_frame = f
                        break

                if target_frame:
                    frame_client = await page.context.new_cdp_session(target_frame)
                    keep_client = False
                    try:
                        new_offset_x = offset_x
                        new_offset_y = offset_y
                        if abs_pos:
                            new_offset_x += abs_pos.x
                            new_offset_y += abs_pos.y

                        try:
                            node.content_document = await self._build_tree_for_session(
                                frame_client, page, new_offset_x, new_offset_y,
                                viewport_width, viewport_height,
                                cross_origin_iframes=True,
                                device_pixel_ratio=device_pixel_ratio
                            )
                        except asyncio.TimeoutError:
                            logging.warning(f"Timeout building tree for iframe {frame_id}")
                            node.content_document = None

                        if node.content_document:
                            node.content_document.parent_node = node
                            iframe_xpath = node.xpath or current_iframe_xpath

                            def update_frame_id(n):
                                n.frame_id = iframe_xpath
                                for c in n.children_nodes: update_frame_id(c)
                                for s in n.shadow_roots: update_frame_id(s)
                                if n.content_document: update_frame_id(n.content_document)

                            update_frame_id(node.content_document)

                            # Register this iframe's CDP session so click/type
                            # can route backendNodeId calls to it.
                            if browser_session is not None and iframe_xpath:
                                browser_session.frame_cdp_sessions[iframe_xpath] = frame_client
                                # DOM.getContentQuads on frame_client returns
                                # coords in the iframe's own viewport. The
                                # iframe node's absolute_position is the
                                # top-level viewport offset where that inner
                                # viewport starts, so adding it converts
                                # quads → top-level viewport coords. For
                                # nested OOPIFs the iframe node itself was
                                # built inside its parent frame's tree, so
                                # abs_pos already includes every enclosing
                                # iframe's offset.
                                if node.absolute_position is not None:
                                    browser_session.frame_offsets[iframe_xpath] = (
                                        node.absolute_position.x,
                                        node.absolute_position.y,
                                    )
                                keep_client = True
                    finally:
                        if not keep_client:
                            try:
                                await frame_client.detach()
                            except Exception as exc:
                                logging.debug(f"detach unused iframe client failed: {exc}")
            except Exception as e:
                logging.warning(f"Failed to attach to iframe {frame_id}: {e}")

    def _get_children_text(self, node: EnhancedDOMTreeNode) -> str:
        texts = []

        def get_text(n: EnhancedDOMTreeNode):
            if n.node_name.lower() in {'script', 'style', 'noscript'}:
                return
            if n.node_type == 3 and n.node_value:  # Text node
                texts.append(n.node_value)
            elif n.node_type == 1:
                # Add alt text for images
                if n.node_name.lower() == 'img' and n.attributes and n.attributes.get('alt'):
                    texts.append(n.attributes.get('alt'))
                # Add aria-label if present
                elif n.attributes and n.attributes.get('aria-label'):
                    texts.append(n.attributes.get('aria-label'))
            for child in n.children_nodes:
                get_text(child)

        get_text(node)

        # Clean up text
        text = " ".join(texts).strip()
        import re
        text = re.sub(r'\s+', ' ', text)
        return text

    def _has_form_control_descendant(self, node: EnhancedDOMTreeNode, max_depth: int = 2) -> bool:
        if max_depth <= 0:
            return False

        for child in node.children_nodes + node.shadow_roots:
            if child.node_type != 1:
                continue
            if child.node_name.lower() in {'input', 'select', 'textarea'}:
                return True
            if self._has_form_control_descendant(child, max_depth - 1):
                return True
        return False

    def _is_interactive(self, node: EnhancedDOMTreeNode) -> bool:
        if node.node_type != 1:  # ELEMENT_NODE
            return False

        if node.node_name.lower() in {'html', 'body'}:
            return False

        # Filter out elements with pointer-events: none
        if node.snapshot_node and node.snapshot_node.computed_styles.get('pointer-events') == 'none':
            return False

        # Filter out tiny elements
        if node.snapshot_node and node.snapshot_node.bounds:
            b = node.snapshot_node.bounds
            if b.width < 1 or b.height < 1:
                return False

        # Check CDP native isClickable
        if node.snapshot_node and node.snapshot_node.is_clickable:
            return True

        if node.node_name.lower() in {'iframe', 'frame'}:
            if node.snapshot_node and node.snapshot_node.bounds:
                if node.snapshot_node.bounds.width > 100 and node.snapshot_node.bounds.height > 100:
                    return True

        if node.node_name.lower() == 'label':
            if node.attributes and node.attributes.get('for'):
                return False
            if self._has_form_control_descendant(node, max_depth=2):
                return True

        if node.node_name.lower() == 'span':
            if self._has_form_control_descendant(node, max_depth=2):
                return True

        if node.attributes:
            search_indicators = {
                'search', 'magnify', 'glass', 'lookup', 'find', 'query',
                'search-icon', 'search-btn', 'search-button', 'searchbox'
            }

            # Add size limit for search indicators to avoid selecting huge wrappers
            size_ok = True
            if node.snapshot_node and node.snapshot_node.bounds:
                b = node.snapshot_node.bounds
                if b.width > 500 or b.height > 100:
                    size_ok = False

            if size_ok:
                class_list = node.attributes.get('class', '').lower().split()
                if any(indicator in ' '.join(class_list) for indicator in search_indicators):
                    return True

                element_id = node.attributes.get('id', '').lower()
                if any(indicator in element_id for indicator in search_indicators):
                    return True

                for attr_name, attr_value in node.attributes.items():
                    if attr_name.startswith('data-') and any(
                            indicator in attr_value.lower() for indicator in search_indicators):
                        return True

        if node.ax_node and node.ax_node.properties:
            for prop in node.ax_node.properties:
                try:
                    if prop.name == 'disabled' and prop.value:
                        return False
                    if prop.name == 'hidden' and prop.value:
                        return False
                    if prop.name in ['focusable', 'editable', 'settable'] and prop.value:
                        return True
                    if prop.name in ['checked', 'expanded', 'pressed', 'selected']:
                        return True
                    if prop.name in ['required', 'autocomplete'] and prop.value:
                        return True
                    if prop.name == 'keyshortcuts' and prop.value:
                        return True
                except:
                    continue

        interactive_tags = {
            'button', 'input', 'select', 'textarea', 'a',
            'details', 'summary', 'option', 'optgroup'
        }
        if node.node_name.lower() in interactive_tags:
            return True

        if node.attributes:
            interactive_attributes = {'onclick', 'onmousedown', 'onmouseup', 'onkeydown', 'onkeyup'}
            if any(attr in node.attributes for attr in interactive_attributes):
                return True
            if 'tabindex' in node.attributes and node.attributes['tabindex'] != '-1':
                return True

            if 'role' in node.attributes:
                interactive_roles = {
                    'button', 'link', 'menuitem', 'option', 'radio', 'checkbox',
                    'tab', 'textbox', 'combobox', 'slider', 'spinbutton',
                    'search', 'searchbox', 'row', 'cell', 'gridcell'
                }
                if node.attributes['role'] in interactive_roles:
                    return True

        if node.ax_node and node.ax_node.role:
            interactive_ax_roles = {
                'button', 'link', 'menuitem', 'option', 'radio', 'checkbox',
                'tab', 'textbox', 'combobox', 'slider', 'spinbutton', 'listbox',
                'search', 'searchbox', 'row', 'cell', 'gridcell'
            }
            if node.ax_node.role in interactive_ax_roles:
                return True

        if node.snapshot_node and node.snapshot_node.bounds:
            b = node.snapshot_node.bounds
            if 10 <= b.width <= 50 and 10 <= b.height <= 50:
                if node.attributes:
                    icon_attributes = {'class', 'role', 'onclick', 'data-action', 'aria-label'}
                    if any(attr in node.attributes for attr in icon_attributes):
                        return True

        if node.snapshot_node and node.snapshot_node.computed_styles.get('cursor') == 'pointer':
            return True

        return False

    def _get_depth(self, node: EnhancedDOMTreeNode) -> int:
        depth = 0
        current = node.parent_node
        while current:
            depth += 1
            current = current.parent_node
        return depth

    def _get_depth(self, node: EnhancedDOMTreeNode) -> int:
        depth = 0
        current = node.parent_node
        while current:
            depth += 1
            current = current.parent_node
        return depth

    def extract_interactive_elements(self, root: EnhancedDOMTreeNode) -> List[Dict[str, Any]]:
        from reverseloom.browser.dom.serializer.serializer import DOMTreeSerializer
        logging.info("start extract_interactive_elements from dom tree")
        serializer = DOMTreeSerializer(
            root_node=root,
            enable_bbox_filtering=True,
            paint_order_filtering=True
        )

        serialized_state, timing_info = serializer.serialize_accessible_elements()
        logging.info(f"serialize timing: {timing_info}")

        interactive_elements = []
        for backend_node_id, node in serialized_state.xpath_map.items():
            if not node.absolute_position:
                continue

            rect = {
                "x": node.absolute_position.x,
                "y": node.absolute_position.y,
                "width": node.absolute_position.width,
                "height": node.absolute_position.height
            }

            # Get text representation
            text = ""
            if node.ax_node and node.ax_node.name:
                text = node.ax_node.name
            elif node.node_value:
                text = node.node_value
            else:
                # Fallback text extraction
                texts = []

                def get_text(n: EnhancedDOMTreeNode):
                    if n.node_name.lower() in {'script', 'style', 'noscript'}:
                        return
                    if n.node_type == 3 and n.node_value:
                        texts.append(n.node_value)
                    elif n.node_type == 1:
                        if n.node_name.lower() == 'img' and n.attributes and n.attributes.get('alt'):
                            texts.append(n.attributes.get('alt'))
                        elif n.attributes and n.attributes.get('aria-label'):
                            texts.append(n.attributes.get('aria-label'))
                    for child in n.children_nodes:
                        get_text(child)

                get_text(node)
                text = " ".join(texts).strip()
                import re
                text = re.sub(r'\s+', ' ', text)

            if len(text) > 100:
                text = text[:100] + "..."

            interactive_elements.append({
                "backend_node_id": node.backend_node_id,
                "stable_hash": node.stable_hash,
                "text": text,
                "attributes": node.attributes,
                "rect": rect,
                "xpath": node.xpath,
                "identityKey": node.identity_key,
                "is_interactive": self._is_interactive(node),
                "node_name": node.node_name.lower(),
                "is_clickable": node.snapshot_node.is_clickable if node.snapshot_node else False,
                "depth": self._get_depth(node),
                "frame_id": node.frame_id
            })

        return interactive_elements

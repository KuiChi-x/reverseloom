import json
import logging
from typing import Tuple, List, Dict, Any

from reverseloom.browser.element_mapping_service import element_mapping_service
from reverseloom.browser.dom.service import DomService
from reverseloom.browser.browser_manager import browser_manager

# Cache DPR per session to avoid re-running page.evaluate, which hangs when the
# browser is paused at a debugger breakpoint.
_DPR_CACHE: Dict[str, float] = {}


async def execute_get_interactive_elements(page, session_id: str) -> Tuple[str, List[Dict], Dict[str, Any]]:
    """
    在页面中提取元素，格式化为紧凑的 JSON 字符串返回。
    同时提取并返回所有元素的坐标边框信息 (bboxes)，供外部画图使用。
    """
    try:
        client = await browser_manager.get_cdp_client(session_id)

        # Get DPR via JS — the most reliable source, works across all OS/scaling combos.
        # Cached per session: page.evaluate hangs when the browser is paused at a
        # debugger breakpoint, so we only run it once and reuse the value afterwards.
        device_pixel_ratio = _DPR_CACHE.get(session_id)
        if device_pixel_ratio is None and not browser_manager.is_paused(session_id):
            try:
                device_pixel_ratio = float(await page.evaluate("window.devicePixelRatio"))
            except Exception:
                device_pixel_ratio = 1.0
            _DPR_CACHE[session_id] = device_pixel_ratio
        if device_pixel_ratio is None:
            logging.warning(f"[GetInteractiveElements] Failed to get device pixel ratio, using default 1.0")
            device_pixel_ratio = 1.0

        try:
            metrics = await client.send("Page.getLayoutMetrics")
            css_visual_viewport = metrics.get('cssVisualViewport', {})
            css_layout_viewport = metrics.get('cssLayoutViewport', {})

            css_width = css_visual_viewport.get('clientWidth', css_layout_viewport.get('clientWidth', 1920.0))
            css_height = css_visual_viewport.get('clientHeight', css_layout_viewport.get('clientHeight', 1080.0))

            scroll_x = int(css_visual_viewport.get('pageX', 0))
            scroll_y = int(css_visual_viewport.get('pageY', 0))
        except Exception as e:
            logging.warning(f"Failed to get viewport info from CDP: {e}")
            scroll_x, scroll_y, css_width, css_height = 0, 0, 1920, 1080

        viewport_info = {
            "dpr": float(device_pixel_ratio),
            "scrollX": scroll_x,
            "scrollY": scroll_y,
            "width": css_width,
            "height": css_height
        }
        logging.info(
            f"[GetInteractiveElements] viewport: {css_width}x{css_height} CSS px, DPR={device_pixel_ratio:.2f}, scroll=({scroll_x},{scroll_y})")

        dom_service = DomService(client, page)
        dom_tree = await dom_service.get_dom_tree(
            offset_x=-viewport_info["scrollX"],
            offset_y=-viewport_info["scrollY"],
            viewport_width=viewport_info["width"],
            viewport_height=viewport_info["height"],
            device_pixel_ratio=device_pixel_ratio
        )
        interactive_elements = dom_service.extract_interactive_elements(dom_tree)

        compact_results = []
        bboxes = []
        dpr = viewport_info["dpr"]

        for elem in interactive_elements:
            oc_id = element_mapping_service.get_or_create_oc_id(session_id, elem)
            element_mapping_service.register_element(session_id, oc_id, elem)

            text_val = elem.get("text", "")
            if len(text_val) > 2000:
                text_val = text_val[:1997] + "..."

            item = {
                "i": oc_id,
                "l": text_val,
                "a": 1 if elem.get("is_interactive") else 0
            }

            compact_results.append(item)

            if "rect" in elem:
                bboxes.append({
                    "id": oc_id,
                    "rect": {
                        "left": elem["rect"].get("x", 0),
                        "top": elem["rect"].get("y", 0),
                        "width": elem["rect"].get("width", 0),
                        "height": elem["rect"].get("height", 0)
                    }
                })

        if not compact_results:
            return "当前视图内无元素 (No interactive elements found).", [], viewport_info

        return json.dumps(compact_results, ensure_ascii=False), bboxes, viewport_info

    except Exception as e:
        logging.error(f"[GetInteractiveElements] Execution error: {e}")
        return f"Execution error: {str(e)}", [], {"dpr": 1.0, "width": 1920, "height": 1080}

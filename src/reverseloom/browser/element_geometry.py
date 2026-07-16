"""Element geometry helpers — shared CDP-based primitives to resolve an
element's realtime viewport position.

Why this exists: click/type/drag need a center ``(cx, cy)``; screenshot
``clip`` and coordinate-grid overlay need a bbox ``{x, y, width, height}``.
Both derive from the same CDP call chain (``scrollIntoViewIfNeeded`` →
``DOM.getContentQuads`` → OOPIF offset translation). Putting it in one
place keeps ``browser_tools`` and ``captcha_tools`` from each maintaining a
copy of the routing logic.

All returned coordinates are in the top-level page viewport, same frame of
reference used by Playwright's ``page.mouse.click`` and ``page.screenshot(
clip=...)``.
"""
from __future__ import annotations

import asyncio
import logging
from typing import List, Optional, Tuple

from reverseloom.browser.browser_manager import browser_manager


Quad = List[Tuple[float, float]]


async def get_quads(session_id: str, element_info: dict) -> Optional[Quad]:
    """Realtime content-quad (4 corners) for an element in TOP-LEVEL viewport
    coords. Returns None if the node has no content quads (detached,
    zero-sized, display:none) or if the CDP call fails.

    Routing: frame_id picks the owning CDP session (main vs OOPIF) and, for
    OOPIFs, the cumulative top-level offset registered at tree-build time.
    Shadow DOM needs no special handling — backendNodeId is session-global
    and getContentQuads pierces shadow boundaries.
    """
    backend_node_id = element_info.get("backend_node_id")
    if not backend_node_id:
        return None

    frame_id = element_info.get("frame_id")
    client = await browser_manager.get_cdp_client_for_frame(session_id, frame_id)

    try:
        await client.send("DOM.scrollIntoViewIfNeeded", {"backendNodeId": backend_node_id})
    except Exception as exc:
        logging.debug(f"scrollIntoViewIfNeeded failed for backendNodeId {backend_node_id}: {exc}")

    try:
        res = await asyncio.wait_for(
            client.send("DOM.getContentQuads", {"backendNodeId": backend_node_id}),
            timeout=3,
        )
    except Exception as exc:
        logging.debug(f"getContentQuads failed for backendNodeId {backend_node_id}: {exc}")
        return None

    quads = res.get("quads") or []
    if not quads:
        return None
    q = quads[0]
    points: Quad = [(q[0], q[1]), (q[2], q[3]), (q[4], q[5]), (q[6], q[7])]

    # Quads are in the owning frame's viewport. For OOPIFs, translate to
    # top-level viewport using the offset captured at tree-build time.
    if frame_id:
        session = browser_manager.get_session(session_id)
        offset = session.frame_offsets.get(frame_id) if session else None
        if offset is not None:
            ox, oy = offset
            points = [(x + ox, y + oy) for (x, y) in points]

    return points


async def get_center(session_id: str, element_info: dict) -> Optional[Tuple[float, float]]:
    """Top-level viewport ``(cx, cy)`` for click/type/drag targeting."""
    quads = await get_quads(session_id, element_info)
    if not quads:
        return None
    xs = [p[0] for p in quads]
    ys = [p[1] for p in quads]
    return sum(xs) / 4, sum(ys) / 4


async def get_bbox(session_id: str, element_info: dict) -> Optional[dict]:
    """Top-level viewport bbox ``{x, y, width, height}`` for screenshot clips
    and grid overlays."""
    quads = await get_quads(session_id, element_info)
    if not quads:
        return None
    xs = [p[0] for p in quads]
    ys = [p[1] for p in quads]
    x0, y0 = min(xs), min(ys)
    x1, y1 = max(xs), max(ys)
    return {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import statistics
from typing import Dict, List, Tuple

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from reverseloom.browser.browser_manager import browser_manager
from reverseloom.browser.element_geometry import get_bbox as _element_bbox
from reverseloom.browser.coordinate_grid import (
    create_coordinate_grid,
    encode_png,
)
from reverseloom.browser.element_mapping_service import element_mapping_service
from reverseloom.browser.image_preprocess import apply_clahe, highlight_roi
from reverseloom.tools.browser.result_handler import handle_tool_result
from graphloom import StandardThoughtInput


_N_SAMPLES = 1
_UPSCALE = 4
# Padding (viewport px) added around the target bbox before screenshotting,
# so the model sees spatial context (buttons, captions, adjacent chrome).
# The true target is then spotlighted via highlight_roi on Image 1.
_CONTEXT_PAD = 80


class LocatedPoint(BaseModel):
    x: int = Field(description="Viewport-pixel X of the target's GEOMETRIC CENTER (not the left edge), read from the red X tick labels.")
    y: int = Field(description="Viewport-pixel Y of the target's GEOMETRIC CENTER (not the top edge), read from the blue Y tick labels.")


class LocatedPoints(BaseModel):
    points: List[LocatedPoint] = Field(
        description="One point per input target, in the SAME ORDER as the input list. Do not add, drop, or reorder."
    )


def _build_prompt(targets: List[str], target_bbox: dict, grid_bbox: dict) -> List:
    target_block = "\n".join(f"  [{i}] {t}" for i, t in enumerate(targets))
    system = (
        "You read pixel coordinates off a coordinate grid for browser automation. "
        "Output is fed DIRECTLY into page.mouse.click / page.mouse.drag — no snapping, no retry.\n"
        "\n"
        "Two images:\n"
        "  Image 1 (semantic): same region, cyan box marks the target container, "
        "outside is dimmed. Use ONLY to identify which pixel is the target.\n"
        "  Image 2 (grid): same region with red X / blue Y tick labels in the margins. "
        "Use ONLY to read (x, y) off the tick labels.\n"
        "\n"
        "Rules:\n"
        "  - Return the GEOMETRIC CENTER of each target, never an edge or corner.\n"
        "  - Numbers are printed only on MAJOR ticks (every 25 px). Between two "
        "major numbers there are 4 thin minor ticks at 5-px spacing. If the "
        "target sits between majors, read the nearest major then count minor "
        "ticks (e.g. major 300 + 2 minor ticks = 310).\n"
        "  - Read x from red ticks (top or bottom), y from blue ticks (left or right).\n"
        "  - Every (x, y) must lie inside the cyan container bbox.\n"
        "  - Output exactly one point per input target, in the SAME ORDER as the "
        "input list. No prose."
    )
    user_text = (
        f"Cyan container bbox (viewport px): {json.dumps(target_bbox, ensure_ascii=False)}\n"
        f"Grid coverage bbox (viewport px): {json.dumps(grid_bbox, ensure_ascii=False)}\n"
        f"\n"
        f"Targets (return {len(targets)} points in this exact order):\n{target_block}"
    )
    return [system, user_text]


async def _sample_once(llm_structured, messages, n_targets: int) -> List[Tuple[int, int]]:
    """Return a list of (x, y) aligned by position with the input targets.
    Length may differ from n_targets if the model over/under-returns; caller handles."""
    result = await llm_structured.ainvoke(messages)
    points = getattr(result, "points", None) or []
    out: List[Tuple[int, int]] = []
    for p in points:
        x = getattr(p, "x", None) if not isinstance(p, dict) else p.get("x")
        y = getattr(p, "y", None) if not isinstance(p, dict) else p.get("y")
        if x is None or y is None:
            continue
        out.append((int(x), int(y)))
    return out


def _median_consensus(
    samples: List[List[Tuple[int, int]]], targets: List[str]
) -> Dict[str, Dict[str, int]]:
    """Positional MEAN: for each target index, average x/y across samples that
    produced a value at that index. Mean (not median) so all N calls
    contribute — a median of 3 would just pick 1 sample and waste the other 2.
    Name kept for backward compatibility."""
    out: Dict[str, Dict[str, int]] = {}
    for i, name in enumerate(targets):
        xs = [s[i][0] for s in samples if i < len(s)]
        ys = [s[i][1] for s in samples if i < len(s)]
        if not xs or not ys:
            continue
        out[name] = {
            "x": int(round(statistics.mean(xs))),
            "y": int(round(statistics.mean(ys))),
        }
    return out


class VisualLocateInput(StandardThoughtInput):
    target_id: str = Field(
        description=(
            "ocId of the container element to analyze visually (a <canvas>, "
            "captcha widget, puzzle board, slider track, or any region whose "
            "internal click targets cannot be enumerated via DOM)."
        )
    )
    targets: List[str] = Field(
        description=(
            "Natural-language descriptions of the points to locate inside the container. "
            "Each string must be SPECIFIC enough to disambiguate visually similar objects — "
            "include object identity + a distinguishing attribute (color/shape/label/position). "
            "For drags, put the role in the name (e.g. 'source: ...', 'destination: ...').\n"
            "\n"
            "Good: ['slider handle (blue circle at left end of track)',\n"
            "       'puzzle gap (dashed outline on the right)']\n"
            "Bad:  ['slider', 'button', 'drag start', 'drag end']\n"
            "\n"
            "Returns {name: {x, y}} in viewport pixels, (x, y) being the geometric center "
            "of each target — plug directly into browser_click / browser_drag."
        )
    )


@tool("visual_locate", args_schema=VisualLocateInput)
async def visual_locate(target_id: str, targets: List[str], **kwargs):
    """Find click/drag coordinates inside a container that DOM enumeration can't reach
    (canvases, captcha widgets, slider tracks, puzzle boards).

    Returns {name: {x, y}} in viewport pixels — each (x, y) is the geometric center of
    the named target, ready to pass to browser_click / browser_drag.

    Write each target description specifically — 'slider handle (blue circle at left end)'
    works; 'slider' does not. See VisualLocateInput.targets for the rubric.
    """
    session_id = kwargs.get("session_id")

    if not targets:
        return "Failed: targets must be a non-empty list of descriptions."

    async def _action():
        element_info = element_mapping_service.get_mapping(session_id, target_id)
        if not element_info:
            raise ValueError(f"No element mapping found for ocId {target_id}")

        bbox = await _element_bbox(session_id, element_info)
        if bbox is None or bbox["width"] <= 0 or bbox["height"] <= 0:
            cached = element_info.get("rect") or {}
            if cached.get("width", 0) > 0 and cached.get("height", 0) > 0:
                bbox = dict(cached)
            else:
                raise ValueError(
                    f"Cannot resolve bbox for ocId {target_id}; re-observe the page"
                )

        page = browser_manager.get_page(session_id)
        vp = page.viewport_size or {"width": 1920, "height": 1080}

        # True target bbox (clipped to viewport).
        tgt_x = max(0.0, bbox["x"])
        tgt_y = max(0.0, bbox["y"])
        tgt_right = min(float(vp["width"]), bbox["x"] + bbox["width"])
        tgt_bottom = min(float(vp["height"]), bbox["y"] + bbox["height"])
        tgt_w = max(1.0, tgt_right - tgt_x)
        tgt_h = max(1.0, tgt_bottom - tgt_y)
        effective_bbox = {"x": tgt_x, "y": tgt_y, "width": tgt_w, "height": tgt_h}

        # Expanded clip with context padding so the model sees surrounding
        # chrome (buttons, labels, popups) for semantic disambiguation. The
        # true target will be spotlighted via highlight_roi.
        ctx_x = max(0.0, tgt_x - _CONTEXT_PAD)
        ctx_y = max(0.0, tgt_y - _CONTEXT_PAD)
        ctx_right = min(float(vp["width"]), tgt_right + _CONTEXT_PAD)
        ctx_bottom = min(float(vp["height"]), tgt_bottom + _CONTEXT_PAD)
        ctx_w = max(1.0, ctx_right - ctx_x)
        ctx_h = max(1.0, ctx_bottom - ctx_y)
        context_bbox = {"x": ctx_x, "y": ctx_y, "width": ctx_w, "height": ctx_h}

        ctx_png = await page.screenshot(type="png", clip=context_bbox, timeout=5_000)

        # Image-level enhancement #1: CLAHE on the luminance channel —
        # pulls out detail in washed-out / underexposed captchas without
        # touching hue. Applied once; both Image 1 (ROI-highlighted) and
        # Image 2 (grid) are derived from the enhanced version so the LLM
        # reads the same pixels on both.
        from PIL import Image as _PILImage
        ctx_pil = _PILImage.open(io.BytesIO(ctx_png)).convert("RGB")
        try:
            ctx_pil = apply_clahe(ctx_pil, clip_limit=2.0, tile_grid=8)
        except Exception as exc:
            logging.warning(f"visual_locate CLAHE failed, using raw: {exc}")

        # Image 1: ROI spotlight — dim the padding area, neon-glow the true
        # target rectangle. Coordinates inside ctx_pil are IMAGE pixels;
        # convert viewport → image by subtracting context_bbox origin.
        roi_img = (int(tgt_x - ctx_x), int(tgt_y - ctx_y), int(tgt_w), int(tgt_h))
        try:
            raw_highlighted = highlight_roi(
                ctx_pil,
                roi_img,
                dim_factor=0.6,
                glow_color=(0, 255, 200),
                glow_width=6,
            )
        except Exception as exc:
            logging.warning(f"visual_locate highlight_roi failed, using raw: {exc}")
            raw_highlighted = ctx_pil

        buf = io.BytesIO()
        raw_highlighted.save(buf, format="PNG")
        raw_png = buf.getvalue()

        # Image 2: grid overlay. Grid labels must still be in viewport
        # coordinates (what browser_click consumes), so the bbox passed to
        # create_coordinate_grid is the context_bbox in viewport space; the
        # image is the CLAHE-enhanced ctx_pil (no ROI overlay here — ROI
        # glow would fight the red/blue axis colors).
        grid_img = create_coordinate_grid(
            ctx_pil,
            context_bbox,
            adaptive_contrast=True,
            upscale=_UPSCALE,
        )
        grid_png = encode_png(grid_img)
        grid_url = f"data:image/png;base64,{base64.b64encode(grid_png).decode('utf-8')}"
        raw_url = f"data:image/png;base64,{base64.b64encode(raw_png).decode('utf-8')}"

        from reverseloom.agent.build import build_llm
        llm_structured = build_llm().with_structured_output(
            LocatedPoints, method="function_calling"
        )

        system_text, user_text = _build_prompt(targets, effective_bbox, context_bbox)
        # Send TWO images, following the Global→Local pattern:
        #   1) raw screenshot — clean, for semantic identification of the
        #      target (no grid lines occluding the content)
        #   2) grid overlay — for reading out absolute viewport coords
        # This mirrors hcaptcha-challenger's approach and gives the model a
        # clear handoff between "what/where is the target" and "what are
        # the exact pixel coordinates".
        messages = [
            SystemMessage(content=system_text),
            HumanMessage(content=[
                {"type": "text", "text": user_text},
                {"type": "text", "text": "[Image 1 — RAW screenshot, use for semantic identification]"},
                {"type": "image_url", "image_url": {"url": raw_url, "detail": "high"}},
                {"type": "text", "text": "[Image 2 — SAME region with coordinate grid, use ONLY for reading (x, y) off the red/blue tick labels]"},
                {"type": "image_url", "image_url": {"url": grid_url, "detail": "high"}},
            ]),
        ]

        sample_tasks = [_sample_once(llm_structured, messages, len(targets)) for _ in range(_N_SAMPLES)]
        raw_samples = await asyncio.gather(*sample_tasks, return_exceptions=True)
        samples: List[List[Tuple[int, int]]] = [
            s for s in raw_samples if isinstance(s, list) and s
        ]
        if not samples:
            errs = [str(e) for e in raw_samples if isinstance(e, Exception)]
            raise RuntimeError(f"visual_locate sampling failed: {errs}")

        coords = _median_consensus(samples, targets)
        if not coords:
            raise RuntimeError(
                f"visual_locate returned no coords for any of: {targets}"
            )

        element_mapping_service.track_element(session_id, target_id)
        element_mapping_service.set_last_action(
            session_id, f"visual_locate({target_id}, {targets})"
        )

        missing = [t for t in targets if t not in coords]
        body = json.dumps(coords, ensure_ascii=False)
        if missing:
            return f"{body} (note: no consensus for {missing})"
        return body

    try:
        return await handle_tool_result(_action, session_id=session_id)
    except Exception as e:
        return f"Failed: {str(e)}"

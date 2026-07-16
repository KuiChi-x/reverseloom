"""Coordinate-grid overlay for screenshots (Pillow-only).

Designed for multimodal LLMs to read pixel coordinates off the axes:

- **X axis = red, Y axis = blue** — exploits the universal math-plot color
  convention, so the model can instantly tell which label governs which
  direction, even when several labels are visually close.
- **Labels on both sides** (top+bottom for X, left+right for Y) — LLM
  reads the nearest label instead of tracing a long line.
- **Pill-shaped label backgrounds** (white fill + dark text + colored
  border) — legible against any underlying image brightness, no
  `adaptive_contrast` gymnastics needed for the text.
- **Major ticks every 5th step**: bold line + bold label + a small cross
  marker at each major intersection (anchor points).
- **Nice tick steps** (5/10/25/50/100 ...) aligned to integer multiples
  rather than `linspace` values — labels are always round numbers.
- Optional ``upscale`` enlarges the base image before drawing so tick
  reads become more accurate.
"""
from __future__ import annotations

import io
from pathlib import Path
from typing import List, Tuple, TypedDict, Union

from PIL import Image, ImageDraw, ImageFont


class FloatRect(TypedDict):
    x: float
    y: float
    width: float
    height: float


BBox = Union[FloatRect, Tuple[float, float, float, float], List[float]]

# Space reserved for axis tick labels drawn outside the image content.
_MARGIN_LEFT = 60
_MARGIN_TOP = 32
_MARGIN_RIGHT = 60
_MARGIN_BOTTOM = 32
_TICK_LEN = 6

# Axis semantics: red X, blue Y (universal math-plot convention).
_X_AXIS_RGB = (200, 30, 30)
_Y_AXIS_RGB = (30, 80, 200)

# Neutral grid color for the interior (kept gray so the image stays visible).
_GRID_RGB = (110, 110, 110)

# Label styling (pill around text).
_LABEL_TEXT_RGB = (20, 20, 20)
_LABEL_BG_RGBA = (255, 255, 255, 235)
_LABEL_PAD_X = 3
_LABEL_PAD_Y = 1


def _load_image(image: Union[str, Path, Image.Image, bytes, bytearray]) -> Image.Image:
    if isinstance(image, Image.Image):
        return image.convert("RGB")
    if isinstance(image, (bytes, bytearray)):
        return Image.open(io.BytesIO(image)).convert("RGB")
    return Image.open(str(image)).convert("RGB")


def _extract_bbox(bbox: BBox) -> Tuple[float, float, float, float]:
    if isinstance(bbox, dict):
        return float(bbox["x"]), float(bbox["y"]), float(bbox["width"]), float(bbox["height"])
    return float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])


def _load_font(size: int) -> ImageFont.ImageFont:
    for name in ("arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _load_bold_font(size: int) -> ImageFont.ImageFont:
    for name in ("arialbd.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return _load_font(size)


def _hot_color(t: float) -> Tuple[int, int, int]:
    """matplotlib 'hot' colormap approximation: black → red → orange → yellow → white.
    ``t`` in [0, 1]."""
    t = max(0.0, min(1.0, t))
    if t < 0.333:
        # black → red
        u = t / 0.333
        return (int(255 * u), 0, 0)
    if t < 0.666:
        # red → yellow
        u = (t - 0.333) / 0.333
        return (255, int(255 * u), 0)
    # yellow → white
    u = (t - 0.666) / 0.334
    return (255, 255, int(255 * u))


def _cool_color(t: float) -> Tuple[int, int, int]:
    """matplotlib 'cool' colormap approximation: cyan → blue → violet.
    ``t`` in [0, 1]."""
    t = max(0.0, min(1.0, t))
    # cyan (0, 255, 255) → blue (0, 0, 255) → violet (150, 0, 200)
    if t < 0.5:
        u = t / 0.5
        return (0, int(255 * (1 - u)), 255)
    u = (t - 0.5) / 0.5
    return (int(150 * u), 0, int(255 - 55 * u))


def _nice_step(span: float, target_ticks: int = 15) -> int:
    """Pick a 'nice' tick step close to span/target. Includes small steps
    (1/2/5) so that tiny bboxes still get a usable number of divisions —
    hcaptcha-challenger defaults to ~15-20 tick lines; we match that."""
    if span <= 0 or target_ticks <= 1:
        return 10
    raw = span / target_ticks
    for step in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500):
        if step >= raw:
            return step
    return 1000


def _aligned_ticks(start: float, end: float, step: int) -> List[float]:
    """Tick values aligned to multiples of ``step``, within [start, end]."""
    if step <= 0:
        return [start, end]
    lo = int(start // step) * step
    if lo < start:
        lo += step
    ticks: List[float] = []
    v = lo
    while v <= end + 1e-6:
        ticks.append(float(v))
        v += step
    if not ticks:
        ticks = [start, end]
    return ticks


def _text_size(draw: ImageDraw.ImageDraw, text: str, font) -> Tuple[int, int]:
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0], bbox[3] - bbox[1]
    except AttributeError:
        return draw.textsize(text, font=font)  # type: ignore[attr-defined]


def _draw_pill_label(
    draw: ImageDraw.ImageDraw,
    cx: float,
    cy: float,
    text: str,
    font,
    border_rgb: Tuple[int, int, int],
    bold: bool = False,
) -> None:
    """Draw ``text`` centered at (cx, cy) in axis color — no pill background.

    Tick labels sit in the white margin, so they already have maximum
    contrast against their background. Dropping the pill cuts visual noise
    and reduces cognitive load for the LLM: it sees plain colored digits
    instead of a dense grid of rounded boxes.

    Name kept for backward compatibility with legend/callers.
    """
    tw, th = _text_size(draw, text, font)
    x0 = int(round(cx - tw / 2))
    y0 = int(round(cy - th / 2))
    draw.text(
        (x0, y0 - 1),
        text,
        fill=border_rgb + (255,),
        font=font,
    )


def create_coordinate_grid(
    image: Union[str, Path, Image.Image, bytes, bytearray],
    bbox: BBox,
    *,
    x_line_space_num: int = 11,  # kept for signature compat; unused now
    y_line_space_num: int = 20,  # kept for signature compat; unused now
    adaptive_contrast: bool = False,
    tick_labels_size: int = 12,
    color: str = "gray",  # kept for signature compat
    upscale: int = 2,
) -> Image.Image:
    """Overlay a LLM-friendly coordinate grid on ``image``.

    - Red X-axis labels (top + bottom), blue Y-axis labels (left + right).
    - Pill-shaped white-backed labels so they stay readable on any image.
    - Major grid lines every 5th step, with small crosses at intersections.
    - ``adaptive_contrast``: when True, sample the base image mean
      brightness (grayscale) and flip the minor grid-line color —
      near-black lines on bright backgrounds, near-white lines on dark
      backgrounds — so ticks stay visible on pure-black captchas and
      washed-out canvases alike. Mirrors hcaptcha-challenger's
      ``grid_color = 'black' if avg_brightness > 0.5 else 'white'``.
    """
    base = _load_image(image)
    if upscale and upscale > 1:
        base = base.resize(
            (base.size[0] * upscale, base.size[1] * upscale),
            Image.LANCZOS,
        )
    bw, bh = base.size
    bx, by, width, height = _extract_bbox(bbox)

    canvas_w = bw + _MARGIN_LEFT + _MARGIN_RIGHT
    canvas_h = bh + _MARGIN_TOP + _MARGIN_BOTTOM
    canvas = Image.new("RGB", (canvas_w, canvas_h), (255, 255, 255))
    canvas.paste(base, (_MARGIN_LEFT, _MARGIN_TOP))

    draw = ImageDraw.Draw(canvas, "RGBA")
    font = _load_font(tick_labels_size)
    font_bold = _load_bold_font(tick_labels_size)

    # Content area in canvas coordinates.
    left = _MARGIN_LEFT
    top = _MARGIN_TOP
    right = _MARGIN_LEFT + bw
    bottom = _MARGIN_TOP + bh

    x_step = 5
    y_step = 5
    x_ticks_val = _aligned_ticks(bx, bx + width, x_step)
    y_ticks_val = _aligned_ticks(by, by + height, y_step)

    def to_px_x(val: float) -> int:
        if width <= 0:
            return left
        return int(round(left + (val - bx) / width * bw))

    def to_px_y(val: float) -> int:
        if height <= 0:
            return top
        return int(round(top + (val - by) / height * bh))

    # --- Adaptive contrast: flip the minor grid-line color based on the
    # base image's mean brightness. This mirrors hcaptcha-challenger's
    # core idea (`grid_color = 'black' if avg_brightness > 0.5 else 'white'`):
    # gray lines disappear on pure-black or pure-white backgrounds, but
    # black-on-bright / white-on-dark stays visible on any screenshot.
    # The red/blue major lines and pill labels have their own high-contrast
    # backgrounds so they don't need to flip.
    minor_rgb = _GRID_RGB
    minor_alpha = 110
    major_alpha = 180
    if adaptive_contrast:
        try:
            thumb = base.convert("L").resize((32, 32))
            mean_l = sum(thumb.getdata()) / (32 * 32) / 255.0
            # Flip minor line color: bright bg → near-black lines,
            # dark bg → near-white lines. Mid-gray stays on neutral gray.
            if mean_l > 0.6:
                minor_rgb = (25, 25, 25)
            elif mean_l < 0.4:
                minor_rgb = (235, 235, 235)
            # Also boost opacity on both extremes so lines don't get
            # visually lost in high-contrast regions.
            extremity = abs(mean_l - 0.5) * 2  # 0 at mid-gray, 1 at 0 or 1
            minor_alpha = int(round(140 + 80 * extremity))   # 140..220
            major_alpha = int(round(190 + 50 * extremity))   # 190..240
        except Exception:
            pass

    # --- Grid lines inside the image ---
    minor_rgba = minor_rgb + (minor_alpha,)
    major_x_rgba = _X_AXIS_RGB + (major_alpha,)
    major_y_rgba = _Y_AXIS_RGB + (major_alpha,)

    # --- Per-cell heat-map tint (hcaptcha-challenger-style) ---
    # Each cell of the MAJOR grid (every 5th tick) gets a low-alpha color
    # swatch. In dense grids (20×15) this gives every cell a distinct visual
    # identity so the LLM stops "row-jumping" when reading tick labels.
    #   - dark backgrounds  → hot colormap (black→red→yellow→white)
    #   - bright backgrounds → cool colormap (cyan→blue→violet)
    # Only drawn when adaptive_contrast=True. Alpha kept low so the
    # underlying screenshot stays fully recognizable.
    if adaptive_contrast:
        try:
            thumb2 = base.convert("L").resize((32, 32))
            mean_l2 = sum(thumb2.getdata()) / (32 * 32) / 255.0
            cmap = _hot_color if mean_l2 < 0.5 else _cool_color
            # Walk every adjacent MAJOR tick pair to form cells.
            major_x_vals = [v for v in x_ticks_val if int(round(v)) % (x_step * 5) == 0]
            major_y_vals = [v for v in y_ticks_val if int(round(v)) % (y_step * 5) == 0]
            # If majors don't cover edges, extend with bbox borders so the
            # cells still tile the full region.
            if not major_x_vals or major_x_vals[0] > bx:
                major_x_vals = [bx] + major_x_vals
            if not major_x_vals or major_x_vals[-1] < bx + width:
                major_x_vals = major_x_vals + [bx + width]
            if not major_y_vals or major_y_vals[0] > by:
                major_y_vals = [by] + major_y_vals
            if not major_y_vals or major_y_vals[-1] < by + height:
                major_y_vals = major_y_vals + [by + height]

            n_cols = max(1, len(major_x_vals) - 1)
            n_rows = max(1, len(major_y_vals) - 1)
            n_total = n_cols * n_rows
            for i in range(n_cols):
                for j in range(n_rows):
                    idx = i + j * n_cols
                    t = idx / max(1, n_total - 1)
                    r, g, b = cmap(t)
                    x0 = to_px_x(major_x_vals[i])
                    x1 = to_px_x(major_x_vals[i + 1])
                    y0 = to_px_y(major_y_vals[j])
                    y1 = to_px_y(major_y_vals[j + 1])
                    draw.rectangle(
                        [x0, y0, x1, y1],
                        fill=(r, g, b, 38),  # alpha ~15%
                    )
        except Exception:
            pass

    for val in x_ticks_val:
        px = to_px_x(val)
        is_major = int(round(val)) % (x_step * 5) == 0
        draw.line(
            [(px, top), (px, bottom)],
            fill=major_x_rgba if is_major else minor_rgba,
            width=2 if is_major else 1,
        )
    for val in y_ticks_val:
        py = to_px_y(val)
        is_major = int(round(val)) % (y_step * 5) == 0
        draw.line(
            [(left, py), (right, py)],
            fill=major_y_rgba if is_major else minor_rgba,
            width=2 if is_major else 1,
        )

    # --- Crosses at major intersections (anchor points) ---
    cross_rgba = (40, 40, 40, 230)
    cross_len = 4
    for xv in x_ticks_val:
        if int(round(xv)) % (x_step * 5) != 0:
            continue
        for yv in y_ticks_val:
            if int(round(yv)) % (y_step * 5) != 0:
                continue
            px = to_px_x(xv)
            py = to_px_y(yv)
            draw.line([(px - cross_len, py), (px + cross_len, py)], fill=cross_rgba, width=1)
            draw.line([(px, py - cross_len), (px, py + cross_len)], fill=cross_rgba, width=1)

    # --- Outer frame (uses axis colors on the corresponding edges) ---
    draw.line([(left, top), (right, top)], fill=_X_AXIS_RGB + (255,), width=2)
    draw.line([(left, bottom), (right, bottom)], fill=_X_AXIS_RGB + (255,), width=2)
    draw.line([(left, top), (left, bottom)], fill=_Y_AXIS_RGB + (255,), width=2)
    draw.line([(right, top), (right, bottom)], fill=_Y_AXIS_RGB + (255,), width=2)

    # --- Tick marks + labels ---
    # Labels ONLY on major ticks (every 5th). Minor ticks get a short line
    # segment as a visual subdivision. Dense grids (step=5) would otherwise
    # render 4-digit labels every ~20 canvas px and smear into an illegible
    # red/blue band. Major labels land every 25 viewport-px → plenty of
    # room, and the LLM reads "nearest bold number + count minor ticks".
    top_label_y = top - _TICK_LEN - tick_labels_size / 2 - 2
    bottom_label_y = bottom + _TICK_LEN + tick_labels_size / 2 + 2
    for val in x_ticks_val:
        px = to_px_x(val)
        is_major = int(round(val)) % (x_step * 5) == 0
        draw.line([(px, top - _TICK_LEN), (px, top)], fill=_X_AXIS_RGB + (255,), width=1)
        draw.line([(px, bottom), (px, bottom + _TICK_LEN)], fill=_X_AXIS_RGB + (255,), width=1)
        if is_major:
            label = str(int(round(val)))
            _draw_pill_label(draw, px, top_label_y, label, font_bold, _X_AXIS_RGB, bold=True)
            _draw_pill_label(draw, px, bottom_label_y, label, font_bold, _X_AXIS_RGB, bold=True)

    left_label_cx = left - _TICK_LEN - 22
    right_label_cx = right + _TICK_LEN + 22
    for val in y_ticks_val:
        py = to_px_y(val)
        is_major = int(round(val)) % (y_step * 5) == 0
        draw.line([(left - _TICK_LEN, py), (left, py)], fill=_Y_AXIS_RGB + (255,), width=1)
        draw.line([(right, py), (right + _TICK_LEN, py)], fill=_Y_AXIS_RGB + (255,), width=1)
        if is_major:
            label = str(int(round(val)))
            _draw_pill_label(draw, left_label_cx, py, label, font_bold, _Y_AXIS_RGB, bold=True)
            _draw_pill_label(draw, right_label_cx, py, label, font_bold, _Y_AXIS_RGB, bold=True)

    # Axis legend in the top-left margin — spells out the color mapping
    # explicitly so the model can't confuse which axis is which.
    legend_y = _MARGIN_TOP / 2
    _draw_pill_label(draw, left / 2, legend_y - 6, "X=red", font_bold, _X_AXIS_RGB, bold=True)
    _draw_pill_label(draw, left / 2, legend_y + 6, "Y=blue", font_bold, _Y_AXIS_RGB, bold=True)

    return canvas


def encode_png(img: Image.Image) -> bytes:
    """PIL Image → PNG bytes."""
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

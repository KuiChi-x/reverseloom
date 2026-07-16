import base64
import io
import logging
from typing import List, Dict, Any

from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

# Font cache
_FONT_CACHE = {}

_FONT_PATHS = [
    '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',  # Linux
    '/System/Library/Fonts/Arial.ttf',  # macOS
    'C:\\Windows\\Fonts\\arial.ttf',  # Windows
    'arial.ttf',  # Windows fallback
    'Arial Bold.ttf',  # macOS alternative
]

def get_cross_platform_font(font_size: int):
    cache_key = ('system_font', font_size)
    if cache_key in _FONT_CACHE:
        return _FONT_CACHE[cache_key]

    font = None
    for font_path in _FONT_PATHS:
        try:
            font = ImageFont.truetype(font_path, font_size)
            break
        except OSError:
            continue

    _FONT_CACHE[cache_key] = font
    return font

def draw_bounding_boxes(
    screenshot_b64: str,
    bboxes: List[Dict[str, Any]],
    device_pixel_ratio: float = 1.0,
) -> str:
    """
    Draw DevTools-style bounding boxes on a base64 encoded screenshot.
    All visual parameters scale with device_pixel_ratio so boxes look
    consistent across 1x / 2x / 3x screens and arbitrary browser zoom levels.

    The annotated screenshot is returned as a data URL and is never persisted.
    """
    logging.info("Drawing bounding boxes on screenshot")
    try:
        screenshot_data = base64.b64decode(
            screenshot_b64.split(",")[-1] if "," in screenshot_b64 else screenshot_b64
        )
        image = Image.open(io.BytesIO(screenshot_data)).convert('RGBA')
        img_width, img_height = image.size
        dpr = device_pixel_ratio
        logging.info(f"[DrawBBox] image={img_width}x{img_height}, DPR={dpr:.2f}, bboxes={len(bboxes)}")

        # --- DPR-aware visual parameters ---
        line_width = max(2, round(2 * dpr))
        font_size = max(12, round(12 * dpr))
        label_pad_x = max(3, round(4 * dpr))
        label_pad_y = max(2, round(3 * dpr))
        min_box_device_px = max(4, round(4 * dpr))

        DISTINCT_COLORS = [
            (228, 26, 28),   # Red
            (55, 126, 184),  # Blue
            (77, 175, 74),   # Green
            (152, 78, 163),  # Purple
            (255, 127, 0),   # Orange
            (166, 86, 40),   # Brown
            (0, 139, 139),   # DarkCyan
            (220, 20, 60),   # Crimson
            (0, 0, 139),     # DarkBlue
            (210, 105, 30),  # Chocolate
        ]

        font = get_cross_platform_font(font_size)

        # Semi-transparent overlay layer (for fills)
        overlay = Image.new('RGBA', image.size, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        # Opaque layer for outlines and labels
        draw = ImageDraw.Draw(image)

        for b in bboxes:
            rect = b.get("rect")
            if not rect or rect.get("width", 0) <= 0 or rect.get("height", 0) <= 0:
                continue

            raw_id = b.get("id", "")
            label = raw_id[2:] if raw_id.startswith("o_") else raw_id
            if not label:
                continue

            # Deterministic distinct color based on label
            import hashlib
            color_idx = int(hashlib.md5(label.encode()).hexdigest(), 16) % len(DISTINCT_COLORS)
            base_color = DISTINCT_COLORS[color_idx]
            outline_color = base_color
            fill_color = (*base_color, 38)
            label_bg_color = (*base_color, 230)
            label_text_color = (255, 255, 255, 255)

            # CSS pixels → device pixels
            x1 = int(rect["left"] * dpr)
            y1 = int(rect["top"] * dpr)
            x2 = int((rect["left"] + rect["width"]) * dpr)
            y2 = int((rect["top"] + rect["height"]) * dpr)

            # Clamp to image bounds
            x1 = max(0, min(x1, img_width - 1))
            y1 = max(0, min(y1, img_height - 1))
            x2 = max(x1 + 1, min(x2, img_width))
            y2 = max(y1 + 1, min(y2, img_height))

            if x2 - x1 < min_box_device_px or y2 - y1 < min_box_device_px:
                continue

            # 1) Semi-transparent fill
            overlay_draw.rectangle([x1, y1, x2, y2], fill=fill_color)

            # 2) Solid outline
            draw.rectangle([x1, y1, x2, y2], outline=outline_color, width=line_width)

            text_bbox = draw.textbbox((0, 0), label, font=font)
            tw = text_bbox[2] - text_bbox[0]
            th = text_bbox[3] - text_bbox[1]
            text_y_offset = text_bbox[1]  # baseline offset correction

            pill_w = tw + label_pad_x * 2
            pill_h = th + label_pad_y * 2

            # Default: top-left corner, outside the box
            lx = x1
            ly = y1 - pill_h

            # If not enough room above, place inside top-left
            if ly < 0:
                ly = y1

            # If overflows right edge, shift left
            if lx + pill_w > img_width:
                lx = img_width - pill_w
            if lx < 0:
                lx = 0
            # If overflows bottom edge
            if ly + pill_h > img_height:
                ly = img_height - pill_h

            # Draw label pill (on overlay for alpha support)
            overlay_draw.rectangle(
                [lx, ly, lx + pill_w, ly + pill_h],
                fill=label_bg_color
            )
            # Draw text (on overlay so it sits above the pill)
            overlay_draw.text(
                (lx + label_pad_x, ly + label_pad_y - text_y_offset),
                label,
                fill=label_text_color,
                font=font,
            )

        # Composite overlay onto image
        image = Image.alpha_composite(image, overlay)

        # Encode as JPEG (quality=85) — much smaller than PNG for UI screenshots
        output_buffer = io.BytesIO()
        image.convert('RGB').save(output_buffer, format='JPEG', quality=85, optimize=True)
        jpeg_bytes = output_buffer.getvalue()
        highlighted_b64 = f"data:image/jpeg;base64,{base64.b64encode(jpeg_bytes).decode('utf-8')}"

        output_buffer.close()
        image.close()
        overlay.close()

        return highlighted_b64

    except Exception as e:
        logger.error(f"Failed to draw bounding boxes: {e}")
        return screenshot_b64

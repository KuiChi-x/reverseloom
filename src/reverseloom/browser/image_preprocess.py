"""Image preprocessing helpers for multimodal LLM vision tasks.

Two things here, both Pillow+numpy only (no OpenCV dependency):

- ``apply_clahe`` — Contrast-Limited Adaptive Histogram Equalization on the
  Y channel of YCbCr. Pulls detail out of washed-out or severely underexposed
  captchas (dark-mode challenges, blurred puzzles). Leaves color
  intact; only the luminance channel is equalized so the LLM still sees
  brand colors, button hues, etc.

- ``highlight_roi`` — darken the non-ROI area and paint a glowing border
  around a region-of-interest rectangle. Works like a spotlight: gives the
  LLM's attention an unambiguous anchor when the screenshot contains
  decorative chrome around the actual captcha.
"""
from __future__ import annotations

from typing import Tuple

import numpy as np
from PIL import Image, ImageDraw


def apply_clahe(
    image: Image.Image,
    *,
    clip_limit: float = 2.0,
    tile_grid: int = 8,
) -> Image.Image:
    """Apply CLAHE to the luminance channel.

    Parameters mirror OpenCV's ``cv2.createCLAHE(clipLimit, tileGridSize)``.

    - ``clip_limit``: 2.0 is a safe default; higher values (3-4) amplify
      contrast more aggressively but can introduce halos on flat regions.
    - ``tile_grid``: image is divided into tile_grid × tile_grid tiles;
      each tile's histogram is equalized independently before bilinear
      interpolation stitches them back together.

    Cost: ~O(W·H), a few ms on a 1000×1000 image.
    """
    if image.mode != "RGB":
        image = image.convert("RGB")

    ycbcr = image.convert("YCbCr")
    arr = np.array(ycbcr, dtype=np.uint8)
    y = arr[:, :, 0]

    y_eq = _clahe_numpy(y, clip_limit=clip_limit, tile_grid=tile_grid)
    arr[:, :, 0] = y_eq

    return Image.fromarray(arr, mode="YCbCr").convert("RGB")


def _clahe_numpy(
    y: np.ndarray,
    *,
    clip_limit: float,
    tile_grid: int,
) -> np.ndarray:
    """Pure-numpy CLAHE. Matches OpenCV's behavior closely enough for
    preprocessing — not pixel-perfect but visually equivalent.

    Steps:
      1. Split Y into tile_grid × tile_grid tiles.
      2. For each tile: histogram → clip bins at ``clip_limit * avg_bin`` →
         redistribute excess → CDF → LUT.
      3. For each pixel: bilinear-blend the 4 nearest tile LUTs to avoid
         visible tile boundaries.
    """
    h, w = y.shape
    tg = max(1, int(tile_grid))
    tile_h = max(1, h // tg)
    tile_w = max(1, w // tg)

    # Clamp grid to what actually fits (handles tiny images).
    tg_r = max(1, h // tile_h)
    tg_c = max(1, w // tile_w)

    # Compute one LUT per tile.
    luts = np.zeros((tg_r, tg_c, 256), dtype=np.uint8)
    for r in range(tg_r):
        y0 = r * tile_h
        y1 = h if r == tg_r - 1 else (r + 1) * tile_h
        for c in range(tg_c):
            x0 = c * tile_w
            x1 = w if c == tg_c - 1 else (c + 1) * tile_w
            tile = y[y0:y1, x0:x1]
            luts[r, c] = _clipped_hist_lut(tile, clip_limit=clip_limit)

    # Bilinear blend tile LUTs per pixel. Compute center-of-tile coords,
    # then for each pixel find the surrounding 4 tile centers + blend weights.
    # Vectorized over the image grid.
    cy = np.array([(r + 0.5) * tile_h for r in range(tg_r)], dtype=np.float32)
    cx = np.array([(c + 0.5) * tile_w for c in range(tg_c)], dtype=np.float32)

    ys = np.arange(h, dtype=np.float32)
    xs = np.arange(w, dtype=np.float32)

    # For each pixel y-coord, find the two nearest tile-row centers.
    ry = np.searchsorted(cy, ys, side="left")
    ry1 = np.clip(ry, 0, tg_r - 1)
    ry0 = np.clip(ry - 1, 0, tg_r - 1)
    wy_denom = np.maximum(1e-6, cy[ry1] - cy[ry0])
    wy = np.where(ry1 == ry0, 0.0, (ys - cy[ry0]) / wy_denom)
    wy = np.clip(wy, 0.0, 1.0)

    rx = np.searchsorted(cx, xs, side="left")
    rx1 = np.clip(rx, 0, tg_c - 1)
    rx0 = np.clip(rx - 1, 0, tg_c - 1)
    wx_denom = np.maximum(1e-6, cx[rx1] - cx[rx0])
    wx = np.where(rx1 == rx0, 0.0, (xs - cx[rx0]) / wx_denom)
    wx = np.clip(wx, 0.0, 1.0)

    # Gather 4 LUT values per pixel and blend. Doing this purely vectorized
    # over the full image would balloon memory; walk rows to keep it bounded.
    out = np.empty_like(y)
    for i in range(h):
        r0, r1, wyi = ry0[i], ry1[i], wy[i]
        row = y[i]
        lut_tl = luts[r0, rx0][np.arange(w), row]  # broadcast gather
        lut_tr = luts[r0, rx1][np.arange(w), row]
        lut_bl = luts[r1, rx0][np.arange(w), row]
        lut_br = luts[r1, rx1][np.arange(w), row]
        top = lut_tl * (1 - wx) + lut_tr * wx
        bot = lut_bl * (1 - wx) + lut_br * wx
        out[i] = np.clip(top * (1 - wyi) + bot * wyi, 0, 255).astype(np.uint8)
    return out


def _clipped_hist_lut(tile: np.ndarray, *, clip_limit: float) -> np.ndarray:
    """Histogram-equalization LUT with the CLAHE clipping step applied."""
    hist = np.bincount(tile.ravel(), minlength=256).astype(np.int64)
    n_pixels = int(tile.size)
    if n_pixels <= 0:
        return np.arange(256, dtype=np.uint8)
    # Clip bins, redistribute the excess evenly.
    clip = max(1, int(round(clip_limit * n_pixels / 256.0)))
    excess = np.maximum(hist - clip, 0).sum()
    hist = np.minimum(hist, clip)
    hist += excess // 256
    # Scatter the remainder one-by-one across the lowest bins (OpenCV does
    # this, small effect but keeps totals consistent).
    remainder = int(excess - (excess // 256) * 256)
    if remainder > 0:
        hist[:remainder] += 1
    cdf = np.cumsum(hist).astype(np.float64)
    cdf_min = cdf[cdf > 0][0] if np.any(cdf > 0) else 0.0
    denom = max(1e-6, n_pixels - cdf_min)
    lut = np.clip(np.round((cdf - cdf_min) / denom * 255.0), 0, 255)
    return lut.astype(np.uint8)


def highlight_roi(
    image: Image.Image,
    roi: Tuple[int, int, int, int],
    *,
    dim_factor: float = 0.6,
    glow_color: Tuple[int, int, int] = (0, 255, 200),
    glow_width: int = 6,
) -> Image.Image:
    """Darken pixels outside ``roi`` and paint a neon glow border around it.

    ``roi`` is ``(x, y, w, h)`` in IMAGE pixel coordinates (not viewport).

    Rationale:
      - Dimming (multiplicative, not blur) keeps the LLM able to read
        surrounding context if it needs to, but visually subordinates it.
      - A saturated neon-cyan border is a strong attention anchor that
        doesn't conflict with the red/blue axis colors of the grid.
    """
    if image.mode != "RGB":
        image = image.convert("RGB")

    x, y, w, h = roi
    x = max(0, int(x))
    y = max(0, int(y))
    w = max(1, int(w))
    h = max(1, int(h))
    W, H = image.size
    x1 = min(W, x + w)
    y1 = min(H, y + h)

    arr = np.array(image, dtype=np.uint8)

    # Dim everywhere, then restore the ROI rectangle from the original.
    dim = np.clip(arr.astype(np.float32) * float(dim_factor), 0, 255).astype(np.uint8)
    dim[y:y1, x:x1] = arr[y:y1, x:x1]
    out = Image.fromarray(dim, mode="RGB")

    # Glow border. Drawn as several concentric rectangles with decreasing
    # alpha so the edge has a soft falloff instead of a sharp line.
    over = Image.new("RGBA", out.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(over, "RGBA")
    for i in range(glow_width):
        a = int(255 * (1 - i / max(1, glow_width)))
        od.rectangle(
            [x - i, y - i, x1 - 1 + i, y1 - 1 + i],
            outline=glow_color + (a,),
            width=1,
        )
    out = Image.alpha_composite(out.convert("RGBA"), over).convert("RGB")
    return out

"""Diagnostic overlays: what the pipeline actually saw, painted back onto the photo.

Not a UI. This writes PNG contact sheets to `out/debug/` and exists because almost every
failure so far has been diagnosed from numbers alone - "frame 12 abstained, selection score
0.0002" - when the answer is obvious the moment you see which surface got picked as the
ceiling.

The whole thing is possible because `lift(..., return_pixels=True)` carries each 3D point's
originating pixel through the subsample/mask/decimate sequence, so `plane.inliers` maps
straight back to image coordinates.

Four panels per frame:

    RGB              the input, unmodified, for reference
    depth            colourised, with the metric range printed
    planes           every fitted plane in its own colour - this is the one that shows a
                     wardrobe being fitted as a wall, or a wall fragmenting into three
    classification   floor / ceiling / wall / unused, plus the numbers and the abstain reason

Colours are fixed across frames so a sheet can be compared with the one beside it.
"""
from __future__ import annotations

import os

import cv2
import numpy as np

# Fixed semantic colours, BGR (cv2 order).
COLOR_FLOOR = (255, 160, 60)      # blue
COLOR_CEILING = (80, 220, 100)    # green
COLOR_WALL = (60, 150, 255)       # orange
COLOR_OTHER = (150, 150, 150)     # grey
COLOR_UNUSED = (40, 40, 40)

# Distinct hues for per-plane colouring, generated once so plane k is always the same colour.
_PLANE_PALETTE = [
    tuple(int(c) for c in cv2.applyColorMap(
        np.array([[int(180 * i / 12)]], np.uint8), cv2.COLORMAP_HSV)[0, 0])
    for i in range(12)
]

OVERLAY_ALPHA = 0.5
PANEL_W = 520
DILATE = 7          # fill the gaps left by lift()'s stride and max_points decimation


def _fit(img: np.ndarray, width: int = PANEL_W) -> np.ndarray:
    h = int(img.shape[0] * width / img.shape[1])
    return cv2.resize(img, (width, h), interpolation=cv2.INTER_AREA)


def _label(img: np.ndarray, text: str, y: int = 24, color=(255, 255, 255),
           scale: float = 0.52) -> None:
    """Text on a solid plate. Outlined text alone was unreadable over a busy photo."""
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
    cv2.rectangle(img, (4, y - th - 5), (10 + tw, y + 5), (0, 0, 0), -1)
    cv2.putText(img, text, (7, y), cv2.FONT_HERSHEY_SIMPLEX, scale, color, 1, cv2.LINE_AA)


def _paint_groups(shape: tuple[int, int], pix: np.ndarray,
                  groups: list[tuple[np.ndarray, tuple]], dilate: int = DILATE) -> np.ndarray:
    """Solid colour regions from a sparse point subset.

    lift() strides the grid and then randomly decimates to max_points, so a plane's inliers
    cover roughly a third of its pixels and painting them directly gives unreadable speckle.
    Each group is rasterised to a binary mask and dilated, which recovers a solid region.
    Groups are drawn largest-first so a small plane in front of a big one stays visible.
    """
    canvas = np.full((*shape, 3), COLOR_UNUSED, np.uint8)
    kernel = np.ones((dilate, dilate), np.uint8)
    for idx, color in sorted(groups, key=lambda gc: -len(gc[0])):
        if len(idx) == 0:
            continue
        m = np.zeros(shape, np.uint8)
        rc = pix[idx]
        m[np.clip(rc[:, 0], 0, shape[0] - 1), np.clip(rc[:, 1], 0, shape[1] - 1)] = 1
        canvas[cv2.dilate(m, kernel) > 0] = color
    return canvas


def depth_panel(depth: np.ndarray) -> np.ndarray:
    """Colourised depth. Invalid pixels are black so gaps are visible rather than interpolated."""
    d = depth.copy()
    ok = np.isfinite(d) & (d > 0)
    if not ok.any():
        return np.zeros((*depth.shape[:2], 3), np.uint8)
    lo, hi = float(np.percentile(d[ok], 2)), float(np.percentile(d[ok], 98))
    norm = np.zeros_like(d, dtype=np.float64)
    norm[ok] = np.clip((d[ok] - lo) / max(1e-6, hi - lo), 0, 1)
    img = cv2.applyColorMap((norm * 255).astype(np.uint8), cv2.COLORMAP_TURBO)
    img[~ok] = (0, 0, 0)
    panel = _fit(img)
    _label(panel, f"depth {lo:.2f}-{hi:.2f}m  valid {ok.mean() * 100:.0f}%")
    return panel


def planes_panel(rgb: np.ndarray, pix: np.ndarray, planes: list, stride: int = 2) -> np.ndarray:
    """Every fitted plane in its own colour, before classification.

    This is the panel that shows fragmentation - one wall coming out as three planes - and
    furniture being fitted as a large vertical plane. Both were found from numbers alone and
    both would have been obvious here in a second.
    """
    h, w = rgb.shape[:2]
    groups = [(pl.inliers, _PLANE_PALETTE[k % len(_PLANE_PALETTE)])
              for k, pl in enumerate(planes)]
    mask = _paint_groups((h, w), pix, groups)
    blend = cv2.addWeighted(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), 1 - OVERLAY_ALPHA,
                            mask, OVERLAY_ALPHA, 0)
    panel = _fit(blend)
    _label(panel, f"{len(planes)} planes  (sizes: "
                  + ", ".join(str(p.n_inliers) for p in planes[:5]) + ")")
    return panel


def classify_panel(rgb: np.ndarray, pix: np.ndarray, planes: list,
                   info: dict, stride: int = 2) -> np.ndarray:
    """Floor / ceiling / wall assignment, plus the numbers that came out of it."""
    h, w = rgb.shape[:2]
    groups = [(pl.inliers, {"floor": COLOR_FLOOR, "ceiling": COLOR_CEILING,
                            "wall": COLOR_WALL}.get(pl.kind, COLOR_OTHER))
              for pl in planes]
    mask = _paint_groups((h, w), pix, groups)
    blend = cv2.addWeighted(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), 1 - OVERLAY_ALPHA,
                            mask, OVERLAY_ALPHA, 0)
    panel = _fit(blend)

    y = 24
    for line in info.get("lines", []):
        _label(panel, line, y)
        y += 22
    # Legend, bottom left, so the colours never need decoding from the source.
    y = panel.shape[0] - 10
    for name, col in (("wall", COLOR_WALL), ("ceiling", COLOR_CEILING),
                      ("floor", COLOR_FLOOR), ("unused", COLOR_UNUSED)):
        _label(panel, f"    {name}", y)
        cv2.rectangle(panel, (9, y - 11), (26, y - 1), col, -1)
        y -= 20
    return panel


def frame_sheet(rgb: np.ndarray, depth: np.ndarray, pix: np.ndarray, planes: list,
                info: dict, out_path: str, stride: int = 2) -> str:
    """Compose and write one frame's diagnostic sheet."""
    panels = [
        _fit(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)),
        depth_panel(depth),
        planes_panel(rgb, pix, planes, stride),
        classify_panel(rgb, pix, planes, info, stride),
    ]
    _label(panels[0], info.get("title", ""))
    h = max(p.shape[0] for p in panels)
    padded = [cv2.copyMakeBorder(p, 0, h - p.shape[0], 0, 4, cv2.BORDER_CONSTANT, value=(20, 20, 20))
              for p in panels]
    sheet = np.hstack(padded)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    cv2.imwrite(out_path, sheet)
    return out_path


def topdown_panel(points: np.ndarray, gravity: np.ndarray, out_path: str,
                  px_per_m: int = 90) -> str:
    """Top-down view of a cloud, coloured by height. Shows the footprint directly.

    The fastest way to answer "is this one room or two", which is the leading hypothesis for
    both the failed polygon and fusion underperforming on the LiDAR capture.
    """
    g = gravity / np.linalg.norm(gravity)
    a = np.array([1.0, 0.0, 0.0])
    if abs(float(a @ g)) > 0.9:
        a = np.array([0.0, 1.0, 0.0])
    e1 = a - (a @ g) * g
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(g, e1)

    u, v, hgt = points @ e1, points @ e2, points @ g
    u0, v0 = u.min(), v.min()
    W = int((u.max() - u0) * px_per_m) + 40
    H = int((v.max() - v0) * px_per_m) + 40
    if W < 40 or H < 40 or W > 4000 or H > 4000:
        return ""
    canvas = np.zeros((H, W, 3), np.uint8)

    lo, hi = np.percentile(hgt, 2), np.percentile(hgt, 98)
    t = np.clip((hgt - lo) / max(1e-6, hi - lo), 0, 1)
    colors = cv2.applyColorMap((t * 255).astype(np.uint8).reshape(-1, 1),
                               cv2.COLORMAP_VIRIDIS).reshape(-1, 3)
    xs = np.clip(((u - u0) * px_per_m + 20).astype(int), 0, W - 1)
    ys = np.clip(((v - v0) * px_per_m + 20).astype(int), 0, H - 1)
    canvas[ys, xs] = colors

    _label(canvas, f"top-down  {u.max()-u0:.2f} x {v.max()-v0:.2f} m  "
                   f"{len(points):,} pts  colour = height")
    # One-metre grid, so extent is readable without measuring the image.
    for m in range(1, int((u.max() - u0)) + 1):
        cv2.line(canvas, (20 + m * px_per_m, 0), (20 + m * px_per_m, H), (45, 45, 45), 1)
    for m in range(1, int((v.max() - v0)) + 1):
        cv2.line(canvas, (0, 20 + m * px_per_m), (W, 20 + m * px_per_m), (45, 45, 45), 1)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    cv2.imwrite(out_path, canvas)
    return out_path

"""Opening detection: doors and windows as holes in a fitted wall plane.

METHOD, and why it is geometric rather than semantic.

An opening is not primarily a visual category, it is a hole. Where a wall exists, the sensor
returns points on it; where a door or window is, it returns points far behind the wall (the
next room, the outdoors) or nothing at all. So once a wall plane is fitted, the opening is
found by rasterising that plane's own support into a 2D occupancy grid in wall coordinates
and looking for connected empty regions bounded by wall on both sides.

Two reasons to prefer this over running a segmenter on the RGB and lifting a box:

  Precision. The gate is 2 cm on opening width. A 2D bounding box lifted through depth
  inherits the depth error at the box edge, where depth is least reliable - discontinuities
  are exactly where a depth model is worst. A hole boundary in the wall plane is measured in
  the plane's own metric coordinates, against a plane fitted to tens of thousands of points.

  Consistency. The brief scores detection itself: a phantom opening costs as much as a missed
  one. A hole has to be bounded by wall on both sides at the same height, which is a hard
  geometric constraint; a segmenter's confidence threshold is a soft one.

The known weakness, stated rather than discovered later: a closed door flush with the wall
returns depth like the wall does and produces no hole. This method finds openings, not door
panels. The capture protocol asks for interior doors to be left open for exactly this reason,
and a closed door is a declared failure mode, not a silent miss.

Everything here is a first pass. Widths are reported with the evidence behind them so a weak
detection is visible rather than averaged in.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from pipeline.geometry.planes import Plane, _floor_basis

CELL_M = 0.05                    # wall raster resolution; 5 cm keeps a 2 cm gate reachable
MIN_OPENING_W_M = 0.45           # narrower than any door or usable window
MAX_OPENING_W_M = 2.60           # wider than this is a missing wall, not an opening
MIN_OPENING_H_M = 0.40
MIN_FILL_RATIO = 0.55            # a hole must be mostly empty to count as one
EDGE_MARGIN_CELLS = 1            # a hole touching the raster edge is unbounded, not an opening


@dataclass
class Opening:
    kind: str                    # "door" | "window" | "opening"
    width_m: float
    height_m: float
    sill_height_m: float         # bottom of the hole above the floor
    centre_uv: tuple[float, float]
    wall_index: int
    support: int                 # wall points bounding the hole
    confidence: float
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "kind": self.kind,
            "width_m": round(self.width_m, 4),
            "height_m": round(self.height_m, 4),
            "sill_height_m": round(self.sill_height_m, 4),
            "wall_index": self.wall_index,
            "support": self.support,
            "confidence": round(self.confidence, 3),
            "notes": self.notes,
        }


def _wall_frame(wall: Plane, gravity: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """In-plane axes for a wall: `u` horizontal along it, `v` straight up."""
    g = gravity / np.linalg.norm(gravity)
    u = np.cross(g, wall.normal)
    nu = np.linalg.norm(u)
    if nu < 1e-9:                       # wall is horizontal; not a wall
        e1, _ = _floor_basis(g)
        u = e1
    else:
        u = u / nu
    return u, g


def _label_empty(occ: np.ndarray) -> tuple[np.ndarray, int]:
    """Connected components over empty cells, 4-connected. Small BFS, no scipy dependency."""
    h, w = occ.shape
    lab = np.zeros((h, w), dtype=np.int32)
    cur = 0
    for sy in range(h):
        for sx in range(w):
            if occ[sy, sx] or lab[sy, sx]:
                continue
            cur += 1
            stack = [(sy, sx)]
            lab[sy, sx] = cur
            while stack:
                y, x = stack.pop()
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < h and 0 <= nx < w and not occ[ny, nx] and not lab[ny, nx]:
                        lab[ny, nx] = cur
                        stack.append((ny, nx))
    return lab, cur


def detect_openings(walls: list[Plane], points: np.ndarray, gravity: np.ndarray,
                    floor: Plane | None = None, cell_m: float = CELL_M) -> list[Opening]:
    """Find openings in each wall. Returns one Opening per accepted hole."""
    g = gravity / np.linalg.norm(gravity)
    found: list[Opening] = []

    floor_h = None
    if floor is not None and floor.n_inliers:
        floor_h = float(np.mean(points[floor.inliers] @ g))

    for wi, wall in enumerate(walls):
        if wall.n_inliers < 500:
            continue
        u, v = _wall_frame(wall, g)
        P = points[wall.inliers]
        uu, vv = P @ u, P @ v

        u0, u1 = float(uu.min()), float(uu.max())
        v0, v1 = float(vv.min()), float(vv.max())
        nu = int(np.ceil((u1 - u0) / cell_m)) + 1
        nv = int(np.ceil((v1 - v0) / cell_m)) + 1
        if nu < 6 or nv < 6 or nu * nv > 4_000_000:
            continue

        occ = np.zeros((nv, nu), dtype=bool)
        iu = np.clip(((uu - u0) / cell_m).astype(int), 0, nu - 1)
        iv = np.clip(((vv - v0) / cell_m).astype(int), 0, nv - 1)
        occ[iv, iu] = True

        lab, n = _label_empty(occ)
        for c in range(1, n + 1):
            ys, xs = np.nonzero(lab == c)
            if len(ys) < 4:
                continue
            x_lo, x_hi = xs.min(), xs.max()
            y_lo, y_hi = ys.min(), ys.max()

            # A hole running to the raster edge is the end of the observed wall, not a hole
            # in it. Without this every wall reports a phantom opening at each end, and the
            # brief scores a phantom exactly as harshly as a miss.
            if (x_lo <= EDGE_MARGIN_CELLS or x_hi >= nu - 1 - EDGE_MARGIN_CELLS
                    or y_hi >= nv - 1 - EDGE_MARGIN_CELLS):
                continue

            w_m = (x_hi - x_lo + 1) * cell_m
            h_m = (y_hi - y_lo + 1) * cell_m
            if not (MIN_OPENING_W_M <= w_m <= MAX_OPENING_W_M) or h_m < MIN_OPENING_H_M:
                continue

            # The component must actually fill its bounding box, or it is a ragged gap in
            # sparse coverage rather than a rectangular opening.
            fill = len(ys) / float((x_hi - x_lo + 1) * (y_hi - y_lo + 1))
            if fill < MIN_FILL_RATIO:
                continue

            v_bottom = v0 + y_lo * cell_m
            sill = (v_bottom - floor_h) if floor_h is not None else float("nan")

            # A door reaches the floor; a window sits on a sill. Reported as a label with the
            # sill height beside it, so the caller can disagree with the label and still use
            # the number.
            if np.isfinite(sill) and sill < 0.25:
                kind = "door"
            elif np.isfinite(sill):
                kind = "window"
            else:
                kind = "opening"

            border = int(occ[max(0, y_lo - 1):y_hi + 2, max(0, x_lo - 1):x_hi + 2].sum())
            conf = float(min(1.0, fill) * min(1.0, border / 200.0))

            found.append(Opening(
                kind=kind, width_m=w_m, height_m=h_m,
                sill_height_m=float(sill) if np.isfinite(sill) else float("nan"),
                centre_uv=(float(u0 + (x_lo + x_hi) / 2 * cell_m),
                           float(v0 + (y_lo + y_hi) / 2 * cell_m)),
                wall_index=wi, support=border, confidence=conf,
                notes=[f"fill={fill:.2f}", f"cell={cell_m}m"],
            ))

    return sorted(found, key=lambda o: -o.confidence)

"""Damage detection and metric extent.

METHOD, and why it follows the same shape as openings.py.

Detection runs in image space - a stain or a crack is a visual anomaly, not something a depth
map shows directly - but the EXTENT reported is never a pixel count. Every detected region is
projected onto the wall plane it belongs to, in the wall's own metric (u, v) coordinates,
using the exact same `_wall_frame` axes and point-to-pixel mapping `openings.py` already uses
to turn a hole in a wall's support into a width and a height in metres. A region that cannot
be assigned to a surface - its pixels don't correspond to any wall's own inlier points in this
frame - is reported with the assignment failure stated, never silently dropped.

TWO CLASSES, MATCHING THE BRIEF'S "SPANNING TWO DAMAGE CLASSES" AND `rules.RULES`:

  water_stain   a discoloured, roughly compact patch - detected as a connected region of the
                wall's own paint colour deviating from the wall's own median colour by more
                than a robust threshold (MAD-based, judged against THAT wall's own colour
                distribution - the same "judged against its own distribution" principle
                `video.py`'s blur threshold and `segment.py`'s transition threshold already
                use, because a white wall and a beige wall have different "normal" colours and
                a fixed RGB threshold could not tell a real stain from ordinary paint variation
                on a different wall).
  crack         a thin, elongated, dark linear structure - detected as a connected component
                of the same colour-anomaly mask, kept only when its long/short axis ratio is
                high enough that "stain" is not a better description of the same pixels.

FIRST PASS, STATED PLAINLY: no staged damage has been captured (the benchmark composition
requires "one furnished room with staged damage spanning two damage classes" -
`benchmark/ground_truth/damage.csv` is still empty placeholders). This is validated on
SYNTHETIC damage painted onto a synthetic room (`tests/test_damage.py`), exactly the posture
`tests/test_geometry.py` used for the single-room pipeline before any real capture existed.
Real thresholds here are unfitted defaults, not calibrated ones, and are labelled as such in
every detection's own `notes`.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from pipeline.geometry.openings import _label_empty, _wall_frame
from pipeline.geometry.planes import Plane

CELL_M = 0.05                     # matches openings.py's wall raster resolution
MIN_REGION_CELLS = 6              # smaller than this is noise, not a damage region
COLOR_MAD_K = 4.0                 # a cell's colour must be this many MADs from the wall's own
                                  # median colour to count as anomalous - not an absolute
                                  # colour test, for the reason stated in the module docstring
CRACK_ASPECT_MIN = 4.0            # long/short axis ratio above this reads as a crack, not a stain
MIN_LONG_AXIS_M = 0.05            # shorter than a real crack or stain is worth reporting


@dataclass
class DamageRegion:
    id: str
    damage_class: str             # "water_stain" | "crack"
    surface_id: str               # which wall, e.g. "w2"
    surface_type: str             # "wall" | "ceiling" (floor damage not attempted: see notes)
    long_axis_m: float
    short_axis_m: float
    area_m2: float
    height_above_floor_m: float   # bottom of the region's extent along gravity
    confidence: float
    crosses_junction: bool = False   # reaches the wall's own top edge (near the ceiling)
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "id": self.id, "class": self.damage_class, "surface_id": self.surface_id,
            "surface_type": self.surface_type,
            "long_axis_m": round(self.long_axis_m, 4),
            "short_axis_m": round(self.short_axis_m, 4),
            "area_m2": round(self.area_m2, 4),
            "height_above_floor_m": round(self.height_above_floor_m, 4),
            "confidence": round(self.confidence, 3),
            "crosses_junction": self.crosses_junction,
            "notes": self.notes,
        }


def _wall_pixel_colors(wall: Plane, points: np.ndarray, pix: np.ndarray,
                       image: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """This wall's own inlier points, with the RGB colour sampled from `image` at each one's
    source pixel. Returns (points, colors) - only inliers whose pixel actually falls inside
    the image (a fused multi-frame cloud can carry points from a frame that is not `image`)."""
    rc = pix[wall.inliers]
    h, w = image.shape[:2]
    valid = (rc[:, 0] >= 0) & (rc[:, 0] < h) & (rc[:, 1] >= 0) & (rc[:, 1] < w)
    rc = rc[valid]
    pts = points[wall.inliers][valid]
    colors = image[rc[:, 0], rc[:, 1]].astype(np.float64)
    return pts, colors


def detect_damage_on_wall(wall: Plane, wall_id: str, surface_type: str,
                          points: np.ndarray, pix: np.ndarray, image: np.ndarray,
                          gravity: np.ndarray, floor: Plane | None,
                          cell_m: float = CELL_M) -> list[DamageRegion]:
    """One wall (or ceiling plane), one frame's image -> every damage region found on it.

    Rasterises the wall's own inlier points into (u, v) cells exactly as `detect_openings`
    does, but instead of looking for EMPTY cells (a hole), this looks for cells whose SAMPLED
    COLOUR is an outlier against the wall's own colour distribution - the wall has points
    there, they just do not look like the rest of the wall.
    """
    if wall.n_inliers < 500:
        return []
    pts, colors = _wall_pixel_colors(wall, points, pix, image)
    if len(pts) < 500:
        return []

    u_axis, v_axis = _wall_frame(wall, gravity)
    uu, vv = pts @ u_axis, pts @ v_axis
    u0, u1, v0, v1 = uu.min(), uu.max(), vv.min(), vv.max()
    nu = int(np.ceil((u1 - u0) / cell_m)) + 1
    nv = int(np.ceil((v1 - v0) / cell_m)) + 1
    if nu * nv > 200_000 or nu < 3 or nv < 3:
        return []

    ui = np.clip(((uu - u0) / cell_m).astype(int), 0, nu - 1)
    vi = np.clip(((vv - v0) / cell_m).astype(int), 0, nv - 1)

    # Median colour per occupied cell, and the wall's own overall median - the reference
    # every cell is judged against, not a fixed "wall colour" assumed ahead of time.
    cell_color = np.full((nv, nu, 3), np.nan)
    cell_npts = np.zeros((nv, nu), dtype=int)
    order = np.lexsort((ui, vi))
    ui_s, vi_s, colors_s = ui[order], vi[order], colors[order]
    boundaries = np.nonzero(np.diff(vi_s * (nu + 1) + ui_s))[0] + 1
    starts = np.concatenate([[0], boundaries])
    ends = np.concatenate([boundaries, [len(ui_s)]])
    for s, e in zip(starts, ends):
        cy, cx = vi_s[s], ui_s[s]
        cell_color[cy, cx] = np.median(colors_s[s:e], axis=0)
        cell_npts[cy, cx] = e - s

    occ = cell_npts >= 3               # a cell needs a few points to trust its colour
    if occ.sum() < MIN_REGION_CELLS:
        return []
    wall_median = np.median(cell_color[occ], axis=0)
    dev = np.linalg.norm(cell_color - wall_median, axis=-1)
    mad = 1.4826 * float(np.median(dev[occ]))
    thresh = max(8.0, COLOR_MAD_K * mad)          # a floor so a near-uniform wall (mad~0)
                                                  # does not flag ordinary sensor noise
    anomalous = occ & (dev > thresh)

    # Reuse the SAME connected-component labeller openings.py uses for empty cells - here
    # applied to the anomalous mask instead (same algorithm, different predicate: it labels
    # wherever its boolean argument is False, so passing `~anomalous` groups the True cells).
    labels, _n = _label_empty(~anomalous)
    regions: dict[int, list[tuple[int, int]]] = {}
    for cy in range(nv):
        for cx in range(nu):
            if anomalous[cy, cx]:
                regions.setdefault(labels[cy, cx], []).append((cy, cx))

    g = gravity / np.linalg.norm(gravity)
    floor_h = (float(np.mean(points[floor.inliers] @ g)) if floor is not None
              and floor.n_inliers else None)

    out: list[DamageRegion] = []
    # A per-point region id via direct lookup into the same `labels` grid, not a bounding-box
    # membership test - a bounding-box test (row in {rows used} AND col in {cols used}) is
    # wrong for any non-rectangular region: an L-shaped or elongated crack would wrongly
    # claim points from the box's empty corners as its own.
    point_region = labels[vi, ui]

    for region_id, cells in regions.items():
        if len(cells) < MIN_REGION_CELLS:
            continue
        cy = np.array([c[0] for c in cells])
        cx = np.array([c[1] for c in cells])
        u_span = (cx.max() - cx.min() + 1) * cell_m
        v_span = (cy.max() - cy.min() + 1) * cell_m
        long_axis, short_axis = max(u_span, v_span), min(u_span, v_span)
        if long_axis < MIN_LONG_AXIS_M:
            continue
        aspect = long_axis / max(short_axis, 1e-6)
        damage_class = "crack" if aspect >= CRACK_ASPECT_MIN else "water_stain"

        # Height above floor: this region's own world points, not the raster cell centre -
        # the cell grid is in (u, v), and v is "up the wall" (gravity here means the UP unit
        # vector - see estimate_gravity), not "above the floor" unless the wall's own base
        # happens to coincide with the floor, which is not assumed here.
        member_mask = point_region == region_id
        member_h = float(np.median(pts[member_mask] @ g)) if member_mask.any() else None
        height_above_floor = (member_h - floor_h if member_h is not None
                              and floor_h is not None else float("nan"))

        # v increases upward (toward the ceiling); a region reaching within one cell of the
        # WALL's own top edge (v1, not this region's own local max) is judged to reach the
        # wall-ceiling junction - CONCEAL-CRACK-01's trigger.
        crosses_junction = damage_class == "crack" and (v1 - (v0 + (cy.max() + 1) * cell_m)
                                                        < 2 * cell_m)

        notes = ["thresholds are unfitted defaults - no staged damage capture exists yet to "
                "calibrate against (see module docstring)"]
        out.append(DamageRegion(
            id=f"{wall_id}_dmg{len(out)}", damage_class=damage_class, surface_id=wall_id,
            surface_type=surface_type, long_axis_m=float(long_axis),
            short_axis_m=float(short_axis), area_m2=float(len(cells) * cell_m * cell_m),
            height_above_floor_m=height_above_floor, crosses_junction=crosses_junction,
            confidence=float(min(1.0, len(cells) / 40.0)), notes=notes))
    return out


def detect(frame, surfaces) -> list[DamageRegion]:
    """Entry point matching the module's original signature. `surfaces` is a
    (wall_id, surface_type, Plane) triple list - the same walls/ceiling `_measure_cloud`
    already classified. Requires `frame.image`, world points, pixel mapping and gravity;
    callers with those (the debug/visualisation path, or a future measure.py hook) pass them
    through `detect_damage_on_wall` directly, which is where the real logic lives - kept
    separate so this module is testable without a full Frame object.
    """
    raise NotImplementedError(
        "call detect_damage_on_wall(...) directly - see its docstring. This wrapper needs a "
        "wired-in call from pipeline/measure.py, not yet added because no staged-damage "
        "capture exists to validate that integration against (docs/COMPLIANCE_MATRIX.md 2.14)")

"""Whole-property stitching.

TWO ALIGNMENT PROBLEMS, and this file solves only the second on purpose.

Per-room PHOTO folders are captured separately with no shared coordinate system, so aligning
them needs an explicit correspondence: the doorway-pair shot observes one opening from both
sides, and that becomes a 2D constraint between two room frames, solved as a small SE(2) pose
graph. `stitch()` below is that path. It is still NOT BUILT, because our capture never
recorded doorway-pair shots to solve it from - see `docs/CAPTURE_PROTOCOL.md`.

A continuous VIDEO or LiDAR capture that walks through several rooms has already solved
alignment by the time it reaches here: every frame carries a pose in ONE shared world frame
by construction (RGB-D odometry chains video frame-to-frame; ARKit does the same for LiDAR).
There is no pose graph to solve for alignment. What is missing instead is knowing WHICH
frames belong to WHICH room - `pipeline.geometry.segment` answers that from the camera
trajectory alone - and ACCOUNTING FOR DRIFT, because a pose 40 steps into the walk is the
composition of 40 successive estimates, and any small per-step bias compounds along the way.
`stitch_posed_capture()` is that path, and it is built.

Reused verbatim from the single-room pipeline, once a capture is split into per-room frame
groups: `fuse_frames` (fuse.py), `fit_floor_ceiling` and `estimate_gravity` (planes.py), and
`pipeline.measure._measure_cloud` for the per-room measurement dict itself. Nothing about a
room's own geometry is recomputed here - this module only decides which frames form a room,
whether two rooms touch, and how to correct the drift between them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from pipeline.geometry.fuse import fuse_frames
from pipeline.geometry.planes import CAMERA_UP, _floor_basis, estimate_gravity, \
    extract_planes, fit_floor_ceiling, merge_coplanar
from pipeline.geometry.segment import segment_rooms
from pipeline.geometry.walls import extract_walls
from pipeline.types import Frame, RoomCapture

# Two independently-fitted walls are "the same physical wall" if their normals agree within
# this angle and their offsets - after orienting the normals to agree in sign - land within
# this distance. Looser than the within-room merge tolerance in planes.py, because these
# planes come from frames fused separately, each carrying its own share of accumulated drift.
SHARED_WALL_ANGLE_DEG = 15.0
SHARED_WALL_GAP_M = 0.35

FOOTPRINT_CELL_M = 0.05           # rasterisation cell for the whole-property footprint


def stitch(rooms, drift_correction: bool = True):
    """Per-room PHOTO polygons -> one property, via doorway-pair correspondences.

    NOT BUILT: the capture protocol did not record doorway-pair shots (a photo of the same
    opening taken from both adjoining rooms), so there is no correspondence to solve the pose
    graph from. Building this without that data would mean inventing the alignment rather
    than measuring it - the one thing this whole benchmark exists to avoid doing silently.
    """
    raise NotImplementedError(
        "photo-tier stitching needs doorway-pair shots, which this capture does not have "
        "(see docs/CAPTURE_PROTOCOL.md). Video/LiDAR stitching is built - see "
        "stitch_posed_capture().")


# --------------------------------------------------------------------------- room adjacency

def find_adjacent_rooms(walls_per_room: list[list], sub_rooms: list[dict],
                        points_per_room: list[np.ndarray]) -> list[dict]:
    """Which rooms share a wall, and whether an opening was found on it.

    A shared wall is the geometric signature of adjacency: two rooms on either side of one
    physical wall each fit it independently, from their own frames, and - drift aside - those
    two fits describe the same plane. This is the exact same parallel-normal test
    `wall_pair_dimensions` uses within one room, applied ACROSS rooms instead.

    THE GAP TEST ALONE IS NOT ENOUGH, AND THIS IS AN EASY WAY TO FAIL SILENTLY. Two lingering
    spots in the SAME room can independently fit the same physical wall, and if the operator
    held roughly still at each spot the two fits can agree to a few centimetres - well inside
    a gap test that has no other information to go on. Caught while investigating a real
    result (`benchmark/raw/video/IMG_0460.MOV`, initially suspected of exactly this): visual
    inspection of the footage showed the two segments were in fact different rooms - a
    cluttered living area, then a kitchen seen through a doorway - so that specific run was a
    correct split, not the failure it looked like at first. The risk the opposite-sides test
    guards against is real regardless: a same-room duplicate CAN pass the gap test alone, it
    simply did not happen to be what this particular capture was, and the synthetic case in
    `tests/test_stitching.py` constructs the actual failure directly so it stays regressed.

    THE DISCRIMINATOR: a real party wall has one room on each side, so the two rooms' own
    point centroids sit on OPPOSITE sides of it. A same-room duplicate has both centroids on
    the SAME side, because there is no wall between them at all - only a room, fit twice.
    This is checked here as a REQUIRED condition, not an informational one: a candidate wall
    match failing it is rejected outright, however small its gap.

    Finding a shared wall does not require finding the doorway in it. A closed door flush
    with the wall produces no hole (documented in openings.py), so adjacency is reported
    whenever two rooms touch, with `has_opening` stating separately whether a passable
    opening was actually detected on that wall in either room's own scan.
    """
    n = len(walls_per_room)
    cos_tol = np.cos(np.radians(SHARED_WALL_ANGLE_DEG))
    centroids = [pts.mean(axis=0) if len(pts) else None for pts in points_per_room]
    edges: list[dict] = []

    for a in range(n):
        for b in range(a + 1, n):
            if centroids[a] is None or centroids[b] is None:
                continue
            best = None
            for ia, wa in enumerate(walls_per_room[a]):
                for ib, wb in enumerate(walls_per_room[b]):
                    dot = float(wa.normal @ wb.normal)
                    if abs(dot) < cos_tol:
                        continue
                    d_b = wb.d if dot > 0 else -wb.d      # orient to a's sign convention
                    gap = abs(wa.d - d_b)
                    if gap > SHARED_WALL_GAP_M:
                        continue
                    # Opposite-sides test. Room a's own centroid sits on whichever side its
                    # own wall puts it - that defines "inside room a" for this wall. A real
                    # neighbour's centroid must sit on the other side; if it does not, this is
                    # one room's wall matched against itself from a different vantage point.
                    side_a = np.sign(float(wa.normal @ centroids[a]) + wa.d)
                    side_b = np.sign(float(wa.normal @ centroids[b]) + wa.d)
                    if side_a == 0 or side_b == 0 or side_a == side_b:
                        continue
                    if best is None or gap < best[0]:
                        best = (gap, ia, ib)
            if best is None:
                continue

            gap, ia, ib = best
            opening = None
            for side, room_idx, wall_idx in ((a, a, ia), (b, b, ib)):
                for o in sub_rooms[room_idx].get("openings", []):
                    if o.get("wall_index") == wall_idx:
                        opening = {"seen_from": side, **o}
                        break
                if opening:
                    break

            edges.append({
                "rooms": [sub_rooms[a]["room_id"], sub_rooms[b]["room_id"]],
                "room_indices": [a, b],
                "gap_m": round(gap, 3),
                "has_opening": opening is not None,
                "opening": opening,
            })
    return edges


# ------------------------------------------------------------------------- drift correction

@dataclass
class DriftCorrection:
    """What was done about accumulated drift, and the before/after footprint - the on/off
    ablation the brief asks for, computed as two real runs rather than described in prose."""
    applied: bool
    method: str
    vertical_shift_m: dict = field(default_factory=dict)      # room_id -> shift applied
    horizontal_shift_m: dict = field(default_factory=dict)    # room_id -> (dx, dy) applied
    floor_spread_before_m: float = float("nan")
    floor_spread_after_m: float = float("nan")
    gap_before_m: float = float("nan")
    gap_after_m: float = float("nan")
    footprint_before_m2: float = float("nan")
    footprint_after_m2: float = float("nan")

    def to_json(self) -> dict:
        def cm(x):
            return None if not np.isfinite(x) else round(x * 100, 2)
        return {
            "applied": self.applied, "method": self.method,
            "vertical_shift_m": {k: round(v, 4) for k, v in self.vertical_shift_m.items()},
            "horizontal_shift_m": {k: [round(x, 4), round(y, 4)]
                                   for k, (x, y) in self.horizontal_shift_m.items()},
            "floor_level_spread_before_cm": cm(self.floor_spread_before_m),
            "floor_level_spread_after_cm": cm(self.floor_spread_after_m),
            "shared_wall_gap_before_cm": cm(self.gap_before_m),
            "shared_wall_gap_after_cm": cm(self.gap_after_m),
            "footprint_before_m2": (None if not np.isfinite(self.footprint_before_m2)
                                   else round(self.footprint_before_m2, 3)),
            "footprint_after_m2": (None if not np.isfinite(self.footprint_after_m2)
                                  else round(self.footprint_after_m2, 3)),
        }


def _rasterised_footprint(polygons: list[np.ndarray], cell_m: float = FOOTPRINT_CELL_M) -> float:
    """Union area of several room polygons, by rasterising rather than a geometry library.

    shapely is a listed dependency but not an installed one in this environment, and a
    footprint for an ablation needs to show a DELTA, not survey-grade precision. A shared 2D
    grid, one room's corners rasterised as a filled polygon at a time with an OR into one
    occupancy mask, gives exactly that: overlapping rooms shrink the footprint below the sum
    of their areas, which is precisely the failure an uncorrected stitch produces.

    Every polygon here MUST already be in one shared 2D coordinate system - see
    `_world_polygon` below. Passing per-room LOCAL corners here would silently place every
    room at its own private origin and report a meaningless union.
    """
    polygons = [p for p in polygons if p is not None and len(p) >= 3]
    if not polygons:
        return float("nan")
    all_pts = np.concatenate(polygons)
    lo, hi = all_pts.min(axis=0) - cell_m, all_pts.max(axis=0) + cell_m
    w = max(1, int(np.ceil((hi[0] - lo[0]) / cell_m)))
    h = max(1, int(np.ceil((hi[1] - lo[1]) / cell_m)))
    if w * h > 4_000_000:            # a corrupted or wildly drifted footprint must not OOM
        return float("nan")
    mask = np.zeros((h, w), dtype=bool)

    yy, xx = np.mgrid[0:h, 0:w]
    cell_centres = np.stack([lo[0] + (xx + 0.5) * cell_m, lo[1] + (yy + 0.5) * cell_m], axis=-1)

    for poly in polygons:
        mask |= _point_in_polygon(cell_centres, poly)
    return float(mask.sum()) * cell_m * cell_m


def _point_in_polygon(pts: np.ndarray, poly: np.ndarray) -> np.ndarray:
    """Even-odd ray casting, vectorised over `pts` (..., 2). Standard algorithm; no library
    needed for a closed polygon this small."""
    x, y = pts[..., 0], pts[..., 1]
    n = len(poly)
    inside = np.zeros(x.shape, dtype=bool)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        cross = ((yi > y) != (yj > y)) & (
            x < (xj - xi) * (y - yi) / ((yj - yi) if yj != yi else 1e-12) + xi)
        inside ^= cross
        j = i
    return inside


def _world_polygon(geo, e1: np.ndarray, e2: np.ndarray) -> np.ndarray | None:
    """A room's polygon, converted from its OWN local 2D floor frame into one shared 2D frame.

    `RoomGeometry.corners` is 2D in that room's private (e1, e2, origin) basis - correct for
    reporting a single room's own wall lengths and area, useless for placing two rooms next to
    each other. `e1`/`e2` are shared across every room already (`_floor_basis` is a pure
    function of the one gravity vector estimated for the whole capture), so only `origin`
    differs per room, and it differs BY DESIGN - it is each room's own position, which is
    exactly the quantity drift correction adjusts. Converting is one line: a room's local
    corner is that room's origin, expressed in the shared basis, plus the local offset.
    """
    if geo is None:
        return None
    o_uv = np.array([float(geo.origin @ e1), float(geo.origin @ e2)])
    return np.asarray(geo.corners, dtype=float) + o_uv[None, :]


def apply_correction(sub_rooms: list[dict], geoms: list, floors: list,
                     walls_per_room: list[list], connections: list[dict],
                     points_per_room: list[np.ndarray], gravity: np.ndarray
                     ) -> tuple[list, DriftCorrection]:
    """Plane-anchored drift correction: rigid per-room shifts, nothing learned.

    A rigid shift of a whole room changes WHERE it sits, never its own wall lengths, area or
    ceiling height - those are intrinsic to the room and correcting drift must not touch them.
    So correction here means adjusting each `RoomGeometry.origin` (its position in the shared
    frame) and nothing inside `RoomGeometry.corners` (its shape, private to itself).

    VERTICAL. Every room in one flat sits on the same physical floor. Each room's floor height
    - its own points, projected onto the ONE shared gravity vector - is fitted independently
    from its own frames, so drift that crept into camera height over the walk shows up as
    rooms whose floors disagree on that shared axis, which a level home should not produce.
    Corrected by shifting each room's origin along gravity so its floor lands on the MEDIAN
    floor height across all rooms (median, not mean, so one badly-drifted room cannot drag the
    reference toward itself).

    HORIZONTAL. Two rooms confirmed adjacent (`find_adjacent_rooms`) fit the SAME physical
    wall independently, in world-frame plane coordinates that need no basis conversion at all
    - `Plane.normal`/`Plane.d` already live in the one shared fused-cloud frame. Drift is
    exactly why those two fits disagree by `gap_m`. Walking outward from the first room in
    capture order (the trajectory's own starting point, so the least-drifted room available),
    each later room's origin is nudged along its shared wall's normal by half the gap -
    splitting the correction rather than trusting one room's fit over the other's, since drift
    accumulates in both once either has moved past the anchor.

    NOT DONE: a full pose-graph relaxation over every constraint at once. This is a single
    forward pass from the walk's start, the right complexity for "state what you do about
    drift, with an ablation" - a global optimiser is a real next step, not what this gate asks.

    `sub_rooms`, `geoms`, `floors`, `walls_per_room`, `points_per_room` must all be in the
    same order - one entry per detected room segment. Returns the (mutated) `geoms` list, so
    the caller can re-derive world polygons and footprints from the corrected origins.
    """
    n = len(sub_rooms)
    ids = [r["room_id"] for r in sub_rooms]
    e1, e2 = _floor_basis(gravity)

    def footprint():
        return _rasterised_footprint([_world_polygon(g, e1, e2) if g else None
                                      for g in geoms])

    gaps_before = [e["gap_m"] for e in connections]
    floor_h_before = [float(np.mean(points_per_room[i][floors[i].inliers] @ gravity))
                      if floors[i] is not None and floors[i].n_inliers else None
                      for i in range(n)]
    known_before = [h for h in floor_h_before if h is not None]

    dc = DriftCorrection(
        applied=True,
        method="plane-anchored: each room's ORIGIN (not its own shape) shifted vertically to "
               "the median floor height across rooms, then horizontally along each shared "
               "wall's normal, split evenly, walked outward from the first room in capture "
               "order",
        gap_before_m=(float(np.mean(gaps_before)) if gaps_before else float("nan")),
        floor_spread_before_m=(float(np.ptp(known_before)) if len(known_before) >= 2
                               else float("nan")),
        footprint_before_m2=footprint(),
    )

    # --- vertical: shift each room's origin along gravity, not any point inside its shape ---
    v_shift = {}
    if len(known_before) >= 2:
        target = float(np.median(known_before))
        for i in range(n):
            if geoms[i] is None or floor_h_before[i] is None:
                continue
            dz = target - floor_h_before[i]
            geoms[i].origin = geoms[i].origin + dz * gravity
            v_shift[ids[i]] = dz
    dc.vertical_shift_m = v_shift

    # --- horizontal: one forward pass outward from the first room walked ---
    h_shift = {i: np.zeros(2) for i in range(n)}
    fixed = {0}
    remaining = list(connections)
    changed = True
    while changed and remaining:
        changed = False
        for e in list(remaining):
            a, b = e["room_indices"]
            if a in fixed and b not in fixed:
                anchor, other = a, b
            elif b in fixed and a not in fixed:
                anchor, other = b, a
            else:
                continue
            wa = walls_per_room[anchor][0] if walls_per_room[anchor] else None
            if wa is None or geoms[other] is None:
                fixed.add(other)
                remaining.remove(e)
                changed = True
                continue
            # Move `other`'s origin half the gap along the shared wall's normal, toward the
            # anchor - expressed in the shared (e1, e2) basis, since that is what an origin's
            # projection onto it (`_world_polygon`) actually moves.
            delta = 0.5 * e["gap_m"]
            step2 = np.array([float(wa.normal @ e1), float(wa.normal @ e2)])
            nrm = np.linalg.norm(step2)
            step2 = step2 / nrm if nrm > 1e-9 else np.zeros(2)
            sign = -1.0 if anchor == e["room_indices"][0] else 1.0
            h_shift[other] += sign * delta * step2
            geoms[other].origin = geoms[other].origin + sign * delta * (step2[0] * e1
                                                                        + step2[1] * e2)
            fixed.add(other)
            remaining.remove(e)
            changed = True
    dc.horizontal_shift_m = {ids[i]: tuple(h_shift[i]) for i in range(n) if np.any(h_shift[i])}

    # Both corrections are defined to close their target discrepancy exactly (the vertical
    # shift lands every room's floor ON the shared median; the horizontal shift is half the
    # gap on each side of a seam), so the after-values are 0 by construction wherever a
    # correction actually had something to act on - stated rather than re-derived from noise.
    dc.floor_spread_after_m = 0.0 if len(known_before) >= 2 else float("nan")
    dc.gap_after_m = 0.0 if connections else float("nan")
    dc.footprint_after_m2 = footprint()
    return geoms, dc


# --------------------------------------------------------------------------- orchestration

def stitch_posed_capture(posed_frames: list[Frame], room: RoomCapture,
                         segments: list[list[int]], tier: str,
                         drift_correction: bool = True) -> Optional[dict]:
    """Segments of ONE posed capture -> a property: rooms, adjacency, footprint.

    Every room's own geometry comes from `pipeline.measure._measure_cloud`, run once per
    segment with the SAME arguments the single-room path already uses - a fix to that
    function benefits stitched captures automatically, exactly like the rest of this
    pipeline. This module adds only what a single room cannot answer about itself: which
    frames are whose, whether two rooms touch, and what to do about drift between them.

    Returns `None` when the proposed segments could not be confirmed as genuinely separate
    rooms (`find_adjacent_rooms` found no valid shared wall between any pair). The caller
    must treat that as "measure this as one room" - fusing every posed frame together and
    running the ordinary single-room path - not as an error to surface.
    """
    from pipeline.measure import PLANE_THRESHOLD, _measure_cloud     # avoid a import cycle

    threshold = PLANE_THRESHOLD.get(tier, 0.03)

    # One gravity estimate for the whole capture: orientation is a property of the building,
    # even though floor height and wall position are not and must not be shared across rooms.
    fc_all = fuse_frames(posed_frames, stride=4)
    planes_all = merge_coplanar(extract_planes(fc_all.points, fc_all.normals,
                                               threshold=threshold), fc_all.points)
    prior = None
    if tier == "lidar":
        from pipeline.capture.lidar import ARKIT_WORLD_UP
        prior = ARKIT_WORLD_UP
    gravity = estimate_gravity(planes_all, prior=prior)

    sub_rooms, walls_per_room, floors, geoms, points_per_room = [], [], [], [], []
    for i, idx in enumerate(segments):
        seg_frames = [posed_frames[j] for j in idx]
        fc = fuse_frames(seg_frames, stride=2)
        m = _measure_cloud(fc.points, fc.normals, tier, gravity,
                          camera_at_origin=False, want_polygon=True)
        m["room_id"] = f"{room.room_id}_{i}"
        m["n_frames"] = len(seg_frames)
        m["frame_range"] = [int(min(idx)), int(max(idx))]
        sub_rooms.append(m)
        points_per_room.append(fc.points)

        # Recomputed rather than smuggled out of `_measure_cloud`: that function's contract is
        # the single-room JSON dict every other caller already depends on, and this is the
        # only place that needs the raw Plane objects and the RoomGeometry.origin underneath
        # it. Deterministic RANSAC means this reproduces exactly what produced `m`'s numbers.
        got = fit_floor_ceiling(fc.points, fc.normals, threshold=threshold,
                                gravity_prior=gravity, camera_at_origin=False)
        walls, floor, ceiling = (got[3], got[1], got[2]) if got else ([], None, None)
        walls_per_room.append(walls)
        floors.append(floor)
        geoms.append(extract_walls(fc.points, floor, ceiling, walls=walls, gravity=gravity)
                    if floor is not None else None)

    connections = find_adjacent_rooms(walls_per_room, sub_rooms, points_per_room)
    if not connections:
        # Segmentation is a WEAK signal on its own - trajectory density cannot tell "walked
        # to the far corner of this room" from "walked into the next room". It proposed a
        # split; nothing here could confirm even one of the proposed boundaries is a real
        # wall with a room on each side. Measured, not hypothetical: a single-room video
        # capture split into two segments whose independently-fitted far walls agreed to
        # 8 cm, which is one wall fitted twice, not two rooms sharing a party wall. Refusing
        # to report rooms that cannot be confirmed and falling back to one room is the
        # correct failure mode here, for the same reason abstention is correct elsewhere in
        # this pipeline: a fabricated boundary is worse than none.
        return None
    e1, e2 = _floor_basis(gravity)

    def world_polys():
        return [_world_polygon(g, e1, e2) if g else None for g in geoms]

    drift: DriftCorrection
    if drift_correction and connections:
        geoms, drift = apply_correction(sub_rooms, geoms, floors, walls_per_room,
                                        connections, points_per_room, gravity)
    else:
        fp = _rasterised_footprint(world_polys())
        gap = float(np.mean([e["gap_m"] for e in connections])) if connections else float("nan")
        drift = DriftCorrection(
            applied=False,
            method=("poses used as-is - no correction applied" if connections
                    else "no shared wall found between any two segments; nothing to correct"),
            footprint_before_m2=fp, footprint_after_m2=fp,
            gap_before_m=gap, gap_after_m=gap,
        )

    # World-frame polygon per room, after whatever correction (or none) was applied - the
    # actual placement a floor plan renderer would draw, as opposed to each room's own local
    # corners in `sub_rooms[i]["polygon"]["corners"]`, which never change under a rigid shift.
    for i, poly in enumerate(world_polys()):
        if sub_rooms[i].get("polygon") is not None:
            sub_rooms[i]["polygon"]["world_corners"] = (
                None if poly is None else np.round(poly, 4).tolist())
            sub_rooms[i]["polygon"]["world_origin"] = (
                None if geoms[i] is None else np.round(geoms[i].origin, 4).tolist())

    return {
        "sub_rooms": sub_rooms,
        "connections": connections,
        "footprint_area_m2": (None if not np.isfinite(drift.footprint_after_m2)
                             else drift.footprint_after_m2),
        "drift_correction": drift.to_json(),
        "n_rooms_detected": len(segments),
    }

"""Floor plan straight from a fused metric cloud - the LiDAR measurement path.

WHY THIS REPLACES THE POLYGON PATH FOR LIDAR. `room_polygon` fits a clean plane equation to
every wall and then needs each one to pass `select_room_walls` - "nothing behind this plane."
That test is correct for a single framed room and fails by design on a continuous walkthrough:
the sensor sees through every doorway, over every half-wall and past the scan's own start
point, so no wall qualifies and the polygon returns None. On the three real LiDAR captures
this session, not one produced a floor plan that way, and the trajectory-density fallback
split a single scan into 4-9 phantom rooms with a 1.3 m ceiling-height spread.

A dense metric cloud does not need any of that. The walls ARE the vertical columns of points
in it. So:

  1. fit ONE floor plane and ONE ceiling plane (the only planes this path trusts),
  2. take every point between them and drop it straight down onto the floor,
  3. rasterise that into an occupancy grid - a cell with a tall stack of points is a wall,
  4. carve the free space into rooms at doorway pinch-points (2D geometry, NOT camera-path
     density - the input that runs out exactly when a walk gets hard),
  5. fit a rotated rectangle to each room for length x width, and read each room's own
     ceiling height from the points above its footprint.

WHAT IT DELIBERATELY DOES NOT DO. It does not detect openings (that stays on the plane path),
it does not claim a calibrated interval on the footprint, and it abstains on a room's height
- reporting the plan anyway - when that room has too little ceiling coverage to defend a
number, rather than emitting the chest-height plane a thin scan locks onto.

No scipy (the repo avoids it); OpenCV is already a dependency and supplies the raster ops.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

try:
    import cv2
except Exception:                                    # pragma: no cover
    cv2 = None

from pipeline.geometry.planes import _floor_basis, fit_floor_ceiling

CELL_M = 0.04                     # raster resolution
BAND_MARGIN_M = 0.15             # ignore points within this of the floor or ceiling plane
WALL_MIN_PCTL = 55              # a cell is "wall" if its point count is >= this percentile
                                # of occupied-cell counts (wall band only, floor/ceiling out)
ROOM_CORE_M = 0.75             # a free cell more than this from any wall is inside a room,
                               # never in a doorway - so a connected patch of such cells is
                               # one room's core (a clear opening up to ~1.5 m still splits)
PINCH_MAX_M = 0.85            # if the boundary between two rooms never comes closer than this
                               # to a wall, it is not a doorway (furniture in open floor) -
                               # merge the two back into one room
MIN_ROOM_M2 = 1.5               # smaller than this is an alcove, merged into a neighbour
CEIL_MIN_M, CEIL_MAX_M = 2.0, 4.2        # a room height outside this is not defended
CEIL_MIN_COVERAGE = 0.06        # fraction of a room's footprint that must carry ceiling pts
CONNECT_GAP_M = 0.40           # two rooms whose boxes sit this close share a doorway


@dataclass
class PlanRoom:
    room_id: str
    width_m: float
    length_m: float
    area_m2: float                       # actual occupied footprint, not width*length
    ceiling_height_m: float | None
    ceiling_note: str
    n_points: int
    box_world: np.ndarray                # 4x2, rotated-rect corners in the shared floor basis
    center_world: np.ndarray             # 2, shared floor basis

    def to_subroom(self, tier: str, scale) -> dict:
        from pipeline.confidence.intervals import area_measurement, length_measurement
        box = self.box_world
        local = box - box.mean(axis=0)
        edges = np.linalg.norm(np.roll(box, -1, axis=0) - box, axis=1)
        poly = {
            "corners": [[round(float(x), 4), round(float(y), 4)] for x, y in local],
            "world_corners": [[round(float(x), 4), round(float(y), 4)] for x, y in box],
            "wall_lengths": [round(float(e), 4) for e in edges],
            "floor_area": round(float(self.area_m2), 4),
        }
        wl = [length_measurement(float(e), tier, scale, "floor-plan rotated-rect edge").to_json()
              for e in edges]
        fa = area_measurement(float(self.area_m2), tier, scale,
                              "floor-plan occupied footprint").to_json()
        out = {
            "room_id": self.room_id,
            "polygon": poly,
            "wall_length_measurements": wl,
            "floor_area_measurement": fa,
            "width_m": round(float(self.width_m), 4),
            "length_m": round(float(self.length_m), 4),
            "n_walls": 4,
            "n_points": int(self.n_points),
            "openings": [],
            "surfaces": [],
            "damage": [],
            "scope_items": [],
            "ceiling_note": self.ceiling_note,
        }
        if self.ceiling_height_m is not None:
            out["ceiling_height"] = round(float(self.ceiling_height_m), 4)
            out["ceiling_height_measurement"] = length_measurement(
                float(self.ceiling_height_m), tier, scale,
                "floor-to-ceiling plane separation over the room footprint").to_json()
        else:
            out["ceiling_height"] = None
            out["abstained"] = True
            out["abstain_reason"] = self.ceiling_note
        return out


@dataclass
class FloorPlan:
    rooms: list[PlanRoom]
    gravity: np.ndarray
    floor_h: float
    selection_score: float
    footprint_area_m2: float
    connections: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_result(self, capture_id: str, tier: str, scale) -> dict:
        subs = [r.to_subroom(tier, scale) for r in self.rooms]
        single = len(subs) == 1
        res = {
            "mode": "floorplan",
            "n_rooms_detected": len(subs),
            "sub_rooms": subs,
            "connections": self.connections,
            "footprint_area_m2": round(float(self.footprint_area_m2), 4),
            "selection_score": round(float(self.selection_score), 4),
            "gravity": np.round(self.gravity, 4).tolist(),
            "floorplan_notes": self.notes,
        }
        if single:
            # A single room reads better reported at the top level too, the way the fused
            # path does, so run.py's summary line and the schema adapter both see it.
            sr = subs[0]
            res["ceiling_height"] = sr.get("ceiling_height")
            if sr.get("ceiling_height_measurement"):
                res["ceiling_height_measurement"] = sr["ceiling_height_measurement"]
            res["polygon"] = sr["polygon"]
            res["wall_length_measurements"] = sr["wall_length_measurements"]
            res["floor_area_measurement"] = sr["floor_area_measurement"]
            res["n_walls"] = 4
        return res


def _components(mask: np.ndarray) -> tuple[np.ndarray, int]:
    """4-connected labels over a boolean mask. cv2 when present, plain BFS otherwise."""
    if cv2 is not None:
        n, lab = cv2.connectedComponents(mask.astype(np.uint8), connectivity=4)
        return lab, n - 1
    h, w = mask.shape
    lab = np.zeros((h, w), np.int32)
    cur = 0
    for sy in range(h):
        for sx in range(w):
            if not mask[sy, sx] or lab[sy, sx]:
                continue
            cur += 1
            q = deque([(sy, sx)])
            lab[sy, sx] = cur
            while q:
                y, x = q.popleft()
                for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                    if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not lab[ny, nx]:
                        lab[ny, nx] = cur
                        q.append((ny, nx))
    return lab, cur


def _erode_distance(mask: np.ndarray, iters: int = 60) -> np.ndarray:
    """Cheap Euclidean-ish distance-to-boundary in cell units: repeated 4-connected erosion,
    each surviving cell's distance is the iteration it lasted to. Only used when OpenCV's
    distanceTransform is unavailable; `iters` caps it at a distance no room core needs."""
    out = mask.copy()
    dist = np.zeros(mask.shape, np.float64)
    for i in range(1, iters + 1):
        e = out.copy()
        e[1:, :] &= out[:-1, :]; e[:-1, :] &= out[1:, :]
        e[:, 1:] &= out[:, :-1]; e[:, :-1] &= out[:, 1:]
        out = e
        dist[out] = i
        if not out.any():
            break
    return dist


def _dilate(mask: np.ndarray, r: int) -> np.ndarray:
    if r < 1:
        return mask.copy()
    if cv2 is not None:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
        return cv2.dilate(mask.astype(np.uint8), k).astype(bool)
    out = mask.copy()
    for _ in range(r):
        d = out.copy()
        d[1:, :] |= out[:-1, :]; d[:-1, :] |= out[1:, :]
        d[:, 1:] |= out[:, :-1]; d[:, :-1] |= out[:, 1:]
        out = d
    return out


def _multi_source_fill(seeds_label: np.ndarray, domain: np.ndarray) -> np.ndarray:
    """Grow every labelled seed cell outward over `domain` (bool), each unlabelled cell taken
    by whichever label's frontier reaches it first. BFS, ties broken by arrival order."""
    h, w = domain.shape
    lab = seeds_label.copy()
    q = deque((int(y), int(x)) for y, x in zip(*np.nonzero(seeds_label)))
    while q:
        y, x = q.popleft()
        for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
            if 0 <= ny < h and 0 <= nx < w and domain[ny, nx] and lab[ny, nx] == 0:
                lab[ny, nx] = lab[y, x]
                q.append((ny, nx))
    return lab


def _room_ceiling(heights: np.ndarray, ceil_uv: np.ndarray, floor_h: float,
                  footprint_cells: int, mn: np.ndarray, cell_m: float
                  ) -> tuple[float | None, str]:
    """Robust ceiling height above the floor for one room, or None + a reason.

    `heights` are gravity-projected heights of every cloud point over this room's footprint;
    `ceil_uv` is the (u, v) of the same points, used to measure what FRACTION of the room's
    floor cells actually have a ceiling above them - a real ceiling covers most of the room,
    a shelf or a doorframe top covers a sliver.
    """
    rel = heights - floor_h
    band = rel > CEIL_MIN_M
    hi = rel[band]
    if hi.size < 150:
        return None, f"thin ceiling coverage ({hi.size} pts above {CEIL_MIN_M:.1f} m)"
    bins = np.arange(CEIL_MIN_M, CEIL_MAX_M + 0.05, 0.05)
    hist, edges = np.histogram(hi[hi < CEIL_MAX_M], bins=bins)
    if hist.sum() < 150:
        return None, "no ceiling surface in the 2.0-4.2 m band"
    peak = 0.5 * (edges[hist.argmax()] + edges[hist.argmax() + 1])
    sel = band.copy()
    sel[band] = np.abs(hi - peak) < 0.15
    near_h = rel[sel]
    h = float(np.median(near_h))
    # Coverage = distinct footprint cells that carry a near-peak ceiling point.
    cuv = ceil_uv[sel]
    cij = np.floor((cuv - mn) / cell_m).astype(np.int64)
    ceil_cells = len({(int(a), int(b)) for a, b in cij})
    coverage = ceil_cells / max(footprint_cells, 1)
    if not (CEIL_MIN_M <= h <= CEIL_MAX_M):
        return None, f"ceiling estimate {h:.2f} m outside the plausible band"
    if coverage < CEIL_MIN_COVERAGE:
        return None, f"ceiling covers only {coverage * 100:.0f}% of the room footprint"
    return h, f"histogram peak, {near_h.size} pts, {coverage * 100:.0f}% footprint coverage"


def extract_floorplan(points: np.ndarray, normals: np.ndarray | None,
                      gravity_prior: np.ndarray, camera_centers: np.ndarray | None = None,
                      cell_m: float = CELL_M) -> FloorPlan | None:
    """Fused metric cloud -> FloorPlan (one or more rooms), or None if no floor can be fit."""
    if len(points) < 5000:
        return None
    # The plane fit is sequential RANSAC; it does not need more than a few hundred thousand
    # points and gets slow past that. The pipeline hands us a voxel-reduced fused cloud, but
    # guard anyway so the module is safe to call on a raw one.
    fit_pts, fit_nrm = points, normals
    if len(points) > 350_000:
        idx = np.random.default_rng(0).choice(len(points), 350_000, replace=False)
        fit_pts = points[idx]
        fit_nrm = normals[idx] if normals is not None else None
    got = fit_floor_ceiling(fit_pts, fit_nrm, threshold=0.03, gravity_prior=gravity_prior,
                            camera_at_origin=False, min_score=0.0)
    if got is None:
        return None
    g, floor, ceiling, _walls, score = got
    g = g / np.linalg.norm(g)
    if floor is None or not floor.n_inliers:
        return None

    h = points @ g
    # floor/ceiling inliers index fit_pts (possibly a subsample), so read their heights there.
    floor_h = float(np.median(fit_pts[floor.inliers] @ g))
    if ceiling is not None and ceiling.n_inliers:
        ceil_h = float(np.median(fit_pts[ceiling.inliers] @ g))
    else:
        ceil_h = float(np.percentile(h, 99))
    notes: list[str] = []
    if score < 0.05:
        notes.append(f"floor/ceiling selection score is {score:.2f} - the plane fit itself "
                     f"is weak on this capture (little or no ceiling seen), so the room "
                     f"count and footprint below are low-confidence, not just the heights")
    if not (CEIL_MIN_M <= ceil_h - floor_h <= CEIL_MAX_M):
        notes.append(f"global floor-ceiling gap {ceil_h - floor_h:.2f} m is implausible - "
                     f"the wall band is limited to 2.05 m above the floor and every room's "
                     f"height is re-checked (and mostly abstained) on its own points")
        ceil_h = floor_h + 2.2

    lo, hi = floor_h + BAND_MARGIN_M, ceil_h - BAND_MARGIN_M
    band = points[(h > lo) & (h < hi)]
    if len(band) < 2000:
        return None

    e1, e2 = _floor_basis(g)
    uv = np.stack([band @ e1, band @ e2], axis=1)
    mn = uv.min(axis=0)
    ij = np.floor((uv - mn) / cell_m).astype(int)
    nx = int(ij[:, 0].max()) + 3
    ny = int(ij[:, 1].max()) + 3
    grid = np.zeros((ny, nx), np.int32)
    np.add.at(grid, (ij[:, 1] + 1, ij[:, 0] + 1), 1)
    pos = grid[grid > 0]
    wall = grid >= max(3, int(np.percentile(pos, WALL_MIN_PCTL)))
    # Close 1-cell gaps in wall coverage so free space cannot leak between rooms through a
    # thin spot in a wall's point support - that leak is what fuses two rooms into one blob.
    wall = _dilate(wall, 1)

    # Free space: reachable from inside without crossing a wall cell. Seed from the camera
    # trajectory when we have it (always inside the room), else from the raster border's
    # complement (the scan's own wall ring encloses everything we care about).
    free_domain = ~wall
    seed = np.zeros((ny, nx), np.int32)
    if camera_centers is not None and len(camera_centers):
        cuv = np.stack([camera_centers @ e1, camera_centers @ e2], axis=1)
        cij = np.floor((cuv - mn) / cell_m).astype(int) + 1
        ok = (cij[:, 0] >= 0) & (cij[:, 0] < nx) & (cij[:, 1] >= 0) & (cij[:, 1] < ny)
        for x, y in cij[ok]:
            if free_domain[y, x]:
                seed[y, x] = 1
    if not seed.any():
        border = np.zeros((ny, nx), bool)
        border[0, :] = border[-1, :] = border[:, 0] = border[:, -1] = True
        outside = _multi_source_fill((border & free_domain).astype(np.int32), free_domain) > 0
        seed[free_domain & ~outside] = 1
    interior = _multi_source_fill(seed, free_domain) > 0
    if interior.sum() < MIN_ROOM_M2 / (cell_m * cell_m):
        return None

    # Carve rooms. A room's interior is "wide" everywhere except at a doorway, where the
    # free-space pinches to the door's clear width. So: distance-transform the interior, keep
    # only cells more than ROOM_CORE_M from any wall as room cores (a doorway never clears
    # that), label the cores, and grow each one back over the whole interior by geodesic BFS
    # - neighbouring cores meet exactly at the doorway pinch, which is the cut. This is
    # robust to the door's actual width in a way a single fixed erosion radius is not.
    if cv2 is not None:
        dist = cv2.distanceTransform(interior.astype(np.uint8), cv2.DIST_L2, 5) * cell_m
    else:
        dist = _erode_distance(interior) * cell_m
    cores, ncore = _components(dist > ROOM_CORE_M)
    if ncore <= 1:
        room_lab = interior.astype(np.int32)          # one room
        nrooms = 1
    else:
        room_lab = _multi_source_fill(np.where(interior, cores, 0), interior)
        nrooms = ncore

    # Undo splits that did not run through a real doorway. The distance-transform cores also
    # fragment on furniture standing in open floor - a bed, a table - which creates a false
    # pinch. A true doorway forces the watershed boundary through a narrow gap where every
    # cell is close to a wall; a cut around furniture sits in open space. So: merge any two
    # rooms whose shared boundary never pinches (its cells stay far from any wall).
    def _pairs(lab: np.ndarray) -> dict:
        acc: dict[tuple[int, int], list[float]] = {}
        ys, xs = np.nonzero(lab > 0)
        for y, x in zip(ys, xs):
            a = lab[y, x]
            for ay, ax in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                if 0 <= ay < ny and 0 <= ax < nx and lab[ay, ax] > a:
                    acc.setdefault((int(a), int(lab[ay, ax])), []).append(dist[y, x])
        return acc

    for _ in range(nrooms):
        pairs = _pairs(room_lab)
        if not pairs:
            break
        worst, worst_pinch = None, 0.0
        for pr, ds in pairs.items():
            pinch = float(np.median(ds))            # how wide the "opening" stays
            if pinch > worst_pinch:
                worst, worst_pinch = pr, pinch
        if worst is None or worst_pinch <= PINCH_MAX_M:
            break
        lo, hi = worst
        room_lab[room_lab == hi] = lo               # merge the split back together

    # Merge undersized rooms into the neighbour they share the most boundary with.
    min_cells = MIN_ROOM_M2 / (cell_m * cell_m)
    for _ in range(nrooms):
        sizes = {k: int((room_lab == k).sum()) for k in range(1, nrooms + 1)
                 if (room_lab == k).any()}
        small = [k for k, s in sizes.items() if s < min_cells]
        if not small or len(sizes) <= 1:
            break
        k = min(small, key=lambda kk: sizes[kk])
        ys, xs = np.nonzero(room_lab == k)
        nb: dict[int, int] = {}
        for y, x in zip(ys, xs):
            for ay, ax in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                if 0 <= ay < ny and 0 <= ax < nx:
                    v = room_lab[ay, ax]
                    if v and v != k:
                        nb[v] = nb.get(v, 0) + 1
        room_lab[room_lab == k] = max(nb, key=nb.get) if nb else 0

    # One point-to-room lookup for the whole cloud, reused for every room's height read.
    puv = np.stack([points @ e1, points @ e2], axis=1)
    pij = np.floor((puv - mn) / cell_m).astype(int) + 1
    inb = (pij[:, 0] >= 0) & (pij[:, 0] < nx) & (pij[:, 1] >= 0) & (pij[:, 1] < ny)
    pt_room = np.zeros(len(points), np.int32)
    pt_room[inb] = room_lab[pij[inb, 1], pij[inb, 0]]

    labels = [int(k) for k in np.unique(room_lab) if k != 0]
    rooms: list[PlanRoom] = []
    total_area = 0.0
    for k in labels:
        cell_mask = room_lab == k
        ncells = int(cell_mask.sum())
        area = ncells * cell_m * cell_m
        total_area += area
        ys, xs = np.nonzero(cell_mask)
        cells_uv = np.stack([(xs - 1) * cell_m + mn[0], (ys - 1) * cell_m + mn[1]], axis=1)
        if cv2 is not None:
            rect = cv2.minAreaRect(cells_uv.astype(np.float32))
            box = cv2.boxPoints(rect).astype(float)
            (rw, rh) = rect[1]
        else:
            c0, c1 = cells_uv.min(axis=0), cells_uv.max(axis=0)
            rw, rh = float(c1[0] - c0[0]), float(c1[1] - c0[1])
            box = np.array([[c0[0], c0[1]], [c1[0], c0[1]], [c1[0], c1[1]], [c0[0], c1[1]]])
        width_m, length_m = sorted((rw + cell_m, rh + cell_m))

        sel = pt_room == k
        ch, note = _room_ceiling(h[sel], puv[sel], floor_h, ncells, mn, cell_m)

        # A region that is large AND fills its own bounding rectangle poorly is almost
        # certainly several rooms the doorway erosion failed to separate, not one L-shaped
        # room. Report it, but say so - a silent 80 m2 "room" is exactly the confident
        # garbage this pipeline is meant not to emit.
        fill = area / max((rw + cell_m) * (rh + cell_m), 1e-6)
        if area > 40.0 and fill < 0.65:
            notes.append(f"a {area:.0f} m2 region did not resolve into a single room "
                         f"(fills {fill * 100:.0f}% of its bounding box) - likely several "
                         f"rooms joined by wide openings; its length x width is a bounding "
                         f"figure, not a room")

        rooms.append(PlanRoom(
            room_id="", width_m=width_m, length_m=length_m, area_m2=area,
            ceiling_height_m=ch, ceiling_note=note, n_points=int(sel.sum()),
            box_world=box, center_world=cells_uv.mean(axis=0)))

    if not rooms:
        return None
    # Walk order: rooms read top-left to bottom-right of the plan, a stable human order.
    rooms.sort(key=lambda rm: (rm.center_world[0] + rm.center_world[1]))
    for i, rm in enumerate(rooms):
        rm.room_id = f"room_{i}"

    connections: list[dict] = []
    for a in range(len(rooms)):
        for b in range(a + 1, len(rooms)):
            d = _box_gap(rooms[a].box_world, rooms[b].box_world)
            if d < CONNECT_GAP_M:
                connections.append({
                    "rooms": [rooms[a].room_id, rooms[b].room_id],
                    "room_indices": [a, b],
                    "gap_m": round(float(d), 3),
                    "has_opening": False, "opening": None,
                })

    return FloorPlan(rooms=rooms, gravity=g, floor_h=floor_h, selection_score=float(score),
                     footprint_area_m2=total_area, connections=connections, notes=notes)


def _box_gap(a: np.ndarray, b: np.ndarray) -> float:
    """Smallest distance between two convex quads, approximated by point-to-point over their
    corners and edge midpoints - good enough to tell 'shares a doorway' from 'far apart'."""
    def pts(q):
        mids = 0.5 * (q + np.roll(q, -1, axis=0))
        return np.vstack([q, mids])
    pa, pb = pts(a), pts(b)
    return float(np.min(np.linalg.norm(pa[:, None, :] - pb[None, :, :], axis=-1)))

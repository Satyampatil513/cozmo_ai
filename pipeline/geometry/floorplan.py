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
  4. ROOM CARVING is the wall-line-arrangement method (Ochmann et al., and what
     point-cloud->floor-plan products use), not camera-path density and not a raw
     distance-transform watershed (which fragments on furniture):
       a. extract wall CENTRE-LINES from the raster (Hough), snap them onto a few dominant
          orientations, and merge the parallel pair that is the two faces of one wall,
       b. extend every line across the plan - they cut it into an arrangement of FACES,
       c. LABEL the faces into rooms: start from the arrangement faces and greedily merge
          any two whose shared edge carries little real wall evidence. This is the graph-cut
          smoothness term done greedily - "it only costs to put a wall between two faces
          where a wall was actually seen." A doorway (small gap in an otherwise solid wall)
          keeps the two rooms apart; an extended line with no wall under it, or a wide
          opening, does not.
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

# --- wall-line extraction & regularisation (arrangement family) ---
LINE_MIN_SUPPORT_M = 0.80      # a wall centre-line needs this much collinear point support
LINE_MERGE_OFFSET_M = 0.16    # parallel lines closer than this are the two faces of one wall
LINE_ANGLE_SNAP_DEG = 8.0     # snap a line's angle onto a dominant orientation within this
LINE_ENDPOINT_PAD_M = 0.30    # a face edge counts as "backed" by a line only within its own
                               # observed extent plus this pad - stops an extended line from
                               # inventing a wall in space it was never seen occupying

# --- face labelling (the graph-cut smoothness term, done greedily) ---
FACE_MERGE_SUPPORT = 0.34     # merge two faces whose shared edge has less real wall evidence
                               # than this: the line between them is an extension artefact or
                               # a wide opening, not a room boundary
WALL_EVIDENCE_CELLS = 1        # a boundary cell is "wall-backed" if a real wall cell (point
                               # support, not just a drawn line) lies within this many cells
OUTSIDE_MAX_PTS_PER_CELL = 0.4  # a border-touching face this sparse, with no camera in it,
                                 # is outside the building, not a room

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


def _wall_centre_lines(wall_raw: np.ndarray, cell_m: float) -> list[dict]:
    """Wall centre-lines from the wall raster (arrangement step a + b).

    Hough segments -> per-segment (angle, perpendicular offset) -> snap angles onto the
    capture's dominant orientation and its perpendicular where they are close -> cluster by
    offset so the two faces of one wall become one line. Returns dicts with `angle` (deg),
    `rho` (cell offset of the infinite line), and `t0,t1` the along-line span where wall
    support was actually observed (cells), used later to tell a real edge from an extension.
    """
    if cv2 is None:
        return []
    H, W = wall_raw.shape
    min_len = max(6, int(LINE_MIN_SUPPORT_M / cell_m))
    segs = cv2.HoughLinesP(wall_raw.astype(np.uint8) * 255, 1, np.pi / 360.0,
                           threshold=min_len, minLineLength=min_len,
                           maxLineGap=int(round(0.35 / cell_m)))
    if segs is None:
        return []
    segs = segs.reshape(-1, 4).astype(float)                 # x1,y1,x2,y2 (col,row)
    ang = np.degrees(np.arctan2(segs[:, 3] - segs[:, 1], segs[:, 2] - segs[:, 0])) % 180.0
    length = np.hypot(segs[:, 2] - segs[:, 0], segs[:, 3] - segs[:, 1])

    a0 = float(ang[np.argmax(length)])                        # longest segment sets the grid
    def _snap(a: float) -> float:
        for base in (a0, (a0 + 90.0) % 180.0):
            d = abs((a - base + 90.0) % 180.0 - 90.0)
            if d <= LINE_ANGLE_SNAP_DEG:
                return base
        return a
    sang = np.array([_snap(a) for a in ang])

    lines: list[dict] = []
    used = np.zeros(len(segs), bool)
    merge_off = LINE_MERGE_OFFSET_M / cell_m
    order = np.argsort(-length)
    for i in order:
        if used[i]:
            continue
        th = np.radians(sang[i])
        d = np.array([np.cos(th), np.sin(th)])
        nrm = np.array([-d[1], d[0]])
        rho_i = float(nrm @ segs[i, :2])
        members = [i]
        for j in order:
            if used[j] or j == i or abs(sang[j] - sang[i]) > 3.0:
                continue
            rho_j = float(nrm @ segs[j, :2])
            if abs(rho_j - rho_i) <= merge_off:
                members.append(j)
        used[members] = True
        pts = np.vstack([segs[members][:, :2], segs[members][:, 2:]])
        t = pts @ d
        rho = float(np.mean([nrm @ p for p in pts]))
        lines.append({"angle": float(sang[i]), "rho": rho, "d": d, "nrm": nrm,
                      "t0": float(t.min()), "t1": float(t.max()),
                      "support": float(sum(length[members]))})
    return [ln for ln in lines if ln["support"] >= min_len]


def _segment_by_arrangement(wall_raw: np.ndarray, wall_dil: np.ndarray, grid: np.ndarray,
                            interior: np.ndarray, cam_cells: np.ndarray, cell_m: float
                            ) -> tuple[np.ndarray, int]:
    """Faces of the wall-line arrangement, labelled into rooms by greedy wall-supported
    merging (arrangement step c). Returns (room_lab over `interior`, n_rooms)."""
    H, W = wall_raw.shape
    lines = _wall_centre_lines(wall_raw, cell_m)
    if not lines:
        return interior.astype(np.int32), 1

    # Draw every line across the whole plan: these cuts define the arrangement faces.
    cut = np.zeros((H, W), np.uint8)
    diag = float(np.hypot(H, W))
    for ln in lines:
        c = ln["rho"] * ln["nrm"]                             # a point on the line
        p0 = (c - diag * ln["d"]).round().astype(int)
        p1 = (c + diag * ln["d"]).round().astype(int)
        if cv2 is not None:
            cv2.line(cut, (int(p0[0]), int(p0[1])), (int(p1[0]), int(p1[1])), 1, 1)

    faces0, nf = _components(interior & (cut == 0))          # faces, cut cells still 0
    if nf <= 1:
        return interior.astype(np.int32), 1
    faces = _multi_source_fill(np.where(interior, faces0, 0), interior)  # grow over the cuts

    # Per-face evidence (the data term): point mass, and whether the camera stood in it.
    cam_set = {(int(y), int(x)) for y, x in cam_cells} if len(cam_cells) else set()
    border = np.zeros((H, W), bool)
    border[0, :] = border[-1, :] = border[:, 0] = border[:, -1] = True

    def _face_stats(lab: np.ndarray) -> tuple[dict, dict, dict, dict]:
        ids = [int(k) for k in np.unique(lab) if k != 0]
        return ({k: float(grid[lab == k].sum()) for k in ids},
                {k: int((lab == k).sum()) for k in ids},
                {k: any(lab[y, x] == k for y, x in cam_set) for k in ids},
                {k: bool((lab == k)[border].any()) for k in ids})

    # The smoothness term. The arrangement EDGE between two faces is the whole run of cut
    # cells with one face on each side - the full wall line, doorway gap included - not just
    # where the two rooms' free space happens to touch. Its wall support is the fraction of
    # that run on real wall point-support: a doorway is a short unbacked stretch in an
    # otherwise backed line (rooms stay apart), an extended line with no wall under it scores
    # ~0 (faces merge). Computed once here against the fixed `faces0`; the greedy merge below
    # only relabels, so edges re-aggregate by remap without re-scanning the grid.
    cyx = np.argwhere(cut > 0)
    e_a = np.zeros(len(cyx), np.int32)
    e_b = np.zeros(len(cyx), np.int32)
    e_backed = wall_dil[cyx[:, 0], cyx[:, 1]].astype(np.int8)
    for i, (y, x) in enumerate(cyx):
        s = {int(v) for v in (faces0[y - 1, x] if y > 0 else 0,
                              faces0[y + 1, x] if y < H - 1 else 0,
                              faces0[y, x - 1] if x > 0 else 0,
                              faces0[y, x + 1] if x < W - 1 else 0) if v > 0}
        if len(s) == 2:
            e_a[i], e_b[i] = sorted(s)
    keep = e_a > 0
    e_a, e_b, e_backed = e_a[keep], e_b[keep], e_backed[keep]

    remap: dict[int, int] = {}

    def _cur(k: int) -> int:
        while k in remap:
            k = remap[k]
        return k

    for _ in range(nf):
        ca = np.array([_cur(v) for v in e_a])
        cb = np.array([_cur(v) for v in e_b])
        m = ca != cb
        if not m.any():
            break
        key = ca[m] * 1_000_003 + cb[m]
        best_pair, best_supp = None, 1.0
        for k in np.unique(key):
            supp = float(e_backed[m][key == k].mean())
            if supp < best_supp:
                best_supp, best_pair = supp, (int(ca[m][key == k][0]), int(cb[m][key == k][0]))
        if best_pair is None or best_supp >= FACE_MERGE_SUPPORT:
            break
        lo_, hi_ = sorted(best_pair)
        remap[hi_] = lo_

    if remap:
        flat = np.array([_cur(int(k)) for k in range(faces.max() + 1)])
        faces = flat[faces]
    fmass, farea, fcam, fborder = _face_stats(faces)

    # Drop faces that are outside the building: on the border, sparse, no camera.
    for k in list(np.unique(faces)):
        if k == 0:
            continue
        if (fborder.get(int(k)) and not fcam.get(int(k))
                and fmass.get(int(k), 0) / max(farea.get(int(k), 1), 1) < OUTSIDE_MAX_PTS_PER_CELL):
            faces[faces == k] = 0

    # Relabel 1..K.
    keep = [int(k) for k in np.unique(faces) if k != 0]
    remap = {k: i + 1 for i, k in enumerate(keep)}
    out = np.zeros_like(faces)
    for k, v in remap.items():
        out[faces == k] = v
    return out, len(keep)


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
    wall_raw = grid >= max(3, int(np.percentile(pos, WALL_MIN_PCTL)))
    # `wall` (gap-closed) guards free-space flooding and supplies wall evidence for face
    # merging; `wall_raw` (thin) is what the Hough line finder reads.
    wall = _dilate(wall_raw, 1)

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

    # Carve rooms: wall-line arrangement + greedy wall-supported face merging (see the module
    # docstring, step 4). Camera cells are the trajectory, offset by the +1 raster pad.
    cam_cells = np.empty((0, 2), int)
    if camera_centers is not None and len(camera_centers):
        cc = np.stack([camera_centers @ e1, camera_centers @ e2], axis=1)
        cc = np.floor((cc - mn) / cell_m).astype(int) + 1
        m = (cc[:, 0] >= 0) & (cc[:, 0] < nx) & (cc[:, 1] >= 0) & (cc[:, 1] < ny)
        cam_cells = np.stack([cc[m, 1], cc[m, 0]], axis=1)          # (row, col)
    room_lab, nrooms = _segment_by_arrangement(wall_raw, wall, grid, interior,
                                               cam_cells, cell_m)
    if nrooms == 0:
        room_lab, nrooms = interior.astype(np.int32), 1

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

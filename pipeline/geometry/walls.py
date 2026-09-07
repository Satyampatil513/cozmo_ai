"""Room polygon and dimensions from fitted planes.

Once planes exist, every dimension the contract asks for is deterministic geometry:

    ceiling height  = separation of the floor and ceiling planes
    room corner     = intersection of two adjacent wall planes with the floor plane
    wall length     = distance between the two corners bounding that wall
    floor area      = area of the closed corner polygon

No learned component anywhere below this line, which is what makes the numbers defensible
and is also, in practice, what makes them accurate: a plane fitted to ten thousand points is
a far better estimate of a wall than any per-image prediction of where that wall is.

The work is not the formulas, it is deciding *which* planes bound the room and in what
order. That is what `room_polygon` does.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from pipeline.geometry.planes import Plane, _floor_basis

# Two walls closer than this in azimuth are the same wall as far as the polygon is
# concerned, and intersecting them produces a corner at absurd distance.
MIN_CORNER_ANGLE_DEG = 20.0

# A corner further than this beyond the observed extent of both its walls is an
# extrapolation artefact, not a corner.
MAX_CORNER_OVERSHOOT_M = 1.0

# A room-bounding wall has almost nothing behind it. Above this fraction of the cloud
# sitting outside a candidate plane, it is furniture, not a wall.
MAX_MASS_BEHIND = 0.02
BEHIND_TOL_FLOOR_M = 0.05        # never tighter than this, however clean the data looks
BEHIND_TOL_SIGMAS = 3.0          # ... and never tighter than this much measured scatter

# A real wall runs floor to ceiling. Furniture fronts do not.
MIN_HEIGHT_COVERAGE = 0.60


def select_room_walls(walls: list[Plane], points: np.ndarray, gravity: np.ndarray,
                      floor: Plane, ceiling: Plane | None) -> list[Plane]:
    """Keep only the planes that actually bound the room, and orient them inward.

    Fitting large vertical planes is not enough: a wardrobe front, the back of a bookcase
    and a floor-length curtain are all large, vertical and planar, and RANSAC likes them.
    Accepting one as a wall was measured at 14% wall-length error even with LiDAR-grade
    noise - far and away the largest error source found, dwarfing depth noise.

    Two physical tests separate a wall from furniture:

    - **Nothing behind it.** A room-bounding plane has the entire cloud on one side. A
      wardrobe standing 60 cm off the wall has the real wall, and slices of floor and
      ceiling, behind it. This is the decisive test and it needs no thresholds tuned per
      scene.
    - **It runs floor to ceiling.** Furniture stops short. Weaker than the first test, but
      it catches a cabinet pushed flush against a wall, where nothing is behind it.

    Orienting each kept normal inward also gives the rest of the pipeline a consistent
    convention, which the raw SVD normals do not have.
    """
    if not walls:
        return []
    g = gravity / np.linalg.norm(gravity)
    centroid = points.mean(axis=0)

    h = points @ g
    room_h = float(np.percentile(h, 99) - np.percentile(h, 1))

    # Calibrate the "behind" band to the data's own scatter rather than a fixed constant.
    # A fixed 5 cm band silently assumes LiDAR-grade depth: at photo-tier noise a wall's own
    # points spill past it - with sigma = 5 cm, 16% of every wall's inliers land more than
    # 5 cm behind their own plane - and every genuine wall gets rejected as furniture. The
    # band has to track how noisy the cloud actually is, so it is measured here.
    resid = np.concatenate([np.abs(points[w.inliers] @ w.normal + w.d) for w in walls])
    sigma = 1.4826 * float(np.median(resid))          # MAD -> sigma, outlier-robust
    tol = max(BEHIND_TOL_FLOOR_M, BEHIND_TOL_SIGMAS * sigma)

    kept = []
    for w in walls:
        # Orient inward: the bulk of the room should be on the positive side.
        n, d = w.normal, w.d
        if float(n @ centroid) + d < 0:
            n, d = -n, -d

        # A plane's own inliers are excluded from its behind-test. We are asking whether
        # anything *else* sits outside this plane; counting its own noise scatter answers a
        # different and useless question.
        other = np.ones(len(points), dtype=bool)
        other[w.inliers] = False
        behind = float(np.mean((points[other] @ n + d) < -tol)) if other.any() else 0.0
        if behind > MAX_MASS_BEHIND:
            continue

        if room_h > 1e-6:
            wh = points[w.inliers] @ g
            if float(wh.max() - wh.min()) / room_h < MIN_HEIGHT_COVERAGE:
                continue

        w.normal, w.d = n, d
        kept.append(w)
    return kept


@dataclass
class RoomGeometry:
    corners: np.ndarray             # Nx2, ordered, in floor-plane coordinates (metres)
    wall_lengths: np.ndarray        # N, wall_lengths[i] spans corners[i] -> corners[i+1]
    floor_area: float
    ceiling_height: float
    ceiling_height_spread: float    # max-min of per-sample height, the gate cares about this
    origin: np.ndarray              # 3, world point that maps to (0, 0)
    basis: tuple[np.ndarray, np.ndarray]
    walls_used: int
    walls_dropped: int


def ceiling_height(floor: Plane, ceiling: Plane, points: np.ndarray
                   ) -> tuple[float, float]:
    """Floor-to-ceiling separation, plus its spread across the ceiling.

    Returns (height, spread). The brief scores repeatable-but-biased separately from
    unrepeatable and requires a per-room spread, so a single number is not enough: we measure
    the floor plane's distance at every ceiling inlier and report both the mean and the range.

    Measuring pointwise rather than differencing the two plane offsets also stays honest when
    the planes are not exactly parallel - a sloped or vaulted ceiling gives a large spread,
    which is information, where the offset difference would silently return one number.
    """
    if floor is None or ceiling is None:
        return float("nan"), float("nan")
    pts = points[ceiling.inliers]
    if pts.size == 0:
        return float("nan"), float("nan")
    d = np.abs(pts @ floor.normal + floor.d)
    return float(np.mean(d)), float(np.percentile(d, 97.5) - np.percentile(d, 2.5))


def _wall_lines(walls: list[Plane], gravity: np.ndarray, origin: np.ndarray,
                points: np.ndarray) -> list[tuple[np.ndarray, float, np.ndarray, Plane]]:
    """Project each vertical wall plane to a 2D line on the floor.

    A wall plane whose normal is perpendicular to gravity intersects the floor in a line.
    In the (e1, e2) floor basis that line is  n2 . p + c = 0,  with n2 the wall normal's
    horizontal part and c its offset re-expressed about `origin`.
    """
    e1, e2 = _floor_basis(gravity)
    out = []
    for w in walls:
        n2 = np.array([float(w.normal @ e1), float(w.normal @ e2)])
        ln = np.linalg.norm(n2)
        if ln < 1e-6:                      # degenerate: not actually a wall
            continue
        n2 = n2 / ln
        c = (float(w.normal @ origin) + w.d) / ln
        p = points[w.inliers]
        rel = p - origin
        span = np.stack([rel @ e1, rel @ e2], axis=1)
        out.append((n2, c, span, w))
    return out


def _intersect(n_a: np.ndarray, c_a: float, n_b: np.ndarray, c_b: float) -> np.ndarray | None:
    A = np.stack([n_a, n_b])
    det = float(np.linalg.det(A))
    if abs(det) < 1e-9:
        return None
    return np.linalg.solve(A, np.array([-c_a, -c_b]))


def room_polygon(walls: list[Plane], floor: Plane, gravity: np.ndarray,
                 points: np.ndarray) -> RoomGeometry | None:
    """Close the wall planes into an ordered room polygon.

    Ordering is by the azimuth of each wall's own point mass about the room centre, not by
    the wall's normal. Normals from SVD have arbitrary sign, so ordering by normal direction
    scrambles opposite walls; where the wall's points actually sit does not.

    Adjacent walls are then intersected pairwise. Two guards decide what counts as a corner:

    - Near-parallel walls (< MIN_CORNER_ANGLE_DEG apart) are skipped. Their intersection
      shoots off toward infinity and would drag the polygon with it.
    - A corner lying far beyond the observed extent of both its walls is dropped as
      extrapolation. This is what stops one spurious plane - a wardrobe front, a curtain -
      from inventing a corner in open space.
    """
    if floor is None or len(walls) < 3:
        return None

    g = gravity / np.linalg.norm(gravity)
    walls = select_room_walls(walls, points, g, floor, None)
    if len(walls) < 3:
        return None
    e1, e2 = _floor_basis(g)
    # Put the 2D origin on the floor, under the centroid of the wall evidence.
    wall_pts = np.concatenate([points[w.inliers] for w in walls]) if walls else points
    origin = wall_pts.mean(axis=0)
    origin = origin - (float(origin @ floor.normal) + floor.d) * floor.normal

    lines = _wall_lines(walls, g, origin, points)
    if len(lines) < 3:
        return None

    # Order walls by where their evidence sits, going round the room.
    centres = np.array([span.mean(axis=0) for _, _, span, _ in lines])
    room_c = centres.mean(axis=0)
    order = np.argsort(np.arctan2(centres[:, 1] - room_c[1], centres[:, 0] - room_c[0]))
    lines = [lines[i] for i in order]

    min_cos = np.cos(np.radians(90.0 - MIN_CORNER_ANGLE_DEG))
    corners, used, dropped = [], 0, 0
    n = len(lines)
    for i in range(n):
        n_a, c_a, span_a, _ = lines[i]
        n_b, c_b, span_b, _ = lines[(i + 1) % n]
        if abs(float(n_a @ n_b)) > min_cos:      # too close to parallel
            dropped += 1
            continue
        p = _intersect(n_a, c_a, n_b, c_b)
        if p is None:
            dropped += 1
            continue
        # The corner must lie near the observed extent of both walls it belongs to.
        ok = True
        for span in (span_a, span_b):
            lo, hi = span.min(axis=0), span.max(axis=0)
            if np.any(p < lo - MAX_CORNER_OVERSHOOT_M) or np.any(p > hi + MAX_CORNER_OVERSHOOT_M):
                ok = False
                break
        if not ok:
            dropped += 1
            continue
        corners.append(p)
        used += 1

    if len(corners) < 3:
        return None
    C = np.array(corners)

    edges = np.roll(C, -1, axis=0) - C
    lengths = np.linalg.norm(edges, axis=1)
    x, y = C[:, 0], C[:, 1]
    area = 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))

    return RoomGeometry(
        corners=C, wall_lengths=lengths, floor_area=area,
        ceiling_height=float("nan"), ceiling_height_spread=float("nan"),
        origin=origin, basis=(e1, e2), walls_used=used, walls_dropped=dropped,
    )


def extract_walls(points, floor_plane, ceiling_plane, walls=None, gravity=None):
    """Entry point kept for run.py: planes in, RoomGeometry out."""
    if gravity is None or walls is None:
        raise ValueError("extract_walls needs gravity and wall planes from planes.py")
    geo = room_polygon(walls, floor_plane, gravity, points)
    if geo is None:
        return None
    h, spread = ceiling_height(floor_plane, ceiling_plane, points)
    geo.ceiling_height, geo.ceiling_height_spread = h, spread
    return geo


# Two walls count as an opposite pair if their normals are parallel to within this angle.
# Sign-free: a fitted normal's direction is arbitrary (SVD gives no orientation), so opposite
# walls of a room may come back parallel or antiparallel depending on which way each fit
# happened to land. Testing for antiparallel alone found zero pairs on real data where five
# walls and two obvious pairs were present.
PAIR_ANGLE_DEG = 15.0
# A room dimension below this is a nook or a mis-merge, not a room width.
MIN_ROOM_SPAN_M = 1.2


def wall_pair_dimensions(walls: list[Plane], gravity: np.ndarray,
                         points: np.ndarray) -> list[dict]:
    """Room dimensions from opposite parallel wall pairs, independent of the polygon.

    A closed polygon is the richer output, but it is also the fragile one: it needs every
    bounding wall found, correctly ordered and successfully intersected, and it returns
    nothing at all when a capture spills into an adjoining space. On the fused LiDAR room the
    polygon failed for exactly that reason while the underlying planes were fine.

    The separation between two opposite walls needs none of that. It is the distance between
    two parallel planes, each fitted to tens of thousands of points, and it is available
    whenever both walls were seen - which makes it the most reliable metric measurement in the
    whole pipeline and a useful cross-check on any polygon we do produce.

    Pairs are ranked by combined support so the dominant pair of a room comes first. Returned
    per pair: separation, the two normals' agreement, and the evidence behind each wall.
    """
    cos_tol = np.cos(np.radians(PAIR_ANGLE_DEG))

    out: list[dict] = []
    for i in range(len(walls)):
        for j in range(i + 1, len(walls)):
            a, b = walls[i], walls[j]
            dot = float(a.normal @ b.normal)
            if abs(dot) < cos_tol:            # not the same orientation, so not a pair
                continue
            ca = points[a.inliers].mean(axis=0)
            sep = abs(float(b.normal @ ca) + b.d)
            if sep < MIN_ROOM_SPAN_M:         # coincident or merged: one wall, not two
                continue
            out.append({
                "separation_m": round(sep, 4),
                "parallel_deg": round(float(np.degrees(np.arccos(min(1.0, abs(dot))))), 2),
                "support": int(a.n_inliers + b.n_inliers),
                "normal_a": np.round(a.normal, 3).tolist(),
                "normal_b": np.round(b.normal, 3).tolist(),
            })
    return sorted(out, key=lambda d: -d["support"])

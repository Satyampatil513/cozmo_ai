"""Plane extraction, gravity estimation, and floor/ceiling/wall classification.

This is the deterministic half of the pipeline and it is where the accuracy comes from.
Models upstream supply a point cloud (LiDAR depth, or metric monocular depth lifted through
intrinsics); everything here is geometry with no learned component, which is both more
accurate than asking a network for a dimension and defensible line by line.

Written against numpy rather than Open3D. Open3D publishes no wheels for the Python we run
on, and a RANSAC plane fit is fifty lines - taking the dependency would buy nothing and cost
us an explanation.

Regularisation policy, which is a deliberate choice and not an oversight: planes are snapped
to vertical/horizontal and to a shared Manhattan frame **only when they are already within a
few degrees of it**. A room that is genuinely not rectangular must come out not rectangular.
Forcing right angles would flatter our own benchmark - most rooms are boxes - and then fail
in front of Cozmo on the one room that is not.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# A plane is kept if it explains at least this fraction of the points it was fitted from.
MIN_INLIER_FRACTION = 0.02
MIN_INLIER_COUNT = 200

# Angular tolerances, degrees.
AXIS_TOL_DEG = 15.0          # how far from gravity a plane can be and still count horizontal
SNAP_TOL_DEG = 5.0           # verticality / horizontality: gravity is well determined
MANHATTAN_TOL_DEG = 2.0      # right angles: much tighter, and see regularize() for why


@dataclass
class Plane:
    """n . x + d = 0, with n a unit normal."""
    normal: np.ndarray
    d: float
    inliers: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=int))
    kind: str = "unknown"            # floor | ceiling | wall | unknown
    snapped: bool = False            # was this regularised, or left as fitted

    @property
    def n_inliers(self) -> int:
        return int(self.inliers.size)

    def distance(self, points: np.ndarray) -> np.ndarray:
        """Signed point-plane distance."""
        return points @ self.normal + self.d

    def refit(self, points: np.ndarray) -> "Plane":
        """Total-least-squares refit on the current inliers.

        RANSAC's winning hypothesis comes from a minimal sample, so it is a consistent but
        noisy estimate. Refitting on every inlier is what actually buys the precision.
        """
        if self.inliers.size < 3:
            return self
        p = points[self.inliers]
        c = p.mean(axis=0)
        _, _, vt = np.linalg.svd(p - c, full_matrices=False)
        n = vt[-1]
        n = n / np.linalg.norm(n)
        if n @ self.normal < 0:          # keep the original orientation
            n = -n
        return Plane(n, float(-n @ c), self.inliers, self.kind, self.snapped)


def _plane_from_sample(pts: np.ndarray) -> tuple[np.ndarray, float] | None:
    v1, v2 = pts[1] - pts[0], pts[2] - pts[0]
    n = np.cross(v1, v2)
    ln = np.linalg.norm(n)
    if ln < 1e-9:
        return None
    n = n / ln
    return n, float(-n @ pts[0])


def ransac_plane(
    points: np.ndarray,
    threshold: float,
    normals: np.ndarray | None = None,
    iters: int = 400,
    rng: np.random.Generator | None = None,
    normal_tol_deg: float = 20.0,
) -> Plane | None:
    """Single dominant plane by RANSAC.

    When per-point normals are available (Metric3D predicts them alongside depth, and LiDAR
    gives them cheaply from the depth gradient) a hypothesis needs only **one** sample point
    rather than three, since the point and its normal already define a plane. That collapses
    the sample size from 3 to 1, which raises the probability that a hypothesis is
    outlier-free from p^3 to p, and it lets us reject inliers whose surface orientation
    disagrees with the plane - a point that happens to lie on the wall plane but belongs to a
    table edge is excluded on orientation even though its distance passes.
    """
    n_pts = len(points)
    if n_pts < 3:
        return None
    rng = rng or np.random.default_rng(0)
    cos_tol = np.cos(np.radians(normal_tol_deg))

    best_mask, best_count = None, 0
    for _ in range(iters):
        if normals is not None:
            i = int(rng.integers(n_pts))
            n = normals[i]
            nn = np.linalg.norm(n)
            if nn < 1e-9:
                continue
            n = n / nn
            d = float(-n @ points[i])
        else:
            idx = rng.choice(n_pts, 3, replace=False)
            got = _plane_from_sample(points[idx])
            if got is None:
                continue
            n, d = got

        mask = np.abs(points @ n + d) < threshold
        if normals is not None:
            mask &= np.abs(normals @ n) > cos_tol
        c = int(mask.sum())
        if c > best_count:
            best_count, best_mask = c, mask

    if best_mask is None or best_count < 3:
        return None
    idx = np.flatnonzero(best_mask)
    seed = Plane(np.zeros(3), 0.0, idx)
    # Recover an orientation for refit() to preserve, from the winning hypothesis.
    p = points[idx]
    c = p.mean(axis=0)
    _, _, vt = np.linalg.svd(p - c, full_matrices=False)
    seed.normal = vt[-1] / np.linalg.norm(vt[-1])
    seed.d = float(-seed.normal @ c)
    return seed


def extract_planes(
    points: np.ndarray,
    normals: np.ndarray | None = None,
    threshold: float = 0.03,
    max_planes: int = 12,
    rng: np.random.Generator | None = None,
) -> list[Plane]:
    """Iteratively pull out dominant planes, removing inliers as we go.

    `threshold` is the inlier band in metres and should track the noise of the source:
    ~1-2 cm for LiDAR, ~3-5 cm for lifted monocular depth. Too tight and a real wall
    fragments into several planes; too loose and a wall swallows the furniture in front
    of it.
    """
    rng = rng or np.random.default_rng(0)
    remaining = np.arange(len(points))
    out: list[Plane] = []
    floor_count = max(MIN_INLIER_COUNT, int(MIN_INLIER_FRACTION * len(points)))

    for _ in range(max_planes):
        if remaining.size < floor_count:
            break
        sub = points[remaining]
        sub_n = normals[remaining] if normals is not None else None
        pl = ransac_plane(sub, threshold, sub_n, rng=rng)
        if pl is None or pl.n_inliers < floor_count:
            break
        # Map indices back to the original cloud, then refit on all of them.
        pl.inliers = remaining[pl.inliers]
        pl = pl.refit(points)
        out.append(pl)
        remaining = np.setdiff1d(remaining, pl.inliers, assume_unique=False)

    return sorted(out, key=lambda p: -p.n_inliers)


# A handheld phone held as the protocol asks - upright, chest height, level - puts world-up
# within roughly this cone of the camera's -y axis. Wider than any careful operator, narrow
# enough to exclude a wall normal, which is what it exists to do.
GRAVITY_PRIOR_CONE_DEG = 45.0

# Camera convention: x right, y down, z forward. Up is -y.
CAMERA_UP = np.array([0.0, -1.0, 0.0])


def estimate_gravity(planes: list[Plane], prior: np.ndarray | None = CAMERA_UP,
                     cone_deg: float = GRAVITY_PRIOR_CONE_DEG) -> np.ndarray:
    """Find the up axis: a Manhattan-frame vote, restricted to a cone around a prior.

    The vote alone: every plane normal is a candidate up axis, and each candidate scores the
    inlier mass of all planes either parallel to it (floors, ceilings, tabletops) or
    perpendicular to it (walls). A room's true vertical maximises that, and weighting by
    inlier count stops a cluster of small clutter planes outvoting the floor.

    The vote alone is not enough, and this was measured rather than anticipated. In a single
    photo of a room, a wall is frequently the largest plane in frame and the floor is a
    foreshortened sliver, so the unconstrained vote picks the wall: on real captures it
    returned normals like [0.73, 0.07, -0.68] and the "floor" and "ceiling" it then chose were
    two walls, giving ceiling heights of 2 to 22 centimetres. The vote is a good tiebreaker
    and a bad primary.

    So a prior is applied. The protocol asks the operator to hold the phone upright and level,
    which puts world-up near the camera's -y axis, and unlike the scene content that prior
    does not depend on which wall happens to dominate the frame. Candidates outside the cone
    are rejected outright; if none survive, the prior itself is returned rather than a
    confidently wrong answer.

    Pass prior=None on the LiDAR tier, where ARKit supplies real gravity and this becomes a
    cross-check, or where a full multi-view reconstruction has already fixed the vertical.
    """
    if not planes:
        return prior.copy() if prior is not None else np.array([0.0, 0.0, 1.0])

    par = np.cos(np.radians(AXIS_TOL_DEG))
    perp = np.sin(np.radians(AXIS_TOL_DEG))

    candidates: list[np.ndarray] = []
    for cand in planes:
        n = cand.normal / np.linalg.norm(cand.normal)
        for signed in (n, -n):                    # normals are sign-ambiguous
            if prior is None or float(signed @ prior) >= np.cos(np.radians(cone_deg)):
                candidates.append(signed)

    if not candidates:
        return prior.copy() if prior is not None else planes[0].normal

    best, best_score = candidates[0], -1.0
    for g in candidates:
        score = 0.0
        for p in planes:
            c = abs(float(p.normal @ g))
            if c > par or c < perp:
                score += p.n_inliers
        if score > best_score:
            best, best_score = g, score

    return best / np.linalg.norm(best)


# Soft priors for floor/ceiling selection. These are DEFENSIVE HEURISTICS tuned on the
# benchmark scenes, not universal physical truths: real properties have ceilings under 1.9 m
# and over 4.5 m, split levels, and mezzanines. So they are stated as priors, they decay
# smoothly rather than cutting off, and a frame with no plausible candidate abstains instead
# of emitting a measurement. The report says exactly this rather than claiming all rooms obey
# a fixed range.
SEP_PRIOR_M = (2.70, 0.90)          # floor-ceiling separation: centre, width
CAM_HEIGHT_PRIOR_M = (1.45, 0.45)   # handheld camera above the floor: centre, width
MIN_HEADROOM_M = 0.30               # ceiling must be meaningfully above the camera
BOUND_TOL_M = 0.08                  # slack when asking "is anything outside this pair"


def _soft(x: float, centre: float, width: float) -> float:
    """Gaussian bump in [0, 1]. Smooth, so an unusual room is penalised, never excluded."""
    return float(np.exp(-(((x - centre) / width) ** 2)))


def select_floor_ceiling(planes: list[Plane], gravity: np.ndarray, points: np.ndarray,
                         camera_at_origin: bool = True
                         ) -> tuple[Plane | None, Plane | None, float]:
    """Choose the floor/ceiling pair *jointly*, by scoring every candidate pair.

    The previous rule - take the two largest horizontal planes - fails in a way that is not
    rare. In a frame looking through an open doorway the two largest horizontals can be a near
    floor and a door head, and their separation gets reported as a ceiling height. Three of 28
    real frames did exactly that, returning 0.44 m to 0.97 m.

    A hard separation range alone would not fix it. Two large horizontal surfaces that happen
    to sit 2-4 m apart - a table top and the ceiling, a bed and the ceiling - pass a range
    filter and are still the wrong pair. What actually distinguishes a real floor/ceiling pair
    is a conjunction of properties, so they are scored together and the pair chosen jointly:

      support        both planes carry real evidence, saturating so one huge plane cannot buy
                     its way past a failure on another term
      parallelism    a floor and its ceiling are parallel to each other
      horizontality  both are perpendicular to gravity
      separation     their spacing is plausible for a habitable room (soft prior)
      bounding       almost nothing lies below the floor or above the ceiling - the pair
                     should enclose the scene, which a table top does not
      camera         the camera sits between them, roughly a person's height above the floor

    Multiplicative rather than additive on purpose: these are conditions that must hold
    together, and a weighted sum lets one very large plane outvote a hard geometric
    contradiction.

    Returns (floor, ceiling, score). Score is 0 when no pair is defensible, and the caller is
    expected to abstain rather than report.
    """
    par = np.cos(np.radians(AXIS_TOL_DEG))
    g = gravity / np.linalg.norm(gravity)

    horiz = [p for p in planes if abs(float(p.normal @ g)) > par and p.n_inliers > 0]
    if len(horiz) < 2:
        return (horiz[0] if horiz else None), None, 0.0

    height = {id(p): float(np.mean(points[p.inliers] @ g)) for p in horiz}
    hp = points @ g
    total = max(1, len(points))
    saturate = 0.15 * total

    best = None
    for f in horiz:
        for c in horiz:
            if f is c or height[id(c)] <= height[id(f)]:
                continue
            sep = height[id(c)] - height[id(f)]

            s_support = min(1.0, (f.n_inliers + c.n_inliers) / saturate)
            s_parallel = abs(float(f.normal @ c.normal))
            s_horiz = min(abs(float(f.normal @ g)), abs(float(c.normal @ g)))
            s_sep = _soft(sep, *SEP_PRIOR_M)

            below = float(np.mean(hp < height[id(f)] - BOUND_TOL_M))
            above = float(np.mean(hp > height[id(c)] + BOUND_TOL_M))
            s_bound = (1.0 - below) * (1.0 - above)

            s_cam = 1.0
            if camera_at_origin:
                # Points are in the camera frame with the camera at the origin, so the floor
                # sits at negative height along gravity and the ceiling at positive.
                cam_h = -height[id(f)]
                headroom = height[id(c)]
                s_cam = _soft(cam_h, *CAM_HEIGHT_PRIOR_M)
                if headroom < MIN_HEADROOM_M:
                    s_cam *= 0.05

            score = s_support * s_parallel * s_horiz * s_sep * s_bound * s_cam
            if best is None or score > best[0]:
                best = (score, f, c)

    if best is None:
        return None, None, 0.0
    return best[1], best[2], best[0]


def classify(planes: list[Plane], gravity: np.ndarray, points: np.ndarray,
             camera_at_origin: bool = True
             ) -> tuple[list[Plane], np.ndarray, float]:
    """Label each plane floor / ceiling / wall, orient gravity up, and score the choice.

    Floor and ceiling are separated by height, not by normal direction. A fitted normal's sign
    is arbitrary - SVD gives no orientation - so "its normal points up" is not information we
    actually have, and using it would coin-flip the two apart.

    The third return value is the floor/ceiling selection confidence. Callers abstain on a low
    score rather than reporting a number they cannot defend.
    """
    par = np.cos(np.radians(AXIS_TOL_DEG))
    perp = np.sin(np.radians(AXIS_TOL_DEG))

    for p in planes:
        c = abs(float(p.normal @ gravity))
        p.kind = "wall" if c < perp else ("horizontal" if c > par else "unknown")

    floor, ceiling, score = select_floor_ceiling(planes, gravity, points, camera_at_origin)
    if floor is not None:
        floor.kind = "floor"
    if ceiling is not None:
        ceiling.kind = "ceiling"
        if floor is not None:
            h_f = float(np.mean(points[floor.inliers] @ gravity))
            h_c = float(np.mean(points[ceiling.inliers] @ gravity))
            if h_c < h_f:                       # gravity was pointing down
                gravity = -gravity

    return planes, gravity, score


def regularize(planes: list[Plane], gravity: np.ndarray, points: np.ndarray) -> list[Plane]:
    """Snap planes to vertical/horizontal and a shared Manhattan frame - conservatively.

    Only planes already within SNAP_TOL_DEG of the ideal are moved; everything else is left
    exactly as fitted.

    That restraint is the whole point. Most rooms are boxes, so hard Manhattan forcing would
    improve almost every number in our own benchmark and then produce a confidently wrong
    plan for the first bay window or angled partition Cozmo walks us into. Small residual
    non-orthogonality is usually real, and snapping it away destroys the thing we are meant
    to be measuring.

    Every snap re-derives `d` from the plane's own inlier centroid, so rotating the normal
    pivots the plane about its evidence instead of sliding it through space.
    """
    g = gravity / np.linalg.norm(gravity)
    tol = np.radians(SNAP_TOL_DEG)

    def resnap(pl: Plane, n_new: np.ndarray) -> None:
        c = points[pl.inliers].mean(axis=0)
        pl.normal = n_new
        pl.d = float(-n_new @ c)
        pl.snapped = True

    walls = [p for p in planes if p.kind == "wall"]

    # Verticality: a wall normal should be perpendicular to gravity.
    for pl in walls:
        tilt = abs(np.arcsin(np.clip(abs(float(pl.normal @ g)), -1.0, 1.0)))
        if tilt < tol:
            n = pl.normal - (pl.normal @ g) * g
            ln = np.linalg.norm(n)
            if ln > 1e-9:
                resnap(pl, n / ln)

    # Horizontality: floor and ceiling normals should be parallel to gravity.
    for pl in planes:
        if pl.kind in ("floor", "ceiling", "horizontal"):
            ang = np.arccos(np.clip(abs(float(pl.normal @ g)), -1.0, 1.0))
            if ang < tol:
                resnap(pl, g if pl.normal @ g > 0 else -g)

    # Manhattan frame: all or nothing, never per wall.
    #
    # Deciding this wall by wall is wrong, and measurably so. The reference frame is fitted
    # as a compromise across the walls, so in a room skewed by theta each wall sits only
    # theta/2 from that compromise. Judge each wall against it independently and both ends of
    # a 6.8-degree skew look "within 5 degrees of square", both get pulled onto the grid, and
    # a room whose real corners are 83 and 97 degrees is reported as a perfect rectangle.
    # That was the measured behaviour before this changed.
    #
    # Orthogonality is a property of the room, not of any single wall, so the whole room has
    # to qualify: if any wall's residual to the fitted grid exceeds tolerance, the room is not
    # Manhattan and nothing is snapped. The tolerance is halved relative to SNAP_TOL_DEG for
    # the same theta/2 reason.
    if len(walls) >= 2:
        e1, e2 = _floor_basis(g)
        az = np.array([np.arctan2(float(pl.normal @ e2), float(pl.normal @ e1)) for pl in walls])
        w = np.array([pl.n_inliers for pl in walls], dtype=float)
        # Wall normals are sign-ambiguous and orthogonal walls sit 90 degrees apart, so these
        # azimuths live on a 90-degree circle. Average them there, via the 4th harmonic.
        ref = float(np.arctan2(np.sum(w * np.sin(4 * az)), np.sum(w * np.cos(4 * az))) / 4.0)
        targets = np.array([ref + round((a - ref) / (np.pi / 2)) * (np.pi / 2) for a in az])
        residuals = np.array([abs(_wrap(a - t)) for a, t in zip(az, targets)])

        if residuals.max() < np.radians(MANHATTAN_TOL_DEG):
            for pl, t in zip(walls, targets):
                resnap(pl, np.cos(t) * e1 + np.sin(t) * e2)

    return planes


def _wrap(a: float) -> float:
    return (a + np.pi) % (2 * np.pi) - np.pi


def _floor_basis(gravity: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Any orthonormal pair spanning the horizontal plane."""
    g = gravity / np.linalg.norm(gravity)
    seed = np.array([1.0, 0.0, 0.0]) if abs(g[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    e1 = seed - (seed @ g) * g
    e1 /= np.linalg.norm(e1)
    return e1, np.cross(g, e1)


# Below this floor/ceiling selection score we decline to report a height. Calibrated on the
# benchmark: sound frames score far above it, the catastrophic frames far below.
MIN_SELECTION_SCORE = 0.02


def fit_floor_ceiling(points, normals=None, threshold: float = 0.03,
                      gravity_prior: np.ndarray | None = CAMERA_UP,
                      camera_at_origin: bool = True,
                      min_score: float = MIN_SELECTION_SCORE):
    """Point cloud in, (gravity, floor, ceiling, walls, score) out.

    Returns ceiling=None when the floor/ceiling selection cannot be defended, so the caller
    abstains rather than reporting a confident number. Abstaining is the correct behaviour:
    the brief penalises confident garbage on thin input, and a frame that cannot see a whole
    room is not a measurement of one.
    """
    planes = extract_planes(points, normals, threshold=threshold)
    if not planes:
        return None
    gravity = estimate_gravity(planes, prior=gravity_prior)
    planes, gravity, score = classify(planes, gravity, points, camera_at_origin)
    planes = regularize(planes, gravity, points)
    floor = next((p for p in planes if p.kind == "floor"), None)
    ceiling = next((p for p in planes if p.kind == "ceiling"), None)
    walls = [p for p in planes if p.kind == "wall"]
    if score < min_score:
        ceiling = None                  # abstain: no defensible floor/ceiling pair
    return gravity, floor, ceiling, walls, score

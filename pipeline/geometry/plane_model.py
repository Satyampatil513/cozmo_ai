"""A room model built by agreeing planes across frames, instead of fitting one fused blob.

THE PROBLEM THIS REPLACES. Fusing every frame into one cloud and running RANSAC on it gave
nine planes for a four-wall room, five of them labelled "wall" but really two directions:

    W0-W2  3.0 deg apart      W0-W3 10.8 deg      W2-W3  7.8 deg      W1-W4  6.4 deg

W2 and W3 sat 7 cm and 7.8 degrees apart - one wall fitted twice. Fragments like that survive
because in a fused cloud a wall's points come from several frames whose depth scales disagree
slightly, so the surface is not quite planar any more and RANSAC carves it into slices. The
mush is a product of fusing first.

THE APPROACH. Fit planes per frame, where each surface is seen from one viewpoint with one
consistent depth scale and comes out genuinely flat. Then transform those planes into the
world with the known poses and match them to each other by identity: same normal, same offset,
same plane. Planes that several frames independently agree on become the room model; ones only
one frame ever saw are held separately as unconfirmed.

Two things fall out of this that fusing-then-fitting cannot give:

  Consensus as evidence. A wall four frames agree on is a different proposition from a plane
  one frame hallucinated, and the model records which is which rather than treating a big
  plane and a real plane as the same thing.

  Per-frame labels carry over. Each frame already classifies its own floor, ceiling and walls
  correctly - measured on the real capture, all six frames of Room 1 found floor, ceiling and
  at least two wall directions. Matching planes across frames inherits that labelling instead
  of re-deriving it from a cloud where the floor and a bed have merged.

Frames are processed cleanest-first, so the model is seeded by a view that saw the room
plainly and later, messier frames extend it rather than defining it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from pipeline.geometry.planes import Plane

# Two planes are the same surface if their normals and offsets both agree this closely.
# Looser than the within-frame merge tolerance, because these come from different frames whose
# poses and depth scales carry their own error.
MATCH_ANGLE_DEG = 15.0
MATCH_DIST_M = 0.18

# A plane confirmed by this many frames is treated as part of the room rather than a guess.
MIN_CONSENSUS = 2

# Frames contributing to a real structural surface agree on where it is. A large spread means
# the "plane" is an average over things that are not the same surface.
MAX_SPREAD_M = 0.10


@dataclass
class ModelPlane:
    normal: np.ndarray            # world frame, oriented consistently across members
    d: float
    kind: str
    frames: list[int] = field(default_factory=list)
    inliers_total: int = 0
    members: list[tuple[int, np.ndarray, float]] = field(default_factory=list)

    @property
    def support(self) -> int:
        return len(self.frames)

    @property
    def confirmed(self) -> bool:
        return self.support >= MIN_CONSENSUS

    def spread_m(self) -> float:
        """How far apart the contributing frames put this plane. A direct consistency check."""
        if len(self.members) < 2:
            return 0.0
        ds = [d for _f, _n, d in self.members]
        return float(max(ds) - min(ds))

    def to_plane(self) -> Plane:
        return Plane(self.normal.copy(), self.d, np.empty(0, dtype=int), self.kind, False)


def transform_plane(normal: np.ndarray, d: float, T_wc: np.ndarray) -> tuple[np.ndarray, float]:
    """Camera-frame plane -> world frame.

    For x_world = R x_cam + t, a point on the plane satisfies n . x_cam + d = 0, so
    substituting x_cam = R^T (x_world - t) gives (R n) . x_world + (d - (R n) . t) = 0.
    """
    R, t = T_wc[:3, :3], T_wc[:3, 3]
    n_w = R @ normal
    ln = np.linalg.norm(n_w)
    if ln > 1e-9:
        n_w = n_w / ln
    return n_w, float(d / max(ln, 1e-9) - n_w @ t)


def cleanliness(planes: list[Plane]) -> float:
    """How plainly one frame saw the room. Higher is a better anchor.

    Rewards having a floor, a ceiling and at least two distinct wall directions - the minimum
    to pin a room's orientation - and penalises plane count, because a frame that fragments
    into eleven surfaces is describing clutter, not structure.
    """
    kinds = {p.kind for p in planes}
    walls = sorted([p for p in planes if p.kind == "wall"], key=lambda p: -p.n_inliers)
    dirs: list[np.ndarray] = []
    for w in walls:
        if not any(abs(float(w.normal @ d)) > np.cos(np.radians(20.0)) for d in dirs):
            dirs.append(w.normal)

    score = 0.0
    score += 2.0 if "floor" in kinds else 0.0
    score += 2.0 if "ceiling" in kinds else 0.0
    score += min(len(dirs), 3) * 1.5
    score += min(sum(p.n_inliers for p in planes) / 20000.0, 2.0)
    score -= 0.25 * max(0, len(planes) - 6)
    return score


def build_room_model(per_frame: list[tuple[int, list[Plane], np.ndarray]],
                     match_angle_deg: float = MATCH_ANGLE_DEG,
                     match_dist_m: float = MATCH_DIST_M) -> list[ModelPlane]:
    """Accumulate per-frame planes into one consensus model.

    `per_frame` is (frame_index, planes in that frame's camera coords, camera-to-world pose).
    Frames are consumed cleanest-first so the model is seeded by a clear view.
    """
    ordered = sorted(per_frame, key=lambda t: -cleanliness(t[1]))
    cos_tol = np.cos(np.radians(match_angle_deg))
    model: list[ModelPlane] = []

    for fi, planes, T in ordered:
        if T is None:
            continue
        for p in sorted(planes, key=lambda q: -q.n_inliers):
            n_w, d_w = transform_plane(p.normal, p.d, T)

            best, best_err = None, None
            for m in model:
                dot = float(n_w @ m.normal)
                # Fitted normals carry an arbitrary sign, so compare sign-free and flip the
                # incoming plane to the model's convention before comparing offsets.
                flip = dot < 0
                nn, dd = (-n_w, -d_w) if flip else (n_w, d_w)
                if abs(float(nn @ m.normal)) < cos_tol:
                    continue
                err = abs(dd - m.d)
                if err > match_dist_m:
                    continue
                if best is None or err < best_err:
                    best, best_err = (m, nn, dd), err

            if best is None:
                model.append(ModelPlane(normal=n_w, d=d_w, kind=p.kind, frames=[fi],
                                        inliers_total=p.n_inliers,
                                        members=[(fi, n_w, d_w)]))
                continue

            m, nn, dd = best
            # Inlier-weighted running average, so a wall seen well once is not dragged by a
            # sliver of the same wall glimpsed in another frame.
            w_old, w_new = m.inliers_total, p.n_inliers
            tot = max(1, w_old + w_new)
            m.normal = (m.normal * w_old + nn * w_new) / tot
            m.normal /= max(1e-9, np.linalg.norm(m.normal))
            m.d = (m.d * w_old + dd * w_new) / tot
            m.inliers_total = tot
            m.members.append((fi, nn, dd))
            if fi not in m.frames:
                m.frames.append(fi)
            # A confirmed structural label beats "horizontal"/"unknown" from a poorer view.
            if m.kind in ("horizontal", "unknown") and p.kind in ("floor", "ceiling", "wall"):
                m.kind = p.kind

    return sorted(model, key=lambda m: (-m.support, -m.inliers_total))


def room_dimensions(model: list[ModelPlane], gravity: np.ndarray) -> dict:
    """Ceiling height and wall-pair spans from the consensus model alone.

    Uses only plane offsets, so it needs no point cloud: for two parallel planes with a shared
    normal convention the separation is just the difference of their offsets.
    """
    g = gravity / np.linalg.norm(gravity)
    conf = [m for m in model if m.confirmed]
    out: dict = {"confirmed_planes": len(conf), "total_planes": len(model)}

    par = np.cos(np.radians(25.0))

    # Floor and ceiling are the LOWEST and HIGHEST confirmed horizontal planes, not the
    # largest. This is the whole fix, and the consensus model is what made it safe to make.
    #
    # A furnished bedroom contains several confirmed horizontal surfaces - in Room 1 the model
    # held planes 0.88 m, 1.15 m and 1.53 m below the camera, all labelled "floor". The one at
    # 0.88 m is the bed and carried the most inliers (38k against 14k), so choosing by support
    # picked the bed every time and reported bed-to-ceiling as the room height, about 30 cm
    # short. Height is the discriminating property here; inlier count is not, because a bed
    # seen close up is simply bigger in frame than a floor glimpsed past it.
    #
    # This was not safe to do on a fused cloud, where a single mis-fitted sliver below the
    # floor would define the room. It is safe here because "confirmed" already means two or
    # more frames independently agreed the surface exists.
    horiz = [m for m in conf if m.kind in ("floor", "ceiling", "horizontal")
             and abs(float(m.normal @ g)) > par]
    if len(horiz) >= 2:
        def height(m: ModelPlane) -> float:
            # Signed height along gravity, sign-agnostic in the fitted normal.
            return -m.d / float(m.normal @ g)

        # Only surfaces the contributing frames actually agree on are eligible to bound the
        # room. Without this the ladder is full of averages-over-nothing.
        agreed = [m for m in horiz if m.spread_m() <= MAX_SPREAD_M] or horiz
        ranked = sorted(agreed, key=height)

        # FLOOR and CEILING are chosen by DIFFERENT rules, because they fail differently.
        #
        # Floor: the LOWEST agreed surface. Nothing in a room sits below the floor - furniture
        # is always above it - so "lowest" is a physical guarantee, not a heuristic. Choosing
        # by support or inliers instead picks the bed, which is closer to the camera and
        # therefore larger in frame: in Room 1 the bed carried 38k inliers against the floor's
        # 14k and won every time.
        #
        # Ceiling: the BEST-SUPPORTED agreed surface above the camera, NOT the highest. There
        # is no furniture between the camera and the ceiling, so occlusion is not the problem
        # there; fragmentation is. Room 3 produced three "ceiling" planes at +1.18, +1.42 and
        # +1.61 m, and taking the highest overshot by ~20 cm. The true ceiling is the one most
        # frames independently landed on.
        f = ranked[0]
        above = [m for m in ranked if height(m) > height(f) + 1.2]
        if not above:
            return out
        c = max(above, key=lambda m: (m.support, m.inliers_total))
        sep = height(c) - height(f)
        out["ceiling_height"] = float(sep)
        out["floor_support"] = f.support
        out["ceiling_support"] = c.support
        out["floor_spread_m"] = round(f.spread_m(), 4)
        out["ceiling_spread_m"] = round(c.spread_m(), 4)
        out["floor_height"] = round(height(f), 4)
        out["ceiling_height_abs"] = round(height(c), 4)
        # Everything between them: usually furniture, and worth reporting rather than hiding,
        # because a surface here is exactly what used to be mistaken for the floor.
        out["intermediate_horizontals"] = [
            {"height": round(height(m), 3), "support": m.support,
             "inliers": m.inliers_total, "kind": m.kind}
            for m in ranked[1:-1]
        ]

    walls = [m for m in conf if m.kind == "wall"]
    pairs = []
    for i in range(len(walls)):
        for j in range(i + 1, len(walls)):
            a, b = walls[i], walls[j]
            dot = float(a.normal @ b.normal)
            if abs(dot) < par:
                continue
            sep = abs(a.d - (b.d if dot > 0 else -b.d))
            if sep < 1.2:
                continue
            pairs.append({"separation_m": round(sep, 4),
                          "support": a.support + b.support,
                          "inliers": a.inliers_total + b.inliers_total})
    out["wall_pairs"] = sorted(pairs, key=lambda p: -p["support"])[:6]
    return out

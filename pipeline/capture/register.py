"""Register a set of unordered photos into one coordinate frame, and prove it worked.

The photo tier has no poses, so every frame is measured in its own camera frame and the
results disagree - most damagingly when different frames see different surfaces, which is how
a bed came to be fitted as a floor. Fusion fixes that, and fusion needs poses.

METHOD: 3D-3D similarity alignment, not PnP.

Both frames have depth, so a matched feature gives a 3D point in *each* camera's frame. That
turns registration into: find (s, R, t) with

    P_a  ~=  s * R * P_b  +  t

which is Umeyama's closed-form solution, wrapped in RANSAC. PnP would use 3D points from one
frame and 2D pixels from the other, throwing away half the depth we already paid for.

The scale term is the reason this is worth doing rather than a detail. `s` measures how much
frame b's depth scale disagrees with frame a's, pairwise, with no ground truth involved. Our
entire error budget says per-frame depth scale dominates, and until now the only way to see it
was against a tape measure. A rigid-only fit would silently absorb that disagreement into a
wrong translation.

VERIFICATION: the same corner, seen twice, must land in the same place.

If a feature is at P_a in frame a and P_b in frame b, then after registration
`T_a · P_a` and `T_b · P_b` are two estimates of one physical point. The distance between them
is a direct, ground-truth-free measure of whether the alignment is real. Reported per pair and
overall, because a fused cloud built on bad poses looks like a reconstruction and is not one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np

from pipeline.types import Frame

MIN_MATCHES = 12                # below this a pair is not worth fitting
MIN_INLIERS = 10                # below this the fit is not trusted
RANSAC_ITERS = 300
INLIER_THRESH_M = 0.10          # 3D residual for a correspondence to count as an inlier
SCALE_BOUNDS = (0.5, 2.0)       # a pair disagreeing by more than 2x is a bad match, not scale
SIFT_FEATURES = 3000
RATIO = 0.75


@dataclass
class PairMatch:
    i: int
    j: int
    n_matches: int = 0
    n_inliers: int = 0
    scale: float = 1.0          # frame j's depth scale relative to frame i
    T_ij: Optional[np.ndarray] = None      # 4x4 mapping a point in j's frame into i's
    residual_m: float = float("nan")       # median 3D residual over inliers
    pts_i: Optional[np.ndarray] = None     # inlier 3D points, frame i
    pts_j: Optional[np.ndarray] = None     # same features, frame j
    px_i: Optional[np.ndarray] = None      # their pixels, for the correspondence overlay
    px_j: Optional[np.ndarray] = None

    @property
    def ok(self) -> bool:
        return self.T_ij is not None and self.n_inliers >= MIN_INLIERS


def umeyama(A: np.ndarray, B: np.ndarray, with_scale: bool = True):
    """Least-squares similarity transform mapping B onto A: A ~= s*R*B + t.

    Umeyama's closed form. The reflection guard on the SVD matters: without it a degenerate
    or noisy correspondence set can return a mirrored "rotation" with det -1, which fits the
    points and describes a physically impossible camera.
    """
    mu_a, mu_b = A.mean(axis=0), B.mean(axis=0)
    Ac, Bc = A - mu_a, B - mu_b
    H = (Bc.T @ Ac) / len(A)
    U, S, Vt = np.linalg.svd(H)
    D = np.eye(3)
    if np.linalg.det(Vt.T @ U.T) < 0:
        D[2, 2] = -1.0
    R = Vt.T @ D @ U.T
    if with_scale:
        var_b = float((Bc ** 2).sum() / len(B))
        s = float(np.trace(np.diag(S) @ D) / var_b) if var_b > 1e-12 else 1.0
    else:
        s = 1.0
    t = mu_a - s * (R @ mu_b)
    return s, R, t


def _ransac_similarity(A: np.ndarray, B: np.ndarray, rng: np.random.Generator):
    """RANSAC over Umeyama. Returns (s, R, t, inlier_mask) or None."""
    n = len(A)
    if n < 3:
        return None
    best_mask, best_count = None, 0
    for _ in range(RANSAC_ITERS):
        idx = rng.choice(n, 3, replace=False)
        try:
            s, R, t = umeyama(A[idx], B[idx])
        except np.linalg.LinAlgError:
            continue
        if not (SCALE_BOUNDS[0] < s < SCALE_BOUNDS[1]):
            continue
        resid = np.linalg.norm(A - (s * (B @ R.T) + t), axis=1)
        mask = resid < INLIER_THRESH_M
        c = int(mask.sum())
        if c > best_count:
            best_count, best_mask = c, mask
    if best_mask is None or best_count < MIN_INLIERS:
        return None
    # Refit on all inliers: the winning hypothesis came from 3 points and is noisy.
    s, R, t = umeyama(A[best_mask], B[best_mask])
    return s, R, t, best_mask


def _features(frame: Frame):
    gray = cv2.cvtColor(frame.image, cv2.COLOR_RGB2GRAY)
    return cv2.SIFT_create(nfeatures=SIFT_FEATURES).detectAndCompute(gray, None)


def _backproject(kp_xy: np.ndarray, depth: np.ndarray, K: np.ndarray):
    """Pixels -> 3D camera points, dropping any without valid depth. Returns (P, keep)."""
    h, w = depth.shape[:2]
    u = np.clip(np.round(kp_xy[:, 0]).astype(int), 0, w - 1)
    v = np.clip(np.round(kp_xy[:, 1]).astype(int), 0, h - 1)
    z = depth[v, u]
    keep = np.isfinite(z) & (z > 0.2) & (z < 12.0)
    fx, fy, cx, cy = K[0, 0], K[1, 1], K[0, 2], K[1, 2]
    P = np.stack([(kp_xy[:, 0] - cx) * z / fx, (kp_xy[:, 1] - cy) * z / fy, z], axis=1)
    return P, keep


def match_pair(frames: list[Frame], i: int, j: int, feats,
               rng: np.random.Generator) -> PairMatch:
    """Match two frames and fit the similarity transform between them."""
    pm = PairMatch(i=i, j=j)
    (ki, di), (kj, dj) = feats[i], feats[j]
    if di is None or dj is None:
        return pm

    good = [m for m, n in cv2.BFMatcher().knnMatch(di, dj, k=2) if m.distance < RATIO * n.distance]
    pm.n_matches = len(good)
    if len(good) < MIN_MATCHES:
        return pm

    xy_i = np.array([ki[m.queryIdx].pt for m in good], dtype=np.float64)
    xy_j = np.array([kj[m.trainIdx].pt for m in good], dtype=np.float64)

    Pi, keep_i = _backproject(xy_i, frames[i].depth, frames[i].K)
    Pj, keep_j = _backproject(xy_j, frames[j].depth, frames[j].K)
    keep = keep_i & keep_j
    if int(keep.sum()) < MIN_MATCHES:
        return pm

    A, B = Pi[keep], Pj[keep]
    got = _ransac_similarity(A, B, rng)
    if got is None:
        return pm
    s, R, t, mask = got

    T = np.eye(4)
    T[:3, :3] = s * R
    T[:3, 3] = t
    pm.T_ij = T
    pm.scale = float(s)
    pm.n_inliers = int(mask.sum())
    pm.pts_i, pm.pts_j = A[mask], B[mask]
    pm.px_i, pm.px_j = xy_i[keep][mask], xy_j[keep][mask]
    pm.residual_m = float(np.median(
        np.linalg.norm(pm.pts_i - (s * (pm.pts_j @ R.T) + t), axis=1)))
    return pm


def match_all(frames: list[Frame], seed: int = 0) -> list[PairMatch]:
    """Every pair. A room holds 2-8 photos, so 28 pairs at worst - exhaustive is fine here,
    and it avoids assuming the capture order says anything about which views overlap."""
    rng = np.random.default_rng(seed)
    feats = [_features(f) for f in frames]
    out = []
    for i in range(len(frames)):
        for j in range(i + 1, len(frames)):
            out.append(match_pair(frames, i, j, feats, rng))
    return out


@dataclass
class Registration:
    poses: list[Optional[np.ndarray]]        # 4x4 camera-to-world, None if unregistered
    scales: list[float]                      # per-frame depth scale relative to the reference
    reference: int
    n_registered: int
    edges_used: list[tuple[int, int]] = field(default_factory=list)
    residual_median_m: float = float("nan")
    residual_p90_m: float = float("nan")
    per_pair: list[dict] = field(default_factory=list)

    @property
    def summary(self) -> str:
        return (f"{self.n_registered}/{len(self.poses)} registered from ref {self.reference}, "
                f"median residual {self.residual_median_m * 100:.1f} cm")


def register(frames: list[Frame], pairs: list[PairMatch]) -> Registration:
    """Chain pairwise transforms into one frame via a maximum-support spanning tree.

    Sequential chaining is wrong for photos: they are unordered, and the strongest overlap is
    often not between consecutive files. So edges are taken strongest-first (Kruskal-style),
    which keeps the composed transforms short and well-supported - error accumulates along the
    tree, so a tree built from the best edges accumulates the least.

    Frames that no good edge reaches stay None. That is deliberate: an unregistered frame
    dropped into the world at identity would pile its whole geometry onto the reference camera
    and corrupt every plane fitted afterwards.
    """
    n = len(frames)
    good = sorted([p for p in pairs if p.ok], key=lambda p: -p.n_inliers)

    # Reference = the frame with the most inlier support, so the tree grows from the
    # best-connected view rather than from whichever file sorted first.
    support = np.zeros(n)
    for p in good:
        support[p.i] += p.n_inliers
        support[p.j] += p.n_inliers
    ref = int(np.argmax(support)) if good else 0

    poses: list[Optional[np.ndarray]] = [None] * n
    scales = [1.0] * n
    poses[ref] = np.eye(4)
    edges: list[tuple[int, int]] = []

    changed = True
    while changed:
        changed = False
        for p in good:
            for a, b in ((p.i, p.j), (p.j, p.i)):
                if poses[a] is not None and poses[b] is None:
                    # T_ij maps a point in j's frame into i's frame.
                    T = p.T_ij if (a, b) == (p.i, p.j) else np.linalg.inv(p.T_ij)
                    poses[b] = poses[a] @ T
                    scales[b] = scales[a] * (p.scale if (a, b) == (p.i, p.j) else 1.0 / p.scale)
                    edges.append((a, b))
                    changed = True

    reg = Registration(poses=poses, scales=scales, reference=ref,
                       n_registered=sum(1 for p in poses if p is not None),
                       edges_used=edges)

    # Verification: the same feature, seen in two frames, must land in the same world point.
    res_all: list[float] = []
    for p in pairs:
        if not p.ok or poses[p.i] is None or poses[p.j] is None:
            continue
        wi = (poses[p.i][:3, :3] @ p.pts_i.T).T + poses[p.i][:3, 3]
        wj = (poses[p.j][:3, :3] @ p.pts_j.T).T + poses[p.j][:3, 3]
        d = np.linalg.norm(wi - wj, axis=1)
        res_all.extend(d.tolist())
        reg.per_pair.append({
            "i": p.i, "j": p.j, "inliers": p.n_inliers,
            "pair_scale": round(p.scale, 4),
            "pair_residual_cm": round(p.residual_m * 100, 2),
            "world_residual_median_cm": round(float(np.median(d)) * 100, 2),
        })
    if res_all:
        arr = np.array(res_all)
        reg.residual_median_m = float(np.median(arr))
        reg.residual_p90_m = float(np.percentile(arr, 90))
    return reg

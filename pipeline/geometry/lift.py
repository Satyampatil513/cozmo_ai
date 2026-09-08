"""Depth map -> oriented point cloud.

The bridge between the learned half of the pipeline and the deterministic half. A depth map
plus intrinsics is a 3D point per pixel; the plane fitter downstream wants those points and,
ideally, a normal for each of them.

Normals are computed here rather than taken from a model, for two reasons. They are available
on every tier this way - LiDAR depth and predicted depth are both just depth - and they come
out consistent with the very points the planes will be fitted to, where a separately predicted
normal field can disagree with its own depth map and quietly poison the RANSAC that trusts it.
"""
from __future__ import annotations

import numpy as np

# A pixel's normal is only meaningful if its neighbours lie on the same surface. Across a
# depth discontinuity - a doorway, the edge of a table - the cross product connects two
# unrelated surfaces and yields a normal pointing nowhere real. Neighbours further apart than
# this fraction of the local depth are treated as a different surface.
DISCONTINUITY_REL = 0.02

# Depth outside this band is dropped. Phone depth is unreliable very close and effectively
# unconstrained far away, and a handful of 40-metre points wreck the plane RANSAC's inlier
# statistics far out of proportion to their number.
MIN_DEPTH_M = 0.2
MAX_DEPTH_M = 12.0


def pixel_rays(K: np.ndarray, h: int, w: int) -> np.ndarray:
    """Unit-z camera rays for every pixel: (h, w, 3), with z == 1."""
    u = np.arange(w, dtype=np.float64)
    v = np.arange(h, dtype=np.float64)
    uu, vv = np.meshgrid(u, v)
    x = (uu - K[0, 2]) / K[0, 0]
    y = (vv - K[1, 2]) / K[1, 1]
    return np.stack([x, y, np.ones_like(x)], axis=-1)


def depth_to_points(depth: np.ndarray, K: np.ndarray) -> np.ndarray:
    """(h, w) metric depth -> (h, w, 3) camera-frame points.

    Depth is along the optical axis (z), not along the ray, which is the convention every
    metric depth model here emits. Treating it as ray length instead would stretch the scene
    radially outward from the image centre - a distortion that leaves the centre of the room
    correct and pushes the corners out, so it survives casual inspection.
    """
    return pixel_rays(K, *depth.shape[:2]) * depth[..., None]


def normals_from_points(P: np.ndarray, depth: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per-pixel normals by central differences on the point grid.

    Returns (normals, valid). Normals point toward the camera, which gives the plane fitter a
    consistent orientation convention that SVD alone cannot supply.
    """
    h, w = depth.shape[:2]
    valid = np.isfinite(depth) & (depth > MIN_DEPTH_M) & (depth < MAX_DEPTH_M)

    dx = np.zeros_like(P)
    dy = np.zeros_like(P)
    dx[:, 1:-1] = P[:, 2:] - P[:, :-2]
    dy[1:-1, :] = P[2:, :] - P[:-2, :]

    n = np.cross(dx, dy)
    ln = np.linalg.norm(n, axis=-1, keepdims=True)
    with np.errstate(invalid="ignore", divide="ignore"):
        n = np.where(ln > 1e-12, n / ln, 0.0)

    # Point normals back toward the camera (camera sits at the origin looking down +z).
    flip = np.sum(n * P, axis=-1) > 0
    n[flip] *= -1.0

    # Kill normals that straddle a depth discontinuity.
    ok = np.zeros((h, w), dtype=bool)
    ok[1:-1, 1:-1] = True
    d = depth
    with np.errstate(invalid="ignore"):
        jump_x = np.zeros((h, w), dtype=bool)
        jump_y = np.zeros((h, w), dtype=bool)
        jump_x[:, 1:-1] = np.abs(d[:, 2:] - d[:, :-2]) > DISCONTINUITY_REL * d[:, 1:-1] * 2
        jump_y[1:-1, :] = np.abs(d[2:, :] - d[:-2, :]) > DISCONTINUITY_REL * d[1:-1, :] * 2
    valid = valid & ok & ~jump_x & ~jump_y & (ln[..., 0] > 1e-12)
    return n, valid


def lift(depth: np.ndarray, K: np.ndarray, T_wc: np.ndarray | None = None,
         stride: int = 2, max_points: int | None = 120_000,
         rng: np.random.Generator | None = None,
         return_pixels: bool = False):
    """Depth map -> (points Nx3, normals Nx3), optionally transformed into world frame.

    `stride` subsamples the pixel grid before anything else. A 1024x768 depth map is 786k
    points, which is far more than plane fitting needs and makes RANSAC needlessly slow;
    surfaces are smooth, so every second pixel carries almost the same information.

    With `return_pixels`, also returns an Nx2 array of the (row, col) each surviving point
    came from, in the ORIGINAL depth grid. That mapping is what lets a plane's inliers be
    painted back onto the photo they came from, which turns "frame 12 abstained, score
    0.0002" into a picture of which surface the selector actually chose. Carried through the
    same subsample-mask-decimate sequence as the points, so it cannot drift out of step.
    """
    P = depth_to_points(depth, K)
    n, valid = normals_from_points(P, depth)

    h, w = depth.shape[:2]
    rows, cols = np.meshgrid(np.arange(h), np.arange(w), indexing="ij")
    pix = np.stack([rows, cols], axis=-1)

    P = P[::stride, ::stride].reshape(-1, 3)
    n = n[::stride, ::stride].reshape(-1, 3)
    pix = pix[::stride, ::stride].reshape(-1, 2)
    valid = valid[::stride, ::stride].reshape(-1)

    P, n, pix = P[valid], n[valid], pix[valid]

    if max_points is not None and len(P) > max_points:
        rng = rng or np.random.default_rng(0)
        idx = rng.choice(len(P), max_points, replace=False)
        P, n, pix = P[idx], n[idx], pix[idx]

    if T_wc is not None:
        R, t = T_wc[:3, :3], T_wc[:3, 3]
        P = P @ R.T + t
        n = n @ R.T

    if return_pixels:
        return P, n, pix
    return P, n

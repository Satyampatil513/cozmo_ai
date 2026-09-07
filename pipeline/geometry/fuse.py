"""Multi-view fusion: many posed depth frames -> one world-frame point cloud.

This is what single-frame estimation cannot do. A photo sees part of a room from one place,
so it can never close a polygon and its scale error is whatever that one frame's depth
happened to be. Fusing posed frames gives one cloud covering the whole room, from which a
single floor plane, a single ceiling plane and a closed wall polygon are fitted once.

What fusion does and does not fix, stated up front because it is easy to overclaim:

  it DOES reduce the random, per-frame component of the error, as 1/sqrt(N) over frames, and
  it is the only route to a room polygon or a stitched multi-room plan at all.

  it does NOT remove a systematic bias shared by every frame of a scene. If a depth model is
  8% long everywhere in one room, the average of forty such frames is still 8% long. Measured
  on the photo tier: within-room SD was 4-6% while the between-room bias was 20-60%.

Two reduction steps, both deliberately cheap and deterministic:

  voxel averaging       one point per occupied voxel, at the mean of its members. This both
                        bounds memory (a 40-frame LiDAR capture is ~2M points) and averages
                        the range noise of every frame that saw that voxel.
  sparse-voxel removal  drop voxels seen by too few points. Real surfaces are observed many
                        times from many frames; flying pixels at depth discontinuities and
                        stray returns off glass are seen once. This is a multi-view outlier
                        test that a single frame cannot perform, and it is the main reason
                        fusion improves plane fitting rather than merely enlarging the cloud.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from pipeline.geometry.lift import lift
from pipeline.types import Frame

DEFAULT_VOXEL_M = 0.02          # 2 cm: below LiDAR noise, well under any gate we chase
DEFAULT_MIN_HITS = 2            # a voxel seen once is not evidence of a surface


@dataclass
class FusedCloud:
    points: np.ndarray          # Nx3, world frame
    normals: np.ndarray         # Nx3, world frame
    n_frames: int
    n_raw: int                  # points before voxel reduction
    n_voxels: int               # occupied voxels before the sparse filter
    dropped_sparse: int

    @property
    def summary(self) -> str:
        return (f"{self.n_frames} frames, {self.n_raw:,} raw -> {len(self.points):,} points "
                f"({self.dropped_sparse:,} sparse voxels dropped)")


def voxel_reduce(points: np.ndarray, normals: np.ndarray,
                 voxel_m: float = DEFAULT_VOXEL_M,
                 min_hits: int = DEFAULT_MIN_HITS) -> tuple[np.ndarray, np.ndarray, int, int]:
    """One averaged point per occupied voxel, dropping voxels with too few observations.

    Implemented with a lexicographic sort over integer voxel keys rather than a hash map: it
    is a few lines of numpy, has no dependency, and is deterministic, which matters because
    the benchmark has to regenerate byte-identical results.
    """
    if len(points) == 0:
        return points, normals, 0, 0

    keys = np.floor(points / voxel_m).astype(np.int64)
    # Pack the 3D index into one sortable value. Offset keeps negatives non-negative.
    off = keys.min(axis=0)
    span = (keys.max(axis=0) - off + 1).astype(np.int64)
    flat = ((keys[:, 0] - off[0]) * span[1] * span[2]
            + (keys[:, 1] - off[1]) * span[2]
            + (keys[:, 2] - off[2]))

    order = np.argsort(flat, kind="stable")
    flat_s = flat[order]
    uniq, start, counts = np.unique(flat_s, return_index=True, return_counts=True)
    n_voxels = len(uniq)

    keep = counts >= min_hits
    dropped = int((~keep).sum())

    sums_p = np.add.reduceat(points[order], start, axis=0)
    sums_n = np.add.reduceat(normals[order], start, axis=0)
    mean_p = sums_p[keep] / counts[keep, None]
    mean_n = sums_n[keep]
    ln = np.linalg.norm(mean_n, axis=1, keepdims=True)
    mean_n = np.where(ln > 1e-12, mean_n / ln, 0.0)

    return mean_p, mean_n, n_voxels, dropped


def fuse_frames(frames: list[Frame], stride: int = 2,
                voxel_m: float = DEFAULT_VOXEL_M,
                min_hits: int = DEFAULT_MIN_HITS,
                max_points_per_frame: int | None = 60_000) -> FusedCloud:
    """Lift every posed frame into the world frame and reduce to one cloud.

    Frames without a pose are skipped rather than dropped silently into the origin, which
    would pile an entire frame's geometry on top of the first camera position and corrupt
    every plane fitted afterwards.
    """
    P: list[np.ndarray] = []
    N: list[np.ndarray] = []
    used = 0
    for f in frames:
        if f.depth is None or f.K is None or f.T_wc is None:
            continue
        pts, nrm = lift(f.depth, f.K, T_wc=f.T_wc, stride=stride,
                        max_points=max_points_per_frame)
        if len(pts):
            P.append(pts)
            N.append(nrm)
            used += 1

    if not P:
        return FusedCloud(np.empty((0, 3)), np.empty((0, 3)), 0, 0, 0, 0)

    pts = np.concatenate(P)
    nrm = np.concatenate(N)
    n_raw = len(pts)
    pts, nrm, n_voxels, dropped = voxel_reduce(pts, nrm, voxel_m, min_hits)
    return FusedCloud(pts, nrm, used, n_raw, n_voxels, dropped)

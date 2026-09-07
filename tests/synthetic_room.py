"""Synthetic rooms with exactly known dimensions.

The geometry stage can be tested to convergence before a single real capture exists, because
its input is a point cloud and its output is a number we can check against the truth we
generated. That is worth doing first: if the deterministic half is wrong, no amount of
depth-model quality rescues it, and on real data the two failures are indistinguishable.

Noise here is deliberately shaped like the real thing rather than uniform:

- Per-point noise along the surface normal, which is what range noise looks like.
- A per-surface bias, because monocular metric depth is systematically off per surface -
  it does not resample cleanly per point, and averaging does not remove it. Modelling this
  is the whole reason the error budget is not just "noise / sqrt(N)".
- Clutter: furniture-like boxes standing off the walls, plus scattered outliers.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class TruthRoom:
    width: float
    depth: float
    height: float
    points: np.ndarray
    normals: np.ndarray
    shear: float = 0.0

    @property
    def corners(self) -> np.ndarray:
        """Floor-plan corners, sheared if the room is not rectangular."""
        w, d, k = self.width, self.depth, self.shear
        return np.array([[0.0, 0.0], [w, 0.0], [w + k * d, d], [k * d, d]])

    @property
    def wall_lengths(self) -> np.ndarray:
        c = self.corners
        return np.linalg.norm(np.roll(c, -1, axis=0) - c, axis=1)

    @property
    def floor_area(self) -> float:
        return self.width * self.depth        # shear preserves area

    @property
    def corner_angles_deg(self) -> np.ndarray:
        c = self.corners
        prev, nxt = np.roll(c, 1, axis=0), np.roll(c, -1, axis=0)
        a, b = prev - c, nxt - c
        a /= np.linalg.norm(a, axis=1, keepdims=True)
        b /= np.linalg.norm(b, axis=1, keepdims=True)
        return np.degrees(np.arccos(np.clip(np.sum(a * b, axis=1), -1, 1)))


def _grid(u_range, v_range, density):
    nu = max(2, int(u_range * density))
    nv = max(2, int(v_range * density))
    u = np.linspace(0, u_range, nu)
    v = np.linspace(0, v_range, nv)
    uu, vv = np.meshgrid(u, v, indexing="ij")
    return uu.ravel(), vv.ravel()


def make_room(
    width: float = 4.20,
    depth: float = 3.10,
    height: float = 2.65,
    density: float = 40.0,          # points per metre along each axis
    noise_m: float = 0.0,           # per-point sigma along the surface normal
    surface_bias_m: float = 0.0,    # per-surface constant offset, sigma
    clutter: bool = False,
    outlier_frac: float = 0.0,
    yaw_deg: float = 0.0,           # rotate so the room is not axis-aligned
    shear: float = 0.0,             # shear the floor plan so corners are NOT 90 degrees
    seed: int = 0,
) -> TruthRoom:
    """Build a rectangular room as an oriented point cloud with per-point normals."""
    rng = np.random.default_rng(seed)
    P, N = [], []

    def add(pts, normal):
        pts = np.asarray(pts, dtype=float)
        nrm = np.tile(np.asarray(normal, dtype=float), (len(pts), 1))
        if surface_bias_m > 0:
            pts = pts + rng.normal(0.0, surface_bias_m) * nrm
        if noise_m > 0:
            pts = pts + rng.normal(0.0, noise_m, size=(len(pts), 1)) * nrm
        P.append(pts)
        N.append(nrm)

    # floor / ceiling
    u, v = _grid(width, depth, density)
    add(np.stack([u, v, np.zeros_like(u)], 1), [0, 0, 1])
    add(np.stack([u, v, np.full_like(u, height)], 1), [0, 0, -1])
    # walls: x=0, x=width, y=0, y=depth
    u, v = _grid(depth, height, density)
    add(np.stack([np.zeros_like(u), u, v], 1), [1, 0, 0])
    add(np.stack([np.full_like(u, width), u, v], 1), [-1, 0, 0])
    u, v = _grid(width, height, density)
    add(np.stack([u, np.zeros_like(u), v], 1), [0, 1, 0])
    add(np.stack([u, np.full_like(u, depth), v], 1), [0, -1, 0])

    if clutter:
        # A wardrobe against one wall and a table in the middle: large planar surfaces that
        # are not the room. These are what a naive "largest plane is the wall" rule gets
        # wrong, and what the corner-overshoot guard exists to reject.
        u, v = _grid(1.2, 2.0, density)
        add(np.stack([np.full_like(u, 0.60), u + 0.4, v], 1), [-1, 0, 0])   # wardrobe front
        u, v = _grid(1.4, 0.8, density)
        add(np.stack([u + 1.5, v + 1.0, np.full_like(u, 0.75)], 1), [0, 0, 1])  # table top

    pts = np.concatenate(P)
    nrm = np.concatenate(N)

    if outlier_frac > 0:
        k = int(len(pts) * outlier_frac)
        stray = rng.uniform([0, 0, 0], [width, depth, height], size=(k, 3))
        sn = rng.normal(size=(k, 3))
        sn /= np.linalg.norm(sn, axis=1, keepdims=True)
        pts = np.concatenate([pts, stray])
        nrm = np.concatenate([nrm, sn])

    if shear:
        # Shear in the floor plane turns the box into a parallelepiped whose corners are
        # genuinely not 90 degrees. This is the case that catches a pipeline which forces a
        # Manhattan frame: hard snapping would "fix" these corners back to square and report
        # a room that does not exist. Shear maps planes to planes, so the scene stays valid;
        # normals transform by the inverse transpose, not by the shear itself.
        S = np.array([[1.0, shear, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
        pts = pts @ S.T
        nrm = nrm @ np.linalg.inv(S)
        nrm /= np.linalg.norm(nrm, axis=1, keepdims=True)

    if yaw_deg:
        a = np.radians(yaw_deg)
        R = np.array([[np.cos(a), -np.sin(a), 0], [np.sin(a), np.cos(a), 0], [0, 0, 1]])
        pts, nrm = pts @ R.T, nrm @ R.T

    return TruthRoom(width, depth, height, pts, nrm, shear)

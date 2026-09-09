"""Report figures for the LiDAR floor-plan-from-cloud path (technical report section 9).

For each LiDAR capture: the wall-band occupancy raster (what the geometry actually sees),
a 3D view of the fused cloud coloured by height above the floor, and a pointer to the
pipeline's own blueprint (`<out>/blueprint.png`, written by run.py). Writes:

    docs/report_assets/15_lidar_raster.png
    docs/report_assets/16_lidar_3d.png

Run:  python benchmark/scripts/lidar_plan_figures.py
"""
from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

import matplotlib                                                    # noqa: E402
matplotlib.use("Agg")
import matplotlib.pyplot as plt                                     # noqa: E402
from mpl_toolkits.mplot3d import Axes3D                             # noqa: E402,F401

from pipeline.capture.lidar import ARKIT_WORLD_UP                   # noqa: E402
from pipeline.geometry.fuse import fuse_frames                      # noqa: E402
from pipeline.geometry.planes import _floor_basis, fit_floor_ceiling  # noqa: E402
from pipeline.geometry import floorplan as F                        # noqa: E402

CAPTURES = [
    ("benchmark .r3d (one room)",
     os.path.join(ROOT, "benchmark/raw/lidar/2026-09-08--00-36-25.r3d"),
     os.path.join(ROOT, "out_L2/r3d/result.json")),
    ("single_scan_with_ceiling (multi-room)",
     r"C:\Users\sattu\Downloads\single_scan_with_ceiling",
     os.path.join(ROOT, "out_L2/scan/result.json")),
]
PAL = ["#4363d8", "#e6194B", "#3cb44b", "#f58231", "#911eb4", "#42d4f4", "#bfef45"]


def _load(path: str):
    if path.lower().endswith(".r3d"):
        from pipeline.capture.lidar import load as load_r3d
        scene = load_r3d(path, stride=30)
    else:
        from pipeline.capture.stray import load as load_stray
        scene = load_stray(path, stride=30)
    frames = [f for f in scene.rooms[0].frames if f.T_wc is not None and f.depth is not None]
    return frames


def _raster(points, normals, cam):
    got = fit_floor_ceiling(points, normals, threshold=0.03, gravity_prior=ARKIT_WORLD_UP,
                            camera_at_origin=False, min_score=0.0)
    g, floor, ceiling, _w, score = got
    g = g / np.linalg.norm(g)
    hgt = points @ g
    fh = float(np.median(points[floor.inliers] @ g))
    ch = (float(np.median(points[ceiling.inliers] @ g))
          if ceiling is not None and ceiling.n_inliers else float(np.percentile(hgt, 99)))
    if not (2.0 <= ch - fh <= 4.2):
        ch = fh + 2.2
    e1, e2 = _floor_basis(g)
    band = points[(hgt > fh + 0.15) & (hgt < ch - 0.15)]
    uv = np.stack([band @ e1, band @ e2], axis=1)
    mn = uv.min(0)
    cell = 0.04
    ij = np.floor((uv - mn) / cell).astype(int)
    nx, ny = int(ij[:, 0].max()) + 3, int(ij[:, 1].max()) + 3
    grid = np.zeros((ny, nx), int)
    np.add.at(grid, (ij[:, 1] + 1, ij[:, 0] + 1), 1)
    wall = grid >= max(3, int(np.percentile(grid[grid > 0], 55)))
    cuv = np.stack([cam @ e1, cam @ e2], axis=1)
    return wall, mn, cell, cuv, (ch - fh), score, g, e1, e2, fh


def _extrude(ax, poly_xy, h_lo, h_hi, color):
    """Draw one room as a 3D prism: floor polygon, ceiling polygon, and vertical wall edges."""
    p = np.asarray(poly_xy, float)
    n = len(p)
    ax.plot(np.r_[p[:, 0], p[0, 0]], np.r_[p[:, 1], p[0, 1]], h_lo, c=color, lw=1.6)
    ax.plot(np.r_[p[:, 0], p[0, 0]], np.r_[p[:, 1], p[0, 1]], h_hi, c=color, lw=1.0, alpha=0.7)
    for k in range(n):
        ax.plot([p[k, 0], p[k, 0]], [p[k, 1], p[k, 1]], [h_lo, h_hi], c=color, lw=0.8, alpha=0.5)


def main():
    import json
    caps = [c for c in CAPTURES if os.path.exists(c[1]) and os.path.exists(c[2])]
    if not caps:
        raise SystemExit("no LiDAR capture + result.json pair found - run "
                         "`python run.py <capture> --tier lidar --out out_L2/<name>` first")
    fig_r, ax_r = plt.subplots(1, len(caps), figsize=(7.2 * len(caps), 7), squeeze=False)
    fig_3, ax_3 = plt.subplots(1, len(caps), figsize=(7.2 * len(caps), 6.6),
                               subplot_kw={"projection": "3d"}, squeeze=False)
    ax_r, ax_3 = ax_r[0], ax_3[0]
    for i, (label, path, res_path) in enumerate(caps):
        frames = _load(path)
        fc = fuse_frames(frames, stride=2)
        cam = np.array([f.T_wc[:3, 3] for f in frames])
        wall, mn, cell, cuv, room_h, score, g, e1, e2, fh = _raster(fc.points, fc.normals, cam)

        ny, nx = wall.shape
        ext = [mn[0], mn[0] + nx * cell, mn[1], mn[1] + ny * cell]
        ax_r[i].imshow(wall, origin="lower", extent=ext, cmap="binary")
        ax_r[i].plot(cuv[:, 0], cuv[:, 1], ".", ms=2, c="#e6194B", alpha=0.5, label="camera path")
        ax_r[i].set_title(f"{label}\nwall occupancy raster (4 cm)  |  ceiling {room_h:.2f} m  "
                          f"|  plane score {score:.2f}", fontsize=10)
        ax_r[i].set_aspect("equal"); ax_r[i].set_xlabel("m")
        ax_r[i].legend(loc="lower right", fontsize=8)

        # 3D: the pipeline's own room polygons, extruded floor->ceiling.
        rooms = json.load(open(res_path))["rooms"][0]["sub_rooms"]
        allx = []
        for j, sr in enumerate(rooms):
            poly = np.asarray(sr["polygon"]["world_corners"], float)
            allx.append(poly)
            hh = sr.get("ceiling_height") or room_h
            _extrude(ax_3[i], poly, 0.0, hh, PAL[j % len(PAL)])
        allx = np.vstack(allx)
        ax_3[i].set_title(f"{label}\npipeline room polygons, extruded to each room's ceiling",
                          fontsize=10)
        ax_3[i].set_zlim(0, max(room_h * 1.1, 3.2))
        try:
            ax_3[i].set_box_aspect((np.ptp(allx[:, 0]), np.ptp(allx[:, 1]), max(room_h, 2.6)))
        except Exception:
            pass
        ax_3[i].view_init(elev=28, azim=-58)
        ax_3[i].set_xlabel("m"); ax_3[i].set_ylabel("m")

    for fig, name in ((fig_r, "15_lidar_raster.png"), (fig_3, "16_lidar_3d.png")):
        fig.tight_layout()
        out = os.path.join(ROOT, "docs", "report_assets", name)
        fig.savefig(out, dpi=95)
        print("wrote", out)


if __name__ == "__main__":
    main()

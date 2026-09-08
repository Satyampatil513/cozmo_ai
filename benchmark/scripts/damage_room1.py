"""Run the first-pass damage detector against a real photo-tier room and render the overlay.

    python benchmark/scripts/damage_room1.py --room "Room 1"

Damage detection is NOT wired into `run.py` (`pipeline/damage/detect.detect` still raises -
there is no staged-damage capture to validate a `measure.py` hook against). This script
reproduces the per-frame path just far enough to hand each fitted wall plane to
`detect_damage_on_wall`, then paints every region it returns back onto the source photo.

Thresholds are the module's unfitted defaults and there is no ground truth for this room, so
every region drawn here is a CANDIDATE, not a confirmed defect. The point of running it on a
real room is to see the false-positive load and the class confusion on real walls, which
synthetic tests cannot show. Writes `docs/report_assets/14_damage_room1.png` (technical
report section 8).

The raster block in `_region_pixels` is a faithful copy of `detect_damage_on_wall`'s own,
kept only so the overlay can colour the exact pixels each region claims; it imports the
module's constants and helpers rather than re-deriving them.
"""
from __future__ import annotations

import argparse
import os
import sys
import warnings

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
warnings.filterwarnings("ignore")

import matplotlib                                                    # noqa: E402
matplotlib.use("Agg")
import matplotlib.patches as mpatches                               # noqa: E402
import matplotlib.pyplot as plt                                     # noqa: E402

from pipeline.capture.depth_cache import infer_cached               # noqa: E402
from pipeline.capture.photo import WORK_PX, load                    # noqa: E402
from pipeline.damage.detect import (CELL_M, COLOR_MAD_K, CRACK_ASPECT_MIN,   # noqa: E402
                                    MIN_LONG_AXIS_M, MIN_REGION_CELLS,
                                    detect_damage_on_wall)
from pipeline.damage.rules import evaluate                          # noqa: E402
from pipeline.geometry.lift import lift                             # noqa: E402
from pipeline.geometry.openings import _label_empty, _wall_frame    # noqa: E402
from pipeline.geometry.planes import CAMERA_UP, fit_floor_ceiling   # noqa: E402

OUT = os.path.join(ROOT, "docs", "report_assets", "14_damage_room1.png")


class _CacheOnlyBackend:
    """Replays the on-disk depth cache and nothing else. Used when the real depth backend
    cannot be constructed (no weights / no torch) but every frame was already inferred."""

    name = "metric3d_v2_vit_small"

    def infer(self, rgb, K):
        raise RuntimeError(
            "depth cache miss and no live backend available - run `python "
            "benchmark/scripts/report.py --run` once to populate benchmark/cache/depth, "
            "or pass --backend with weights installed")


def _make_backend(name: str):
    try:
        from pipeline.capture.depth import get_backend
        return get_backend(name)
    except Exception as exc:                       # noqa: BLE001 - any import/weight failure
        print(f"  (live backend unavailable: {type(exc).__name__}; using cache only)")
        return _CacheOnlyBackend()


def _region_pixels(wall, pts_all, pix_all, image, gravity, cell_m=CELL_M):
    """Per-region source-pixel coordinates, mirroring `detect_damage_on_wall`'s raster so the
    overlay highlights exactly the cells that detector flags. Returns [{class, pixels Nx2}]."""
    rc = pix_all[wall.inliers]
    h, w = image.shape[:2]
    valid = (rc[:, 0] >= 0) & (rc[:, 0] < h) & (rc[:, 1] >= 0) & (rc[:, 1] < w)
    rc = rc[valid]
    pts = pts_all[wall.inliers][valid]
    if len(pts) < 500:
        return []
    colors = image[rc[:, 0], rc[:, 1]].astype(np.float64)

    u_axis, v_axis = _wall_frame(wall, gravity)
    uu, vv = pts @ u_axis, pts @ v_axis
    u0, u1, v0, v1 = uu.min(), uu.max(), vv.min(), vv.max()
    nu = int(np.ceil((u1 - u0) / cell_m)) + 1
    nv = int(np.ceil((v1 - v0) / cell_m)) + 1
    if nu * nv > 200_000 or nu < 3 or nv < 3:
        return []
    ui = np.clip(((uu - u0) / cell_m).astype(int), 0, nu - 1)
    vi = np.clip(((vv - v0) / cell_m).astype(int), 0, nv - 1)

    cell_color = np.full((nv, nu, 3), np.nan)
    cell_npts = np.zeros((nv, nu), dtype=int)
    order = np.lexsort((ui, vi))
    ui_s, vi_s, colors_s = ui[order], vi[order], colors[order]
    bounds = np.nonzero(np.diff(vi_s * (nu + 1) + ui_s))[0] + 1
    for s, e in zip(np.r_[0, bounds], np.r_[bounds, len(ui_s)]):
        cell_color[vi_s[s], ui_s[s]] = np.median(colors_s[s:e], axis=0)
        cell_npts[vi_s[s], ui_s[s]] = e - s

    occ = cell_npts >= 3
    if occ.sum() < MIN_REGION_CELLS:
        return []
    wall_median = np.median(cell_color[occ], axis=0)
    dev = np.linalg.norm(cell_color - wall_median, axis=-1)
    mad = 1.4826 * float(np.median(dev[occ]))
    thresh = max(8.0, COLOR_MAD_K * mad)
    anomalous = occ & (dev > thresh)

    labels, _n = _label_empty(~anomalous)
    cells_by_region: dict[int, list] = {}
    for cy in range(nv):
        for cx in range(nu):
            if anomalous[cy, cx]:
                cells_by_region.setdefault(labels[cy, cx], []).append((cy, cx))

    point_region = labels[vi, ui]
    out = []
    for region_id, cells in cells_by_region.items():
        if len(cells) < MIN_REGION_CELLS:
            continue
        cy = np.array([c[0] for c in cells])
        cx = np.array([c[1] for c in cells])
        long_axis = max((cx.max() - cx.min() + 1), (cy.max() - cy.min() + 1)) * cell_m
        short_axis = min((cx.max() - cx.min() + 1), (cy.max() - cy.min() + 1)) * cell_m
        if long_axis < MIN_LONG_AXIS_M:
            continue
        cls = ("crack" if long_axis / max(short_axis, 1e-6) >= CRACK_ASPECT_MIN
               else "water_stain")
        out.append({"class": cls, "pixels": rc[point_region == region_id]})
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--room", default="Room 1")
    ap.add_argument("--backend", default="metric3d_v2")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    room_dir = os.path.join(ROOT, "benchmark", "raw", "photo", args.room)
    scene = load(room_dir)
    room = scene.rooms[0]
    backend = _make_backend(args.backend)
    print(f"room {room.room_id!r}  tier={room.tier}  frames={len(room.frames)}")

    n = len(room.frames)
    cols = 3
    rows = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(6 * cols, 5.3 * rows))
    axes = np.atleast_1d(axes).ravel()
    for ax in axes[n:]:
        ax.axis("off")

    table = []
    for ax, f in zip(axes, room.frames):
        depth, cached = infer_cached(backend, f.image_path, f.image, f.K, WORK_PX,
                                     use_cache=not args.no_cache)
        name = os.path.basename(f.image_path)
        img = f.image
        if img.shape[:2] != depth.shape[:2]:
            from PIL import Image
            img = np.asarray(Image.fromarray(img).resize(
                (depth.shape[1], depth.shape[0]), Image.LANCZOS))

        pts, nrm, pix = lift(depth, f.K, stride=2, return_pixels=True)
        ax.imshow(img)
        ax.set_xticks([])
        ax.set_yticks([])

        got = fit_floor_ceiling(pts, nrm, threshold=0.05, gravity_prior=CAMERA_UP,
                                camera_at_origin=True)
        if got is None:
            ax.set_title(f"{name}: no planes fitted", fontsize=10)
            continue
        g, floor, _ceiling, walls, _score = got

        n_reg = 0
        for wi, wall in enumerate(walls):
            regs = detect_damage_on_wall(wall, f"{name}:w{wi}", "wall", pts, pix, img, g,
                                         floor)
            for r, v in zip(regs, _region_pixels(wall, pts, pix, img, g)):
                n_reg += 1
                col = "#e6194B" if r.damage_class == "crack" else "#4363d8"
                px = v["pixels"]
                if len(px):
                    ax.scatter(px[:, 1], px[:, 0], s=3, c=col, alpha=0.5, linewidths=0)
                    y0, x0, y1, x1 = (px[:, 0].min(), px[:, 1].min(),
                                     px[:, 0].max(), px[:, 1].max())
                    ax.add_patch(mpatches.Rectangle((x0, y0), x1 - x0, y1 - y0, fill=False,
                                                    edgecolor=col, lw=1.5))
                flag = evaluate(r, "wall")
                table.append((name, r.damage_class, r.long_axis_m, r.short_axis_m, r.area_m2,
                              r.height_above_floor_m, r.confidence, flag.get("rule_id", "")))
        ax.set_title(f"{name}  -  {len(walls)} wall plane(s), {n_reg} region(s)", fontsize=10)

    fig.suptitle("First-pass damage detector on " + room.room_id
                 + " (photo tier) - unfitted thresholds, no ground truth\n"
                 "red = classified crack,  blue = classified water_stain", fontsize=13)
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    fig.savefig(OUT, dpi=85)
    print(f"wrote {OUT}\n")

    print(f"{'frame':16s} {'class':12s} {'long':>6s} {'short':>6s} {'area':>7s} "
          f"{'h_flr':>7s} {'conf':>5s}  rule")
    for row in table:
        print(f"{row[0]:16s} {row[1]:12s} {row[2]:6.2f} {row[3]:6.2f} {row[4]:7.3f} "
              f"{row[5]:7.2f} {row[6]:5.2f}  {row[7]}")
    cr = sum(1 for r in table if r[1] == "crack")
    ws = sum(1 for r in table if r[1] == "water_stain")
    fired = sum(1 for r in table if r[7])
    print(f"\n{len(table)} regions across {len(room.frames)} frames  |  crack {cr}, "
          f"water_stain {ws}  |  concealed-damage rules fired: {fired}")


if __name__ == "__main__":
    main()

"""LiDAR tier: does pose-based multi-view fusion beat single-frame estimation?

    python benchmark/scripts/lidar_fusion.py benchmark/raw/lidar/<capture>.r3d

Runs the same capture two ways through the SAME downstream geometry:

  single    every sampled frame independently, in its own camera frame
  fused     all frames lifted into the ARKit world frame with Record3D's poses, voxel
            averaged, then fitted once

and reports both against tape measurements when a `measurements.md` sits beside the capture.

The comparison is the point. Fusion should reduce the spread across frames, because the
random component of per-frame error averages down; it should NOT remove a bias shared by
every frame. Reporting both makes that claim checkable rather than asserted.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import warnings

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
warnings.filterwarnings("ignore")

from pipeline.capture.lidar import ARKIT_WORLD_UP, load                    # noqa: E402
from pipeline.geometry.fuse import fuse_frames                             # noqa: E402
from pipeline.geometry.lift import lift                                    # noqa: E402
from pipeline.geometry.planes import fit_floor_ceiling                     # noqa: E402
from pipeline.geometry.walls import (                                      # noqa: E402
    ceiling_height, extract_walls, wall_pair_dimensions,
)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DIR = os.path.join(ROOT, "benchmark", "results")


def read_measurements(capture_path: str) -> dict:
    """Parse a free-text measurements.md sitting next to the capture.

    Deliberately forgiving about wording - it is written by whoever held the phone, not by
    us - but it never invents a value. A field it cannot find is simply absent, and the
    report then says the number is unverified rather than scoring against a guess.
    """
    d = os.path.dirname(capture_path)
    for name in ("measurements.md", "measurements.txt", "ground_truth.md"):
        p = os.path.join(d, name)
        if not os.path.isfile(p):
            continue
        txt = open(p, encoding="utf-8", errors="replace").read().lower()
        out: dict = {"_source": p}
        for key, pats in {
            "height_m": (r"(?:room\s*)?height\D{0,12}([\d.]+)\s*m",),
            "breadth_m": (r"(?:bredth|breadth|width)\D{0,12}([\d.]+)\s*m",),
            "length_m": (r"length\D{0,12}([\d.]+)\s*m",),
            "window_m": (r"window\D{0,12}([\d.]+)\s*m",),
            "door_m": (r"door\D{0,12}([\d.]+)\s*m",),
        }.items():
            for pat in pats:
                m = re.search(pat, txt)
                if m:
                    out[key] = float(m.group(1))
                    break
        return out
    return {}


def err_pct(got: float, truth: float | None) -> float | None:
    if truth is None or not np.isfinite(got) or truth == 0:
        return None
    return round((got - truth) / truth * 100, 2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("capture")
    ap.add_argument("--stride", type=int, default=30, help="frame subsampling")
    ap.add_argument("--single-n", type=int, default=12, help="frames for the single-frame arm")
    ap.add_argument("--voxel", type=float, default=0.02)
    args = ap.parse_args()

    if not os.path.isfile(args.capture):
        print(f"no such capture: {args.capture}", file=sys.stderr)
        return 2

    truth = read_measurements(args.capture)
    print(f"ground truth: {truth if truth else 'NONE FOUND (errors will be omitted)'}")

    t0 = time.time()
    scene = load(args.capture, stride=args.stride)
    frames = scene.rooms[0].frames
    print(f"loaded {len(frames)} frames in {time.time() - t0:.1f}s")

    # ---- arm 1: single frame, camera frame, no poses used ----------------------------
    t0 = time.time()
    singles = []
    for f in frames[:: max(1, len(frames) // args.single_n)][: args.single_n]:
        pts, nrm = lift(f.depth, f.K, stride=1)
        if len(pts) < 5000:
            continue
        got = fit_floor_ceiling(pts, nrm, threshold=0.03)      # camera-frame prior
        if got is None:
            continue
        _g, fl, ce, _wl, score = got
        if fl is None or ce is None:
            singles.append({"frame": f.image_path, "ceiling_m": None, "score": round(score, 4)})
            continue
        h, sp = ceiling_height(fl, ce, pts)
        singles.append({"frame": f.image_path,
                        "ceiling_m": round(float(h), 4) if np.isfinite(h) else None,
                        "spread_cm": round(float(sp) * 100, 1) if np.isfinite(sp) else None,
                        "score": round(float(score), 4)})
    single_s = time.time() - t0
    vals = np.array([s["ceiling_m"] for s in singles if s["ceiling_m"] is not None])
    print(f"\nsingle-frame arm: {len(vals)}/{len(singles)} reported in {single_s:.1f}s")
    if vals.size:
        print(f"  ceiling {vals.mean():.3f} +- {vals.std():.3f} m   "
              f"range {vals.min():.3f}..{vals.max():.3f}")

    # ---- arm 2: fused, world frame, poses used ---------------------------------------
    t0 = time.time()
    fc = fuse_frames(frames, stride=2, voxel_m=args.voxel)
    fuse_s = time.time() - t0
    print(f"\nfused: {fc.summary}  ({fuse_s:.1f}s)")

    t0 = time.time()
    got = fit_floor_ceiling(fc.points, fc.normals, threshold=0.03,
                            gravity_prior=ARKIT_WORLD_UP, camera_at_origin=False)
    fused = {"ceiling_m": None, "pairs": [], "polygon": None}
    if got is not None:
        g, fl, ce, wl, score = got
        fused["selection_score"] = round(float(score), 4)
        fused["n_walls"] = len(wl)
        fused["gravity"] = np.round(g, 4).tolist()
        if fl is not None and ce is not None:
            h, sp = ceiling_height(fl, ce, fc.points)
            fused["ceiling_m"] = round(float(h), 4)
            fused["ceiling_spread_cm"] = round(float(sp) * 100, 1)
        pairs = wall_pair_dimensions(wl, g, fc.points)
        fused["pairs"] = pairs[:4]
        geo = extract_walls(fc.points, fl, ce, walls=wl, gravity=g)
        if geo is not None:
            fused["polygon"] = {
                "corners": int(len(geo.corners)),
                "wall_lengths_m": np.round(np.sort(geo.wall_lengths), 3).tolist(),
                "floor_area_m2": round(float(geo.floor_area), 3),
            }
    fused_s = time.time() - t0
    print(f"geometry on fused cloud: {fused_s:.1f}s")

    # ---- report ----------------------------------------------------------------------
    th = truth.get("height_m")
    print(f"\n{'metric':<28} {'value':>12} {'truth':>8} {'err':>9}")
    print("-" * 62)
    if vals.size:
        print(f"{'single-frame ceiling (mean)':<28} {vals.mean():11.3f}m {str(th or '-'):>8} "
              f"{str(err_pct(vals.mean(), th)) + '%' if th else '-':>9}")
        print(f"{'single-frame ceiling (SD)':<28} {vals.std():11.3f}m {'':>8} {'':>9}")
    if fused["ceiling_m"] is not None:
        print(f"{'FUSED ceiling':<28} {fused['ceiling_m']:11.3f}m {str(th or '-'):>8} "
              f"{str(err_pct(fused['ceiling_m'], th)) + '%' if th else '-':>9}")

    dims = [truth.get("breadth_m"), truth.get("length_m")]
    dims = sorted([d for d in dims if d])
    for i, pr in enumerate(fused["pairs"][:2]):
        t = dims[i] if i < len(dims) else None
        # Pairs are ranked by support, truths sorted ascending - so this is indicative only.
        print(f"{'FUSED wall pair ' + str(i + 1):<28} {pr['separation_m']:11.3f}m "
              f"{str(t or '-'):>8} {str(err_pct(pr['separation_m'], t)) + '%' if t else '-':>9}")

    payload = {
        "capture": os.path.basename(args.capture),
        "frames_loaded": len(frames),
        "stride": args.stride,
        "ground_truth": truth,
        "single_frame": {"per_frame": singles,
                         "reported": int(vals.size),
                         "mean_m": round(float(vals.mean()), 4) if vals.size else None,
                         "sd_m": round(float(vals.std()), 4) if vals.size else None,
                         "seconds": round(single_s, 1)},
        "fused": {**fused,
                  "n_points": int(len(fc.points)),
                  "n_raw": int(fc.n_raw),
                  "seconds": round(fuse_s + fused_s, 1)},
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, "lidar_fusion.json")
    with open(out, "w") as fh:
        json.dump(payload, fh, indent=2)
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Report what a Record3D `.r3d` capture actually contains.

    python benchmark/scripts/inspect_r3d.py benchmark/raw/lidar/*.r3d

Run this on any new capture before trusting it. Record3D's export varies with app version,
device and settings, and the failure mode is not a crash - it is a missing pose list or a
different depth dtype quietly producing a plausible, wrong reconstruction.
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from pipeline.capture.lidar import ARKIT_WORLD_UP, inspect, load  # noqa: E402


def report(path: str, verbose: bool = True) -> int:
    info = inspect(path)
    print(f"\n{'=' * 72}\n{os.path.basename(path)}   "
          f"({os.path.getsize(path) / 1e6:.0f} MB)\n{'=' * 72}")
    print(f"  frames              {info.n_frames}")
    print(f"  RGB resolution      {info.rgb_size[0]}x{info.rgb_size[1]}"
          if info.rgb_size else "  RGB resolution      MISSING")
    print(f"  depth resolution    {info.depth_size[0]}x{info.depth_size[1]}"
          if info.depth_size else "  depth resolution    MISSING")
    print(f"  depth dtype         {info.depth_dtype or 'UNKNOWN'}")
    print(f"  depth units         {info.depth_units}")
    print(f"  fps                 {info.fps}")
    print(f"  camera type         {info.camera_type}")
    print(f"  confidence maps     {'yes (0/1/2)' if info.has_confidence else 'no'}")
    print(f"  poses               {'yes, per frame' if info.has_poses else 'NO'}")
    print(f"  per-frame intrinsics{' yes' if info.has_per_frame_intrinsics else ' no'}")

    if info.K_rgb is not None:
        K = info.K_rgb
        print(f"  intrinsics (RGB)    fx={K[0,0]:.2f} fy={K[1,1]:.2f} "
              f"cx={K[0,2]:.2f} cy={K[1,2]:.2f}")
        if info.rgb_size:
            w, h = info.rgb_size
            fov = 2 * np.degrees(np.arctan(np.hypot(w, h) / (2 * K[0, 0])))
            print(f"  implied diagonal FOV {fov:.1f} deg")

    if info.timestamps is not None and len(info.timestamps) > 1:
        t = info.timestamps
        print(f"  timestamps          {len(t)}, {t[0]:.3f}s .. {t[-1]:.3f}s "
              f"(duration {t[-1] - t[0]:.1f}s)")

    if info.missing:
        print(f"  MISSING KEYS        {', '.join(info.missing)}")
    for n in info.notes:
        print(f"  NOTE                {n}")

    if not verbose:
        return 0

    # Decode a small sample so the report reflects real data, not just the header.
    sc = load(path, stride=max(1, info.n_frames // 8), max_frames=8)
    fr = sc.rooms[0].frames
    print(f"\n  --- sampled {len(fr)} frames ---")
    d0 = fr[0].depth
    valid = np.isfinite(d0)
    print(f"  depth[0]            {d0.shape}  valid {valid.mean() * 100:.1f}%  "
          f"range {np.nanmin(d0):.2f}..{np.nanmax(d0):.2f} m")
    print(f"  scale source        {sc.rooms[0].scale.source}")
    print(f"  K (depth grid)      fx={fr[0].K[0,0]:.2f} cx={fr[0].K[0,2]:.2f} "
          f"cy={fr[0].K[1,2]:.2f}")

    if fr[0].T_wc is not None:
        P = np.array([f.T_wc[:3, 3] for f in fr])
        span = P.max(0) - P.min(0)
        print(f"  camera positions    span {np.round(span, 3)} m over the sample")
        # ARKit is Y-up: a walked capture spans far less vertically than horizontally.
        vertical = float(span[1])
        horizontal = float(max(span[0], span[2]))
        verdict = "consistent with Y-up" if vertical < horizontal else "UNEXPECTED"
        print(f"  vertical vs horizontal span  {vertical:.2f} m vs {horizontal:.2f} m  "
              f"-> {verdict}")
        print(f"  world up assumed    {ARKIT_WORLD_UP} (ARKit Y-up)")
    else:
        print("  camera positions    NO POSES - fusion unavailable, single-frame only")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="*",
                    default=["benchmark/raw/lidar/*.r3d", "benchmark/raw/dev_remote/*.r3d"])
    ap.add_argument("--brief", action="store_true", help="header only, do not decode frames")
    args = ap.parse_args()

    files: list[str] = []
    for p in args.paths:
        files += sorted(glob.glob(p)) if any(c in p for c in "*?[") else [p]
    files = [f for f in files if os.path.isfile(f)]

    if not files:
        print("no .r3d files found. Looked in:", ", ".join(args.paths))
        print("The loader is ready; drop a capture in benchmark/raw/lidar/ and re-run.")
        return 1

    for f in files:
        report(f, verbose=not args.brief)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Build the input-data archive used with a clone of the repository.

    python scripts/make_reproduction_bundle.py [--out cozmo_input_data.zip] [--lite]

Contents:
  benchmark/raw/          sensor logs - photo stills, video clips, the .r3d LiDAR scan
  benchmark/ground_truth/ laser survey (rooms, openings, room mapping)

`--lite` drops benchmark/raw/video (the two walkthrough clips, ~650 MB) - keep it for a
fast share when the video-tier numbers are not being re-checked.

The team-supplied LiDAR scans referenced in technical report section 9
(`single_room`, `single_scan_with_ceiling`, `single_scan_floor_only`) are large Stray
Scanner captures that live outside this repo; point `run.py --tier lidar` at their
directories directly. They are not bundled here.
"""
from __future__ import annotations

import argparse
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

INPUT_DIRS = [
    "benchmark/raw",
    "benchmark/ground_truth",
]
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "cozmo_input_data.zip"))
    ap.add_argument("--lite", action="store_true", help="omit benchmark/raw/video (~650 MB)")
    args = ap.parse_args()

    dirs = [d for d in INPUT_DIRS if not (args.lite and d == "benchmark/raw/video")]
    n, total = 0, 0
    with zipfile.ZipFile(args.out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for d in dirs:
            base = os.path.join(ROOT, d)
            if not os.path.isdir(base):
                print(f"  skip (missing): {d}")
                continue
            for root, _, files in os.walk(base):
                for f in files:
                    if f.lower() == "readme.md":
                        continue
                    fp = os.path.join(root, f)
                    z.write(fp, os.path.relpath(fp, ROOT))
                    n += 1
                    total += os.path.getsize(fp)
            print(f"  added {d}")
    size_mb = os.path.getsize(args.out) / 1e6
    print(f"\nwrote {args.out}  ({n} files, {total/1e6:.0f} MB raw -> {size_mb:.0f} MB zip)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

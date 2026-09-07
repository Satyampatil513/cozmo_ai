"""Benchmark harness.

    python benchmark.py --input benchmark/raw/room_01_living --ground-truth benchmark/ground_truth/room_01_living.csv

Prints one row per gate with the measured number, the gate threshold, and PASS or FAIL.
A failing gate is a result, not an error: the report shows every gate at every tier.
"""
from __future__ import annotations

import argparse

GATES = {
    "opening_width":       {"tolerance_m": 0.02, "min_fraction": 0.85, "tiers": ["lidar"]},
    "ceiling_height":      {"tolerance_m": 0.015, "tiers": ["lidar"]},
    "ceiling_spread":      {"tolerance_m": 0.010, "tiers": ["lidar"]},
    "repeatability_wall":  {"tolerance_m": 0.010, "tolerance_rel": 0.005, "tiers": ["lidar", "video", "photo"]},
    "wall_length_photo":   {"tolerance_rel": 0.08, "tiers": ["photo"]},
    "wall_length_video":   {"tolerance_rel": 0.03, "tiers": ["video"]},
    "footprint_photo":     {"tolerance_rel": 0.08, "tiers": ["photo"]},
    "room_overlap":        {"max_overlap_m2": 0.0, "tiers": ["photo", "video", "lidar"]},
    "interval_coverage":   {"target": 0.95, "tolerance": 0.05, "tiers": ["photo", "video", "lidar"]},
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--ground-truth", required=True)
    ap.add_argument("--tier", default=None)
    ap.parse_args()
    print("NOT BUILT: benchmark harness")
    for name, spec in GATES.items():
        print(f"  {name:22s} {spec}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

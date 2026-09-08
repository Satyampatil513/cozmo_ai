"""Drift-accountability ablation: the stitched footprint with plane-anchored correction ON and OFF.

    python benchmark/scripts/ablation.py

The brief's drift row: "an ablation shows the stitched footprint with it on and off. 'Poses
used as-is' is an automatic fail." This produces that ablation as a regenerable number.

WHAT IT RUNS ON, stated plainly. No real capture in this benchmark has produced a closed
multi-room stitch - every real cross-room edge is rejected by the verification gates, correctly
(see docs/TECHNICAL_REPORT.md sections 4 and 7). So the ablation runs on a SYNTHETIC two-room
flat with a known shared wall and a known rigid drift injected into the second room's poses -
exactly the posture tests/test_geometry.py used for the single-room pipeline before any real
multi-room capture existed. `stitch_posed_capture` is the same function run.py calls; only
`drift_correction` is toggled, which is the same toggle run.py exposes as `--no-drift-correction`.

Writes benchmark/results/drift_ablation.md.
"""
from __future__ import annotations

import os
import sys
import warnings

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
warnings.filterwarnings("ignore")

from pipeline.geometry.segment import segment_rooms                     # noqa: E402
from pipeline.stitching.stitch import stitch_posed_capture             # noqa: E402
from pipeline.types import RoomCapture                                 # noqa: E402
from tests.test_stitching import WORLD_UP, two_room_capture            # noqa: E402

OUT_MD = os.path.join(ROOT, "benchmark", "results", "drift_ablation.md")

# Each row: a label and the rigid drift injected into every room-B pose (yaw about world-up,
# then a fixed translation in metres). Row 0 is the control - no drift, correction must be
# close to a no-op.
CASES = [
    ("no drift (control)", 0.0, np.zeros(3)),
    ("small drift", 3.0, np.array([0.08, -0.04, 0.02])),
    ("large drift", 6.0, np.array([0.15, -0.08, 0.05])),
]


def run_case(label: str, yaw_deg: float, shift: np.ndarray, seed: int) -> dict | None:
    frames, _truth = two_room_capture(seed=seed, drift_yaw_deg=yaw_deg, drift_shift=shift)
    room = RoomCapture(room_id="flat", frames=frames, scale=None, tier="lidar")
    posed = [f for f in frames if f.T_wc is not None]
    segs = segment_rooms(posed, WORLD_UP)
    if len(segs) != 2:
        return {"label": label, "error": f"segmentation gave {len(segs)} segment(s), expected 2"}

    off = stitch_posed_capture(posed, room, segs, tier="lidar", drift_correction=False)
    on = stitch_posed_capture(posed, room, segs, tier="lidar", drift_correction=True)
    if off is None or on is None:
        return {"label": label, "error": "stitch not confirmed (no shared wall found)"}

    dc_off, dc_on = off["drift_correction"], on["drift_correction"]
    return {
        "label": label,
        "yaw_deg": yaw_deg,
        "shift_cm": [round(float(x) * 100, 1) for x in shift],
        "gap_before_cm": dc_off.get("shared_wall_gap_before_cm"),
        "gap_after_off_cm": dc_off.get("shared_wall_gap_after_cm"),
        "gap_after_on_cm": dc_on.get("shared_wall_gap_after_cm"),
        "footprint_off_m2": dc_off.get("footprint_after_m2"),
        "footprint_on_m2": dc_on.get("footprint_after_m2"),
    }


def fmt(v, unit=""):
    return "-" if v is None else f"{v:.1f}{unit}" if isinstance(v, float) else f"{v}{unit}"


def main() -> int:
    rows = [run_case(lbl, yaw, shift, seed=20 + i)
            for i, (lbl, yaw, shift) in enumerate(CASES)]

    L = ["# Drift ablation", "",
         "Regenerate: `python benchmark/scripts/ablation.py`", "",
         "Plane-anchored drift correction, ON vs OFF, on a synthetic two-room flat with a known",
         "shared wall and a known rigid drift injected into the second room's poses. Same",
         "`stitch_posed_capture` run.py calls; only `drift_correction` is toggled (run.py",
         "`--no-drift-correction`). No real capture has produced a closed multi-room stitch to",
         "ablate (technical report sections 4 and 7), so this is synthetic ground truth.", "",
         "`drift_correction=False` is \"poses used as-is\" for room placement - the brief's",
         "automatic-fail condition. The point of the row is that it visibly costs something.", "",
         "| Case | injected drift | shared-wall gap, no correction | gap, correction OFF | gap, correction ON | footprint OFF | footprint ON |",
         "|---|---|---|---|---|---|---|"]

    for r in rows:
        if "error" in r:
            L.append(f"| {r['label']} | - | {r['error']} | - | - | - | - |")
            continue
        drift = (f"yaw {r['yaw_deg']:.0f} deg, shift {r['shift_cm']} cm"
                 if r["yaw_deg"] or any(r["shift_cm"]) else "none")
        L.append(f"| {r['label']} | {drift} "
                 f"| {fmt(r['gap_before_cm'], ' cm')} "
                 f"| {fmt(r['gap_after_off_cm'], ' cm')} "
                 f"| {fmt(r['gap_after_on_cm'], ' cm')} "
                 f"| {fmt(r['footprint_off_m2'], ' m2')} "
                 f"| {fmt(r['footprint_on_m2'], ' m2')} |")

    L += ["",
          "**Reading it.** The shared-wall gap is the distance between the two rooms' copies of",
          "the wall they share - 0 for a perfect stitch. With correction OFF that gap is the raw",
          "injected drift; with it ON the gap closes. Where both rooms' polygons also close, the",
          "footprint column shows the union area moving with it. The control row (no injected",
          "drift) is the check that correction is near a no-op when there is nothing to correct.",
          ""]

    with open(OUT_MD, "w") as fh:
        fh.write("\n".join(L))
    print("\n".join(L))
    print(f"\nwrote {OUT_MD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Whole-property stitching.

Rooms arrive as independent polygons in their own local frames. Alignment uses the doorway
correspondences from the capture protocol: the doorway pair shot observes the same opening
from both sides, so the shared opening becomes a 2D constraint between two room frames.
Solved as a small pose graph over rooms (SE(2) per room, one constraint per doorway), then
a non-overlap check.

`drift_correction=False` skips the pose graph and composes poses as-is. This is the ablation
required by the brief, and "poses used as-is" is an automatic fail on that gate, so the
ablation exists to show the delta, not as a shipping mode.

NOT BUILT.
"""


def stitch(rooms, drift_correction: bool = True):
    raise NotImplementedError

"""Opening detection and sizing.

Two independent signals, fused:
  1. Geometric: depth holes and returns beyond the wall plane, i.e. the wall has a gap in it.
  2. Appearance: door and window detection in the RGB frames, projected onto the wall plane.

Detection itself is scored in the brief, and a phantom opening costs the same as a missed one,
so the fusion is deliberately conservative and each opening carries a detection confidence
that the benchmark thresholds rather than a hardcoded cutoff.

NOT BUILT.
"""


def detect_openings(walls, frames):
    raise NotImplementedError

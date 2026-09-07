"""Absolute scale recovery from the printed scale card.

Monocular reconstruction is scale-ambiguous. Rather than pretend otherwise, the capture
protocol places a known-size target in every room and this module turns it into metres.
Detection is ArUco (cv2.aruco), which gives a metric plane pose directly from a known marker
side length. Fallback if no marker is found in a room: monocular metric depth model, with a
wider sigma and `scale_source = monocular_metric` recorded in the output.

NOT BUILT.
"""
from pipeline.types import Scale

MARKER_SIDE_M = 0.150  # printed on assets/scale_card_A4.pdf


def recover(frames) -> Scale:
    raise NotImplementedError("scale card recovery")

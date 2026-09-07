"""Absolute scale recovery from the printed scale card.

Monocular reconstruction is scale-ambiguous. Rather than pretend otherwise, the capture
protocol places a known-size target in every room and this module turns it into metres.

Two separable jobs live here, and they have very different readiness:

`detect()` finds the marker in a single image and scores how usable that sighting is. It
needs nothing but the image, which makes it the one part of the photo/video metric chain
that can be validated the moment a capture lands - before any reconstruction exists. The
capture validator leans on it entirely.

`recover()` turns sightings into a metre scale factor. That genuinely needs the
reconstruction: the marker's corners get triangulated into the reconstructed frame, their
side length is measured in reconstruction units, and the scale factor is the known physical
side divided by that. Not built until the backbone is.

Fallback if no marker is found in a room: monocular metric depth, with a much wider sigma
and `scale_source = monocular_metric` recorded in the output so the thinner evidence is
visible downstream rather than hidden.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np

from pipeline.types import Scale

_SIDECAR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "assets", "scale_card.json",
)


def _load_card_spec() -> dict:
    """Read the card's true dimensions from the generator's sidecar.

    Deliberately not a constant in this file. The nominal 150 mm is not what gets printed -
    ArUco's bit-cell rounding lands it on 149.86 mm - and hardcoding the nominal value here
    would divide every photo- and video-tier measurement by a number that is wrong in one
    direction. scripts/make_scale_card.py owns that number; this reads it.
    """
    with open(_SIDECAR) as fh:
        spec = json.load(fh)
    return spec


_SPEC = _load_card_spec()
MARKER_SIDE_M: float = _SPEC["marker_side_mm"] / 1000.0
_DICT = getattr(cv2.aruco, _SPEC["dictionary"])
_MARKER_ID: int = _SPEC["marker_id"]

# A sighting has to be big enough to localise corners well and square-on enough that the
# corner refinement is not fighting perspective. Both thresholds are deliberately generous:
# this gate exists to catch a card that was photographed from across the room or at 60
# degrees, not to grade good sightings against each other.
MIN_APPARENT_SIDE_PX = 60.0
MAX_OBLIQUITY = 0.30
MIN_SHARPNESS = 120.0
CANONICAL_PX = 200            # marker is rectified to this before sharpness is measured


@dataclass
class Sighting:
    """One detection of the scale card in one image."""
    image_path: str
    corners: np.ndarray              # 4x2, clockwise from top-left of the marker
    apparent_side_px: float
    obliquity: float                 # 0 = square-on, ->1 = extremely oblique
    sharpness: float                 # variance of Laplacian inside the marker
    usable: bool
    reason: str = ""

    def to_json(self) -> dict:
        return {
            "image": os.path.basename(self.image_path),
            "apparent_side_px": round(self.apparent_side_px, 1),
            "obliquity": round(self.obliquity, 3),
            "sharpness": round(self.sharpness, 1),
            "usable": self.usable,
            "reason": self.reason,
        }


def _obliquity(quad: np.ndarray) -> float:
    """How far from square-on this sighting is, from the quad's own shape.

    A marker viewed head-on projects to a square. Two independent things break as the
    viewing angle grows, and we take the worse of them:

    - side ratio: the four sides stop being equal.
    - diagonal ratio: the two diagonals stop being equal.

    Both are needed. Diagonals alone are blind to a *symmetric* keystone - tilt a square
    about a horizontal axis and the trapezoid you get still has two equal diagonals - and
    that is the single most common real sighting, because the card is taped to a wall and
    photographed by someone not standing exactly square to it. Sides alone are the weaker
    signal under in-plane rotation. Neither needs camera intrinsics, which the photo tier
    does not reliably have.
    """
    sides = [float(np.linalg.norm(quad[i] - quad[(i + 1) % 4])) for i in range(4)]
    diags = [float(np.linalg.norm(quad[0] - quad[2])),
             float(np.linalg.norm(quad[1] - quad[3]))]
    if min(sides) <= 0.0 or max(diags) <= 0.0:
        return 1.0
    return max(1.0 - min(sides) / max(sides),
               1.0 - min(diags) / max(diags))


def _sharpness(gray: np.ndarray, quad: np.ndarray) -> float:
    """Laplacian variance on the marker, rectified to a canonical size first.

    Two corrections over the obvious version, both of which bit in testing:

    Measured on the marker rather than the whole frame, because a frame can be sharp
    overall while the card itself is motion-blurred, and it is the card's corners we are
    about to trust to sub-pixel precision.

    Rectified to a fixed size first, because raw Laplacian variance is not comparable
    across apparent sizes - it counts edge energy per pixel, so shrinking a marker
    concentrates its edges and *raises* the score. Left uncorrected, a card shot from
    across the room outscores one shot properly from two metres, which inverts the
    ranking this number exists to provide. Warping to a canonical square makes the
    threshold mean one thing at every distance.
    """
    dst = np.float32([[0, 0], [CANONICAL_PX, 0],
                      [CANONICAL_PX, CANONICAL_PX], [0, CANONICAL_PX]])
    try:
        H = cv2.getPerspectiveTransform(quad.astype(np.float32), dst)
        patch = cv2.warpPerspective(gray, H, (CANONICAL_PX, CANONICAL_PX))
    except cv2.error:
        return 0.0
    return float(cv2.Laplacian(patch, cv2.CV_64F).var())


def detect(image: np.ndarray, image_path: str = "") -> Optional[Sighting]:
    """Find the scale card in one image. Returns None if the marker is not present.

    Present-but-unusable is a different answer from absent, and the caller needs to tell
    them apart: a missing card means the operator forgot it, an unusable one means they
    photographed it badly. Those have different fixes, so a poor sighting comes back with
    `usable=False` and a reason rather than as None.
    """
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    detector = cv2.aruco.ArucoDetector(
        cv2.aruco.getPredefinedDictionary(_DICT), cv2.aruco.DetectorParameters()
    )
    corners, ids, _ = detector.detectMarkers(gray)
    if ids is None or _MARKER_ID not in ids.ravel():
        return None

    quad = corners[int(np.where(ids.ravel() == _MARKER_ID)[0][0])][0].astype(np.float64)
    sides = [float(np.linalg.norm(quad[i] - quad[(i + 1) % 4])) for i in range(4)]
    side_px = float(np.mean(sides))
    obl = _obliquity(quad)
    sharp = _sharpness(gray, quad)

    reasons = []
    if side_px < MIN_APPARENT_SIDE_PX:
        reasons.append(f"card too small in frame ({side_px:.0f}px, need {MIN_APPARENT_SIDE_PX:.0f}px) - shoot it closer")
    if obl > MAX_OBLIQUITY:
        reasons.append(f"card too oblique ({obl:.2f}) - shoot it square-on")
    if sharp < MIN_SHARPNESS:
        reasons.append(f"card blurred (sharpness {sharp:.0f}, need {MIN_SHARPNESS:.0f}) - hold still or shoot it closer")

    return Sighting(
        image_path=image_path,
        corners=quad,
        apparent_side_px=side_px,
        obliquity=obl,
        sharpness=sharp,
        usable=not reasons,
        reason="; ".join(reasons),
    )


def recover(frames) -> Scale:
    """Metre scale factor from triangulated marker corners. Needs the reconstruction."""
    raise NotImplementedError("scale card recovery: blocked on the pointmap backbone")

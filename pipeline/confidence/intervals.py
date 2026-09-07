"""Interval construction.

Intervals are NOT hand-tuned constants. They are built from an error model whose parameters
are fitted on our own benchmark residuals (see calibrate.py) and then widened by the scale
uncertainty of the tier. Confident garbage on thin input is an explicit scoring penalty in
the brief, so the default posture here is to widen, not to narrow.
"""
from __future__ import annotations

import math
from pipeline.types import Measurement, Scale

# Fitted on our own benchmark. Placeholder values until benchmark/scripts/fit_error_model.py
# has run: these MUST be replaced by fitted numbers before submission, and the report must
# say they were fitted rather than chosen.
ERROR_MODEL = {
    #  tier   : (relative sigma, absolute sigma in metres)
    "lidar": (0.004, 0.008),
    "video": (0.020, 0.020),
    "photo": (0.050, 0.040),
}

Z_95 = 1.96


def length_measurement(value_m: float, tier: str, scale: Scale, method: str) -> Measurement:
    rel, abs_m = ERROR_MODEL[tier]
    sigma = math.sqrt((rel * value_m) ** 2 + abs_m ** 2 + (scale.sigma * value_m) ** 2)
    half = Z_95 * sigma
    return Measurement(
        value=value_m,
        unit="m",
        interval=(max(0.0, value_m - half), value_m + half),
        confidence=0.95,
        method=f"{method}; sigma={sigma:.4f}m; scale={scale.source}",
    )


def area_measurement(value_m2: float, tier: str, scale: Scale, method: str) -> Measurement:
    """Area error is roughly double the relative length error, first order."""
    rel, _ = ERROR_MODEL[tier]
    rel_total = math.sqrt((2 * rel) ** 2 + (2 * scale.sigma) ** 2)
    half = Z_95 * rel_total * value_m2
    return Measurement(
        value=value_m2,
        unit="m2",
        interval=(max(0.0, value_m2 - half), value_m2 + half),
        confidence=0.95,
        method=f"{method}; rel_sigma={rel_total:.4f}; scale={scale.source}",
    )

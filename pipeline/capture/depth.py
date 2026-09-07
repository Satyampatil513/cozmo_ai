"""Metric depth from a single RGB image.

This is the one learned component in the measurement path, and it is deliberately isolated
behind a small interface so that swapping the model is a one-line change and the report can
name exactly which weights produced which numbers.

Why the interface takes intrinsics even though not every model uses them: a monocular depth
model has to assume a field of view in order to output metres, and models differ in whether
they infer that from the image or accept it. Metric3D-class models take K explicitly and are
noticeably better when it is correct; the Depth-Anything metric heads bake in an assumption.
Passing K uniformly means the better models can use it and the report can state whether the
number was informed by the real optics or by a prior.

Everything about scale accuracy lives or dies here. The error budget measured in
tests/test_geometry.py says wall error runs ~0.46% per centimetre of per-surface depth bias,
and that bias does not average out across frames of the same room - so this module's output
is the dominant error term in the entire photo and video tiers.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import numpy as np

# Weights are fetched by the model hub on first use and cached under HF_HOME. Kept small on
# purpose: the walk-in test runs live on the operator's machine, so a model that needs more
# VRAM than a laptop GPU has is not a solution regardless of its benchmark numbers.
DEFAULT_MODEL = "depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf"


@dataclass
class DepthResult:
    depth: np.ndarray            # (h, w) metres, aligned to the RGB passed in
    model: str
    used_intrinsics: bool
    device: str

    @property
    def summary(self) -> str:
        d = self.depth[np.isfinite(self.depth)]
        return (f"{self.model.split('/')[-1]} on {self.device}: "
                f"{d.min():.2f}-{d.max():.2f}m, median {np.median(d):.2f}m")


class DepthBackend:
    """Base interface. One method, so alternatives stay cheap to add."""

    name = "abstract"

    def infer(self, rgb: np.ndarray, K: Optional[np.ndarray] = None) -> DepthResult:
        raise NotImplementedError


class DepthAnythingV2Metric(DepthBackend):
    """Depth-Anything V2, indoor metric head, via transformers.

    Chosen as the first backend for reachability rather than peak accuracy: it installs from
    one package, has no build step, and the Small variant runs on CPU and inside 4GB of VRAM.
    It does NOT consume intrinsics, so it assumes a field of view; `used_intrinsics=False` is
    recorded on every result so this assumption is visible in the output rather than implied.

    The indoor-specific head matters. The general metric heads are fitted across indoor and
    outdoor data where the depth range spans two orders of magnitude, and they are measurably
    looser at room scale, which is the only scale we care about.
    """

    name = "depth_anything_v2_metric_indoor"

    def __init__(self, model_id: str = DEFAULT_MODEL, device: str | None = None):
        import torch
        from transformers import AutoImageProcessor, AutoModelForDepthEstimation

        self.torch = torch
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model_id = model_id
        self.processor = AutoImageProcessor.from_pretrained(model_id)
        self.model = AutoModelForDepthEstimation.from_pretrained(model_id).to(self.device)
        self.model.eval()

    def infer(self, rgb: np.ndarray, K: Optional[np.ndarray] = None) -> DepthResult:
        torch = self.torch
        h, w = rgb.shape[:2]
        inputs = self.processor(images=rgb, return_tensors="pt").to(self.device)

        with torch.no_grad():
            out = self.model(**inputs)

        # The model predicts at its own working resolution. Resample back to the exact RGB
        # grid, because the intrinsics we lift with belong to that grid: a depth map even one
        # pixel off in scale would tilt every plane slightly and bias every dimension.
        depth = torch.nn.functional.interpolate(
            out.predicted_depth.unsqueeze(1), size=(h, w),
            mode="bicubic", align_corners=False,
        ).squeeze().cpu().numpy().astype(np.float64)

        return DepthResult(depth=depth, model=self.model_id,
                           used_intrinsics=False, device=self.device)


_BACKENDS = {
    "depth_anything_v2_metric_indoor": DepthAnythingV2Metric,
}

_cache: dict[str, DepthBackend] = {}


def get_backend(name: str = "depth_anything_v2_metric_indoor", **kw) -> DepthBackend:
    """Fetch (and cache) a backend. Cached because loading weights dominates a short run."""
    if name not in _BACKENDS:
        raise ValueError(f"unknown depth backend {name!r}; have {sorted(_BACKENDS)}")
    key = name + repr(sorted(kw.items()))
    if key not in _cache:
        _cache[key] = _BACKENDS[name](**kw)
    return _cache[key]


def available() -> list[str]:
    return sorted(_BACKENDS)

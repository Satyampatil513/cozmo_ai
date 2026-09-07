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

import contextlib
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


@contextlib.contextmanager
def _force_device(device: str):
    """Redirect hardcoded CUDA tensor creation to `device` for the duration of a call.

    Metric3D's decode head builds its depth bins and mesh grid with an explicit
    device="cuda", so on a CPU-only install inference dies inside the model with
    "Torch not compiled with CUDA enabled" - nothing to do with our code or the weights.

    Patching torch's factory functions here rather than editing the cached hub checkout,
    because the checkout is a build artefact on one machine: a fix applied there would work
    locally and silently fail to reproduce for anyone who clones this repo, which is the
    opposite of what the reproduction bundle has to guarantee. The patch is scoped to the
    inference call and restored in a finally block.
    """
    if device.startswith("cuda"):
        yield
        return

    import torch

    factories = ("linspace", "zeros", "ones", "arange", "eye", "empty", "full", "tensor")
    saved = {name: getattr(torch, name) for name in factories}

    def rebind(orig):
        def wrapper(*args, **kwargs):
            d = kwargs.get("device")
            if d is not None and str(d).startswith("cuda"):
                kwargs["device"] = device
            return orig(*args, **kwargs)
        return wrapper

    try:
        for name, orig in saved.items():
            setattr(torch, name, rebind(orig))
        yield
    finally:
        for name, orig in saved.items():
            setattr(torch, name, orig)


class Metric3DV2(DepthBackend):
    """Metric3D v2, which consumes intrinsics explicitly.

    The reason to prefer this over an FOV-inferring head is its canonical camera transform.
    A monocular network cannot recover metres without knowing the field of view - the same
    image at the same pixel dimensions means a different physical scene depending on the
    lens. Metric3D handles that by rescaling the image so its effective focal length matches
    a fixed canonical value (1000 px), predicting depth in that canonical space, then undoing
    the transform by the same ratio. The prediction is therefore conditioned on the real
    optics rather than on a guess.

    That matters here specifically: the baseline run with an FOV-inferring head was +26% on
    ceiling height, and the error varied per room from +2% to +59% - the signature of a scale
    assumption that fits some scenes and not others.

    Preprocessing follows the reference recipe exactly. It is fiddly and every step is
    load-bearing: get the resize, the padding offsets or the final de-canonical multiply
    wrong and the output is still a plausible depth map with the wrong scale.
    """

    name = "metric3d_v2_vit_small"

    # ViT variants are trained at this input size, and the canonical focal length the
    # de-canonical step divides by.
    INPUT_HW = (616, 1064)
    CANONICAL_F = 1000.0
    MEAN = (123.675, 116.28, 103.53)
    STD = (58.395, 57.12, 57.375)

    def __init__(self, variant: str = "metric3d_vit_small", device: str | None = None):
        import torch

        self.torch = torch
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.variant = variant
        self.model = torch.hub.load("yvanyin/metric3d", variant,
                                    pretrain=True, trust_repo=True)
        self.model.to(self.device).eval()

    def infer(self, rgb: np.ndarray, K: Optional[np.ndarray] = None) -> DepthResult:
        import cv2
        torch = self.torch

        h, w = rgb.shape[:2]
        if K is None:
            # Without intrinsics the whole point of this backend is lost, so rather than
            # silently inventing a focal length we fall back to a stated assumption and
            # record it, the same way the loader labels assumed EXIF.
            f = 0.75 * max(h, w)
            K = np.array([[f, 0, w / 2], [0, f, h / 2], [0, 0, 1.0]])
            used_K = False
        else:
            used_K = True

        # 1. Resize so the image fits the training input, tracking the intrinsics with it.
        scale = min(self.INPUT_HW[0] / h, self.INPUT_HW[1] / w)
        rz = cv2.resize(rgb, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_LINEAR)
        fx_scaled = float(K[0, 0]) * scale

        # 2. Pad to the exact training size, remembering the offsets so the prediction can be
        #    cropped back to the real image before anything is measured from it.
        ph, pw = self.INPUT_HW[0] - rz.shape[0], self.INPUT_HW[1] - rz.shape[1]
        t, l = ph // 2, pw // 2
        rz = cv2.copyMakeBorder(rz, t, ph - t, l, pw - l,
                                cv2.BORDER_CONSTANT, value=list(self.MEAN))

        # 3. Normalise and run.
        mean = torch.tensor(self.MEAN).float()[:, None, None]
        std = torch.tensor(self.STD).float()[:, None, None]
        x = torch.from_numpy(rz.transpose(2, 0, 1)).float()
        x = ((x - mean) / std)[None].to(self.device)

        with torch.no_grad(), _force_device(self.device):
            pred, _confidence, _out = self.model.inference({"input": x})

        # 4. Undo the padding, then the resize.
        pred = pred.squeeze()
        pred = pred[t:pred.shape[0] - (ph - t), l:pred.shape[1] - (pw - l)]
        pred = torch.nn.functional.interpolate(pred[None, None], (h, w),
                                               mode="bilinear", align_corners=False).squeeze()

        # 5. De-canonical transform: the model predicted for a 1000px-focal camera, so scale
        #    back by the ratio to this image's actual focal length. This single multiply is
        #    where the intrinsics enter the metric result.
        pred = pred * (fx_scaled / self.CANONICAL_F)
        depth = torch.clamp(pred, 0, 300).cpu().numpy().astype(np.float64)

        return DepthResult(depth=depth, model=f"metric3d/{self.variant}",
                           used_intrinsics=used_K, device=self.device)


_BACKENDS = {
    "depth_anything_v2_metric_indoor": DepthAnythingV2Metric,
    "metric3d_v2": Metric3DV2,
}

_cache: dict[str, DepthBackend] = {}


def get_backend(name: str = "metric3d_v2", **kw) -> DepthBackend:
    """Fetch (and cache) a backend. Cached because loading weights dominates a short run."""
    if name not in _BACKENDS:
        raise ValueError(f"unknown depth backend {name!r}; have {sorted(_BACKENDS)}")
    key = name + repr(sorted(kw.items()))
    if key not in _cache:
        _cache[key] = _BACKENDS[name](**kw)
    return _cache[key]


def available() -> list[str]:
    return sorted(_BACKENDS)

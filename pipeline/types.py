"""Canonical types shared by every tier.

The whole point of this file: a photo capture, a video capture and a LiDAR capture all
become the same `Scene` object, and every stage downstream of `capture/` is tier-agnostic.
If you find yourself branching on `scene.tier` below the capture layer, that is a smell.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional
import numpy as np

Tier = Literal["photo", "video", "lidar"]
ScaleSource = Literal["lidar_metric", "monocular_metric", "floor_plane",
                      "fused_mono_floor", "none"]


@dataclass
class Measurement:
    """Every number that leaves this pipeline is one of these. No bare floats in output."""
    value: float
    unit: str
    interval: tuple[float, float]
    confidence: float
    method: str

    def to_json(self) -> dict:
        return {
            "value": round(self.value, 4),
            "unit": self.unit,
            "interval": [round(self.interval[0], 4), round(self.interval[1], 4)],
            "confidence": round(self.confidence, 3),
            "method": self.method,
        }


@dataclass
class Frame:
    """One observation. depth and pose are optional: that is the entire tier difference."""
    image_path: str
    K: Optional[np.ndarray] = None          # 3x3 intrinsics
    T_wc: Optional[np.ndarray] = None       # 4x4 camera-to-world
    depth: Optional[np.ndarray] = None      # HxW metres, None on photo/video tiers
    depth_confidence: Optional[np.ndarray] = None
    tags: list[str] = field(default_factory=list)   # e.g. ["doorway", "room_02"], ["opening"]


@dataclass
class Scale:
    """How this scene got its metres. Drives interval width more than anything else.

    The capture protocol places nothing in the room, so on the photo and video tiers metres
    come from two independent estimates and never from a prop:

      monocular_metric   a pretrained metric-depth model over the frames
      floor_plane        the detected floor plus the operator's stated camera height
      fused_mono_floor   both, inverse-variance weighted - the default when both are available

    Keeping them separate is the point. Their disagreement is a direct measurement of scale
    uncertainty, which is what `sigma` carries into every interval downstream. One estimate
    would leave us choosing a sigma; two lets us measure it.
    """
    value: float = 1.0
    source: ScaleSource = "none"
    sigma: float = 0.0
    evidence: str = ""


@dataclass
class RoomCapture:
    room_id: str
    frames: list[Frame]
    scale: Scale
    tier: Tier
    doorway_hints: list[str] = field(default_factory=list)


@dataclass
class Scene:
    """A whole property capture at one tier."""
    tier: Tier
    device: str
    rooms: list[RoomCapture]

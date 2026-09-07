"""LiDAR tier loader: Record3D .r3d -> Scene.

Record3D gives synchronised RGB, LiDAR depth, per-frame intrinsics and 6-DoF ARKit pose.
Scale is sensor-metric, so no scale card is needed, but the card is still present in the
capture so that the same rooms can be cross-checked against the photo tier's scale recovery.

NOT BUILT. Signature is fixed so downstream code can be written against it.
"""
from pipeline.types import Scene


def load(capture_dir: str) -> Scene:
    raise NotImplementedError("lidar loader")

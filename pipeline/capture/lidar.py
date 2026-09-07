"""Record3D `.r3d` loader: depth, poses and intrinsics -> canonical Scene.

FORMAT, as observed on a real capture rather than as documented anywhere official. Everything
below was verified against `benchmark/raw/lidar/*.r3d`; nothing here is assumed.

A `.r3d` is a ZIP archive:

    metadata            JSON, see below
    rgbd/<i>.jpg        RGB frame i, w x h
    rgbd/<i>.depth      LZFSE-compressed float32 depth, dw x dh, metres, NaN where invalid
    rgbd/<i>.conf       LZFSE-compressed uint8 confidence, dw x dh, values 0 / 1 / 2
    sound.m4a           audio, ignored

`metadata` keys seen: K, cameraType, dw, dh, fps, frameTimestamps, h, initPose,
perFrameIntrinsicCoeffs, poses, w. Every one is read defensively - a missing key degrades the
Scene and is reported, rather than raising.

  K       nine numbers, COLUMN-major 3x3. [fx,0,0, 0,fy,0, cx,cy,1] reads as the transpose of
          the usual matrix, so it is reshaped (3,3) and transposed. Applies to the RGB
          resolution, not the depth resolution.
  poses   one per frame, [qx, qy, qz, qw, tx, ty, tz]. Quaternion verified unit to 1e-6;
          translation in metres (measured trajectory: 16.6 m of path over 4437 frames).
  depth   float32 at dw x dh, which is 1/3.75 of the RGB resolution on this device. NaN marks
          no return - about 17% of pixels on the sample - and those must be dropped, not
          treated as zero range.
  conf    0 low / 1 medium / 2 high. Filtering to >= 1 is the default; LiDAR's low-confidence
          returns are exactly the mirror-and-glass pixels the brief asks us to handle.

COORDINATE FRAMES, the part most likely to be silently wrong:

  ARKit world is Y-up, right-handed. Confirmed empirically here - the trajectory spans 2.3 m
  and 4.8 m in X and Z but only 0.26 m in Y, which is a person walking a room, not floating.

  ARKit camera is +X right, +Y UP, +Z BACKWARD (it looks down -Z).
  Our lift() uses the OpenCV convention: +X right, +Y DOWN, +Z FORWARD.

  So a camera-frame point converts as  p_arkit = diag(1, -1, -1) @ p_opencv,  and the
  camera-to-world transform we hand downstream is  R_quat @ diag(1, -1, -1).

  Getting this wrong does not crash anything. It produces a reconstruction that is mirrored
  and upside down, which still fits planes happily and yields confident, wrong dimensions.
"""
from __future__ import annotations

import json
import os
import zipfile
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from pipeline.types import Frame, RoomCapture, Scale, Scene

# ARKit camera -> OpenCV camera. Its own inverse.
ARKIT_TO_OPENCV = np.diag([1.0, -1.0, -1.0])

# ARKit world is Y-up, so gravity ("up") is +Y. Handed to the geometry stage instead of the
# camera-frame prior the photo tier uses.
ARKIT_WORLD_UP = np.array([0.0, 1.0, 0.0])

MIN_CONFIDENCE = 1          # drop Record3D's "low" returns by default


@dataclass
class R3DInfo:
    """What a capture actually contains. Populated defensively; missing fields stay None."""
    path: str
    n_frames: int = 0
    rgb_size: Optional[tuple[int, int]] = None       # (w, h)
    depth_size: Optional[tuple[int, int]] = None     # (dw, dh)
    fps: Optional[float] = None
    K_rgb: Optional[np.ndarray] = None
    has_poses: bool = False
    has_per_frame_intrinsics: bool = False
    has_confidence: bool = False
    timestamps: Optional[np.ndarray] = None
    depth_dtype: Optional[str] = None
    depth_units: str = "metres (assumed; float32 values 1.2-3.9 on sample)"
    camera_type: Optional[int] = None
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _decompress(blob: bytes) -> bytes:
    """LZFSE. The `.depth`/`.conf` payloads begin with the 'bvx2' block magic."""
    import liblzfse
    return liblzfse.decompress(blob)


def _K_from_metadata(md: dict) -> Optional[np.ndarray]:
    k = md.get("K")
    if not k or len(k) != 9:
        return None
    return np.asarray(k, dtype=np.float64).reshape(3, 3).T      # stored column-major


def _pose_to_matrix(pose: list[float]) -> np.ndarray:
    """[qx,qy,qz,qw,tx,ty,tz] -> 4x4 OpenCV-camera-to-ARKit-world."""
    qx, qy, qz, qw = pose[0], pose[1], pose[2], pose[3]
    t = np.asarray(pose[4:7], dtype=np.float64)

    n = np.sqrt(qx * qx + qy * qy + qz * qz + qw * qw)
    if n < 1e-12:
        R = np.eye(3)
    else:
        qx, qy, qz, qw = qx / n, qy / n, qz / n, qw / n
        R = np.array([
            [1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qz * qw), 2 * (qx * qz + qy * qw)],
            [2 * (qx * qy + qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qx * qw)],
            [2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw), 1 - 2 * (qx * qx + qy * qy)],
        ])

    T = np.eye(4)
    T[:3, :3] = R @ ARKIT_TO_OPENCV     # so downstream can stay in the OpenCV convention
    T[:3, 3] = t
    return T


def inspect(path: str) -> R3DInfo:
    """Report what a capture contains without decoding every frame."""
    info = R3DInfo(path=path)
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        if "metadata" not in names:
            info.missing.append("metadata")
            return info
        md = json.loads(z.read("metadata"))

        for key in ("K", "poses", "frameTimestamps", "w", "h", "dw", "dh", "fps"):
            if key not in md:
                info.missing.append(key)

        depth_names = sorted(n for n in names if n.endswith(".depth"))
        info.n_frames = len(depth_names)
        if md.get("w") and md.get("h"):
            info.rgb_size = (int(md["w"]), int(md["h"]))
        if md.get("dw") and md.get("dh"):
            info.depth_size = (int(md["dw"]), int(md["dh"]))
        info.fps = float(md["fps"]) if md.get("fps") else None
        info.K_rgb = _K_from_metadata(md)
        info.camera_type = md.get("cameraType")
        info.has_poses = bool(md.get("poses")) and len(md["poses"]) >= info.n_frames
        info.has_per_frame_intrinsics = bool(md.get("perFrameIntrinsicCoeffs"))
        info.has_confidence = any(n.endswith(".conf") for n in names)
        if md.get("frameTimestamps"):
            info.timestamps = np.asarray(md["frameTimestamps"], dtype=np.float64)

        if md.get("poses") and len(md["poses"]) != info.n_frames:
            info.notes.append(
                f"pose count {len(md['poses'])} != depth frame count {info.n_frames}; "
                f"frames beyond the shorter list are skipped")

        if depth_names and info.depth_size:
            raw = _decompress(z.read(depth_names[0]))
            n_px = info.depth_size[0] * info.depth_size[1]
            for dt in (np.float32, np.float16):
                if len(raw) == n_px * np.dtype(dt).itemsize:
                    info.depth_dtype = dt.__name__
                    break
            if info.depth_dtype is None:
                info.notes.append(
                    f"depth payload {len(raw)}B does not match {n_px} px at any known dtype")
    return info


def _read_frame(z: zipfile.ZipFile, i: int, dw: int, dh: int,
                want_conf: bool) -> tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    try:
        raw = _decompress(z.read(f"rgbd/{i}.depth"))
    except KeyError:
        return None, None
    depth = np.frombuffer(raw, dtype=np.float32).reshape(dh, dw).astype(np.float64)

    conf = None
    if want_conf:
        try:
            conf = np.frombuffer(_decompress(z.read(f"rgbd/{i}.conf")),
                                 dtype=np.uint8).reshape(dh, dw)
        except KeyError:
            conf = None
    return depth, conf


def load(path: str, stride: int = 30, max_frames: Optional[int] = None,
         min_confidence: int = MIN_CONFIDENCE, room_id: Optional[str] = None) -> Scene:
    """Load a `.r3d` into a Scene.

    `stride` subsamples frames. A 4437-frame capture at 60 fps is enormously redundant for
    geometry - consecutive frames are centimetres apart - and every frame kept costs memory
    and fusion time for almost no new surface. Default 30 keeps ~2 frames/second.

    Depth is kept at its native resolution with intrinsics scaled to match, rather than
    upsampling to the RGB grid. Upsampling would invent depth values between real returns and
    then fit planes to them.
    """
    info = inspect(path)
    if info.depth_size is None or info.K_rgb is None:
        raise ValueError(f"{path}: missing depth size or intrinsics; got missing={info.missing}")

    dw, dh = info.depth_size
    with zipfile.ZipFile(path) as z:
        md = json.loads(z.read("metadata"))
        poses = md.get("poses") or []
        ts = md.get("frameTimestamps") or []

        # Intrinsics are published for the RGB resolution; depth is a scaled-down grid.
        K = info.K_rgb.copy()
        if info.rgb_size:
            s = dw / float(info.rgb_size[0])
            K[:2, :] *= s

        n = info.n_frames
        if poses:
            n = min(n, len(poses))
        idx = list(range(0, n, max(1, stride)))
        if max_frames:
            idx = idx[:max_frames]

        frames: list[Frame] = []
        for i in idx:
            depth, conf = _read_frame(z, i, dw, dh, info.has_confidence)
            if depth is None:
                continue
            if conf is not None and min_confidence > 0:
                depth = np.where(conf >= min_confidence, depth, np.nan)
            T = _pose_to_matrix(poses[i]) if i < len(poses) else None
            frames.append(Frame(
                image_path=f"{os.path.basename(path)}#rgbd/{i}",
                K=K.copy(),
                T_wc=T,
                depth=depth,
                depth_confidence=conf.astype(np.float64) if conf is not None else None,
                orig_size=(dw, dh),
                K_source="r3d_metadata_K_scaled_to_depth",
                tags=[f"t={ts[i]:.3f}"] if i < len(ts) else [],
            ))

    return Scene(
        tier="lidar",
        device=f"Record3D cameraType={info.camera_type}",
        rooms=[RoomCapture(
            room_id=room_id or os.path.splitext(os.path.basename(path))[0],
            frames=frames,
            scale=Scale(value=1.0, source="lidar_metric", sigma=0.0,
                        evidence="Record3D LiDAR depth, metres"),
            tier="lidar",
        )],
    )

"""Stray Scanner loader: the format the Cozmo team's own sample captures use.

FORMAT, verified against the three shared samples rather than assumed. A capture is a
directory (or a zip of one) containing:

    camera_matrix.csv     3x3 K for the RGB resolution, comma separated
    odometry.csv          timestamp, frame, x, y, z, qx, qy, qz, qw, fx, fy, cx, cy, ...
    imu.csv               timestamp, accelerometer xyz, gyro xyz
    depth/NNNNNN.png      256x192 uint16, MILLIMETRES, 0 = no return
    confidence/NNNNNN.png 256x192 uint8, 0 / 1 / 2
    rgb.mp4               1920x1440 H.264

Differences from Record3D that will silently corrupt a reconstruction if missed:

  Depth is uint16 millimetres in a PNG, not float32 metres in an LZFSE blob. Read as metres
  and everything is 1000x too small; read as float and it is garbage.

  Zero means NO RETURN, it is not a distance. Record3D uses NaN, which propagates loudly.
  A literal 0 does not - it becomes a point at the camera centre and drags every plane fit
  toward the origin.

  Depth is 256x192 LANDSCAPE here, where Record3D's was 192x256 portrait. Anything that
  assumed portrait will transpose the scene.

  Intrinsics are published per frame in odometry.csv as well as globally in
  camera_matrix.csv, and they differ slightly - 1599.70 globally against 1597.89 on frame 0
  of the same capture, because ARKit refines focal length during a session. The per-frame
  value is the better one and is used when present.

POSES ARE **NOT** IN THE SAME CONVENTION AS RECORD3D, and this is the trap that cost the most
time here. Record3D publishes raw ARKit poses - camera +Y up, +Z backward - which need a
diag(1, -1, -1) to reach our OpenCV frame. Stray Scanner already publishes poses in an
OpenCV-style camera frame, so applying that same flip is wrong.

Determined empirically rather than from documentation, by fusing 40 frames under every
plausible combination of quaternion order and axis convention and scoring the peakiness of the
resulting height histogram - a coherent room has sharp floor and ceiling peaks, an incoherent
one is a smear:

    quat   convention          height span   peakiness
    xyzw   R @ diag(1,-1,-1)       5.26 m       0.0442     <- the Record3D convention
    xyzw   R  (identity)           2.37 m       0.0861     <- correct
    xyzw   R @ diag(1,1,-1)        5.25 m       0.0449
    wxyz   any                  5.2-8.3 m    0.021-0.029

The wrong convention does not fail loudly. It produces a full point cloud of the right size
which fits planes happily, and only looks wrong when rendered - the side elevation was
diagonal streaks with no flat surface anywhere, and a "single room" spanned 4.6 m vertically.

Reading works directly from the .zip. The samples extract to thousands of small PNGs and a
partial extraction looks exactly like a capture with missing depth, which is a failure mode
worth designing out rather than debugging later.
"""
from __future__ import annotations

import csv
import io
import os
import zipfile
from dataclasses import dataclass, field
from typing import Optional

import cv2
import numpy as np

from pipeline.capture.lidar import ARKIT_TO_OPENCV, ARKIT_WORLD_UP  # noqa: F401
from pipeline.types import Frame, RoomCapture, Scale, Scene

DEPTH_SCALE = 0.001          # uint16 millimetres -> metres
MIN_CONFIDENCE = 1           # drop Stray's "low" class, as on the LiDAR tier


class _Source:
    """Read a capture from a directory or straight out of its zip, with one interface."""

    def __init__(self, path: str):
        self.path = path
        self.zip: Optional[zipfile.ZipFile] = None
        self.prefix = ""
        if path.lower().endswith(".zip"):
            self.zip = zipfile.ZipFile(path)
            names = self.zip.namelist()
            hit = next((n for n in names if n.endswith("camera_matrix.csv")), None)
            if hit is None:
                raise ValueError(f"{path}: no camera_matrix.csv in the archive")
            self.prefix = hit[: -len("camera_matrix.csv")]
        else:
            root = path
            if not os.path.isfile(os.path.join(root, "camera_matrix.csv")):
                subs = [d for d in sorted(os.listdir(root))
                        if os.path.isfile(os.path.join(root, d, "camera_matrix.csv"))]
                if not subs:
                    raise ValueError(f"{path}: no camera_matrix.csv found")
                root = os.path.join(root, subs[0])
            self.root = root

    def read(self, rel: str) -> Optional[bytes]:
        if self.zip is not None:
            try:
                return self.zip.read(self.prefix + rel)
            except KeyError:
                return None
        p = os.path.join(self.root, rel)
        return open(p, "rb").read() if os.path.isfile(p) else None

    def listdir(self, rel: str) -> list[str]:
        if self.zip is not None:
            pre = self.prefix + rel.rstrip("/") + "/"
            return sorted(n[len(pre):] for n in self.zip.namelist()
                          if n.startswith(pre) and not n.endswith("/"))
        p = os.path.join(self.root, rel)
        return sorted(os.listdir(p)) if os.path.isdir(p) else []

    @property
    def name(self) -> str:
        return os.path.basename(os.path.normpath(self.path))


@dataclass
class StrayInfo:
    path: str
    n_odometry: int = 0
    n_depth: int = 0
    n_confidence: int = 0
    rgb_size: Optional[tuple[int, int]] = None
    depth_size: Optional[tuple[int, int]] = None
    K_rgb: Optional[np.ndarray] = None
    has_per_frame_intrinsics: bool = False
    duration_s: Optional[float] = None
    fps: Optional[float] = None
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _read_odometry(src: _Source) -> list[dict]:
    blob = src.read("odometry.csv")
    if blob is None:
        return []
    rows = []
    rdr = csv.reader(io.StringIO(blob.decode("utf-8", "replace")))
    header = [h.strip() for h in next(rdr)]
    idx = {h: i for i, h in enumerate(header)}
    for r in rdr:
        if len(r) < 9:
            continue
        def g(k, default=None):
            i = idx.get(k)
            if i is None or i >= len(r) or not r[i].strip():
                return default
            return float(r[i])
        rows.append({
            "timestamp": g("timestamp", 0.0),
            "frame": int(float(r[idx["frame"]])),
            "t": np.array([g("x", 0.0), g("y", 0.0), g("z", 0.0)]),
            "q": np.array([g("qx", 0.0), g("qy", 0.0), g("qz", 0.0), g("qw", 1.0)]),
            "fx": g("fx"), "fy": g("fy"), "cx": g("cx"), "cy": g("cy"),
        })
    return rows


def _pose_matrix(q: np.ndarray, t: np.ndarray) -> np.ndarray:
    """[qx,qy,qz,qw] + translation -> 4x4 OpenCV-camera-to-ARKit-world."""
    qx, qy, qz, qw = q
    n = float(np.linalg.norm(q))
    if n < 1e-12:
        R = np.eye(3)
    else:
        qx, qy, qz, qw = q / n
        R = np.array([
            [1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qz * qw), 2 * (qx * qz + qy * qw)],
            [2 * (qx * qy + qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qx * qw)],
            [2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw), 1 - 2 * (qx * qx + qy * qy)],
        ])
    T = np.eye(4)
    # No ARKIT_TO_OPENCV here, unlike the Record3D loader. See the module docstring: Stray
    # already publishes an OpenCV-style camera frame, and applying the flip smears the cloud.
    T[:3, :3] = R
    T[:3, 3] = t
    return T


def inspect(path: str) -> StrayInfo:
    src = _Source(path)
    info = StrayInfo(path=path)

    km = src.read("camera_matrix.csv")
    if km is None:
        info.missing.append("camera_matrix.csv")
    else:
        info.K_rgb = np.array([[float(x) for x in line.split(",")]
                               for line in km.decode().strip().splitlines()])

    odo = _read_odometry(src)
    info.n_odometry = len(odo)
    if not odo:
        info.missing.append("odometry.csv")
    else:
        info.has_per_frame_intrinsics = odo[0]["fx"] is not None
        ts = [r["timestamp"] for r in odo]
        info.duration_s = ts[-1] - ts[0]
        if info.duration_s and len(ts) > 1:
            info.fps = (len(ts) - 1) / info.duration_s

    depths = [n for n in src.listdir("depth") if n.endswith(".png")]
    confs = [n for n in src.listdir("confidence") if n.endswith(".png")]
    info.n_depth, info.n_confidence = len(depths), len(confs)
    if not depths:
        info.missing.append("depth/")

    if depths:
        blob = src.read(f"depth/{depths[0]}")
        arr = cv2.imdecode(np.frombuffer(blob, np.uint8), cv2.IMREAD_UNCHANGED)
        info.depth_size = (arr.shape[1], arr.shape[0])
        if arr.dtype != np.uint16:
            info.notes.append(f"depth dtype {arr.dtype}, expected uint16 millimetres")

    rgb = src.read("rgb.mp4")
    if rgb is None:
        info.notes.append("no rgb.mp4 (geometry still works; overlays will not)")
    elif src.zip is None:
        cap = cv2.VideoCapture(os.path.join(src.root, "rgb.mp4"))
        if cap.isOpened():
            info.rgb_size = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                             int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
        cap.release()

    if info.n_depth and info.n_odometry and abs(info.n_depth - info.n_odometry) > 2:
        info.notes.append(f"depth frames {info.n_depth} != odometry rows {info.n_odometry}; "
                          f"the shorter list wins")
    return info


def load(path: str, stride: int = 30, max_frames: Optional[int] = None,
         min_confidence: int = MIN_CONFIDENCE, room_id: Optional[str] = None) -> Scene:
    """Stray Scanner capture -> canonical Scene with metric depth and ARKit poses."""
    src = _Source(path)
    info = inspect(path)
    if info.K_rgb is None or not info.n_depth:
        raise ValueError(f"{path}: missing {info.missing}")

    odo = _read_odometry(src)
    depth_names = [n for n in src.listdir("depth") if n.endswith(".png")]
    conf_names = set(src.listdir("confidence"))

    n = min(len(odo), len(depth_names))
    idx = list(range(0, n, max(1, stride)))
    if max_frames:
        idx = idx[:max_frames]

    frames: list[Frame] = []
    for i in idx:
        blob = src.read(f"depth/{depth_names[i]}")
        if blob is None:
            continue
        raw = cv2.imdecode(np.frombuffer(blob, np.uint8), cv2.IMREAD_UNCHANGED)
        if raw is None:
            continue
        depth = raw.astype(np.float64) * DEPTH_SCALE
        # 0 is "no return", not "zero metres". Left as 0 it becomes a point at the camera
        # centre and drags every plane fit toward the origin.
        depth[raw == 0] = np.nan

        conf = None
        cn = depth_names[i]
        if cn in conf_names:
            cb = src.read(f"confidence/{cn}")
            if cb is not None:
                conf = cv2.imdecode(np.frombuffer(cb, np.uint8), cv2.IMREAD_UNCHANGED)
                if conf is not None and min_confidence > 0:
                    depth[conf < min_confidence] = np.nan

        dh, dw = depth.shape[:2]
        row = odo[i]
        # Per-frame intrinsics when ARKit published them, else the session-wide matrix.
        # Both are for the RGB resolution and must be scaled to the depth grid.
        if row["fx"] is not None:
            K = np.array([[row["fx"], 0.0, row["cx"]],
                          [0.0, row["fy"], row["cy"]],
                          [0.0, 0.0, 1.0]])
            ksrc = "stray_odometry_per_frame"
        else:
            K = info.K_rgb.copy()
            ksrc = "stray_camera_matrix"
        if info.rgb_size:
            K[:2, :] *= dw / float(info.rgb_size[0])
        else:
            K[:2, :] *= dw / (2.0 * K[0, 2])     # fall back on cx being the RGB half-width

        frames.append(Frame(
            image_path=f"{src.name}#depth/{depth_names[i]}",
            K=K, T_wc=_pose_matrix(row["q"], row["t"]),
            depth=depth,
            depth_confidence=conf.astype(np.float64) if conf is not None else None,
            orig_size=(dw, dh), K_source=ksrc,
            tags=[f"t={row['timestamp']:.3f}", f"frame={row['frame']}"],
        ))

    return Scene(
        tier="lidar", device="Stray Scanner (ARKit)",
        rooms=[RoomCapture(
            room_id=room_id or src.name,
            frames=frames, tier="lidar",
            scale=Scale(value=1.0, source="lidar_metric", sigma=0.0,
                        evidence="ARKit LiDAR depth, uint16 millimetres"),
        )],
    )

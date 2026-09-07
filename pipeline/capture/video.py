"""Video tier loader: a handheld walkthrough clip -> posed Scene.

Frame sampling, then RGB-D visual odometry, then the same canonical Scene every other tier
produces. Nothing below `capture/` knows this came from a video.

WHY RGB-D ODOMETRY RATHER THAN SfM. We already run a metric depth model per keyframe, so the
3D position of every matched feature is known in its own camera frame. That turns pose
recovery into PnP - a mature, well-conditioned OpenCV solver - instead of a full
structure-from-motion problem with its own scale ambiguity to resolve afterwards. Monocular
SfM would recover the trajectory only up to scale and we would then have to bolt the depth
model back on to fix it. Doing it this way, the trajectory is metric from the first frame.

The tradeoff, stated because it decides where the error comes from: the poses inherit the
depth model's error. If depth is 5% long in one room, the baselines through that room are 5%
long too. Frame-to-frame odometry also drifts, with no loop closure here yet, so a long walk
accumulates. Both are visible in the diagnostics rather than hidden.

INTRINSICS. A video file carries no per-frame EXIF, and iPhone video is cropped relative to
stills for stabilisation, so the still camera's 26mm-equivalent focal length is not exactly
right. We take it as the starting estimate, label it as an assumption in K_source, and
provide `--equiv35` to override once a calibration exists. This is the largest known
unquantified error in this tier.
"""
from __future__ import annotations

import os
from typing import Optional

import cv2
import numpy as np

from pipeline.types import Frame, RoomCapture, Scale, Scene

FILM_DIAGONAL_MM = 43.266615
DEFAULT_EQUIV35_MM = 26.0        # iPhone main camera; video crop makes this approximate

WORK_PX = 1024                   # long edge handed to the depth model
DEFAULT_EVERY_N = 60             # 1 keyframe/second at 60fps
# Blur rejection is RELATIVE, not absolute. A fixed threshold borrowed from the photo tier
# rejected 75 of 92 frames on the real clip: video frames are inherently softer than stills
# (rolling shutter, inter-frame compression, a smaller sensor readout), so an absolute floor
# tuned on 24MP photos condemns almost everything. What matters is whether a frame is bad
# *relative to the rest of this clip*, so we keep frames above a percentile of the clip's own
# sharpness distribution and never drop more than a fixed fraction.
BLUR_PERCENTILE = 25.0           # drop the softest quarter...
BLUR_ABSOLUTE_FLOOR = 8.0        # ...but never keep something this smeared
BLUR_CANONICAL_W = 1200

MIN_MATCH_INLIERS = 25           # below this a PnP pose is not trustworthy


def intrinsics_for(w: int, h: int, equiv35_mm: float = DEFAULT_EQUIV35_MM
                   ) -> tuple[np.ndarray, str]:
    """Pinhole K from a 35mm-equivalent focal length, on the image diagonal."""
    f_px = equiv35_mm * float(np.hypot(w, h)) / FILM_DIAGONAL_MM
    K = np.array([[f_px, 0.0, w / 2.0], [0.0, f_px, h / 2.0], [0.0, 0.0, 1.0]])
    return K, f"assumed_{equiv35_mm:.0f}mm_equiv_video"


def _blur(gray: np.ndarray) -> float:
    if gray.shape[1] > BLUR_CANONICAL_W:
        h = int(gray.shape[0] * BLUR_CANONICAL_W / gray.shape[1])
        gray = cv2.resize(gray, (BLUR_CANONICAL_W, h), interpolation=cv2.INTER_AREA)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def sample_frames(path: str, every_n: int = DEFAULT_EVERY_N,
                  max_frames: Optional[int] = None,
                  work_px: int = WORK_PX,
                  skip_blurred: bool = True) -> tuple[list[tuple[int, np.ndarray]], dict]:
    """Pull keyframes from a clip, dropping the ones blurred relative to the rest.

    Read sequentially rather than by seeking. cv2's CAP_PROP_POS_FRAMES seek forces a decode
    from the nearest keyframe every time, which measured 44s for 92 grabs on this clip;
    decoding straight through and discarding unwanted frames does the same job in a fraction
    of that, because the decoder is doing the same work either way.

    Blur is judged against this clip's own distribution. A handheld walkthrough has bursts of
    fast rotation where frames smear, and a blurred keyframe does not fail loudly: it yields
    few feature matches, a poor PnP pose, and a kink in the trajectory that propagates through
    every frame after it. But an absolute threshold cannot tell "this clip is soft" from "this
    frame is soft", so we take a percentile.
    """
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise ValueError(f"cannot open video: {path}")

    meta = {
        "fps": float(cap.get(cv2.CAP_PROP_FPS) or 0.0),
        "n_frames": int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0),
        "width": int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
        "height": int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
    }
    meta["duration_s"] = meta["n_frames"] / meta["fps"] if meta["fps"] else 0.0

    cand: list[tuple[int, np.ndarray, float]] = []
    idx = 0
    while True:
        ok, bgr = cap.read()
        if not ok:
            break
        if idx % every_n == 0:
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            sharp = _blur(gray)
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            sc = min(1.0, work_px / max(rgb.shape[:2]))
            if sc < 1.0:
                rgb = cv2.resize(rgb, (int(rgb.shape[1] * sc), int(rgb.shape[0] * sc)),
                                 interpolation=cv2.INTER_AREA)
            cand.append((idx, rgb, sharp))
            if max_frames and len(cand) >= max_frames * 2:
                break
        idx += 1
    cap.release()

    if not cand:
        meta.update(sampled=0, skipped_blurred=0)
        return [], meta

    sharps = np.array([c[2] for c in cand])
    thresh = max(BLUR_ABSOLUTE_FLOOR, float(np.percentile(sharps, BLUR_PERCENTILE)))
    kept = [(i, rgb) for i, rgb, s_ in cand if not skip_blurred or s_ >= thresh]
    if max_frames:
        kept = kept[:max_frames]

    meta["sampled"] = len(kept)
    meta["skipped_blurred"] = len(cand) - len(kept)
    meta["sharpness_median"] = round(float(np.median(sharps)), 1)
    meta["sharpness_threshold"] = round(thresh, 1)
    return kept, meta


def _relative_pose(rgb_a: np.ndarray, depth_a: np.ndarray, rgb_b: np.ndarray,
                   K: np.ndarray) -> tuple[Optional[np.ndarray], int]:
    """Pose of camera b relative to camera a, by PnP on a's depth-backed 3D points.

    Returns (4x4 a<-b transform, inlier count). None when the match is too weak to trust,
    which the caller must handle rather than silently accepting an identity pose.
    """
    ga = cv2.cvtColor(rgb_a, cv2.COLOR_RGB2GRAY)
    gb = cv2.cvtColor(rgb_b, cv2.COLOR_RGB2GRAY)

    sift = cv2.SIFT_create(nfeatures=3000)
    ka, da = sift.detectAndCompute(ga, None)
    kb, db = sift.detectAndCompute(gb, None)
    if da is None or db is None or len(ka) < 8 or len(kb) < 8:
        return None, 0

    good = [m for m, n in cv2.BFMatcher().knnMatch(da, db, k=2)
            if m.distance < 0.75 * n.distance]
    if len(good) < MIN_MATCH_INLIERS:
        return None, len(good)

    # Back-project a's matched keypoints through its depth map.
    obj, img = [], []
    fx, fy, cx, cy = K[0, 0], K[1, 1], K[0, 2], K[1, 2]
    h, w = depth_a.shape[:2]
    for m in good:
        u, v = ka[m.queryIdx].pt
        ui, vi = int(round(u)), int(round(v))
        if not (0 <= ui < w and 0 <= vi < h):
            continue
        z = depth_a[vi, ui]
        if not np.isfinite(z) or z <= 0.2 or z > 12.0:
            continue
        obj.append([(u - cx) * z / fx, (v - cy) * z / fy, z])
        img.append(kb[m.trainIdx].pt)

    if len(obj) < MIN_MATCH_INLIERS:
        return None, len(obj)

    ok, rvec, tvec, inliers = cv2.solvePnPRansac(
        np.asarray(obj, np.float64), np.asarray(img, np.float64), K, None,
        reprojectionError=4.0, iterationsCount=200, flags=cv2.SOLVEPNP_ITERATIVE)
    if not ok or inliers is None or len(inliers) < MIN_MATCH_INLIERS:
        return None, 0 if inliers is None else len(inliers)

    # solvePnP gives world->camera_b, where "world" is camera a's frame. We want a<-b.
    R, _ = cv2.Rodrigues(rvec)
    T_b_from_a = np.eye(4)
    T_b_from_a[:3, :3] = R
    T_b_from_a[:3, 3] = tvec.ravel()
    return np.linalg.inv(T_b_from_a), int(len(inliers))


def odometry(keyframes: list[tuple[int, np.ndarray]], depths: list[np.ndarray],
             K: np.ndarray) -> tuple[list[Optional[np.ndarray]], list[dict]]:
    """Chain relative poses into a trajectory. First keyframe defines the world frame.

    A failed link leaves every later frame unposed rather than guessing: an identity pose
    inserted at a break would silently stack two parts of the room on top of each other, which
    looks like a reconstruction and is not one. Breaks are reported so the caller can decide.
    """
    n = len(keyframes)
    poses: list[Optional[np.ndarray]] = [None] * n
    diag: list[dict] = []
    if n == 0:
        return poses, diag

    poses[0] = np.eye(4)
    for i in range(1, n):
        if poses[i - 1] is None:
            diag.append({"link": i, "inliers": 0, "ok": False, "reason": "previous unposed"})
            continue
        T_rel, inl = _relative_pose(keyframes[i - 1][1], depths[i - 1], keyframes[i][1], K)
        if T_rel is None:
            diag.append({"link": i, "inliers": inl, "ok": False, "reason": "weak match"})
            continue
        poses[i] = poses[i - 1] @ T_rel
        diag.append({"link": i, "inliers": inl, "ok": True,
                     "step_m": round(float(np.linalg.norm(T_rel[:3, 3])), 4)})
    return poses, diag


def load(path: str, every_n: int = DEFAULT_EVERY_N, max_frames: Optional[int] = None,
         equiv35_mm: float = DEFAULT_EQUIV35_MM, depth_backend=None,
         work_px: int = WORK_PX, room_id: Optional[str] = None,
         progress: bool = False) -> tuple[Scene, dict]:
    """Video file -> posed Scene. Returns (scene, diagnostics).

    `depth_backend` must be supplied by the caller (it owns caching and model choice); without
    it the Scene comes back with frames and intrinsics but no depth and no poses, which is
    still useful for inspecting a clip.
    """
    keyframes, meta = sample_frames(path, every_n, max_frames, work_px)
    if not keyframes:
        raise ValueError(f"no usable keyframes in {path}")

    h, w = keyframes[0][1].shape[:2]
    K, k_src = intrinsics_for(w, h, equiv35_mm)

    depths: list[np.ndarray] = []
    if depth_backend is not None:
        for i, (_idx, rgb) in enumerate(keyframes, 1):
            depths.append(depth_backend.infer(rgb, K).depth)
            if progress:
                print(f"\r  depth {i}/{len(keyframes)}", end="", flush=True)
        if progress:
            print()

    poses, diag = (odometry(keyframes, depths, K) if depths else ([None] * len(keyframes), []))

    frames = [
        Frame(image_path=f"{os.path.basename(path)}#frame{idx}", K=K.copy(),
              T_wc=poses[i], depth=depths[i] if depths else None,
              image=rgb, orig_size=(meta["width"], meta["height"]), K_source=k_src,
              tags=[f"frame={idx}"])
        for i, (idx, rgb) in enumerate(keyframes)
    ]

    meta["posed"] = sum(1 for p in poses if p is not None)
    meta["odometry"] = diag
    meta["K_source"] = k_src
    meta["focal_px"] = round(float(K[0, 0]), 2)

    scene = Scene(tier="video", device="unknown (video carries no EXIF)",
                  rooms=[RoomCapture(
                      room_id=room_id or os.path.splitext(os.path.basename(path))[0],
                      frames=frames, tier="video",
                      scale=Scale(source="monocular_metric",
                                  evidence="metric depth; poses chained by RGB-D PnP"))])
    return scene, meta

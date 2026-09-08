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

    TWO REAL BUGS FIXED HERE, FOUND ON A 190s CLIP RATHER THAN HYPOTHESISED.

    First: `cap.read()` was called on EVERY raw frame, candidate or not - a full decode plus a
    fresh ~6 MB BGR buffer allocation, 11000+ times over, before this function had even
    decided which frames it wanted. On a memory-constrained machine that crashed outright:
    "Failed to allocate 6220800 bytes" mid-decode, that number being exactly one
    1080x1920x3 frame. Fixed by `cap.grab()` (advances the decoder, does not decode or
    allocate a full frame) for every frame that is not a wanted index, and the expensive
    `cap.retrieve()` only for the ones that are.

    Second, and the reason coverage silently stopped partway through a long clip regardless
    of `--max-frames`: candidate indices were only ever taken from the START of the file, in
    order, until enough had been collected. A 190s clip capped at 60 kept frames covered only
    its first ~120s - `max_frames` controlled HOW MANY frames were kept, never WHERE FROM.
    Fixed by choosing candidate indices evenly spread across the WHOLE duration up front, so
    raising or lowering `max_frames` changes density, never which portion of the walk is
    represented - a room near the end of a long walk is no longer invisible to segmentation
    by construction.

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

    # Candidate indices, spread across the ENTIRE file before a single frame is decoded - the
    # continuity fix. `every_n` first gives the natural spacing (walking pace, not file
    # length); if that would produce more candidates than needed, they are THINNED evenly
    # across the whole range rather than truncated from the end, so density drops uniformly
    # instead of coverage collapsing onto the first portion of the walk.
    all_idx = list(range(0, max(1, meta["n_frames"]), every_n)) or [0]
    want_n = max_frames * 2 if max_frames else len(all_idx)
    if len(all_idx) > want_n > 0:
        pick = np.linspace(0, len(all_idx) - 1, want_n).round().astype(int)
        wanted = sorted(set(all_idx[i] for i in pick))
    else:
        wanted = all_idx
    wanted_set = set(wanted)

    cand: list[tuple[int, np.ndarray, float]] = []
    idx = 0
    while True:
        if idx not in wanted_set:
            if not cap.grab():
                break
            idx += 1
            continue
        ok, bgr = cap.read()
        if not ok:
            break
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        sharp = _blur(gray)
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        sc = min(1.0, work_px / max(rgb.shape[:2]))
        if sc < 1.0:
            rgb = cv2.resize(rgb, (int(rgb.shape[1] * sc), int(rgb.shape[0] * sc)),
                             interpolation=cv2.INTER_AREA)
        cand.append((idx, rgb, sharp))
        if idx >= wanted[-1]:
            break
        idx += 1
    cap.release()

    if not cand:
        meta.update(sampled=0, skipped_blurred=0)
        return [], meta

    sharps = np.array([c[2] for c in cand])
    thresh = max(BLUR_ABSOLUTE_FLOOR, float(np.percentile(sharps, BLUR_PERCENTILE)))
    kept = [(i, rgb) for i, rgb, s_ in cand if not skip_blurred or s_ >= thresh]
    if max_frames and len(kept) > max_frames:
        # Thinned evenly across the surviving candidates, not truncated from the front - the
        # same continuity fix applied a second time, because blur filtering can itself remove
        # frames unevenly (a shaky stretch mid-walk drops more than a steady one) and a plain
        # `[:max_frames]` here would collapse coverage back onto the start exactly as before.
        pick = np.linspace(0, len(kept) - 1, max_frames).round().astype(int)
        kept = [kept[i] for i in sorted(set(pick))]

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


MAX_RELOCALIZE_GAP = 15          # frames past a broken link still worth retrying (see below)


def odometry(keyframes: list[tuple[int, np.ndarray]], depths: list[np.ndarray],
             K: np.ndarray, max_relocalize_gap: int = MAX_RELOCALIZE_GAP
             ) -> tuple[list[Optional[np.ndarray]], list[dict]]:
    """Chain relative poses into a trajectory. First keyframe defines the world frame.

    A failed link does not close the trajectory for good. It used to: the previous version of
    this function checked `poses[i-1] is None` and gave up on every frame from there on,
    which is why the real clip posed only 15 of 30 keyframes - one broken link at frame 15 and
    every frame after it was marked unposed regardless of what the camera saw next, even
    though nothing about frame 16 onward was actually wrong.

    So a break now RETRIES against the last successfully posed frame - not just its immediate
    predecessor - for up to `max_relocalize_gap` further keyframes. A camera hiccup (motion
    blur on one frame, a hand crossing the lens) usually clears within a frame or two, and the
    scene a moment later still overlaps heavily with the last good view, which is exactly what
    a relocalization attempt tests for directly rather than assuming.

    THE TRUST BAR DOES NOT MOVE. A relink is accepted through the exact same
    `_relative_pose` call and the same `MIN_MATCH_INLIERS` floor as an ordinary link - this
    recovers frames a stricter policy discarded, it does not admit a weaker pose to do it.
    Inlier counts for a multi-frame gap are typically lower than a normal one-frame step
    (the camera moved further while unposed), so relinking naturally gets harder the longer a
    break lasts rather than needing a separate rule to say so.

    `max_relocalize_gap` bounds how far back in time a relink may reach, for two reasons: an
    unbounded search lets a repetitive surface (the same tile pattern, two doorways that look
    alike) produce a confident but spurious match to a view from much earlier in the walk, and
    it bounds the number of `_relative_pose` calls attempted per break. Once exceeded, frames
    from the anchor onward stay unposed exactly as before this change - relocalization gives a
    stuck trajectory more chances to recover, it does not guarantee one always exists.
    """
    n = len(keyframes)
    poses: list[Optional[np.ndarray]] = [None] * n
    diag: list[dict] = []
    if n == 0:
        return poses, diag

    poses[0] = np.eye(4)
    anchor = 0
    for i in range(1, n):
        gap = i - anchor
        if gap > max_relocalize_gap:
            # This anchor is exhausted - do not spend another `_relative_pose` call on it.
            # Nothing here can invent a new anchor without a pose to hang it from, so the
            # sequence stays unposed from here on, exactly as before this change, but only
            # after `max_relocalize_gap` real attempts rather than zero.
            diag.append({"link": i, "anchor": anchor, "inliers": 0, "ok": False,
                        "reason": f"exceeded the {max_relocalize_gap}-frame relocalization "
                                  f"window against frame {anchor}"})
            continue

        T_rel, inl = _relative_pose(keyframes[anchor][1], depths[anchor], keyframes[i][1], K)
        if T_rel is None:
            reason = "weak match" if gap == 1 else f"relocalize vs frame {anchor} failed"
            diag.append({"link": i, "anchor": anchor, "inliers": inl, "ok": False,
                        "reason": reason})
            continue

        poses[i] = poses[anchor] @ T_rel
        diag.append({"link": i, "anchor": anchor, "inliers": inl, "ok": True,
                     "gap": gap, "relocalized": gap > 1,
                     "step_m": round(float(np.linalg.norm(T_rel[:3, 3])), 4)})
        anchor = i
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

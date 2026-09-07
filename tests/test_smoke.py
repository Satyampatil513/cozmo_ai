"""Smoke tests for all three tiers. Fast, and they skip cleanly when data is absent.

    python tests/test_smoke.py

These do not check accuracy - that is what benchmark/scripts/ is for. They check that each
tier loads, that the canonical Scene invariants hold, and that the shared measurement path
runs. The point is to catch an interface break the moment it happens, since three loaders now
feed one geometry stage and a change to Frame or Scene can quietly break two of them.
"""
from __future__ import annotations

import glob
import os
import sys
import warnings

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.filterwarnings("ignore")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILURES: list[str] = []
SKIPPED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)


def skip(name: str, why: str) -> None:
    print(f"  SKIP  {name}   {why}")
    SKIPPED.append(name)


def first(pattern: str) -> str | None:
    hits = sorted(glob.glob(os.path.join(ROOT, pattern)))
    return hits[0] if hits else None


def test_imports_and_shared_path():
    """Every tier must import and reach the same measurement entry point."""
    from pipeline.capture import lidar, photo, video           # noqa: F401
    from pipeline.measure import measure_room                  # noqa: F401
    from pipeline.geometry import fuse, lift, openings, planes, walls  # noqa: F401
    check("all tier loaders and shared geometry import", True)

    import inspect
    src = inspect.getsource(measure_room)
    check("measure_room dispatches on poses, not on tier",
          "T_wc is not None" in src and 'room.tier ==' not in src.split("posed =")[0],
          "a failed-odometry video must degrade to the unposed path")


def test_photo_loader():
    d = os.path.join(ROOT, "benchmark", "raw", "photo")
    if not os.path.isdir(d) or not any(os.scandir(d)):
        return skip("photo loader", "no capture in benchmark/raw/photo")
    from pipeline.capture.photo import load
    sc = load(d)
    check("photo scene has rooms", len(sc.rooms) > 0, f"{len(sc.rooms)} rooms")
    f = sc.frames[0]
    check("photo frames upright", f.image.shape[0] > f.image.shape[1],
          f"{f.image.shape[1]}x{f.image.shape[0]}")
    check("photo frames carry intrinsics", f.K is not None and f.K[0, 0] > 0)
    check("photo frames have no poses (by design)", all(x.T_wc is None for x in sc.frames))
    check("scale not silently defaulted to 1.0",
          sc.rooms[0].scale.source in ("none", "monocular_metric", "fused_mono_floor"),
          sc.rooms[0].scale.source)


def test_lidar_loader():
    p = first("benchmark/raw/lidar/*.r3d") or first("benchmark/raw/dev_remote/*.r3d")
    if not p:
        return skip("lidar loader", "no .r3d in benchmark/raw/{lidar,dev_remote}")
    from pipeline.capture.lidar import ARKIT_TO_OPENCV, inspect as r3d_inspect, load
    info = r3d_inspect(p)
    check("r3d inspect reports frames", info.n_frames > 0, f"{info.n_frames}")
    check("r3d has depth size and intrinsics",
          info.depth_size is not None and info.K_rgb is not None)

    sc = load(p, stride=max(1, info.n_frames // 4), max_frames=4)
    fr = sc.rooms[0].frames
    check("lidar frames carry depth", all(f.depth is not None for f in fr))
    check("lidar depth is metric and finite somewhere",
          bool(np.isfinite(fr[0].depth).any()) and float(np.nanmax(fr[0].depth)) < 50)
    check("lidar frames carry poses", all(f.T_wc is not None for f in fr))
    check("lidar scale is sensor-metric", sc.rooms[0].scale.source == "lidar_metric")
    check("ARKit<->OpenCV conversion is its own inverse",
          bool(np.allclose(ARKIT_TO_OPENCV @ ARKIT_TO_OPENCV, np.eye(3))))

    T = fr[0].T_wc
    check("pose is a valid rigid transform",
          bool(np.allclose(T[:3, :3] @ T[:3, :3].T, np.eye(3), atol=1e-6))
          and abs(float(np.linalg.det(T[:3, :3])) - 1.0) < 1e-6,
          f"det={np.linalg.det(T[:3, :3]):.6f}")


def test_video_sampling():
    p = first("benchmark/raw/video/*.MOV") or first("benchmark/raw/video/*.mp4")
    if not p:
        return skip("video loader", "no clip in benchmark/raw/video")
    from pipeline.capture.video import intrinsics_for, sample_frames
    kf, meta = sample_frames(p, every_n=300, max_frames=3)
    check("video sampling returns keyframes", len(kf) > 0, f"{len(kf)} of {meta['n_frames']}")
    check("video blur threshold is relative to the clip",
          "sharpness_threshold" in meta,
          f"median {meta.get('sharpness_median')} thresh {meta.get('sharpness_threshold')}")
    if kf:
        h, w = kf[0][1].shape[:2]
        K, src = intrinsics_for(w, h)
        check("video intrinsics labelled as assumed", "assumed" in src, src)


def test_fusion_and_geometry_on_synthetic():
    """The shared path must work without any capture present."""
    from tests.synthetic_room import make_room
    from pipeline.geometry.fuse import voxel_reduce
    from pipeline.geometry.openings import detect_openings
    from pipeline.geometry.planes import fit_floor_ceiling

    r = make_room(noise_m=0.01, seed=5)
    p2, n2, n_vox, dropped = voxel_reduce(r.points, r.normals, 0.03, 2)
    check("voxel reduction shrinks the cloud", len(p2) < len(r.points),
          f"{len(r.points):,} -> {len(p2):,}")
    check("voxel reduction keeps normals unit",
          bool(np.allclose(np.linalg.norm(n2[np.any(n2 != 0, axis=1)], axis=1), 1.0, atol=1e-6)))

    got = fit_floor_ceiling(r.points, r.normals, threshold=0.03,
                            gravity_prior=np.array([0.0, 0.0, 1.0]), camera_at_origin=False)
    check("geometry returns the 5-tuple contract", got is not None and len(got) == 5)
    g, floor, ceiling, walls, score = got
    ops = detect_openings(walls, r.points, g, floor)
    check("opening detector runs and finds none in a sealed box", len(ops) == 0,
          f"{len(ops)} found")


if __name__ == "__main__":
    for fn in [v for k, v in sorted(globals().items()) if k.startswith("test_")]:
        print(f"\n{fn.__name__}")
        fn()
    print(f"\n{len(SKIPPED)} skipped")
    print("ALL PASS" if not FAILURES else "FAILURES: " + ", ".join(FAILURES))
    sys.exit(1 if FAILURES else 0)

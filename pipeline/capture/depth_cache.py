"""Deterministic on-disk cache for depth inference.

Depth is by far the slowest stage (13 s/frame for Metric3D on CPU) and it is a pure function
of (image bytes, intrinsics, backend). Caching it makes the ablations in the fix loop cheap:
three selector configurations over 28 frames become one inference pass and three fast replays
instead of 18 minutes of identical recomputation.

This is also what the brief asks for. Cached model outputs are acceptable "when the cache
replays deterministically and the live path also runs" - so the key is a content hash of
everything that could change the result, and `--no-cache` runs the live path end to end.

Stored as float32, not float16. float16 resolves ~2 mm at 3 m, which looked far finer than any
error worth chasing - but it measurably moved the benchmark: the legacy "two largest planes"
selector shifted from -12.2% to -15.0% mean between a live run and its own cached replay.
Nothing about the scene changed, only 2 mm of depth quantisation, and an argmax over plane
sizes flipped. That is worth knowing about the baseline (it is chaotic under tiny
perturbations, which is itself an argument for the fix), but a cache must not inject variance
into the measurement, so the extra bytes are the correct trade.
"""
from __future__ import annotations

import hashlib
import os

import numpy as np

CACHE_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "benchmark", "cache", "depth",
)


def key_for(image_path: str, K: np.ndarray, backend: str, work_px: int) -> str:
    """Content hash of everything that can change the depth map.

    Hashes the file's bytes rather than its path and mtime: a capture copied to a new
    directory must hit the same cache entry, and an edited file must miss it.
    """
    h = hashlib.sha256()
    with open(image_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    h.update(np.asarray(K, dtype=np.float64).tobytes())
    h.update(backend.encode())
    h.update(str(work_px).encode())
    return h.hexdigest()[:32]


def load(key: str) -> np.ndarray | None:
    path = os.path.join(CACHE_ROOT, f"{key}.npy")
    if not os.path.isfile(path):
        return None
    try:
        return np.load(path).astype(np.float64)
    except Exception:
        return None            # a corrupt entry should cost a recompute, not a crash


def store(key: str, depth: np.ndarray) -> None:
    os.makedirs(CACHE_ROOT, exist_ok=True)
    path = os.path.join(CACHE_ROOT, f"{key}.npy")
    tmp = path + ".tmp"
    # Written through a file handle rather than by name: np.save appends ".npy" to a path
    # that lacks it, so np.save("x.npy.tmp", ...) silently produces "x.npy.tmp.npy" and the
    # rename below then fails on a file that does not exist.
    with open(tmp, "wb") as fh:
        np.save(fh, depth.astype(np.float32))
    os.replace(tmp, path)      # atomic, so an interrupted run cannot leave a half-written entry


def infer_cached(backend, image_path: str, rgb: np.ndarray, K: np.ndarray,
                 work_px: int, use_cache: bool = True) -> tuple[np.ndarray, bool]:
    """Depth for one frame, from cache when possible. Returns (depth, was_cached)."""
    if not use_cache:
        return backend.infer(rgb, K).depth, False
    k = key_for(image_path, K, backend.name, work_px)
    cached = load(k)
    if cached is not None:
        return cached, True
    depth = backend.infer(rgb, K).depth
    store(k, depth)
    return depth, False

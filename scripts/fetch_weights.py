"""Pre-fetch every model weight the pipeline needs, before the first real run.

    python scripts/fetch_weights.py

WHY THIS EXISTS. Both depth backends fetch their own weights lazily, on first
instantiation - `torch.hub.load` for Metric3D, `transformers.AutoModel...from_pretrained` for
the alternate - which is convenient for development but wrong for a cold walk-in test: the
brief's own constraint is "weights and large binaries fetched by script or volume," and the
walk-in test runs the pipeline live, in front of the people scoring it. A depth backend
silently blocking on its first real request to fetch a few hundred MB over whatever network is
available at the venue is not a good way to spend the first thirty seconds of that.

This script does exactly what `get_backend()` would do on first use, just ahead of time and
with visible progress, so `pip install -r requirements.txt && python scripts/fetch_weights.py`
leaves every model already resident in its normal cache directory
(`~/.cache/torch/hub` for Metric3D, `~/.cache/huggingface` for the alternate) and the
FIRST real run of `run.py` is exactly as fast as every run after it.

The default backend (`metric3d_v2`) is fetched unconditionally - it is what every documented
number in this repo was produced with, and what the walk-in test will actually use. The
alternate (`depth_anything_v2_metric_indoor`) is fetched best-effort: it exists in this repo
as a rejected first attempt (see OVERNIGHT_PROGRESS.md's error-budget section for why), kept
runnable rather than deleted, and a network hiccup fetching it should not block a fresh-machine
setup for the backend that actually ships.
"""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def fetch(name: str, required: bool) -> bool:
    from pipeline.capture.depth import get_backend
    print(f"fetching {name} ...", flush=True)
    t0 = time.time()
    try:
        get_backend(name)
    except Exception as exc:
        level = "FAILED (required)" if required else "skipped (optional)"
        print(f"  {level}: {type(exc).__name__}: {exc}")
        return not required
    print(f"  ready in {time.time() - t0:.1f}s")
    return True


def main() -> int:
    ok = fetch("metric3d_v2", required=True)
    fetch("depth_anything_v2_metric_indoor", required=False)
    if not ok:
        print("\nthe DEFAULT backend failed to fetch - the pipeline cannot run without it.")
        return 1
    print("\nall required weights are cached. `run.py` will not need network access for "
         "depth inference from here on (LiDAR/video odometry never needed it).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

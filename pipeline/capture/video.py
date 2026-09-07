"""Video tier loader: walkthrough clip -> sampled frames -> Scene.

Frame sampling is deliberately sparse and blur-filtered: the downstream backbone is the same
one the photo tier uses, so a video is treated as a photo set with better coverage.

NOT BUILT.
"""
from pipeline.types import Scene


def load(capture_dir: str) -> Scene:
    raise NotImplementedError("video loader")

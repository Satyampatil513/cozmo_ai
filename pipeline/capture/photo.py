"""Photo tier loader: per-room folders of 2 to 8 stills -> Scene.

No depth, no poses. Geometry comes from the shared pointmap backbone; absolute scale comes
from the printed scale card detected in at least one frame per room. Doorway pair shots
carry the adjacency hints.

NOT BUILT.
"""
from pipeline.types import Scene


def load(capture_dir: str) -> Scene:
    raise NotImplementedError("photo loader")

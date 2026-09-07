"""Photo tier loader: per-room folders of 2 to 8 stills -> Scene.

No depth, no poses. Geometry comes from the shared pointmap backbone; absolute scale comes
from a metric depth model fused with the floor plane and the operator's stated camera
height - the protocol places nothing in the room. Doorway pair shots carry the adjacency
hints, and are the only evidence a set of per-room folders contains that two rooms touch.

NOT BUILT.
"""
from pipeline.types import Scene


def load(capture_dir: str) -> Scene:
    raise NotImplementedError("photo loader")

"""Damage detection and metric extent.

Detection runs in image space, then every region is projected onto the wall/floor/ceiling
plane it belongs to using geometry that already exists, which is what turns pixels into
square metres. A region that cannot be assigned to a surface is reported with the assignment
failure stated rather than dropped.

NOT BUILT.
"""


def detect(frames, surfaces):
    raise NotImplementedError

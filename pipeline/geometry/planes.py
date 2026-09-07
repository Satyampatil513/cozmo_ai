"""Floor and ceiling plane extraction, and ceiling height.

Method: gravity-aligned point cloud (ARKit gives gravity on the LiDAR tier; on photo/video
the dominant horizontal plane normal is estimated from the pointmap), then a height histogram
with RANSAC plane fits on the two dominant horizontal bands. Ceiling height is the plane
separation, measured at multiple sample points so the report can state spread as well as value.

The brief scores repeatable-but-biased separately from unrepeatable, so this module reports
both a value and a within-capture spread.

NOT BUILT.
"""


def fit_floor_ceiling(points, normals=None):
    raise NotImplementedError

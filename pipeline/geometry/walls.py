"""Wall extraction and room polygon.

Points between floor and ceiling are projected onto the floor plane, giving a 2D occupancy
density. Wall segments are fitted on that density, lightly regularised toward mutually
perpendicular directions where the evidence supports it, then closed into a polygon.
Floor area is the polygon area.

Explicitly NOT Manhattan-forced: a forced right angle is a silent lie about a room that is
not rectangular, and it would flatter the benchmark.

NOT BUILT.
"""


def extract_walls(points, floor_plane, ceiling_plane):
    raise NotImplementedError

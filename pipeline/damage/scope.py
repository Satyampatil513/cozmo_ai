"""Scope line items, keyed to surfaces.

A lookup from (damage class, surface type) to a restoration action and a quantity basis.
Quantity is taken from the metric extent already computed by the geometry stage, so a scope
item inherits the confidence interval of the measurement it is derived from.
"""

SCOPE_TABLE = {
    ("water_stain", "ceiling"): ("Remove and replace affected ceiling board, seal and repaint", "area_m2"),
    ("water_stain", "wall"): ("Strip finish, dry substrate, make good and repaint", "area_m2"),
    ("crack", "wall"): ("Rake out, fill and reinforce crack, make good and repaint", "long_axis_m"),
    ("crack", "ceiling"): ("Rake out, fill and reinforce crack, make good and repaint", "long_axis_m"),
}


def line_items(damage_regions, surfaces):
    raise NotImplementedError

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


_QUANTITY_FIELD = {"area_m2": "area_m2", "long_axis_m": "long_axis_m"}


def line_items(damage_regions: list) -> list[dict]:
    """Every damage region -> one scope line item, quantity keyed to the metric extent
    geometry already computed - never a re-estimate, so a scope item's number and the damage
    region's own number can never silently disagree.

    A region whose (class, surface_type) has no entry in `SCOPE_TABLE` is skipped, not
    defaulted to a generic action: inventing a restoration action for a combination nobody
    defined is exactly the kind of confident-on-thin-input claim this pipeline avoids
    elsewhere, and there is no reason to make an exception for the one output stage that
    exists to hand a contractor a number.
    """
    out: list[dict] = []
    for r in damage_regions:
        key = (r.damage_class, r.surface_type)
        entry = SCOPE_TABLE.get(key)
        if entry is None:
            continue
        action, basis = entry
        quantity_value = getattr(r, _QUANTITY_FIELD[basis])
        out.append({
            "id": f"scope_{r.id}",
            "surface_id": r.surface_id,
            "damage_id": r.id,
            "action": action,
            "quantity": {
                "value": round(quantity_value, 4),
                "unit": "m2" if basis == "area_m2" else "m",
                "basis": basis,
            },
        })
    return out

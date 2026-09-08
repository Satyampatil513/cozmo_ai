"""Concealed-damage rules.

Every flag names the rule that fired. Rules are explicit and readable rather than learned,
because the brief requires the rule to be reported and a learned score is not a rule.
"""

RULES = [
    {
        "id": "CONCEAL-WATER-01",
        "text": "Water staining on a ceiling surface implies possible saturation in the cavity above; inspect from the floor above or from the roof void.",
        "trigger": {"class": "water_stain", "surface_type": "ceiling"},
    },
    {
        "id": "CONCEAL-WATER-02",
        "text": "Water staining on a wall surface within 0.5 m of the floor implies possible substrate and skirting saturation behind the finish.",
        "trigger": {"class": "water_stain", "surface_type": "wall", "max_height_m": 0.5},
    },
    {
        "id": "CONCEAL-CRACK-01",
        "text": "A crack crossing a wall-to-ceiling junction implies possible structural movement beyond the visible finish.",
        "trigger": {"class": "crack", "crosses_junction": True},
    },
]


def evaluate(damage_region, surface_type: str) -> dict:
    """One damage region -> a concealed-damage flag, naming the rule that fired if any did.

    `damage_region` is a `pipeline.damage.detect.DamageRegion`. `surface_type` is passed
    separately rather than read off the region, because the SAME detected region can be
    re-evaluated against a corrected surface classification without re-running detection.

    Returns `{"raised": False}` when no rule fires - not an error, and not omitted: the brief
    requires every damage region to carry a concealed-damage flag object, and "no rule fired"
    is itself the answer for most detections, stated rather than left implicit.

    Only ONE rule fires per region even if several could match, because "which rule" is the
    thing the brief asks be reported - firing several at once would make that ambiguous. The
    first matching rule in `RULES` wins; order is significant and is the whole reason `RULES`
    is a list, not a set.
    """
    for rule in RULES:
        t = rule["trigger"]
        if t.get("class") != damage_region.damage_class:
            continue
        if "surface_type" in t and t["surface_type"] != surface_type:
            continue
        if "max_height_m" in t:
            h = damage_region.height_above_floor_m
            if not (h == h) or h > t["max_height_m"]:      # h==h is False only for NaN
                continue
        if "crosses_junction" in t and t["crosses_junction"] != damage_region.crosses_junction:
            continue
        return {"raised": True, "rule_id": rule["id"], "rule_text": rule["text"],
               "evidence": (f"{damage_region.damage_class} on {surface_type}, "
                            f"height {damage_region.height_above_floor_m:.2f} m, "
                            f"{damage_region.long_axis_m:.2f}x{damage_region.short_axis_m:.2f} m")}
    return {"raised": False}

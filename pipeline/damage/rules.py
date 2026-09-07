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


def evaluate(damage_region, surface):
    raise NotImplementedError

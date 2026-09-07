# Damage log — what to look for in the flat, and how to record it

The brief needs **one furnished room with damage spanning two damage classes**, plus
per-surface damage regions with a class and a metric extent, concealed-damage flags with the
rule that fired, and scope line items keyed to surfaces.

You live in the property, which is an advantage: real damage beats staged damage on every
axis, and you already know where it is. Walk the flat once with a notepad before you shoot.

## Damage classes to use

Fix this list now and use it everywhere — the schema, the detector, the ground truth, the
report. Changing class names later means re-labelling everything.

| Class | What it looks like | Typical location in an Indian flat |
|---|---|---|
| `water_stain` | Brown/yellow discolouration, often with a tide line | Ceiling under a bathroom, external wall after monsoon, around window heads |
| `paint_peel` | Blistering, flaking, bubbling paint | Damp walls, near skirting, bathroom ceilings |
| `crack` | Linear fracture in plaster | Wall–ceiling junction, corners of door and window frames, above lintels |
| `efflorescence` | White powdery salt bloom | Damp external walls, near floor level |
| `mould` | Dark speckled growth | Bathroom ceilings, behind furniture on external walls, window reveals |
| `impact_damage` | Dent, gouge, hole | Door-handle height on walls, corners |

Two of these is the requirement. You will almost certainly find more than two — a flat
through a few monsoons usually has `water_stain` and `paint_peel` together, since they share
a cause.

## Where to actually look

Damage hides in the places you have stopped noticing:

- **Ceiling corners of every bathroom**, and the ceiling of any room directly below one.
- **The external-facing wall of each bedroom**, especially low down near the skirting.
- **Above and beside every window**, inside the reveal.
- **Behind and under furniture** — beds against external walls, the back of a wardrobe.
- **The wall–ceiling junction all the way round** each room; hairline cracks live there.
- **Around the main door frame** and any door that gets slammed.
- **Under the kitchen sink and around the washing machine**, for older leaks.
- **Balcony thresholds**, where water gets driven in.

## Staging, if you must

If only one class is genuinely present, stage the second — a printed stain sheet taped flat,
or a drawn/taped crack line. That is explicitly allowed.

**Say so plainly in the report.** Staged damage is fine; undisclosed staged damage is the
kind of thing that unravels a submission in a live defense. Mark it `staged=yes` in the log
below and keep the same flag in the JSON output.

Prefer staging into a **furnished** room — the requirement is a furnished room with two
classes, and clutter is part of the difficulty being tested.

## Concealed damage

The output contract asks for concealed-damage flags **with the rule that fired**. This is
inference, not detection: visible evidence implying damage you cannot see.

Write the rules down as you observe, because the rule text has to appear in the output:

- Water stain on a ceiling → flag concealed damage in the floor slab or the bathroom above;
  rule: `ceiling_stain_implies_slab_ingress`.
- Peeling paint at skirting level on an external wall → flag rising damp behind the plaster;
  rule: `low_wall_peel_implies_rising_damp`.
- Stain directly under a window sill → flag failed sealant in the reveal; rule:
  `sub_sill_stain_implies_seal_failure`.
- Efflorescence → flag active moisture migration through the wall; rule:
  `efflorescence_implies_active_moisture`.

Each flag needs the rule name and the visible evidence that triggered it. Three or four
well-chosen rules beat a long list you cannot defend.

## How to record each region

Before the tiered capture, for **every** damage region:

1. **One square-on photo** of the region with a tape or ruler laid in frame next to it. This
   photo is ground-truth evidence and is separate from your tier captures.
2. **Measure the long axis and the short axis** in metres.
3. **Note the surface it sits on** — which wall, or ceiling, or floor — using the same wall
   IDs as your ground-truth sheet. The scope line items have to key to surfaces, so a region
   that is not attached to a surface is not usable.
4. **Note whether it is staged.**

Then row it into `benchmark/ground_truth/damage.csv`.

## Ordering

Log and measure damage **before** the photo, video and LiDAR passes. Two reasons: you will
frame the captures better knowing where the regions are, and if you measure afterwards you
will find yourself measuring a region you cannot locate in any frame.

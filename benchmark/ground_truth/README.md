# Ground truth: conventions and coverage

Laser survey of the 3BHK, 2026-09-08. Read by exactly one loader,
`benchmark/scripts/ground_truth.py`, so no two reports can disagree about what the truth is.

## Conventions a reader has to know

**Ceiling height is 2.74 m.** This supersedes an earlier tape figure of **2.64 m** that was
hardcoded in four separate scripts. The difference is 10 cm - nearly seven times the entire
1.5 cm ceiling gate - so which number is used changes the verdict far more than any pipeline
change does. Every scorer now reads the sheet. If you find `2.64` anywhere, it is stale.

**A blank cell and a `0` both mean "nobody measured this".** The sheet uses `0` as a
placeholder in the rows that were skipped. The loader converts both to `None` and every scorer
reports them as unscoreable. This matters more than it looks: a wall nobody measured, read as
0.000 m, scores a 100% error and would appear in the report as a spectacular pipeline failure
that never happened.

**Floor area is derived, never measured.** No row in the survey measures an area - the rows say
"derive from walls if rectangular". The loader derives `dim1 x dim2` at read time and flags the
result `derived=True`. Deriving in the loader rather than writing it back keeps this file a
record of what someone actually pointed a laser at.

**A door's sill is the floor.** Blank `sill_height_m` on a door row is correct and is read as
0.0, not as unmeasured.

## Coverage

| | measured | of | note |
|---|---|---|---|
| Wall lengths | 19 | 20 | `room_03_bed_two` w4 not measured |
| Ceiling heights | 10 | 10 | two readings per room, opposite corners |
| Floor areas | 0 | 5 | 4 derivable from walls; room_03 is not, missing w4 |
| Doors | 5 | 5 | width and height both measured |
| Windows | - | - | **discarded, see below** |

### Two known problems in the sheet

1. **`room_02_bed_master` states a floor area of 2.9 m²** for a room its own walls describe as
   3.05 x 3.40 = **10.37 m²**. The stated figure looks like a stray wall length. The loader
   reports this as a CONFLICT and uses the derived value; it is not entitled to silently
   correct the sheet. Worth confirming on the next visit.
2. **`room_03_bed_two` w4 is unmeasured**, so that room has no floor area and is the one room
   of five that is not complete. If the room is rectangular, w4 should equal w2 (2.90 m) - but
   that is an inference, not a measurement, and it is not written into this file.

## Windows: discarded

Windows were removed from the survey by the capture operator. Consequence, stated so the
opening gate is not read as better than it is: the brief scores opening widths at
**<= 2 cm on >= 85% of openings, with detection itself scored** - a missed opening and a
phantom opening each count as a miss. With windows discarded, that gate is scored **on doors
only**, over 5 doors. Any window our detector finds is now, by construction, unscoreable
rather than correct - it cannot be credited, and it also cannot be counted as a phantom.
The gate report says this on the row rather than reporting a door-only pass as a full pass.

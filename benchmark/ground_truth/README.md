# Ground truth: conventions, coverage, and one deliberate omission

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
| Cross-room spans | 0 | 3 | **deliberately deferred, see below** |
| Damage | 0 | 2 | no damage staged |

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

## Cross-room spans: deliberately not measured

`spans.csv` is empty and is staying empty for now. This is a decision, not an oversight.

Spans exist to score one thing: the **drift accountability** gate, which asks for a stitched
multi-room footprint with drift correction on and off. `pipeline/stitching/stitch.py` raises
`NotImplementedError`. There is no stitched footprint, so there is nothing a span could be
compared against, and the gate fails on the missing stitch regardless of what this file
contains.

**Why they are not derived instead.** A cross-room span is not recoverable from per-room
dimensions - that is the entire reason the sheet asks for it separately, as its own note says:
"per-room truth cannot catch accumulated drift". Computing `span_03` as the hall's own length
would be circular, and computing `span_01` would need the floor plan's adjacency and offsets,
which nothing in this repo records. Writing a plausible number here would be inventing ground
truth, which is the one failure this whole benchmark exists to prevent.

**What to measure when stitching lands**, so the trip is not wasted:

| span | measurement | why this one |
|---|---|---|
| `span_01` | kitchen far wall -> hall far wall | shortest chain that crosses a doorway |
| `span_02` | master-bed far wall -> bed-three far wall | longest chain, through the connector - accumulates the most drift |
| `span_03` | hall end to end | the connector alone, isolating its own error from the chain |

Measure each in one shot with the laser down the open line, not by adding room dimensions -
a summed span inherits every per-room error and measures the same thing the stitch is being
tested for.

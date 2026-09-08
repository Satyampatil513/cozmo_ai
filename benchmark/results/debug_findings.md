 # What the diagnostic overlays found

Regenerate: `python run.py benchmark/raw/photo --tier photo --out out_dbg --debug`
Sheets land in `out_dbg/debug/`, one per frame: RGB, depth, fitted planes, classification.

Built to answer "is anything fundamentally wrong". Something is, and it was **invisible in the
aggregate numbers** — it had in fact been actively misdiagnosed.

---

## Finding 1 — the bed is being fitted as the floor

`Room 2 / IMG_0447` makes it unarguable: the floor plane is painted over the entire bed, and
**no actual floor is visible anywhere in the frame**. Reported ceiling 2.288 m against a 2.64 m
tape — **35 cm low, which is a bed height.**

This directly refutes a claim made in `baseline_ceiling_height.md`:

> "**It is not furniture.** A bed top mistaken for the floor was the leading hypothesis and it
> is wrong: the frame that lands exactly on truth is in a bedroom with a large bed."

That reasoning was bad. One frame landing on truth in a bedroom shows the failure is not
*universal*; it says nothing about whether it happens in other frames. The correct test was to
look at the frames that were *wrong*, not the one that was right.

### Why the joint selector did not catch it

The `bounding` signal exists precisely to reject a surface with the room below it — and it
cannot fire here, because **the true floor was never observed**. In a frame where the bed
fills the lower field of view, the bed *is* the lowest surface in the cloud. No geometric test
can reject a plane for being above a floor that is not in the data.

The `camera` signal should have penalised it: bed-to-camera is ~1.0 m where floor-to-camera is
~1.5 m. Measured implied camera heights confirm the split exactly — 1.01 m and 1.13 m on
bed-floored frames, 1.52 m on a hallway frame. But the prior is `(1.45 m, 0.45 m)` wide, which
scores 1.05 m at 0.45 — a mild penalty, not a rejection.

## Finding 2 — the photo tier's good mean is two errors cancelling

| Group | Mean error | SD | n |
|---|---|---|---|
| Bedrooms (bed or mattress in frame) | −2.2% | **11.2%** | 16 |
| Hallway + Kitchen (open floor visible) | **+8.7%** | 4.5% | 9 |

The gap between the groups is 10.9 percentage points, **29 cm** — squarely a bed height, given
Room 1 has a thin floor mattress (~8 cm, and reads −3.2%) while Rooms 2 and 3 have raised beds.

Within the bedrooms the errors are **bimodal**, not noisy: −13.3, −13.7, −14.4, −19.1% where
the bed is taken as floor, against +10.0, +13.2, +18.9% where real floor is visible and only
the depth-scale bias applies. That bimodality is the signature of two mechanisms, not one
noisy one.

**So the photo tier's headline +1.8% mean is cancellation between a −30 cm geometric error and
a +9% depth-scale error.** It is not accuracy. This is the same trap flagged earlier for Room
3's "+2.0%" — it turns out to apply to the entire tier, and reporting the mean alone would
have been flattering and wrong.

## Finding 3 — the Hallway's error is depth, not geometry

`Hallway / IMG_0431`: classification is visibly correct — floor on floor, ceiling on ceiling,
walls on walls, no furniture confusion. It still reads 2.830 m, +7.2%.

Its depth range is **1.70–9.21 m**, against 1.58–4.53 m in a bedroom. The open-floor group's
tight +8.7% ± 4.5% is a clean scale bias in deep spaces, with the geometry doing its job.

Two different rooms, two different root causes, opposite signs. Any single "fix the ceiling
error" would have addressed at most one of them.

## Finding 4 — the abstentions are correct

`Room 3 / IMG_0458` (score 0.0000) looks through an open doorway into an adjoining space. Two
floor levels and a door head are in frame; there is no defensible floor/ceiling pair. Refusing
to report is the right behaviour and the overlay confirms it is refusing for the right reason.

---

## What follows

**Fusion is now the priority for the photo tier, not a better depth model.** Frames disagree
because different frames see different surfaces — in a fused cloud from several viewpoints, at
least some frames see real floor, and the lowest large horizontal plane is the floor. This
error mode disappears with poses; it cannot be fixed frame by frame.

**Tighten the camera-height prior** as an interim guard. It is the only signal that can
distinguish a bed from a floor when no floor is visible, and it is currently too permissive.
Cheap, and it converts a silent 35 cm error into an abstention.

**Report per-group, never per-tier-mean.** The aggregate hides two opposing biases. The
benchmark should split by whether real floor was observed.

**Method note.** Every one of these was sitting in data collected hours earlier and none was
visible in the tables. The overlays took under an hour to build. That is the argument for
writing the diagnostic view before the third round of numerical tuning, not after.

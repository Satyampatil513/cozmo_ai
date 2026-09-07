# Capture Protocol — Route 2, stock capture

Version 0.3. One page. A non-engineer follows this literally, and at the defense you will.

## Install

| Tier | App | Device needed |
|---|---|---|
| Photo | Native iOS Camera | any iPhone 15+ |
| Video | Native iOS Camera | any iPhone 15+ |
| LiDAR | **Record3D** (App Store, Marek Simonik) | iPhone **Pro / Pro Max** 15 or newer |

LiDAR exists only on Pro-class iPhones. On a non-Pro device the LiDAR tier is unavailable and
our pipeline says so loudly rather than quietly running the video path. Photo and video tiers
run on any iPhone 15 or newer.

**Camera settings:** Formats → Most Compatible. Video → 1080p/30. Live Photos off. **1x wide
lens only** — never 0.5x or 3x, and never switch lens inside a room. Flash off.

## The scale card

Print `assets/scale_card_A4.pdf` at **100% / Actual size — not "Fit to page"**. Verify with a
tape against the ruler printed on the card: the 100 mm mark must measure 100 mm. A print
scaled to 96% makes every wall we report 4% short, and no amount of processing can detect it.

Tape one card per room, **flat on a matte wall at chest height**. Not folded, not curled, not
on glass, mirror or glossy tile. It stays in place for all three tiers of that room.

Photos and video are scale-ambiguous — geometry from images is only recovered up to an
unknown scale factor. The card is where metres come from. Without it those tiers cannot
honestly report any absolute dimension, and we would rather refuse than guess.

If no printer is available, any bank or ID card works as a fallback (ISO/IEC 7810 ID-1,
85.60 × 53.98 mm). It is smaller and therefore less precise at distance; the report states
wherever the fallback was used.

## Folders

One folder per room, real name after the number. The number sets processing order only.

```
capture/
  room_01_living/
  room_02_bed_master/
  room_03_bed_two/
  room_04_bed_three/
  room_05_hall/          <- the connector
```

## Photo tier — 6 to 8 stills per room

Phone upright, chest height, 1x lens.

1. One from each corner, shooting toward the opposite corner.
2. One straight-on frame of the scale-card wall from ~2 m, whole card visible.
3. One frame per opening (door, window), square-on, whole opening in frame.

**Doorway pair, once per connected pair of rooms.** Stand in the doorway and take one photo
into each room **without moving your feet**. Save both into the lower-numbered room's folder
as `doorway_to_room_NN_a.jpg` and `_b.jpg`. This is the only adjacency evidence a set of
photo folders contains; without it the rooms cannot be placed relative to each other.

Consecutive shots should overlap by roughly half a frame. Two stills is the accepted minimum
and the intervals widen accordingly.

## Video tier — one clip per room, 45 to 90 s

Walk the perimeter slowly, phone upright at chest height, tilting gently so the floor-to-wall
join and the ceiling-to-wall join both pass through frame on every wall. Pause 2 s facing the
scale card. Pause 2 s square-on to each opening. Finish where you started, overlapping the
first few seconds.

Do not walk backwards. Do not spin on the spot.

**Whole-property clip:** one continuous recording through every space, walking squarely and
slowly through each doorway, returning to the start.

## LiDAR tier — Record3D

Highest depth quality available; do not change settings between rooms. Same walk as the video
tier. **Stay 0.5 m to 4 m from surfaces** — depth returns nothing closer and degrades badly
further out. Close the loop back to your start point.

Export: Library → select recording → Export → `.r3d`. One file per room, plus one continuous
whole-property recording.

## Damage

Capture existing damage as part of the normal walkthrough — no special handling, no special
framing. Where a second damage class has been staged, the report says so plainly. Staged
damage is acceptable; undisclosed staged damage is not.

## Hard surfaces

Mirrors, glass, large windows in direct sun, glossy or wet-look surfaces, and single-point-lit
rooms all degrade every tier. Where one is unavoidable, capture it and let us report it as a
declared failure mode rather than quietly omitting the surface.

## Handoff

Save to Files → upload to Drive/iCloud → pull down on the processing machine. Then:

```
python run.py capture/ --out out/
```

## Privacy

A lived-in home. Clear documents, screens and people from frame before capturing anything
that will be submitted.

## Ambiguity policy

If any instruction on this page is ambiguous, the capture reflects that and so does the
output. Tell us which line was unclear and this page gets a version bump — that is the
feedback loop the page is for.

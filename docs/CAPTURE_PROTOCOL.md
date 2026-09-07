# Capture Protocol — Route 2, stock capture

Version 0.4. One page. A non-engineer follows this literally, and at the defense you will.

**Nothing to print, nothing to place in the room.** Install an app, walk, hand over the
files. Any picture in, results out.

## Install

| Tier | App | Device needed |
|---|---|---|
| Photo | Native iOS Camera | any iPhone 15+ |
| Video | Native iOS Camera | any iPhone 15+ |
| LiDAR | **Record3D** (App Store, Marek Simonik) | iPhone **Pro / Pro Max** 15 or newer |

LiDAR exists only on Pro-class iPhones. On a non-Pro device the LiDAR tier is unavailable and
our pipeline says so loudly rather than quietly running the video path. Photo and video run
on any iPhone 15 or newer.

**Camera settings:** Formats → Most Compatible. Video → 1080p/30. Live Photos off. **1x wide
lens only** — never 0.5x or 3x, and never switch lens inside a room. Flash off.

## Tell us one number

**Roughly how high you held the phone**, in centimetres — chest height for most people, about
140–155 cm. Your own height is a fine answer if that's easier; we'll take 0.82 of it.

Write it in `capture/capture_info.txt` as `camera_height_cm: 145`, or just tell us.

Photos and video contain no absolute size information — a room and a perfect dollhouse
replica of it produce identical pixels. We recover metres from a monocular metric depth model,
and this one number gives us a second, independent estimate from the floor plane. Where the
two disagree is our scale uncertainty, which is how the confidence intervals get their width
instead of us inventing one. An estimate to the nearest 5 cm is genuinely enough; a wrong
number is worse than a rough one, so guess honestly rather than precisely.

If you skip it we still work, with wider intervals and `scale_source = monocular_metric`
recorded in the output.

## Folders

One folder per room, real name after the number. The number sets processing order only.

```
capture/
  capture_info.txt
  room_01_living/
  room_02_bed_master/
  room_03_bed_two/
  room_04_bed_three/
  room_05_hall/          <- the connector
```

## Photo tier — 6 to 8 stills per room

Phone upright, chest height, held level, 1x lens.

1. One from each corner, shooting toward the opposite corner. Stand about half a metre out
   from the corner, not pressed into it.
2. One frame per opening (door, window), square-on, whole opening in frame.
3. Fill up to 8 from partway along each wall.

**Include the floor.** Each frame should show where the wall meets the floor. The floor plane
is one of our two scale anchors, and frames shot level from chest height give it to us for
free — which is the main reason for "hold the phone level" above.

**Doorway pair, once per connected pair of rooms.** Stand in the doorway and take one photo
into each room **without moving your feet**. Save both into the lower-numbered room's folder
as `doorway_to_room_NN_a.jpg` and `_b.jpg`. This is the only adjacency evidence a set of
photo folders contains; without it the rooms cannot be placed relative to each other.

Consecutive shots should overlap by roughly half a frame. Two stills is the accepted minimum
and the intervals widen accordingly.

## Video tier — one clip per room, 45 to 90 s

Walk the perimeter slowly, phone upright at chest height, tilting gently so the floor-to-wall
join and the ceiling-to-wall join both pass through frame on every wall. Pause 2 s square-on
to each opening. Finish where you started, overlapping the first few seconds.

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

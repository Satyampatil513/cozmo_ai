# Capture Protocol — Route 2, stock capture

Version 0.5. One page. A non-engineer follows this literally, and at the defense you will.

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

## Optional: one number

**Roughly how high you held the phone**, in centimetres — chest height, about 140–155 cm.
Write it in `capture/capture_info.txt` as `camera_height_cm: 145`, or just tell us.

Entirely optional. We recover metres from a metric depth model either way; this gives us a
second, independent estimate off the floor plane, and the two disagreeing is how the
confidence intervals get their width instead of us inventing one. Nearest 5 cm is plenty.
Skip it and everything still runs, with wider intervals and `scale_source =
monocular_metric` recorded in the output.

## Folders

One folder per room. Any names you like.

```
capture/
  living/
  bedroom1/
  bedroom2/
  bedroom3/
  hallway/
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

**One shot through each doorway.** Stand in the doorway and take a photo into the next room.
Name it after that room: `doorway_to_hallway.jpg`. This is the only adjacency evidence a set
of per-room photo folders contains; without it the rooms cannot be placed relative to each
other.

Consecutive shots should overlap by roughly half a frame. Two stills is the accepted minimum
and the intervals widen accordingly.

## Video tier — one walkthrough of the whole property

One continuous clip, no stopping, through every room and back to where you started.

Phone upright at chest height, walking slowly — about a step every two seconds. Tilt gently
up and down as you go so the floor-to-wall join and the ceiling-to-wall join both pass
through frame on every wall. Pause 2 s square-on at each opening. Through a doorway: face it
and walk straight through slowly, without cutting the corner.

Do not walk backwards. Do not spin on the spot. Fast motion is the main cause of an unusable
clip.

Per-room clips are accepted too, if that suits you better — the pipeline segments a
whole-property walk by room either way.

## LiDAR tier — Record3D

Highest depth quality available; do not change settings between rooms. Same walk as the video
tier. **Stay 0.5 m to 4 m from surfaces** — depth returns nothing closer and degrades badly
further out. Close the loop back to your start point.

Export: Library → select recording → Export → `.r3d`. One continuous whole-property
recording, or one file per room.

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

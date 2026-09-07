# Capture Protocol (Route 2: stock capture)

One page. A non-engineer follows this literally. Version 0.2.

## What to install

| Tier | App | Notes |
|---|---|---|
| LiDAR | Record3D (App Store, Marek Simonik) | Exports `.r3d`: RGB, LiDAR depth, camera intrinsics, per-frame 6-DoF ARKit pose. Requires iPhone 15 Pro / Pro Max. Free tier caps clip length, check before a long walkthrough. |
| Video | Native iOS Camera | 1080p or 4K, 30fps, rear wide lens. |
| Photo | Native iOS Camera | Standard stills, rear wide lens, HEIC or JPEG. |

## What you need in the room

One **scale card** per room: an A4 sheet printed from `assets/scale_card_A4.pdf` (ArUco
marker, 150 mm side), or, if no printer is at hand, any standard bank/ID card laid flat
against the wall (ISO/IEC 7810 ID-1, 85.60 x 53.98 mm — every wallet has one). The bank-card
fallback is less robust at distance; note in the report whenever it was used instead of the
printed marker.

Tape or hold the card flat to a wall at roughly chest height, fully visible, not folded, not
on a curved or reflective surface. It stays in place for all three tiers of that room.

The scale card is how absolute size is recovered from photos and video, which are otherwise
scale-ambiguous. Without it those tiers cannot honestly report metres.

## Folder naming (all tiers)

```
capture/
  room_01_living/
  room_02_bedroom_a/
  room_03_bedroom_b/
  room_04_bedroom_c/
  room_05_hall/          <- the connector
```

Real room name after the number. The number sets processing order only.

## Photo tier

Per room, 6 to 8 stills (2 is the accepted minimum, and intervals widen accordingly):

1. One from each corner, shooting toward the opposite corner.
2. One straight-on frame of the wall carrying the scale card, whole card visible.
3. One frame per opening (door or window), square-on, whole opening visible.

**Doorway shot, required once per connected pair.** Stand in the doorway between two rooms and
take one photo into each room without moving your feet. Save both into the lower-numbered
room's folder as `doorway_to_room_NN_a.jpg` and `_b.jpg`. This establishes that the rooms are
adjacent and how they sit relative to each other.

Phone upright, chest height. No zoom, no Portrait mode, no Live Photos.

## Video tier

One continuous clip per room, 45 to 90 seconds. Walk the perimeter slowly and steadily, phone
upright at chest height, sweeping so the floor-to-wall join and the wall-to-ceiling join both
pass through frame. Pause 2 seconds facing the scale card. Pause 2 seconds facing each opening.
Do not walk backwards. Do not spin on the spot.

Whole-property clip: walk room to room without stopping recording, passing slowly through each
doorway.

## LiDAR tier

Record3D, LiDAR mode, higher-quality depth if offered. Same walk as the video tier. Stay
between 0.5 m and 4 m from surfaces; LiDAR depth degrades outside that band. Export `.r3d`
(Library, select recording, Export).

## Damage

If the room has real, existing damage (staining, cracking, peeling), photograph and video it
as part of the normal walkthrough, no special handling. If a second damage class is needed and
isn't naturally present, stage it (printed stain sheet or tape line) and say so plainly in the
report: staged damage is fine, undisclosed staged damage is not.

## What to avoid, all tiers

Mirrors, glass doors, large windows facing direct daylight, glossy or wet-look surfaces, rooms
lit by a single point source. Where unavoidable, capture at least one deliberately, and report
it as a declared hard case rather than skipping it.

## Handoff

Save to Files, upload to Drive/iCloud, pull down on the processing machine (no Mac in this
build, so no AirDrop). Then:

```
python run.py capture/
```

## Privacy

This is a lived-in home. Before capturing, clear documents, screens and people from frame in
every room being submitted as benchmark data.

## Ambiguity policy

If any instruction here is ambiguous, the capture reflects that and so does the output. Report
the ambiguity and this page gets a version bump.

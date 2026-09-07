# Remote dev capture — one room, from wherever the Pro device is

Send the section below the line, unedited. It assumes no context.

## What this is and is not

This is **development data**, not benchmark data, and the distinction is worth being strict
about because it decides what we may later claim.

It unblocks real work: the `.r3d` loader, the plane and wall stages, opening detection, and
a first honest accuracy number for the LiDAR tier against a tape. That is most of the
geometry pipeline, and none of it needs the room to be ours.

It cannot satisfy the benchmark. The brief requires **the same rooms captured at all three
tiers**, with laser or tape ground truth on everything, and every tier comparison,
repeatability number and head-to-head is defined against those same rooms. A room we cannot
re-enter, re-measure or re-shoot is not that. It also cannot serve the walk-in test, where
all three tiers must run cold on a space we have never seen.

So: build against this, report nothing from it. The benchmark still needs a LiDAR-capable
device inside the 3BHK at some point, and that remains an open problem to solve — a local
friend, a rented or borrowed Pro, or a second-hand iPad Pro.

Files land in `benchmark/raw/dev_remote/` — deliberately outside `benchmark/raw/photo|video|lidar`,
so dev data can never be swept into a benchmark run by a glob.

---

# Room capture — what I need, and why

Thanks for doing this. It should take about 40 minutes, and it unblocks the part of the
project I can't build without real sensor data.

I need **one room**, captured three ways, plus about ten tape measurements of it.

**Pick the simplest room you have** — rectangular, four flat walls, not too much furniture.
A bedroom is usually ideal. A room with a sloped ceiling, an L-shape, or wall-to-wall
clutter makes this much less useful.

## Before you start

**Check your phone has LiDAR.** Settings → General → About → Model Name. It needs to say
**Pro** or **Pro Max** (iPhone 12 Pro or later), or be an iPad Pro from 2020 or later.
If it doesn't say Pro, the main part of this isn't possible on that device — tell me and
we'll rethink.

**Install Record3D** (App Store, by Marek Simonik).

**Test the export first, before capturing anything real.** Record 15 seconds of anything,
then: Library → select it → Export → **`.r3d`** → save to Files → upload to the Drive folder
I shared. Message me when it lands and I'll confirm it opens on my machine.

Please don't skip this. Record3D's free tier limits recording length and some export
formats, and it's much better to find that out now than after you've captured the room.

---

## Part 1 — The LiDAR scan (the important one)

Open Record3D, LiDAR / depth mode, highest depth quality it offers.

1. Start in a corner. Hold the phone **upright, at chest height**, in both hands.
2. Press record and **stand still for 3 seconds**.
3. Walk the perimeter of the room **slowly** — roughly one step every two seconds. Slower
   than feels necessary.
4. As you walk, **tilt the phone gently up and down**, so the line where the wall meets the
   **floor** and the line where the wall meets the **ceiling** both pass through the frame
   on every wall. Those two lines are literally what the room's dimensions get computed
   from — a scan that only sees the middle of the walls gives me nothing.
5. **Stay between 0.5 m and 4 m** from whatever you're pointing at. Closer than half a metre
   and the depth sensor returns nothing; past about 4 m it gets too noisy to use.
6. **Pause 3 seconds square-on to the door.** Then 3 seconds square-on to each window.
7. Finish where you started and **keep recording 3 more seconds**, overlapping your opening
   view. Closing the loop lets me correct for drift.

Aim for **60 to 120 seconds**.

**Don't:** walk backwards, spin on the spot, cover the sensor, or move the phone quickly.
Fast motion is the main reason a scan has to be thrown away.

**Then do it a second time.** Leave the room, wait a few minutes, come back and walk it
again **fresh** — don't copy your first walk from memory. I'm testing whether the system
gives the same answer twice, and an exact repeat measures nothing.

Export both as `.r3d`: `dev_room_pass1.r3d`, `dev_room_pass2.r3d`.

---

## Part 2 — Photos and video from the same phone

Same room, straight after, nothing moved.

**Camera settings first:** Settings → Camera → Formats → **Most Compatible**; Record Video →
**1080p / 30 fps**; Live Photo **off**. Stay on **1x** the whole time — never 0.5x or 3x.

**Photos — 8 of them.** Phone vertical, chest height, held level (not tilted down):

- One from **each of the four corners**, shooting toward the opposite corner. Stand about
  half a metre out from the corner, not jammed into it.
- One **square-on to the door**, whole frame in shot.
- One **square-on to each window**.
- Fill up to 8 with shots from halfway along each wall.

The one rule that matters: **each photo should share about half its view with another
photo.** Eight overlapping photos are worth far more than eight unrelated ones.

**Video — one clip, 45 to 90 seconds.** Same slow perimeter walk as the LiDAR scan, same
gentle up-and-down tilt, pause square-on at the door and windows, finish where you started.

## About scale

The LiDAR scan measures real distances by itself, so it needs nothing extra — that part is
covered.

Photos and video **can't** recover real size on their own; they need a known-size object in
frame. Two options:

- **Best:** I'll send you a PDF to print on A4 at **100% / actual size** (not "fit to
  page"). Tape it flat on a wall at chest height and make sure it's in several photos and
  in the video. It has a ruler printed on it — check with a tape that the 100 mm mark
  really is 100 mm.
- **If you can't print:** tape a **bank or ID card** flat to the wall at chest height,
  and get a few photos square-on to it from about a metre. Every such card worldwide is
  exactly 85.60 × 53.98 mm. Less accurate than the printed sheet, but workable.

Either way, leave it in place for the photos and the video.

---

## Part 3 — The measurements

This is what makes the whole thing useful — without it I can't tell whether my output is
right. **Please use a tape, be careful, and write the numbers down in the room** rather than
from memory.

Just send me these as a WhatsApp message, or a photo of your notepad.

```
Room:            (e.g. "bedroom, rectangular")

Wall 1 length:            m      (start at the door wall, go clockwise)
Wall 2 length:            m
Wall 3 length:            m
Wall 4 length:            m

Ceiling height, point A:  m      (in one corner)
Ceiling height, point B:  m      (in the opposite corner - floors aren't level,
                                  and the difference is something I need)

Door width:               m      (measure the frame opening, not the door itself)
Door height:              m

Window 1 width:           m      (the frame opening)
Window 1 height:          m
```

Two things that catch people out:

- **Measure walls at floor level**, along the skirting, not at waist height.
- **Measure openings at the frame** — the hole in the wall — not the door or window panel,
  which is smaller.

If anything is awkward to reach, tell me it's approximate rather than guessing. A number
labelled uncertain is useful; a confident wrong number is worse than none, because I'll
chase a bug that doesn't exist.

Also: a rough **sketch of the room** — a rectangle with the door and windows marked and the
wall numbers on it — is enormously helpful and takes a minute.

---

## Sending it over

Into the shared Drive folder:

```
dev_room_pass1.r3d
dev_room_pass2.r3d
photos/          (the 8 stills)
video.mov
measurements     (message or photo of the notepad)
sketch           (photo)
```

**Please don't delete anything off the phone** until I've confirmed every file opens.

And if any instruction above was unclear, tell me which line — the wording is something I'm
being graded on, so that feedback is genuinely useful rather than a bother.

# Photo and video capture — the whole thing

Nothing to print. Nothing to install. Nothing to put on the walls.

Open the camera, take photos, walk through with video. Turning that into measurements is the
software's job, not yours.

---

## Settings, once

Settings → Camera: **Formats → Most Compatible**, **Record Video → 1080p/30**, Live Photos
**off**.

Then leave the camera on **1x** for everything. Never 0.5x or 3x. This one is worth caring
about — mixing lenses inside a room changes the optics mid-reconstruction and quietly makes
the result worse rather than failing loudly.

## Folders

```
benchmark/raw/photo/
    living/
    bedroom1/
    bedroom2/
    bedroom3/
    hallway/
```

Any names you like, one folder per room. Use the same names in your ground-truth sheet.

---

## Photos — 6 to 8 per room

Phone vertical, chest height, held level.

- One from **each corner**, shooting at the opposite corner.
- One **square-on to each door and window**.
- Fill up to 8 wherever coverage looks thin.

**Two things actually matter:**

**Overlap.** Consecutive photos should share about half their view. Six overlapping photos
beat eight unrelated ones — with no overlap the software cannot tell they are the same room.

**Shoot through the doorways.** For each doorway, stand in it and take one photo into the
next room. Name it after that room: `doorway_to_hallway.jpg`.

That second one is the only thing linking your rooms together. Per-room photo folders contain
nothing else saying two rooms touch, and the whole-property stitch is a scored gate. It is
one extra photo per doorway — four in a 3BHK.

Keeping the **wall-floor line in shot** helps too; held level at chest height it will be.

---

## Video — walk the whole flat, twice

One continuous clip, no stopping, through every room and back to where you started:

living → hallway → bedroom1 → hallway → bedroom2 → hallway → bedroom3 → hallway → living

- Phone vertical, chest height, both hands.
- **Slowly.** About a step every two seconds. Fast motion is the main reason a clip is
  unusable.
- **Tilt gently up and down** as you walk, so the wall-floor line and the wall-ceiling line
  both pass through frame on every wall. Those two lines are what heights and widths get
  measured from.
- Pause 2 seconds square-on at each door and window.
- Through a doorway: face it, walk straight through slowly, do not cut the corner.

**Then do the whole walk a second time**, a few minutes later, walked fresh rather than
copied from memory. Two independent walkthroughs satisfy the repeatability gate for every
room at once, which is the cheapest way to get it.

Do not walk backwards. Do not spin on the spot.

---

## Two small extras the brief asks for

**A 2-photo room.** Pick any room, take exactly 2 photos, put them in `photo/min2_<room>/`.
The brief sets 2 stills as the floor — this is what shows the intervals widening honestly
instead of staying confident on thin input.

**One hard case.** Deliberately shoot something that should break it: a mirror, a glass door,
glossy tile, or a room lit by a single lamp. Into `photo/hard_case/`. A failure you measured
and declared scores; one you never looked for gets found at the defense.

---

## Optional: your phone height

If it is easy, measure how high you hold the phone (chest height, usually 140–155 cm) and
put it in `benchmark/raw/capture_info.txt` as `camera_height_cm: 145`.

Photos carry no absolute size information, so the pipeline gets metres from a depth model.
Your phone height gives it a second, independent estimate off the floor plane, and the two
disagreeing is what sets how wide the confidence intervals are. One tape measurement, once,
ever — not per room, not per capture. Skip it if you like; everything still runs, with wider
intervals.

---

## Ground truth — separate from the capture, and the part that decides your score

These measurements never enter the pipeline. They are what you score its output against, so
if they are sloppy a correct pipeline looks broken and you cannot tell which you have.

Per room, into `benchmark/ground_truth/`:

- Every **wall length**, at floor level along the skirting.
- **Ceiling height at two points**, not one — floors are not level and the gate is tight.
- Every **opening**: width and height, measured at the frame, not the door or window panel.
- **Floor area** — derive from walls if rectangular, measure directly if not.
- **Damage regions**: long axis and short axis.
- A few **cross-room spans** (living wall to hallway wall, and the longest chain you have).
  Per-room measurements cannot detect drift accumulating across the stitch, and drift is its
  own gate.

A **laser measurer** is worth the ₹1,500–2,500. The ceiling gate is 1.5 cm, tighter than a
tape held across a room is reliably good for.

Measure twice where you can and keep both numbers — the disagreement is your ground-truth
uncertainty, and reporting it beats pretending the tape is exact.

---

## Then check it

```
python scripts/validate_capture.py benchmark/raw/photo
python scripts/validate_capture.py benchmark/raw/video --tier video
```

Run this the same day, while you can still walk back into the room. It catches what is
invisible while shooting and unfixable later: a lens that switched to 0.5x, a room with too
few stills, a room nothing connects to, soft frames.

---

## The whole thing on one screen

| | |
|---|---|
| Photos | 6–8 per room, overlapping, **+1 shot through each doorway** |
| Video | one slow walk through the whole flat, **done twice** |
| Extras | a 2-photo room, one deliberately hard surface |
| Optional | phone height in `capture_info.txt` |
| Separately | tape/laser ground truth, including cross-room spans |

**What people get wrong:** walking too fast, no overlap between shots, forgetting to shoot
through the doorways, and letting the camera slip off 1x.

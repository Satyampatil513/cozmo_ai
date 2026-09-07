# Photo and video field guide — print this and carry it

Step by step, in the order you do it. iPhone 17, one room at a time.

`MY_SHOT_LIST.md` is the reasoning. This is the procedure.

---

## Step 0 — Once, before you start (15 minutes)

**Phone settings** (Settings → Camera):

- Formats → **Most Compatible**
- Record Video → **1080p at 30 fps**
- Preserve Settings → Camera Mode → **On**
- Live Photo → **Off** (in the camera app, the circular icon top-right, slash through it)
- Grid → **On**

**Nothing to print, nothing to put on the walls.** Install nothing beyond the stock camera.

**Note your phone height once.** Roughly how high you hold the phone when shooting — chest
height, about 140-155 cm for most people. Measure it once with a tape and write it into
`benchmark/raw/capture_info.txt` as `camera_height_cm: 145`.

That single number is one of the two ways the photo and video tiers recover real-world
size (the other is a metric depth model). To the nearest 5 cm is fine. Then hold the phone
at that height consistently — which you were going to do anyway.

---

## Step 1 — Per room, the photo set

You are taking **6 to 8 photos**. Hold the phone **vertically**, at **chest height**,
**level** — not angled up at the ceiling. Stay on **1x**. Never pinch-zoom.

### The four corner shots

Stand in a corner, back roughly to it, and shoot toward the **opposite** corner.
Repeat in all four corners.

```
   C1 ──────────────── C2         Stand at C1, shoot toward C3.
   │                    │         Stand at C2, shoot toward C4.
   │       room         │         Stand at C3, shoot toward C1.
   │                    │         Stand at C4, shoot toward C2.
   C4 ──────────────── C3
```

**Stand about half a metre out from the corner**, not jammed into it. Pressed against the
wall you lose the two walls right beside you, which are the ones that corner was supposed
to cover.

### Then two to four more

5. **Each door**: stand square-on, whole door frame in shot plus some wall around it.
6. **Each window**: same.
7. **Fill up to 8** from partway along each wall, wherever coverage looks thin.

### Keep the floor in shot

Every frame should show **where the wall meets the floor**. The floor plane is one of the
two things that give the room its real size, and holding the phone level at a known height
hands it to us for nothing. A set of photos angled up at the ceiling has no scale anchor.

### The one rule that matters more than the count

**Every photo must share about half its view with another photo.**

Six overlapping photos beat eight unrelated ones. If two shots have nothing in common, the
software cannot work out that they're the same room. When in doubt, take an extra shot
halfway between two you already have.

---

## Step 2 — The doorway pairs (do not skip)

**Once for every doorway that connects two rooms.**

Stand **in the doorway**. Take one photo facing into the first room. Then, **without moving
your feet**, turn around and take one facing into the other room.

Feet still. That's the whole trick — the two photos share a viewpoint, which is what tells
the software the rooms are next to each other.

Save both into the **lower-numbered** room's folder:

```
doorway_to_room_05_a.jpg      <- facing into this room
doorway_to_room_05_b.jpg      <- facing into room_05
```

In a 3BHK that's normally four pairs: living↔hall, and hall↔each of the three bedrooms.

**If you skip these, the rooms cannot be assembled into a floor plan at all.** Per-room
folders contain nothing else that says two rooms touch.

---

## Step 3 — Per room, the video

**One continuous clip, 45 to 90 seconds.** Phone vertical, chest height, both hands.

1. Start in a corner. Press record. **Stand still for 3 seconds** before moving.
2. Walk the perimeter of the room, **slowly** — about one step every two seconds.
3. As you walk, **tilt gently up and down**, so that the line where the wall meets the
   **floor** and the line where the wall meets the **ceiling** both pass through frame on
   every wall. Those two lines are what the room's dimensions get measured from. A video of
   just the middle of the walls is close to useless.
4. **Stop 2 seconds square-on at each door and each window.**
5. Finish where you started and **keep recording for 3 more seconds**, overlapping your
   opening view. Closing the loop lets drift be corrected; an open-ended walk can't be.

**Never:** walk backwards, spin on the spot, or swing the phone quickly. Fast motion is the
number one cause of a clip that has to be thrown away.

---

## Step 4 — The whole-flat video

**One single clip, no stopping, through every room and back to where you began.**

Walk it as a loop: living → hall → bedroom 1 → back to hall → bedroom 2 → back to hall →
bedroom 3 → hall → living.

Going through a doorway: **face it square, walk straight through slowly, don't cut the
corner.** The doorways are where the rooms get linked, so they're where slowness pays.

If it felt bad, do it again. It's two minutes.

---

## Step 5 — The repeat room

Pick your **emptiest** room. Do the photo set **and** the video again.

Leave the room first, do something else for five minutes, then come back and shoot it
**fresh** — don't retrace your first walk from memory. The point is to test whether the
system gives the same answer twice, and copying yourself exactly measures nothing.

Save into folders ending `_pass2`.

---

## Step 6 — The two odd ones

**The 2-photo room.** Pick any room and take **exactly 2** photos of it, into its own
`_min2` folder. This proves the floor of "any picture in, results out".

**The hard case.** Deliberately shoot one thing that should break the system: a mirror, a
glass door, glossy tile, or a room lit by one lamp at night. Into a `hard_case` folder.
A failure you measured and declared scores; one you didn't find gets found at the defense.

---

## Step 7 — Off the phone, then validate

Files → upload to Drive → download on the laptop into:

```
benchmark/raw/photo/room_01_living/
benchmark/raw/video/room_01_living/
```

Then, **the same evening**:

```
python scripts/validate_capture.py benchmark/raw/photo
python scripts/validate_capture.py benchmark/raw/video --tier video
```

Fix every `FAIL` the same day, while the furniture hasn't moved and you can still walk
back into the room. That is the entire reason to validate on shoot day rather than next week.

---

## Quick reference

| | Per room |
|---|---|
| Photos | 4 corners + 1 per opening + fill = **6-8**, floor visible in each |
| Doorway pairs | 2 photos per connecting door, **feet still** |
| Video | **45-90 s**, slow perimeter, tilt up and down, pause at each opening |
| Whole flat | **one** continuous clip, all rooms, back to start |

**The four things people get wrong:** tilting the phone up so the floor never appears,
walking too fast, forgetting the doorway pairs, and letting the camera switch off 1x.

# My shot list — iPhone 17, photo and video tiers

Your device. Base iPhone 17 has **no LiDAR**, so you own the photo and video tiers and the
friend's Pro device owns the LiDAR tier. That split is fine, and it is exactly what the
device matrix has to state honestly.

Everything here is captured in your own 3BHK, which is worth exploiting: capture is nearly
free for you, so **capture early and capture again**, rather than treating the shoot as one
irreversible event.

## Phone settings, set once before you start

| Setting | Value | Why |
|---|---|---|
| Camera → Formats | **Most Compatible (JPEG/H.264)** | HEIC and HEVC both decode fine, but not for free — this saves an hour of format debugging you don't need |
| Camera → Record Video | **1080p / 30 fps** | 4K buys almost nothing for geometry and triples file size and processing time |
| Camera → Preserve Settings → Camera Mode | **On** | Stops the camera reopening in Portrait or Cinematic mid-shoot |
| Live Photos | **Off** | A Live Photo is a tiny video; it complicates the loader for no benefit |
| Lens | **1x wide only** | Never 0.5x or 3x. Switching lens mid-room changes the intrinsics and silently corrupts the reconstruction |
| Flash | **Off** | Light moving between frames hurts both geometry and damage detection |
| Grid | **On** | Free help keeping the phone upright |

In a room with a bright window, tap-and-hold to lock exposure and focus so frames don't
fight each other on brightness.

## Room naming

Five spaces from your 3BHK. Fixed names, used in every folder, every filename, and the
ground-truth sheet:

```
room_01_living
room_02_bed_master
room_03_bed_two
room_04_bed_three
room_05_hall          <- the connector, and why this satisfies "3+ rooms plus a connector"
```

Kitchen and bathrooms are optional extras. Don't add them until the five above are done —
more rooms is more capture work, not more marks.

## Before touching the camera

1. **Print the scale card.** `assets/scale_card_A4.pdf`, at **100% / Actual size**, never
   "Fit to page". Print five, one per room. Check the printed ruler against a tape: the
   100 mm mark must land on 100 mm. If it doesn't, every measurement is wrong by that same
   percentage, and nothing later in the pipeline can detect it.
2. **Tape one card per room**, flat on a matte wall at chest height. It stays there for your
   photos, your video, and your friend's LiDAR pass of that room. Write the room name on it.
3. **Walk the flat with a notepad and log the damage you actually have** — see
   `docs/capture/DAMAGE_LOG.md`. Do this before you shoot, not after: you frame differently
   once you know what you're looking for.
4. **Privacy pass.** Clear documents, laptop and TV screens, photos of people, and anything
   showing an address or a name out of every frame. This repo is public.
5. **Tidy, but don't stage.** One room must be genuinely furnished for the damage
   requirement. Don't empty the flat to make reconstruction easy — a system that only works
   on an empty room fails on the day.

---

# Photo tier

**6 to 8 stills per room.** The spec allows as few as 2, and you should also capture one
2-shot set to show honestly how intervals widen — but 6 to 8 is the working set.

Per room, in this order:

1. **Four corner shots.** Stand in each corner, shoot toward the opposite corner. Phone
   upright, chest height, back roughly to the corner. These four frames between them should
   see every wall.
2. **One straight-on frame of the scale-card wall**, whole card clearly visible, from about
   2 m. Not at an angle, not from across the room.
3. **One frame per opening** — every door, every window — square-on, whole opening in frame
   plus some surrounding wall.

Then, **once per pair of connected rooms**: stand in the doorway between them and take one
photo into each room **without moving your feet**. Save both into the lower-numbered room's
folder as `doorway_to_room_NN_a.jpg` and `doorway_to_room_NN_b.jpg`.

Those doorway pairs are how the photo tier learns that rooms are adjacent and how they sit
relative to each other. The spec has a dedicated gate for whole-property stitching from
photo folders and explicitly fails a photo path that handles single rooms only. A set of
per-room photo folders contains *no* adjacency information without these pairs. **Do not
skip them**, and get one for every connection: living↔hall, and hall↔each bedroom.

**Overlap matters more than count.** Consecutive shots should share roughly half their view.
Eight photos of eight unrelated views reconstruct worse than six that overlap.

Folder layout:

```
benchmark/raw/photo/room_01_living/
    corner_a.jpg ... corner_d.jpg
    scale_card.jpg
    opening_door_01.jpg
    opening_window_01.jpg
    doorway_to_room_05_a.jpg
    doorway_to_room_05_b.jpg
```

---

# Video tier

**One continuous clip per room, 45 to 90 seconds.**

The same walk your friend does with LiDAR: perimeter, slow, phone upright at chest height,
tilting gently so the floor-to-wall join and the ceiling-to-wall join both pass through
frame on every wall. Pause 2 s on the scale card. Pause 2 s square-on to each opening.
Finish where you started and overlap the beginning.

Don't walk backwards. Don't spin on the spot. Slow is free; fast is unrecoverable.

**Then one continuous whole-flat clip**, all five spaces, no stopping, walking squarely
through each doorway. This is the video tier's answer to the stitching gate.

---

# The four captures that are easy to forget

Not extras. Each is a scored gate, and each is worthless if you realise you need it after
your friend has gone home.

**1. Repeatability.** Pick your **emptiest** room — probably a bedroom. Capture it a second
time at photo and video tier, and have your friend do LiDAR twice. Leave the room, wait a
few minutes, come back, shoot it fresh. Do not retrace your first walk from memory: the gate
asks whether the *system* is repeatable, and copying your own walk measures nothing. Save
as `..._pass2`.

**2. Head-to-head.** Install **Polycam** (free tier) and scan **two rooms**. Polycam's LiDAR
mode needs the Pro device, so run this while your friend is there, on his phone. Export the
result and write down the **app name and exact version**. The spec requires beating or tying
on 70% of shared dimensions and explicitly refuses cost as a reason to skip it.

**3. The hard case.** Deliberately capture one thing that should break: a mirror, a glass
door, glossy tile, or a room lit by a single lamp at night. Every real property has these
and the brief names them directly. A declared, measured failure earns marks; an undeclared
one gets found in the walk-in test.

**4. The 2-photo minimum set.** One room, exactly 2 stills, demonstrating the floor of "any
picture in, results out" and showing your intervals widen honestly rather than collapsing
into confident garbage.

---

# Validate the same evening — before the Pro device arrives

```
python scripts/validate_capture.py benchmark/raw/photo
python scripts/validate_capture.py benchmark/raw/video --tier video
```

Run this the moment the files are off the phone, on the same day you shoot.

It checks the things that are invisible at capture time and unrecoverable afterwards: that
the scale card is actually present *and usable* in every room, that the lens never switched
to 0.5x or 3x mid-room, that every room is reachable through a doorway pair, and that frames
aren't soft. It names the room and tells you what to re-shoot.

`FAIL` means a gate in the brief cannot be met with those files. Fix it while you're still
in the flat and the furniture hasn't moved.

---

# Ground truth — the part that decides your score

Every gate is measured against these numbers. If they're sloppy, a correct pipeline scores
as a broken one and you will not be able to tell which you have.

**Get a laser measurer.** A basic one is ₹1,500–2,500 and is a better investment than the
hour of coding you'd otherwise spend. A tape works, but you will make mistakes on ceiling
height and diagonals, and the ceiling gate is **1.5 cm** — tighter than tape-across-a-room
is reliably good for.

Per room, measured into `benchmark/ground_truth/`:

- **Every wall length**, at floor level, along the skirting.
- **Ceiling height at two points**, not one. The gate cares about spread, and floors in real
  flats are not level.
- **Every opening**: width and height at the frame, and which two rooms each door connects.
- **Floor area**: derive from wall lengths if the room is rectangular; measure directly if
  it isn't.
- **Every damage region**: long axis and short axis.
- **Room-to-room spans**: for at least the living↔hall↔bedroom chain, measure the overall
  distance across rooms. Per-room truth alone cannot catch accumulated drift in the stitch,
  and drift is its own gate.

Measure **twice**, on different days if you can, and keep both numbers. Where they disagree,
that disagreement *is* your ground-truth uncertainty — report it rather than pretending the
tape is exact.

Write it down **in the room, as you measure**. Not from memory afterwards.

---

# Order of the day

Capture is cheap for you and expensive for your friend — he is there once.

**Before he arrives:** cards printed and taped up, damage logged, privacy pass done, all
ground truth measured and written down, your own photo and video tiers already shot.

**While he's there:** LiDAR per room → whole-flat LiDAR loop → repeat room → hard case →
Polycam head-to-head on his phone. Copy files off after every two rooms.

**Do not** leave ground-truth measuring until after he has gone. When a scan disagrees with
the tape, you want to re-measure that wall while the scan is fresh.

# LiDAR capture brief — hand this to the friend, unedited

Send this whole page. It assumes no knowledge of the project.

---

## Before the day: three things to confirm over text

**1. Which iPhone or iPad is it, exactly?** Settings → General → About → Model Name.

LiDAR only exists on **Pro / Pro Max iPhones (12 Pro and later)** and **iPad Pro (2020 and
later)**. A base iPhone 14/15/16/17 or a Plus has no LiDAR and cannot do this tier.
If the answer is a non-Pro iPhone, stop — this tier is not capturable on that device and we
need a different phone.

**2. Install Record3D** (App Store, by Marek Simonik) **and do a 15-second test export
before the day.** Record anything, then Library → select it → Export → **`.r3d`** → save to
Files → upload to the shared Drive folder. Message when it lands.

That test is not optional politeness — it is the single highest-risk item in the whole
project. Record3D's free tier limits recording length and some export formats, and we need
to find out which limits apply on *that* device *before* we've spent an afternoon capturing.
If `.r3d` export turns out to be locked or capped, the fallback is **3D Scanner App**
(Laan Labs, free) → Export → **All Data**, and we need to know that in advance too.

**3. Roughly 2 hours, at my flat.** This is the part that is easy to get wrong: the LiDAR
scans have to be of **my rooms**, the same rooms I am photographing, not of your place. The
whole point of the exercise is comparing three different capture methods on *the same*
physical rooms against tape measurements of those rooms. A perfect scan of a room I cannot
measure is worth nothing.

If coming over is hard, the alternative is lending me the phone for an afternoon — that
works just as well, and honestly better, because then I can re-shoot when something goes wrong.

---

## On the day

### Settings, once

Open Record3D → LiDAR / depth mode → highest depth quality available. Don't change it again
between rooms; a settings change mid-shoot makes the rooms non-comparable.

### Per room, one recording

Five spaces, one recording each. I'll tell you the room name; put it in the filename.

**The walk:**

1. Start in a doorway or corner. Hold the phone **upright, at chest height**, both hands.
2. Walk the perimeter of the room slowly — think "slower than feels necessary". Roughly
   **one step every two seconds**.
3. As you walk, **tilt gently up and down** so that the line where the wall meets the floor
   and the line where the wall meets the ceiling both pass through frame on every wall.
   Those two lines are literally what the room's dimensions get computed from. A scan that
   only sees the middle of the walls gives us nothing.
4. **Stay 0.5 m to 4 m from whatever you're pointing at.** Closer than half a metre and the
   depth sensor returns nothing; further than about 4 m and it gets too noisy to use.
5. **Pause 3 seconds square-on to every door and every window**, whole opening in frame.
6. End where you started, and overlap the last few seconds with the first few. Walking a
   closed loop lets us correct for drift; an open-ended walk does not.

**Do not:** walk backwards, spin on the spot, cover the sensor, or wave the phone quickly.
Fast motion is the main cause of a scan that has to be thrown away.

Target 60–120 seconds per room.

### Then: one continuous whole-flat recording

One single recording, no stopping, walking through **every** space in one loop and back to
where you started. Move slowly, and go through each doorway **slowly and squarely** — face
the doorway, walk straight through it, don't cut the corner.

This one is the hardest and the most important: it's what places the rooms correctly
relative to each other. If it feels like it went badly, just do it again. Two attempts is
normal, and the cost of a redo is two minutes.

### One room, twice

I'll pick a room. Scan it, then **leave the room, do something else for a few minutes, come
back and scan it again** — walking it fresh rather than repeating the first walk from memory.

I'm testing whether the system gives the same answer twice. If you copy the first walk
exactly, the test measures nothing.

### One deliberately awkward capture

I'll point at something reflective — a mirror, a glass pane, a glossy tile. Scan it normally.
It will probably come out badly, and that's the point: we have to report where the method
breaks, and we can only do that if we have an example.

---

## Handing the files over

Per room: Library → select recording → Export → `.r3d` → Files → shared Drive folder.

**Do this after every 2 rooms rather than at the very end.** If an export is broken we want
to catch it while we're still standing in the flat.

Name each file for its room: `room_01_living.r3d`, `room_05_hall_pass2.r3d`, and so on.

Please **don't delete anything off the phone** until I confirm every file opens on my laptop.

---

## A note on the flat

It's a lived-in home. If any of your recordings catch documents, a screen, or a person,
tell me which one and I'll cut it — that data isn't going anywhere public, but I'd rather
re-shoot than redact.

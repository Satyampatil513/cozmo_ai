# Capture sessions

Three sessions, not one. Capture is nearly free in your own home, so don't front-load
everything — build against real data early and let later sessions fix what the first got
wrong.

Procedure lives in `docs/capture/PHOTO_VIDEO_FIELD_GUIDE.md`. This is just the plan.

## Session 1 — Probe (~30 min, one room)

Answer format questions before committing to a full shoot.

- [ ] One room, photos + video, straight off the phone onto the laptop
- [ ] `python scripts/validate_capture.py` runs clean on it
- [ ] A few tape measurements of that room, for a sanity check later
- [ ] Remote friend's `.r3d` export tested and confirmed opening in Python on Windows

If the `.r3d` path doesn't work, fix it now, not at hour 20.

## Session 2 — Full benchmark

- [ ] All 5 spaces: living, 3 bedrooms, hallway (3+ rooms plus a connector)
- [ ] Photos, 6–8 per room, plus one shot through each doorway
- [ ] Whole-flat video walkthrough, **done twice** (covers repeatability for every room)
- [ ] A 2-photo room, and one deliberately hard surface (mirror / glass / low light)
- [ ] Damage: walk the flat first and log what's really there — see `docs/capture/DAMAGE_LOG.md`.
      One furnished room needs two damage classes; stage the second if needed and say so.
- [ ] Ground truth measured and written into `benchmark/ground_truth/`, including cross-room
      spans
- [ ] Privacy pass: no documents, screens or people in frame
- [ ] Second copy of everything in cloud storage
- [ ] Validator clean before you call the session done

## Session 3 — Cold rehearsal (~hour 30)

Simulates the walk-in test for free, and it's the cheapest 30% of the score you'll ever buy.

- [ ] Hand someone the printed `docs/CAPTURE_PROTOCOL.md` and nothing else
- [ ] They pick a room and a tier without asking you anything
- [ ] Run the pipeline cold while you watch, exactly as the defense will go

Whatever breaks here is what would have broken in front of Cozmo. Fix it.

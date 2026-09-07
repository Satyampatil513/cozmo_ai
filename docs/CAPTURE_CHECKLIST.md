# Capture sessions (print this, tick as you go)

Companion to:
- `docs/capture/MY_SHOT_LIST.md` - what I shoot on the iPhone 17 (photo + video)
- `docs/capture/FRIEND_LIDAR_BRIEF.md` - send verbatim to whoever brings the Pro device
- `docs/capture/DAMAGE_LOG.md` - what damage to look for and how to record it
- `docs/CAPTURE_PROTOCOL.md` - the one-pager Cozmo follows at the defense

The borrowed Pro device is the scarce resource in all of this. Everything that does not
need it gets done before it arrives.

Three sessions, not one. Capture is cheap in your own home, so don't front-load everything;
build the loader against real data early and let the later sessions fix what the early one
gets wrong.

## Session 1 — Probe (hour 1, ~30 min, one room only)

Purpose: answer format questions before committing to a full shoot.

- [ ] Friend's device model confirmed as Pro/Pro Max via Settings -> General -> About.
      A base iPhone has no LiDAR and the tier is simply not capturable on it.
- [ ] Record3D installed on friend's iPhone, `.r3d` export tested on ONE short clip
- [ ] File gets off the phone: Files -> Drive/iCloud -> pulled down on the laptop (no AirDrop)
- [ ] `.r3d` opens with the `record3d` Python library on Windows; depth, pose and intrinsics
      all readable. If this fails, fix it now, not at hour 20.
- [ ] One room, all 3 tiers, scale card in frame, quick tape measurements for a sanity check
- [ ] Confirm Record3D's free-tier clip length limit is long enough for a room walkthrough
- [ ] If `.r3d` export is locked or capped: fall back to 3D Scanner App (Laan Labs, free),
      Export -> All Data, and find that out NOW rather than on shoot day
- [ ] Scale card printed at 100% and verified against a tape using the ruler on the card

## Session 2 — Full benchmark (once geometry stage runs on the probe data)

### Rooms
- [ ] Living room + 3 bedrooms + hall/corridor connector (5 spaces, 3+ rooms plus connector met)
- [ ] Pick the emptiest room for the repeatability capture, so a bad fit is geometry, not clutter

### Damage, one furnished room, 2 classes
- [ ] Walk the flat first and note what real damage already exists (staining, cracks, peeling)
- [ ] If only one class is naturally present, stage the second (printed sheet or tape line)
      and mark it staged in the report
- [ ] Measure and photograph every damage region square-on before the tiered capture

### Per room, in this order
- [ ] Scale card (or bank card fallback) placed
- [ ] Photo tier, 6 to 8 stills
- [ ] Doorway pair shots for every connection
- [ ] Video tier, 45 to 90 s
- [ ] LiDAR tier, Record3D, same walk

### Repeatability
- [ ] ONE room captured a second time at BOTH photo and LiDAR tier, walked independently
      (leave, come back, don't retrace the first walk from memory)

### Whole property
- [ ] One continuous video walkthrough, all 5 spaces
- [ ] One continuous Record3D walkthrough, all 5 spaces

### Head-to-head
- [ ] Polycam or magicplan free-tier scan of 2 rooms, export saved, app + version noted

### Hard case
- [ ] Deliberately capture one difficult surface (mirror, glass, glossy tile, low light) and
      report it as a declared failure mode

### Ground truth, into benchmark/ground_truth/
- [ ] Every wall length, per room
- [ ] Ceiling height at 2 points, per room
- [ ] Every opening width and height
- [ ] Floor area (derived or measured directly if non-rectangular)
- [ ] Damage extents (staged and real)

### Before ending the session
- [ ] All files copied to the laptop, second copy in cloud storage
- [ ] Ground-truth CSV filled and committed
- [ ] Privacy check: no documents, screens or people in the frames being submitted

## Session 3 — Cold rehearsal (~hour 30, simulates the walk-in test for free)

- [ ] Hand your friend the printed `CAPTURE_PROTOCOL.md`, no other guidance
- [ ] He picks a room you haven't optimised for and a tier, without asking you anything
- [ ] Run the pipeline cold while you watch, exactly as the defense will happen
- [ ] Whatever breaks here is what would have broken in front of Cozmo. Fix it, don't re-run.

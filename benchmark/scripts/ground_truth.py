"""Read the tape/laser ground truth, and say plainly what it does and does not contain.

One loader, used by every scorer, so no two reports can disagree about what the truth is.

WHY THIS EXISTS RATHER THAN A CONSTANT. Until now the only ground truth in the repo was
`TRUE_CEILING_M = 2.64`, hardcoded in four separate scripts. That was wrong in two ways at
once: four copies of a number can drift apart, and the number itself was superseded by the
laser survey (2.74 m) without any of the four noticing. A scorer that reads the sheet cannot
have that problem.

WHAT IS MEASURED AND WHAT IS DERIVED. Wall lengths and ceiling heights are measured values and
are returned as such. Floor area is NOT measured anywhere in the sheet - its rows say
"derive from walls if rectangular" - so it is derived here, at read time, and flagged
`derived=True`. Deriving in the loader rather than writing the result back into the sheet
keeps the truth file a record of what someone actually measured with a laser.

BLANK IS NOT ZERO. A missing cell means nobody measured it. Every accessor returns None for
those and the scorers report them as unscoreable, because an unmeasured wall scored as 0.000 m
would produce a spectacular and entirely fictional error.
"""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
from typing import Optional

GT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "ground_truth")


def _f(v) -> Optional[float]:
    """Parse a measurement. Blank AND zero both mean "nobody measured this".

    Zero is treated as missing rather than as a value because no wall, ceiling or floor in a
    real room measures 0.000 m, and the sheet uses 0 as a placeholder in exactly the rows
    where a measurement was skipped (room_03 w4, most floor areas). Reading those as real
    would score a 100% error against a wall nobody ever put a laser on - a spectacular and
    entirely fictional failure, which is the specific way an empty-cell convention corrupts a
    benchmark.
    """
    v = (v or "").strip()
    if not v:
        return None
    try:
        f = float(v)
    except ValueError:
        return None
    return None if f <= 0 else f


@dataclass
class RoomTruth:
    room_id: str
    room_type: str = ""
    wall_lengths: list[float] = field(default_factory=list)   # every measured wall
    ceiling_heights: list[float] = field(default_factory=list)
    floor_area_m2: Optional[float] = None
    floor_area_derived: bool = False
    n_walls_expected: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def ceiling_m(self) -> Optional[float]:
        """Mean of the repeated ceiling readings. The sheet asks for two, at opposite corners,
        precisely because floors are not level, so a single reading is not the room's height."""
        if not self.ceiling_heights:
            return None
        return sum(self.ceiling_heights) / len(self.ceiling_heights)

    @property
    def ceiling_spread_m(self) -> Optional[float]:
        if len(self.ceiling_heights) < 2:
            return None
        return max(self.ceiling_heights) - min(self.ceiling_heights)

    @property
    def dimensions(self) -> list[float]:
        """The room's distinct span lengths, largest first.

        A rectangular room's four walls are two lengths repeated, and the pipeline measures
        *separations between opposite walls*, which for a rectangle equals that same pair of
        numbers. So this is directly comparable to `wall_pairs` without further conversion.
        Near-duplicates are collapsed at 5 cm - two walls a centimetre apart are one dimension
        measured twice, not two dimensions.
        """
        out: list[float] = []
        for L in sorted(self.wall_lengths, reverse=True):
            if not any(abs(L - k) < 0.05 for k in out):
                out.append(L)
        return out

    @property
    def complete(self) -> bool:
        return (len(self.wall_lengths) >= self.n_walls_expected > 0
                and bool(self.ceiling_heights))


def load_rooms(path: Optional[str] = None) -> dict[str, RoomTruth]:
    path = path or os.path.join(GT_DIR, "rooms.csv")
    rooms: dict[str, RoomTruth] = {}
    if not os.path.isfile(path):
        return rooms
    for r in csv.DictReader(open(path, newline="", encoding="utf-8")):
        rid = (r.get("room_id") or "").strip()
        if not rid or rid.startswith("#"):
            continue
        rt = rooms.setdefault(rid, RoomTruth(room_id=rid,
                                             room_type=(r.get("room_type") or "").strip()))
        v = _f(r.get("value_m"))
        kind = (r.get("element_type") or "").strip()
        if kind == "wall":
            rt.n_walls_expected += 1
            if v is not None:
                rt.wall_lengths.append(v)
            else:
                rt.notes.append(f"wall {r.get('element_id')} not measured")
        elif kind == "ceiling" and v is not None:
            rt.ceiling_heights.append(v)
        elif kind == "floor" and v is not None:
            rt.floor_area_m2 = v

    # Derive floor area where the sheet did not measure one and the room reads as a rectangle:
    # exactly two distinct spans across four walls. Anything else is a shape this derivation
    # cannot describe, and a wrong area is worse than no area.
    for rt in rooms.values():
        derivable = (rt.n_walls_expected == 4 and len(rt.wall_lengths) == 4
                     and len(rt.dimensions) == 2)
        derived = round(rt.dimensions[0] * rt.dimensions[1], 3) if derivable else None

        if rt.floor_area_m2 is None:
            if derived is not None:
                rt.floor_area_m2, rt.floor_area_derived = derived, True
            else:
                rt.notes.append("floor area not derivable: not all four walls measured")
        elif derived is not None and abs(rt.floor_area_m2 - derived) > 0.2 * derived:
            # A stated area that contradicts the room's own walls is reported, not silently
            # preferred either way. room_02 states 2.9 m2 for a 3.05 x 3.40 room, which is
            # 10.4 m2 - the stated value looks like a stray wall length, but this loader is
            # not entitled to decide that on its own.
            rt.notes.append(
                f"CONFLICT: sheet states floor area {rt.floor_area_m2} m2, but its own walls "
                f"give {derived} m2. Using the derived value; confirm the sheet.")
            rt.floor_area_m2, rt.floor_area_derived = derived, True
    return rooms


def load_openings(path: Optional[str] = None) -> dict[str, list[dict]]:
    path = path or os.path.join(GT_DIR, "openings.csv")
    out: dict[str, list[dict]] = {}
    if not os.path.isfile(path):
        return out
    for r in csv.DictReader(open(path, newline="", encoding="utf-8")):
        rid = (r.get("room_id") or "").strip()
        if not rid or rid.startswith("#"):
            continue
        w, h = _f(r.get("width_m")), _f(r.get("height_m"))
        kind = (r.get("opening_type") or "").strip()
        out.setdefault(rid, []).append({
            "id": (r.get("opening_id") or "").strip(),
            "type": kind,
            "width_m": w, "height_m": h,
            # A door's sill is the floor. Blank in the sheet is correct for a door and must
            # not be read as unmeasured, or every door would look half-surveyed.
            "sill_m": (0.0 if kind == "door" and _f(r.get("sill_height_m")) is None
                       else _f(r.get("sill_height_m"))),
            "measured": w is not None or h is not None,
        })
    return out


def load_mapping(path: Optional[str] = None) -> dict[str, str]:
    """capture folder name -> ground-truth room_id."""
    path = path or os.path.join(GT_DIR, "room_mapping.csv")
    out: dict[str, str] = {}
    if not os.path.isfile(path):
        return out
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("capture_room"):
            continue
        parts = next(csv.reader([line]))
        if len(parts) >= 2 and parts[0].strip() and parts[1].strip():
            out[parts[0].strip()] = parts[1].strip()
    return out


def truth_for(capture_room: str) -> Optional[RoomTruth]:
    rid = load_mapping().get(capture_room)
    return load_rooms().get(rid) if rid else None


def coverage() -> dict:
    """What the sheet actually contains. Printed at the top of every scored report, so a
    reader never has to guess whether a blank row means 'passed' or 'never measured'."""
    rooms, ops = load_rooms(), load_openings()
    walls_measured = sum(len(r.wall_lengths) for r in rooms.values())
    walls_expected = sum(r.n_walls_expected for r in rooms.values())
    op_rows = [o for v in ops.values() for o in v]
    return {
        "rooms": len(rooms),
        "rooms_complete": sum(1 for r in rooms.values() if r.complete),
        "walls_measured": walls_measured,
        "walls_expected": walls_expected,
        "ceilings_measured": sum(len(r.ceiling_heights) for r in rooms.values()),
        "floor_areas_derived": sum(1 for r in rooms.values() if r.floor_area_derived),
        "openings_rows": len(op_rows),
        "openings_measured": sum(1 for o in op_rows if o["measured"]),
        "mapping_entries": len(load_mapping()),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(coverage(), indent=2))
    for rid, rt in sorted(load_rooms().items()):
        print(f"\n{rid}  ({rt.room_type})")
        print(f"  walls    {rt.wall_lengths}  -> dimensions {rt.dimensions}")
        print(f"  ceiling  {rt.ceiling_heights} -> {rt.ceiling_m}")
        print(f"  area     {rt.floor_area_m2}"
              + ("  (derived from walls)" if rt.floor_area_derived else ""))
        for n in rt.notes:
            print(f"  note     {n}")

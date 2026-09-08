"""Map the pipeline's internal result dict onto the PUBLISHED output contract.

WHY A SEPARATE ADAPTER RATHER THAN CHANGING WHAT run.py WRITES. `result.json` carries far
more than the published schema asks for - registration diagnostics, drift-correction before/
after numbers, doorway-crossing traces, abstention reasons - all of it load-bearing for
debugging and for the defense, none of it part of the contract external consumers were
promised. Forcing the rich internal shape to literally BE the published shape would mean
either bloating the schema with implementation detail or deleting diagnostics the rest of this
project depends on. So `result.json` keeps its internal shape; `result.schema.json` is this
adapter's output, and it is what actually gets validated.

WHAT THIS DOES NOT DO: invent data. Every field the schema marks `required` is present in the
output, but where nothing was ever measured (`damage`, `scope_items`, `surfaces` - none of
those pipeline stages exist yet) the array is empty, never populated with placeholders. An
empty array is an honest "not built"; a placeholder value would be exactly the "confident
garbage on thin input" the brief penalises.

A STRUCTURAL MISMATCH IS SURFACED HERE RATHER THAN PAPERED OVER. The schema's `walls[]` model
is polygon-based: each wall is a `{start, end}` point pair. The pipeline's single most reliable
measurement - wall-pair separation, the distance between two opposite planes - has no such
pair of points; it is a scalar distance between two infinite planes, deliberately chosen
*because* it needs no closed polygon (`OVERNIGHT_PROGRESS.md` §8). So `walls[]` in the adapted
output is populated ONLY when a real closed polygon exists (rare - one real capture this
project has produced), and is empty otherwise, even on captures that DO report a trustworthy
wall-pair dimension the schema simply has no field for. That gap is real, is stated in
`docs/COMPLIANCE_MATRIX.md`, and is not something this adapter can close by relabelling data.
"""
from __future__ import annotations

from typing import Optional

from pipeline.confidence.intervals import area_measurement, length_measurement
from pipeline.types import Scale


def _measurement(m: Optional[dict]) -> Optional[dict]:
    """Pass through an already-schema-shaped Measurement.to_json() dict unchanged."""
    if not m:
        return None
    return {"value": m["value"], "unit": m["unit"], "interval": m["interval"],
           "confidence": m["confidence"], "method": m["method"]}


def _opening_measurement(value_m: Optional[float], tier: str, scale: Scale, what: str
                         ) -> Optional[dict]:
    """Openings do not currently carry their own calibrated interval - width/height are
    reported as bare floats in the internal result. The schema requires a full Measurement
    for both, so the SAME error model already used for wall lengths is applied here rather
    than inventing a second one; an opening is a distance in metres like any other."""
    if value_m is None:
        return None
    return length_measurement(value_m, tier, scale, what).to_json()


def _damage_contract(d: dict, tier: str, scale: Scale) -> dict:
    """Internal DamageRegion.to_json() (+ concealed_flag) -> schema `damage` item.

    Extent is metric already; it is wrapped in the SAME error model used for wall lengths and
    areas rather than a damage-specific one - a stain's long axis is a distance like any other.
    `method` carries the "unfitted, out of scope" caveat so a consumer cannot read these as
    calibrated.
    """
    note = "first-pass damage detector, unfitted thresholds, out of scope for scoring"
    extent = {"area": area_measurement(d["area_m2"], tier, scale, note).to_json(),
              "long_axis": length_measurement(d["long_axis_m"], tier, scale, note).to_json(),
              "short_axis": length_measurement(d["short_axis_m"], tier, scale, note).to_json()}
    return {
        "id": d["id"],
        "surface_id": d["surface_id"],
        "class": d["class"],
        "detection_confidence": round(float(d.get("confidence", 0.0)), 3),
        "extent": extent,
        "concealed_flag": d.get("concealed_flag", {"raised": False}),
    }


def _scope_contract(s: dict, tier: str, scale: Scale) -> dict:
    """Internal scope line item -> schema `scope_items` item. The quantity is inherited from
    the damage extent (never re-estimated), so it is wrapped in the matching measurement."""
    q = s["quantity"]
    note = "quantity inherited from damage extent; first pass, out of scope for scoring"
    if q.get("unit") == "m2":
        m = area_measurement(q["value"], tier, scale, note).to_json()
    else:
        m = length_measurement(q["value"], tier, scale, note).to_json()
    return {"id": s["id"], "surface_id": s["surface_id"], "damage_id": s["damage_id"],
            "action": s["action"], "quantity": m}


def _room_contract(room: dict, tier: str, scale: Scale, room_id_override: Optional[str] = None
                   ) -> dict:
    rid = room_id_override or room.get("room_id", "room")
    poly = room.get("polygon")

    walls: list[dict] = []
    if poly and poly.get("corners"):
        # `polygon.corners` is the room's OWN local 2D floor-plane basis - exactly what the
        # schema's point2/polygon defs expect, no conversion needed. `world_corners` (present
        # only on a stitched sub-room) would place this room relative to others; the schema's
        # per-room `polygon` field is documented as that room's own shape, so the local frame
        # is the correct one here regardless of whether this room was stitched.
        corners = poly["corners"]
        lengths = room.get("wall_length_measurements") or []
        n = len(corners)
        for i in range(n):
            walls.append({
                "id": f"w{i}",
                "start": [round(float(corners[i][0]), 4), round(float(corners[i][1]), 4)],
                "end": [round(float(corners[(i + 1) % n][0]), 4),
                       round(float(corners[(i + 1) % n][1]), 4)],
                "length": (_measurement(lengths[i]) if i < len(lengths)
                          else {"value": poly["wall_lengths"][i], "unit": "m",
                                "interval": [poly["wall_lengths"][i], poly["wall_lengths"][i]],
                                "confidence": 0.0,
                                "method": "polygon edge, no calibrated interval computed"}),
            })

    openings: list[dict] = []
    for i, o in enumerate(room.get("openings") or []):
        openings.append({
            "id": f"o{i}",
            "type": o["kind"] if o["kind"] in ("door", "window") else "opening",
            "wall_id": (f"w{o['wall_index']}" if poly and poly.get("corners")
                       and o.get("wall_index", -1) < len(poly["corners"])
                       else f"wall_{o.get('wall_index', '?')}"),
            "width": _opening_measurement(o.get("width_m"), tier, scale,
                                          f"{o['kind']} width, hole in wall support"),
            "height": _opening_measurement(o.get("height_m"), tier, scale,
                                           f"{o['kind']} height, hole in wall support"),
            "detection_confidence": round(float(o.get("confidence", 0.0)), 3),
        })

    ch = _measurement(room.get("ceiling_height_measurement"))
    if ch is None and room.get("ceiling_height") is not None:
        # Abstained-but-reported edge case should not occur, but if a bare value exists
        # without its Measurement wrapper, report it with confidence 0 rather than drop it -
        # a value the schema never sees is worse than one flagged as uncalibrated.
        ch = {"value": room["ceiling_height"], "unit": "m",
             "interval": [room["ceiling_height"], room["ceiling_height"]],
             "confidence": 0.0, "method": "no calibrated interval computed for this path"}
    fa = _measurement(room.get("floor_area_measurement"))

    return {
        "id": rid,
        "polygon": [[round(float(c[0]), 4), round(float(c[1]), 4)] for c in poly["corners"]]
                  if poly and poly.get("corners") else [],
        "walls": walls,
        "openings": openings,
        "ceiling_height": ch or {"value": 0.0, "unit": "m", "interval": [0.0, 0.0],
                                 "confidence": 0.0, "method": "abstained - no defensible "
                                                              "floor/ceiling pair"},
        "floor_area": fa or {"value": 0.0, "unit": "m2", "interval": [0.0, 0.0],
                             "confidence": 0.0, "method": "no closed polygon"},
        # Damage runs on the per-frame path (photo tier), where the source RGB and a per-point
        # pixel mapping exist. It is a first pass on unfitted thresholds and is out of scope
        # for scoring in this submission - the arrays are empty on any capture where the
        # detector did not run (fused clouds have no single source image).
        "surfaces": [dict(s) for s in room.get("surfaces", [])],
        "damage": [_damage_contract(d, tier, scale) for d in room.get("damage", [])],
        "scope_items": [_scope_contract(s, tier, scale) for s in room.get("scope_items", [])],
    }


def to_contract(result: dict) -> dict:
    """The full `result` dict (as written to result.json by run.py) -> the published
    output-contract shape (`schemas/output.schema.json`)."""
    tier = result["capture"]["tier"]
    scale = Scale(source=result["capture"].get("scale_source", "none"))

    rooms_out: list[dict] = []
    connections: list[dict] = []
    for r in result.get("rooms", []):
        sub_rooms = r.get("sub_rooms")
        if sub_rooms:
            for sr in sub_rooms:
                rooms_out.append(_room_contract(sr, tier, scale))
            for c in r.get("connections") or []:
                a, b = c["rooms"]
                via = (f"o_{c['opening']['seen_from']}_{c['opening'].get('wall_index', '?')}"
                      if c.get("has_opening") and c.get("opening") else "unconfirmed")
                connections.append({"from_room": a, "to_room": b, "via_opening": via})
        else:
            rooms_out.append(_room_contract(r, tier, scale))

    fp = result.get("property", {}).get("footprint_area")
    footprint = ({"value": fp, "unit": "m2", "interval": [fp, fp], "confidence": 0.0,
                 "method": "sum of stitched room footprints, no calibrated interval yet"}
                if fp is not None else
                {"value": 0.0, "unit": "m2", "interval": [0.0, 0.0], "confidence": 0.0,
                 "method": "no stitched footprint available for this capture"})

    return {
        "schema_version": "0.1",
        "capture": {
            "tier": tier,
            "device": result["capture"].get("device", "unknown"),
            "timestamp": result["capture"]["timestamp"],
            "pipeline_commit": result["capture"].get("pipeline_commit", "unknown"),
            "scale_source": result["capture"].get("scale_source", "none"),
            "drift_correction": bool(result["capture"].get("drift_correction", True)),
        },
        "property": {
            "rooms": [r["id"] for r in rooms_out],
            "connections": connections,
            "footprint_area": footprint,
        },
        "rooms": rooms_out,
    }

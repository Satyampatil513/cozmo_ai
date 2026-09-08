"""Does result.json actually conform to the published contract?

Run: python tests/test_output_schema.py

This was an honest gap, not a checked-and-passing one: `jsonschema` has been a listed
dependency since the schema was written, and nothing ever called `validate()`. The internal
result shape drifted a long way from `schemas/output.schema.json` while this project's own
measurement contract was being built out - different field names, no Measurement wrapper on
opening width/height, connections keyed by room-id pairs rather than from_room/to_room. None
of that was wrong to do; it was wrong to never check it against the contract that was
published.

`pipeline/output/schema_adapter.py` is the fix: it maps the rich internal result onto the
published shape, honestly - empty arrays where nothing was measured, never a fabricated value.
This test runs that adapter over every real result.json this repo has on disk and validates
the output with jsonschema's FormatChecker enabled, so `"format": "date-time"` is actually
checked and not silently skipped (jsonschema's default validator ignores format entirely
unless a FormatChecker is passed - which would have made this test look like it passed for
the wrong reason).
"""
from __future__ import annotations

import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import jsonschema                                          # noqa: E402

from pipeline.output.schema_adapter import to_contract      # noqa: E402

FAILURES: list[str] = []
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def check(name: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))
    if not cond:
        FAILURES.append(name)


def load_schema() -> dict:
    with open(os.path.join(ROOT, "schemas", "output.schema.json")) as fh:
        return json.load(fh)


def find_real_results() -> list[str]:
    """Every `result.json` this repo actually produced, wherever it landed - real runs, not
    fixtures, because a schema check that only ever sees a hand-built example proves the
    schema and the fixture agree with each other, not that the pipeline's real output does."""
    return sorted(glob.glob(os.path.join(ROOT, "out_*", "result.json")))


def test_schema_itself_is_valid_json_schema():
    schema = load_schema()
    validator_cls = jsonschema.validators.validator_for(schema)
    try:
        validator_cls.check_schema(schema)
        check("schemas/output.schema.json is itself a valid JSON Schema", True)
    except jsonschema.exceptions.SchemaError as exc:
        check("schemas/output.schema.json is itself a valid JSON Schema", False, str(exc))


def test_every_real_result_validates():
    schema = load_schema()
    results = find_real_results()
    if not results:
        check("at least one real result.json exists to validate", False,
              "run.py has never been run - nothing to check")
        return

    checker = jsonschema.FormatChecker()
    any_with_polygon = False
    any_without_polygon = False

    for path in results:
        with open(path) as fh:
            result = json.load(fh)
        try:
            adapted = to_contract(result)
        except Exception as exc:
            check(f"{os.path.relpath(path, ROOT)}: adapter runs without error", False,
                  f"{type(exc).__name__}: {exc}")
            continue

        if any(r["polygon"] for r in adapted["rooms"]):
            any_with_polygon = True
        if any(not r["polygon"] for r in adapted["rooms"]):
            any_without_polygon = True

        try:
            jsonschema.validate(adapted, schema, format_checker=checker)
            check(f"{os.path.relpath(path, ROOT)}: validates against the published schema",
                  True)
        except jsonschema.exceptions.ValidationError as exc:
            check(f"{os.path.relpath(path, ROOT)}: validates against the published schema",
                  False, f"{exc.message}  (at {'/'.join(str(p) for p in exc.absolute_path)})")

    # Both branches of the adapter's real structural decision (§ module docstring: walls[] is
    # only populated when a closed polygon exists) need to actually be exercised by SOMETHING
    # on disk, or this test could pass while only ever checking the easy, empty-arrays case.
    check("at least one validated result has a closed polygon (walls[] populated branch)",
          any_with_polygon,
          "" if any_with_polygon else
          "no on-disk result has ever closed a polygon - OK to be false today, but should "
          "not be missed as a gap if it stays false")
    check("at least one validated result has NO polygon (empty walls[] branch)",
          any_without_polygon)


if __name__ == "__main__":
    test_schema_itself_is_valid_json_schema()
    test_every_real_result_validates()
    print("\n" + ("ALL PASS" if not FAILURES else f"FAILURES: {', '.join(FAILURES)}"))
    sys.exit(1 if FAILURES else 0)

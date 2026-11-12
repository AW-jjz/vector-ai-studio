#!/usr/bin/env python3
"""Validate a JSON document against a JSON Schema subset, using stdlib only.

Why not `jsonschema`: this skill ships with no third-party dependencies, and a
dependency-free project that silently skips its own contract check is worse than
one that implements the 15 keywords it actually uses.

Supported keywords (deliberately a subset, and it says so when it meets
something it does not understand):
    type, const, enum, required, properties, patternProperties,
    additionalProperties (true/false only), items, minItems, minLength,
    minimum, maximum, pattern, anyOf

Anything else in a schema is reported as unsupported rather than ignored --
silently ignoring a constraint is how a validator becomes decorative.

Usage:  python scripts/schema_check.py <schema.json> <doc.json> [--json]
Exit:   0 valid, 1 invalid, 2 usage / unreadable input.

F3 (r71): the output is ALWAYS the shared 9-field envelope (L.emit via
L.run_cli), like every other CLI in this skill. The `--json` flag is kept for
call compatibility; the VALID / INVALID semantics live in `data`:
{"schema", "doc", "valid", "problems"}. An unreadable file or malformed JSON
is a code=2 envelope, never a bare stderr line.
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L

SCRIPT = "schema_check.py"

TYPES = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "number": (int, float),
    "integer": int,
    "null": type(None),
}

KNOWN = {"$schema", "$id", "title", "description", "type", "const", "enum",
         "required", "properties", "patternProperties", "additionalProperties",
         "items", "minItems", "minLength", "minimum", "maximum", "pattern",
         "anyOf"}


def _type_ok(value, name):
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if name == "boolean":
        return isinstance(value, bool)
    return isinstance(value, TYPES[name])


def validate(value, schema, path="$"):
    """Return a list of problems. Empty list means valid."""
    problems = []

    for key in schema:
        if key not in KNOWN:
            problems.append(f"{path}: schema keyword '{key}' is not supported by this checker")

    if "const" in schema and value != schema["const"]:
        problems.append(f"{path}: expected const {schema['const']!r}, got {value!r}")

    if "enum" in schema and value not in schema["enum"]:
        problems.append(f"{path}: {value!r} not in enum {schema['enum']!r}")

    if "type" in schema:
        names = schema["type"]
        names = [names] if isinstance(names, str) else list(names)
        if not any(_type_ok(value, n) for n in names):
            problems.append(f"{path}: type mismatch, expected {names}, got {type(value).__name__}")
            return problems          # further checks assume the type held

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            problems.append(f"{path}: length {len(value)} < minLength {schema['minLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            problems.append(f"{path}: {value!r} does not match pattern {schema['pattern']}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            problems.append(f"{path}: {value} < minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            problems.append(f"{path}: {value} > maximum {schema['maximum']}")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            problems.append(f"{path}: {len(value)} items < minItems {schema['minItems']}")
        if "items" in schema:
            for i, item in enumerate(value):
                problems.extend(validate(item, schema["items"], f"{path}[{i}]"))

    if isinstance(value, dict):
        for req in schema.get("required", []):
            if req not in value:
                problems.append(f"{path}: missing required key '{req}'")

        props = schema.get("properties", {})
        pats = schema.get("patternProperties", {})
        for k, v in value.items():
            matched = False
            if k in props:
                matched = True
                problems.extend(validate(v, props[k], f"{path}.{k}"))
            for pattern, sub in pats.items():
                if re.search(pattern, k):
                    matched = True
                    problems.extend(validate(v, sub, f"{path}.{k}"))
            if not matched and schema.get("additionalProperties") is False:
                problems.append(f"{path}: key '{k}' is not allowed here")

    if "anyOf" in schema:
        branch_problems = [validate(value, s, path) for s in schema["anyOf"]]
        if all(b for b in branch_problems):
            problems.append(f"{path}: value matches none of the anyOf branches")

    return problems


def main():
    ap = argparse.ArgumentParser(description="validate a JSON document against a schema subset")
    ap.add_argument("schema")
    ap.add_argument("doc")
    ap.add_argument("--json", action="store_true",
                    help="kept for call compatibility; the envelope is always emitted")
    args = ap.parse_args()

    sp, dp = Path(args.schema), Path(args.doc)
    try:
        schema = json.loads(sp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise L.ToolError("E-SCHEMA",
                          "cannot read schema: " + type(e).__name__ + ": " + str(e),
                          L.EXIT_USAGE)
    try:
        doc = json.loads(dp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise L.ToolError("E-SCHEMA",
                          "cannot read input: " + type(e).__name__ + ": " + str(e),
                          L.EXIT_USAGE)

    problems = validate(doc, schema)
    data = {"schema": sp.name, "doc": dp.name,
            "valid": not problems, "problems": problems}
    if problems:
        return L.emit(SCRIPT, status="fail", code=L.EXIT_FAIL, data=data,
                      errors=[L.err("E-SCHEMA",
                                    "document does not conform to the schema: " +
                                    " | ".join(problems[:5]))])
    return L.emit(SCRIPT, status="ok", code=L.EXIT_OK, data=data)


if __name__ == "__main__":
    sys.exit(L.run_cli(SCRIPT, main))

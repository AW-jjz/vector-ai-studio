#!/usr/bin/env python3
"""C1 front step: validate the LLM-frozen element list + palette, emit spec.json.

Never fabricates requirements: an empty prompt / empty element list is an
explicit E-EMPTY-SPEC failure with a request for more input.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L

SCRIPT = "freeze_spec.py"
SPEC_SCHEMA = "vector-ai-studio/spec-v1"


def _validate(obj):
    prompt = str(obj.get("prompt", "")).strip()
    if not prompt:
        raise L.ToolError("E-EMPTY-SPEC",
                          "prompt is empty; supply the user requirement, do not fabricate",
                          L.EXIT_FAIL)
    els = obj.get("elements")
    if not isinstance(els, list) or len(els) == 0:
        raise L.ToolError("E-EMPTY-SPEC",
                          "elements must be a non-empty list; frozen checklist is mandatory",
                          L.EXIT_FAIL)
    norm_els = []
    seen = set()
    for e in els:
        if not isinstance(e, dict):
            raise L.ToolError("E-EMPTY-SPEC", "each element must be an object", L.EXIT_FAIL)
        eid = str(e.get("id", "")).strip()
        desc = str(e.get("desc", "")).strip()
        if not eid or not desc:
            raise L.ToolError("E-EMPTY-SPEC",
                              "each element needs a non-empty id and desc", L.EXIT_FAIL)
        if eid in seen:
            raise L.ToolError("E-EMPTY-SPEC", "duplicate element id: " + eid, L.EXIT_FAIL)
        seen.add(eid)
        norm_els.append({"id": eid, "desc": desc})
    pal = obj.get("palette")
    if not isinstance(pal, list) or len(pal) == 0:
        raise L.ToolError("E-EMPTY-SPEC",
                          "palette must be a non-empty frozen style color list", L.EXIT_FAIL)
    norm_pal = []
    for c in pal:
        nc = L.normalize_color(c)
        if nc is None:
            raise L.ToolError("E-EMPTY-SPEC", "invalid palette color: " + str(c), L.EXIT_FAIL)
        norm_pal.append(nc)
    attrs = []
    for a in obj.get("attributes", []) or []:
        if isinstance(a, dict) and str(a.get("id", "")).strip():
            attrs.append({"id": str(a.get("id")).strip(),
                          "desc": str(a.get("desc", "")).strip(),
                          "kind": str(a.get("kind", "")).strip()})
    rounds = obj.get("rounds", {}) or {}
    current = rounds.get("current", 1) if isinstance(rounds, dict) else 1
    return {
        "schema": SPEC_SCHEMA,
        "prompt": prompt,
        "elements": norm_els,
        "attributes": attrs,
        "palette": norm_pal,
        "rounds": {"current": current, "K_max_ref": "config/thresholds.json"},
    }


def main():
    ap = argparse.ArgumentParser(description="validate frozen spec")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--out", default="")
    ap.add_argument("--run-id", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    obj = L.read_json(args.spec, expect_schema=SPEC_SCHEMA)
    norm = _validate(obj)
    thresholds = L.load_thresholds()
    rid = args.run_id or L.sha_run_id(
        json.dumps(norm, ensure_ascii=False, sort_keys=True), thresholds["version"])

    artifacts = {}
    if args.out:
        L.write_json_atomic(args.out, norm)
        artifacts["spec"] = Path(args.out).name
    L.append_run_log(Path(args.out).parent if args.out else L.default_output_root(),
                     SCRIPT + " validated spec run_id=" + rid)

    return L.emit(SCRIPT, status="ok", code=L.EXIT_OK, run_id=rid,
                  data={"run_id": rid,
                        "elements": [e["id"] for e in norm["elements"]],
                        "palette": norm["palette"],
                        "element_count": len(norm["elements"]),
                        "attribute_count": len(norm["attributes"])},
                  artifacts=artifacts)


if __name__ == "__main__":
    sys.exit(L.run_cli(SCRIPT, main))

#!/usr/bin/env python3
"""A/B binary-judgment result: schema validation + numeric statistics ONLY.

This script NEVER judges semantics. Hit / miss, conflicting subject, balance
and alignment are decided by the host LLM reading the rendered PNG. The script
only validates the shape of that output and counts hits. When the host cannot
read images (L2) it emits degraded with no fabricated scores.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L

SCRIPT = "llm_judge.py"
JUDGE_SCHEMA = "vector-ai-studio/judge-v1"
SPEC_SCHEMA = "vector-ai-studio/spec-v1"
_FORBIDDEN_KEYS = {"score", "scores", "grade", "level", "excellent", "rating"}


def _rate(rows, key):
    if not rows:
        return None
    hits = sum(1 for r in rows if r.get(key) is True)
    return round(hits / len(rows), 6)


def _validate(obj, spec):
    bad = _FORBIDDEN_KEYS.intersection({k.lower() for k in obj.keys()})
    if bad:
        raise L.ToolError("E-JUDGE-SCHEMA",
                          "judge.json must not contain free-text scores: " + ",".join(sorted(bad)),
                          L.EXIT_FAIL)
    items = obj.get("items")
    if not isinstance(items, list) or len(items) == 0:
        raise L.ToolError("E-JUDGE-SCHEMA", "items must be a non-empty binary list", L.EXIT_FAIL)
    for it in items:
        if not isinstance(it, dict) or not str(it.get("id", "")).strip() or not isinstance(it.get("hit"), bool):
            raise L.ToolError("E-JUDGE-SCHEMA",
                              "each item needs id and a boolean hit; no text scores", L.EXIT_FAIL)
    if not isinstance(obj.get("subject_conflict", False), bool):
        raise L.ToolError("E-JUDGE-SCHEMA", "subject_conflict must be boolean", L.EXIT_FAIL)
    for field, flag in (("attributes", "hit"), ("align", "pass")):
        rows = obj.get(field, []) or []
        if not isinstance(rows, list):
            raise L.ToolError("E-JUDGE-SCHEMA", field + " must be a list", L.EXIT_FAIL)
        for r in rows:
            if not isinstance(r, dict) or not isinstance(r.get(flag), bool):
                raise L.ToolError("E-JUDGE-SCHEMA",
                                  field + " rows need a boolean " + flag, L.EXIT_FAIL)
    if spec is not None:
        want = {str(e.get("id")).strip() for e in spec.get("elements", [])}
        got = {str(it.get("id")).strip() for it in items}
        missing = sorted(want - got)
        if missing:
            raise L.ToolError("E-JUDGE-SCHEMA",
                              "judge items missing frozen element ids: " + ",".join(missing),
                              L.EXIT_FAIL)
    return items


def _normalize(obj, items, degraded, stats, th):
    reason = (th["messages"]["reason_capability_missing"] if degraded
              else th["messages"]["reason_none"])
    return {
        "schema": JUDGE_SCHEMA,
        "readable": bool(obj.get("readable", True)),
        "degraded": degraded,
        "capability_missing": bool(degraded),
        "reason": reason,
        "items": [{"id": str(it["id"]).strip(), "hit": bool(it["hit"])} for it in items],
        "subject_conflict": bool(obj.get("subject_conflict", False)),
        "attributes": [{"id": str(a.get("id", "")).strip(), "hit": bool(a.get("hit", False))}
                       for a in (obj.get("attributes", []) or [])],
        "align": [{"id": str(a.get("id", "")).strip(), "pass": bool(a.get("pass", False))}
                  for a in (obj.get("align", []) or [])],
        "stats": stats,
    }


def main():
    ap = argparse.ArgumentParser(description="validate and count LLM binary judgments")
    ap.add_argument("--judge", required=True)
    ap.add_argument("--validate", action="store_true")
    ap.add_argument("--spec", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--run-id", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    th = L.load_thresholds()
    obj = L.read_json(args.judge, expect_schema=JUDGE_SCHEMA)
    spec = None
    if args.spec:
        spec = L.read_json(args.spec, expect_schema=SPEC_SCHEMA)

    artifacts = {}
    # L2: host cannot read images -> capability_missing, never fabricate scores.
    # hit/alignment rates stay null; the script never judges semantics.
    if obj.get("readable") is False or obj.get("capability_missing") is True:
        stats = {"hit_rate": None, "attr_hit_rate": None,
                 "align_pass_rate": None, "item_count": 0}
        norm = _normalize(obj, [], True, stats, th)
        if args.out:
            L.write_json_atomic(args.out, norm)
            artifacts["judge"] = Path(args.out).name
        L.append_run_log(Path(args.out).parent if args.out else L.default_output_root(),
                         SCRIPT + " L2 degraded: host cannot read images")
        return L.emit(SCRIPT, status="degraded", code=L.EXIT_MISSING,
                      run_id=args.run_id,
                      data={"degraded": True, "readable": False, "stats": stats,
                            "capability_missing": True, "retry": 0,
                            "reason": th["messages"]["reason_capability_missing"],
                            "element_ids": []},
                      degradation={"level": "L2",
                                   "reason": "host LLM cannot read rendered PNG",
                                   "substitute": "A/B capability_missing status + human final review; C/D/F and H1-H4 still scored"},
                      artifacts=artifacts)

    items = None
    last = None
    retry = 0
    # Exactly one retry on E-JUDGE-SCHEMA (never a loop): the producer may have
    # rewritten the file, so the retry re-reads it. A schema failure after the
    # retry stays E-JUDGE-SCHEMA with exit 1.
    for attempt in range(2):
        if attempt == 1:
            retry = 1
            try:
                obj = L.read_json(args.judge, expect_schema=JUDGE_SCHEMA)
            except L.ToolError:
                pass  # keep the previous object; validation below fails again
        try:
            items = _validate(obj, spec)
            break
        except L.ToolError as e:
            if e.code != "E-JUDGE-SCHEMA":
                raise
            last = e
    if items is None:
        return L.fail(SCRIPT, L.EXIT_FAIL, "E-JUDGE-SCHEMA",
                      last.msg + " (after 1 retry)",
                      data={"retry": retry, "schema_valid": False, "readable": True})

    attrs = obj.get("attributes", []) or []
    aligns = obj.get("align", []) or []
    stats = {"hit_rate": _rate(items, "hit"),
             "attr_hit_rate": _rate(attrs, "hit"),
             "align_pass_rate": _rate(aligns, "pass"),
             "item_count": len(items)}
    norm = _normalize(obj, items, False, stats, th)
    if args.out:
        L.write_json_atomic(args.out, norm)
        artifacts["judge"] = Path(args.out).name
    L.append_run_log(Path(args.out).parent if args.out else L.default_output_root(),
                     SCRIPT + " hit_rate=" + str(stats["hit_rate"]) + " retry=" + str(retry))

    return L.emit(SCRIPT, status="ok", code=L.EXIT_OK, run_id=args.run_id,
                  data={"degraded": False, "readable": True, "stats": stats,
                        "retry": retry,
                        "element_ids": [str(it["id"]).strip() for it in items]},
                  artifacts=artifacts)


if __name__ == "__main__":
    sys.exit(L.run_cli(SCRIPT, main))

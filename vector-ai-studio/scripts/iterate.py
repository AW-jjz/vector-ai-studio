#!/usr/bin/env python3
"""C2: ONE independent run's convergence (K is a per-run upper bound).

Extracts failed dims deterministically and emits revision.json. Under L2 the
A/B capability-missing status makes a pass impossible, so the run uses its
full K rounds normally instead of stopping early. Natural-language rewriting
belongs to the LLM; this script only produces a structured skeleton.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L

SCRIPT = "iterate.py"
SCORE_SCHEMA = "vector-ai-studio/score-v1"
SPEC_SCHEMA = "vector-ai-studio/spec-v1"
_METRIC = {"A": "hit_rate", "B": "whitespace", "C": "color_count",
           "D": "degenerate_paths", "E": "average", "F": "h1_pass_rate"}


def main():
    ap = argparse.ArgumentParser(description="deterministic revision skeleton")
    ap.add_argument("--round", type=int, required=True)
    ap.add_argument("--run", default="run-1")
    ap.add_argument("--score", required=True)
    ap.add_argument("--spec", required=True)
    ap.add_argument("--thresholds", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--run-id", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    th = L.load_thresholds(args.thresholds or None)
    sc = L.read_json(args.score, expect_schema=SCORE_SCHEMA)
    spec = L.read_json(args.spec, expect_schema=SPEC_SCHEMA)
    k_max = th["iterate"]["K_max"]
    msg = th["messages"]

    failed = list(sc.get("failed_dims", []))
    passed = list(sc.get("passed_dims", []))
    degraded = list(sc.get("degraded_dims", []))
    scores = sc.get("scores", {})

    l2 = bool(sc.get("capability_missing")) or bool(sc.get("l2_degraded")) or bool(degraded)
    if l2:
        # A/B are capability-missing status, so no pass is possible; each run
        # normally uses its full K rounds (no third grading tier, no early stop_pass).
        decision = "stop_exhausted" if args.round >= k_max else "continue"
        grade = msg["grade_fail"]
    elif not failed:
        decision = "stop_pass"
        grade = msg["grade_pass"]
    elif args.round >= k_max:
        decision = "stop_exhausted"
        grade = msg["grade_fail"]
    else:
        decision = "continue"
        grade = msg["grade_fail"]

    failed_detail = []
    for d in failed:
        v = scores.get(d)
        if not isinstance(v, dict):
            continue
        metric = _METRIC.get(d, "measured")
        measured = v.get("measured", {})
        failed_detail.append({
            "dim": d, "metric": metric,
            "measured": measured.get(metric),
            "threshold": v.get("threshold"),
            "score": v.get("score"),
        })

    must_keep = {}
    if "A" in passed:
        must_keep["A"] = {"rule": "keep-all-hit-elements",
                          "frozen_element_ids": [e.get("id") for e in spec.get("elements", [])]}
    if "D" in passed:
        dv = scores.get("D", {})
        must_keep["D"] = {"rule": "keep-group-structure",
                          "g_ids": (dv.get("measured", {}) or {}).get("g_ids", [])}
    for d in passed:
        if d not in must_keep:
            must_keep[d] = {"rule": "keep-passed-structure-unchanged"}

    revision = {
        "schema": "vector-ai-studio/revision-v1",
        "run_id": args.run_id,
        "source_run": args.run,
        "generations_this_run": 1,
        "from_round": args.round,
        "to_round": args.round + 1 if decision == "continue" else args.round,
        "grade": grade,
        "decision": decision,
        "K_max": k_max,
        "failed_dims": failed_detail,
        "passed_dims": passed,
        "degraded_dims": degraded,
        "change_only": failed,
        "must_keep": must_keep,
        "spec_ref": "spec.json",
        "prev_svg_ref": "round-" + str(args.round) + "/sanitized.svg",
        "headline": msg["l1_headline"] if decision == "stop_exhausted" else "",
    }

    artifacts = {}
    if args.out:
        L.write_json_atomic(args.out, revision)
        artifacts["revision"] = Path(args.out).name
        # P2-2: the producer side of generation_count. batch_check sums these
        # per-run records instead of assuming "1 per run" with no evidence.
        L.write_json_atomic(Path(args.out).parent / "generation.json",
                            {"run": args.run, "round": args.round,
                             "count": 1, "kind": "c2-regeneration"})
    L.append_run_log(Path(args.out).parent if args.out else L.default_output_root(),
                     SCRIPT + " decision=" + decision + " change_only=" + ",".join(failed))

    return L.emit(SCRIPT, status="ok",
                  code=L.EXIT_FAIL if failed or degraded else L.EXIT_OK,
                  run_id=args.run_id,
                  data={"decision": decision, "grade": grade,
                        "change_only": failed, "passed_dims": passed,
                        "failed_dims": failed_detail, "round": args.round,
                        "K_max": k_max},
                  artifacts=artifacts)


if __name__ == "__main__":
    sys.exit(L.run_cli(SCRIPT, main))

#!/usr/bin/env python3
"""calibration_bench.py — does the proposed threshold actually generalise?

The promotion guard (scripts/thresholds_guard.py) checks that a threshold
change carries the right paperwork. Paperwork is not evidence: a proposal can
name a real run log, a sample count and a ruling and still make the gate
worse. This bench is the missing half.

A calibration set is a list of samples with a measured value, a human label
(pass / fail) and a split (fit / holdout):

    {"schema": ".../calibration-set-v1", "field": "C.max_colors_pass",
     "direction": "max",
     "samples": [{"id": "s01", "value": 4, "label": "pass", "split": "fit"},
                 ...]}

The bench applies the CURRENT and the PROPOSED threshold to both splits and
computes the balanced error rate (BER = mean of false-positive rate and
false-negative rate; 0 is perfect, 0.5 is coin-flip). A promotion is accepted
only when

    fit BER does not get worse  AND  holdout BER strictly improves.

The holdout requirement is the point: a threshold tuned on the same samples it
is judged on will always look better. Requiring a strict improvement on data
the proposal did not fit is the cheapest honest defence against painting noise
green. Sample size is reported, not gated; a strict gain on a tiny holdout can
still be luck, and the report says how many samples it came from.

Direction semantics: max accepts value <= t, min accepts value >= t, range
accepts lo <= value <= hi. Labels are the human judgement the threshold is
supposed to approximate.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L

SCRIPT = "calibration_bench.py"
SET_SCHEMA = "vector-ai-studio/calibration-set-v1"
NUM = (int, float)
LABELS = ("pass", "fail")
SPLITS = ("fit", "holdout")


def _numeric(v):
    return isinstance(v, NUM) and not isinstance(v, bool)


def _accept(direction, value, threshold):
    if direction == "max":
        return value <= threshold
    if direction == "min":
        return value >= threshold
    if direction == "range":
        return threshold[0] <= value <= threshold[1]
    raise ValueError("unknown direction: " + repr(direction))


def _rates(samples, direction, threshold):
    tp = fp = tn = fn = 0
    for s in samples:
        predicted = _accept(direction, s["value"], threshold)
        if predicted and s["label"] == "pass":
            tp += 1
        elif predicted and s["label"] == "fail":
            fp += 1
        elif (not predicted) and s["label"] == "fail":
            tn += 1
        else:
            fn += 1
    sens = (tp / float(tp + fn)) if (tp + fn) else None
    spec = (tn / float(tn + fp)) if (tn + fp) else None
    ber = None if sens is None or spec is None else (1.0 - (sens + spec) / 2.0)
    return {"tp": tp, "fp": fp, "tn": tn, "fn": fn,
            "sensitivity": None if sens is None else round(sens, 4),
            "specificity": None if spec is None else round(spec, 4),
            "ber": None if ber is None else round(ber, 4),
            "n": len(samples)}


def evaluate(doc, field, direction, before, after):
    """Validate the set, score both thresholds, decide whether the gain holds."""
    problems = []

    def add(code, msg):
        problems.append({"code": code, "message": msg})

    def done():
        return {
            "schema": "vector-ai-studio/calibration-bench-v1",
            "field": field, "direction": direction, "n": len(samples) if isinstance(samples, list) else 0,
            "fit": report.get("fit"), "holdout": report.get("holdout"),
            "gain": bool(problems == []),
            "problems": problems,
        }

    samples = doc.get("samples")
    report = {}
    if doc.get("schema") != SET_SCHEMA:
        add("E-CALIB-DATASET", "calibration set schema must be " + SET_SCHEMA)
        return done()
    if doc.get("field") != field:
        add("E-CALIB-DATASET", "set field %r != proposal field %r"
            % (doc.get("field"), field))
        return done()
    if doc.get("direction") != direction:
        add("E-CALIB-DATASET", "set direction %r != policy direction %r"
            % (doc.get("direction"), direction))
        return done()
    if not isinstance(samples, list) or not samples:
        add("E-CALIB-DATASET", "calibration set has no samples")
        return done()

    seen = set()
    clean = []
    for n, s in enumerate(samples, 1):
        if not isinstance(s, dict):
            add("E-CALIB-DATASET", "sample %d is not an object" % n)
            continue
        sid = str(s.get("id") or "").strip()
        value = s.get("value")
        label = s.get("label")
        split = s.get("split")
        if not sid:
            add("E-CALIB-DATASET", "sample %d has no id" % n)
            continue
        if sid in seen:
            add("E-CALIB-DATASET", "duplicate sample id %r" % sid)
            continue
        seen.add(sid)
        if not _numeric(value):
            add("E-CALIB-DATASET", "%s value must be numeric" % sid)
            continue
        if label not in LABELS:
            add("E-CALIB-DATASET", "%s label must be pass/fail" % sid)
            continue
        if split not in SPLITS:
            add("E-CALIB-DATASET", "%s split must be fit/holdout" % sid)
            continue
        clean.append({"id": sid, "value": value, "label": label, "split": split})
    if problems:
        return done()

    for split in SPLITS:
        part = [s for s in clean if s["split"] == split]
        label_counts = {lab: sum(1 for s in part if s["label"] == lab)
                        for lab in LABELS}
        if split == "holdout":
            if not part:
                add("E-CALIB-NO-HOLDOUT",
                    "no holdout samples: a threshold judged on its own fit set "
                    "proves nothing")
                continue
            if label_counts["pass"] < 2 or label_counts["fail"] < 2:
                add("E-CALIB-HOLDOUT-THIN",
                    "holdout needs at least 2 pass and 2 fail samples; got %r"
                    % label_counts)
                continue
        elif split == "fit" and (label_counts["pass"] < 2 or label_counts["fail"] < 2):
            add("E-CALIB-DATASET",
                "fit split needs at least 2 pass and 2 fail samples; got %r"
                % label_counts)
            continue
        report[split] = {
            "before": _rates(part, direction, before),
            "after": _rates(part, direction, after),
            "samples": len(part),
        }
    if problems:
        return done()

    fit_b, fit_a = report["fit"]["before"]["ber"], report["fit"]["after"]["ber"]
    hold_b, hold_a = report["holdout"]["before"]["ber"], report["holdout"]["after"]["ber"]
    if fit_b is None or fit_a is None or hold_b is None or hold_a is None:
        add("E-CALIB-DATASET", "BER is undefined: a split is missing a class")
        return done()
    if fit_a > fit_b:
        add("E-CALIB-NO-GAIN",
            "fit BER worsens: %r -> %r" % (fit_b, fit_a))
    if hold_a >= hold_b:
        add("E-CALIB-NO-GAIN",
            "holdout BER does not strictly improve: %r -> %r (n=%d)"
            % (hold_b, hold_a, report["holdout"]["samples"]))
    return done()


def main():
    ap = argparse.ArgumentParser(description="score a threshold change on a held-out set")
    ap.add_argument("--set", required=True, help="calibration set JSON")
    ap.add_argument("--proposal", required=True, help="proposal JSON (field/before/after)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    doc = L.read_json(args.set)
    prop = L.read_json(args.proposal)
    field = str(prop.get("field") or "").strip()
    direction = str(doc.get("direction") or "").strip()
    res = evaluate(doc, field, direction, prop.get("before"), prop.get("after"))
    ok = not res["problems"]
    return L.emit(SCRIPT, status="ok" if ok else "fail",
                  code=L.EXIT_OK if ok else L.EXIT_FAIL,
                  data=res,
                  errors=[L.err(p["code"], p["message"]) for p in res["problems"]])


if __name__ == "__main__":
    sys.exit(main())

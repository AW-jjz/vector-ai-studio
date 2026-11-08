#!/usr/bin/env python3
"""C3 iron rule: deterministic A-F six-dimension scoring.

All numeric / set / structure judgments happen here. The LLM never writes a
score: A consumes llm_judge statistics, B/C/D are geometry / set / structure
measurements, E is Jaccard, F is injected from batch_check. Every threshold is
read from config/thresholds.json - no threshold literal lives in this file.
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L
import perceptual as P

SCRIPT = "score.py"
SPEC_SCHEMA = "vector-ai-studio/spec-v1"
JUDGE_SCHEMA = "vector-ai-studio/judge-v1"
REF_SCHEMA = "vector-ai-studio/reference-v1"


def _in_band(v, band):
    return v is not None and band[0] <= v <= band[1]


def _jaccard(a, b):
    sa, sb = {str(x) for x in a}, {str(x) for x in b}
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _svg_tokens(text, sig):
    corners = sorted(set(re.findall(r"\b(?:rx|ry)\s*=\s*[\"']([^\"']+)[\"']", text)))
    fonts = sorted(set(re.findall(r"font-family\s*[:=]\s*[\"']?([A-Za-z0-9 ,_-]+)", text)))
    return {
        "palette": list(sig["colors"]),
        "stroke_width": list(sig["stroke_widths"]),
        "corner_radius": corners,
        "font_family": [f.strip() for f in fonts],
    }


def _dim(score, measured, threshold, degraded=False):
    return {"score": score, "measured": measured,
            "threshold": threshold, "degraded": degraded}


def _perceptual_block(colors, spec, th):
    """Measure colour perception. Records findings; gates nothing yet.

    Three separate questions, three separate metrics -- collapsing them into one
    "colour score" would hide which one failed:

      duplicates  are two palette members the same colour?  CIEDE2000, which is
                  fitted for SMALL differences and is the right tool here.
      distinct    are declared-distinct colours actually apart?  HyAB, because
                  this is a LARGE-difference question and CIEDE2000 distorts
                  there (Abasi/Amani Tehran/Fairchild 2020).
      contrast    can the pair be read?  WCAG 2.1. Scope is deliberately narrow:
                  SC 1.4.11 constrains only graphics "required to understand the
                  content" and exempts decoration, so an undeclared pair is
                  reported as not_applicable -- never as a silent pass.

    Nothing here flips a dimension to FAIL. Thresholds for the first two are
    uncalibrated by construction (see _src_* in thresholds.json), and a gate set
    on an uncalibrated number is noise with a red light on it.
    """
    C = th["C"]
    warns = []
    labs = {}
    for c in colors:
        rgb = P.parse_hex(c)
        if rgb is not None:
            labs[c] = P.srgb_to_lab(rgb)

    keys = sorted(labs)
    dups = []
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            d = P.delta_e_2000(labs[keys[i]], labs[keys[j]])
            if d < C["duplicate_delta_e_2000_max"]:
                dups.append({"a": keys[i], "b": keys[j],
                             "delta_e_2000": round(d, 6)})
    if dups:
        warns.append("palette contains colours that read as one: "
                     + ", ".join(d["a"] + "/" + d["b"] + " (dE00 "
                                 + str(d["delta_e_2000"]) + ")" for d in dups))

    distinct = {"status": "not_applicable",
                "reason": "spec does not declare palette_distinct",
                "pairs_below": [], "min_hyab": None}
    if spec.get("palette_distinct") is True:
        below, minh = [], None
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                h = P.hyab(labs[keys[i]], labs[keys[j]])
                minh = h if minh is None else min(minh, h)
                if h < C["distinct_hyab_min"]:
                    below.append({"a": keys[i], "b": keys[j],
                                  "hyab": round(h, 6)})
        distinct = {"status": "warn" if below else "pass",
                    "reason": "declared distinct", "pairs_below": below,
                    "min_hyab": None if minh is None else round(minh, 6)}
        if below:
            warns.append("declared-distinct colours are too close under HyAB: "
                         + ", ".join(b["a"] + "/" + b["b"] + " ("
                                     + str(b["hyab"]) + ")" for b in below))

    pairs = spec.get("contrast_pairs") or []
    if not pairs:
        contrast = {"status": "not_applicable",
                    "reason": ("spec declares no contrast_pairs; WCAG 2.1 SC "
                               "1.4.11 covers only graphics required to "
                               "understand the content, so an undeclared pair "
                               "is not silently treated as passing"),
                    "pairs": []}
    else:
        rows, failed, checked, exempted = [], False, 0, 0
        for pair in pairs:
            fg, bg = pair.get("fg"), pair.get("bg")
            role = pair.get("role", "meaning")
            rf, rb = P.parse_hex(fg), P.parse_hex(bg)
            if rf is None or rb is None:
                rows.append({"fg": fg, "bg": bg, "role": role,
                             "status": "invalid",
                             "reason": "unparseable colour"})
                failed = True
                continue
            if role == "decorative":
                # SC 1.4.11 exempts purely aesthetic graphics. Recording the
                # ratio anyway keeps the exemption honest: a reviewer can see
                # what was skipped instead of taking the label on faith.
                rows.append({"fg": fg, "bg": bg, "role": role,
                             "status": "exempt",
                             "normal": round(P.wcag_ratio(rf, rb), 6)})
                exempted += 1
                continue
            ratios = P.contrast_under_cvd(rf, rb)
            floor = (C["contrast_large_text_ratio"] if pair.get("large_text")
                     else C["contrast_text_ratio"]) if role == "text" \
                else C["contrast_graphic_ratio"]
            ok = ratios["normal"] >= floor and ratios["min"] >= C["cvd_contrast_floor"]
            checked += 1
            failed = failed or not ok
            rows.append({"fg": fg, "bg": bg, "role": role,
                         "status": "pass" if ok else "fail",
                         "normal": round(ratios["normal"], 6),
                         "prot": round(ratios["prot"], 6),
                         "deut": round(ratios["deut"], 6),
                         "trit": round(ratios["trit"], 6),
                         "min": round(ratios["min"], 6),
                         "floor": floor})
            if not ok:
                warns.append("contrast below floor for " + str(fg) + " on "
                             + str(bg) + ": normal " + str(round(ratios["normal"], 2))
                             + ", worst observer " + str(round(ratios["min"], 2)))
        # A pair that was never compared against a floor must not read as a
        # pass. Before this branch existed, a spec whose every pair carried
        # role=decorative produced status="pass" with zero comparisons made --
        # the exact shape of "nothing was checked, so it is green".
        if failed:
            c_status, c_reason = "warn", "declared pairs"
        elif checked == 0:
            c_status = "exempt"
            c_reason = ("all " + str(exempted) + " declared pair(s) carry "
                        "role=decorative; none was compared against a floor, "
                        "so this is an exemption, not a pass")
        else:
            c_status = "pass"
            c_reason = ("declared pairs: " + str(checked) + " checked, "
                        + str(exempted) + " exempt")
        contrast = {"status": c_status, "reason": c_reason, "pairs": rows,
                    "checked": checked, "exempted": exempted}

    measured = {"palette_duplicates": {"threshold": C["duplicate_delta_e_2000_max"],
                                       "pairs": dups,
                                       "status": "warn" if dups else "pass"},
                "palette_distinct": distinct,
                "contrast": contrast,
                "note": ("measured only: no threshold below is calibrated, so "
                         "none of these can fail a run")}
    return measured, warns


def main():
    ap = argparse.ArgumentParser(description="deterministic A-F scoring")
    ap.add_argument("--svg", required=True)
    ap.add_argument("--judge", required=True)
    ap.add_argument("--spec", required=True)
    ap.add_argument("--reference", default="")
    ap.add_argument("--thresholds", default="")
    ap.add_argument("--round", type=int, default=1)
    ap.add_argument("--batch-result", default="")
    ap.add_argument("--text-policy", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--run-id", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    th = L.load_thresholds(args.thresholds or None)
    lv = th["score_levels"]
    FAIL, PASS, EXC = lv["fail"], lv["pass"], lv["excellent"]

    spec = L.read_json(args.spec, expect_schema=SPEC_SCHEMA)
    judge = L.read_json(args.judge, expect_schema=JUDGE_SCHEMA)
    src = Path(args.svg)
    if not src.is_file():
        raise L.ToolError("E-SCHEMA", "SVG not found: " + src.name, L.EXIT_USAGE)
    text = src.read_text(encoding="utf-8")
    sig = L.analyze_svg(text)
    if not sig["parse_ok"]:
        raise L.ToolError("E-SCHEMA", "SVG XML parse failed: " + sig["error"], L.EXIT_USAGE)

    stats = judge.get("stats", {}) or {}
    l2 = (bool(judge.get("degraded")) or bool(judge.get("capability_missing"))
          or stats.get("hit_rate") is None)
    cap = L.capability_missing_value(th)
    degraded_dims = []
    scores = {}
    signals = []

    # ---- A: semantic fidelity (consumes LLM judgment statistics only) ----
    # L2: A is an LLM image-reading judgment with no deterministic equivalent.
    # Emit a status enum, NEVER a geometric/color/path-derived number.
    if l2:
        scores["A"] = cap
        degraded_dims.append("A")
    else:
        hr = stats.get("hit_rate")
        attr = stats.get("attr_hit_rate")
        conflict = bool(judge.get("subject_conflict", False))
        a_pass = (hr is not None and hr >= th["A"]["hit_rate_pass"] and not conflict)
        a_exc = (a_pass and len(spec.get("elements", [])) >= th["A"]["excellent_min_elements"]
                 and attr is not None and attr >= th["A"]["excellent_attr_hit_rate"])
        scores["A"] = _dim(EXC if a_exc else (PASS if a_pass else FAIL),
                           {"hit_rate": hr, "attr_hit_rate": attr,
                            "subject_conflict": conflict,
                            "element_count": len(spec.get("elements", []))},
                           {"hit_rate_pass": th["A"]["hit_rate_pass"],
                            "excellent_min_elements": th["A"]["excellent_min_elements"],
                            "excellent_attr_hit_rate": th["A"]["excellent_attr_hit_rate"]})

    # ---- B: composition / visual balance (geometry + LLM alignment rate) ----
    # L2: the binary balance/alignment judgment also has no deterministic
    # substitute. Keep the geometry as measured data in measured_summary, but
    # the B score itself is the capability-missing status enum, not a number.
    if l2:
        scores["B"] = cap
        if "B" not in degraded_dims:
            degraded_dims.append("B")
    else:
        ws = sig["whitespace"]
        off = sig["centroid_offset_ratio"]
        align = stats.get("align_pass_rate")
        oof = sig["out_of_frame"]
        b_pass = (not oof and _in_band(ws, th["B"]["whitespace_pass"])
                  and off is not None and off <= th["B"]["centroid_offset_pass"]
                  and align is not None and align >= th["B"]["align_pass_rate"])
        b_exc = (b_pass and _in_band(ws, th["B"]["whitespace_excellent"])
                 and off is not None and off <= th["B"]["centroid_offset_excellent"]
                 and align is not None and align >= th["B"]["align_pass_rate"])
        scores["B"] = _dim(EXC if b_exc else (PASS if b_pass else FAIL),
                           {"whitespace": ws, "centroid_offset_ratio": off,
                            "out_of_frame": oof, "align_pass_rate": align,
                            "ink_bbox": sig["ink_bbox"], "artboard": sig["artboard"]},
                           {"whitespace_pass": th["B"]["whitespace_pass"],
                            "whitespace_excellent": th["B"]["whitespace_excellent"],
                            "centroid_offset_pass": th["B"]["centroid_offset_pass"],
                            "centroid_offset_excellent": th["B"]["centroid_offset_excellent"],
                            "align_pass_rate": th["B"]["align_pass_rate"]})

    # ---- C: color / style consistency (set comparisons) ----
    colors = list(sig["colors"])
    strokes = list(sig["stroke_widths"])
    frozen = {str(c).lower() for c in spec.get("palette", [])}
    violations = [c for c in colors if c not in frozen and not L.is_neutral(c)]
    groups = sig["groups"]
    total_occ = sum(g.get("color_total", 0) for g in groups)
    unique_occ = sum(len(g.get("colors", [])) for g in groups)
    reuse = round((total_occ - unique_occ) / total_occ, 6) if total_occ else 0.0
    c_pass = (len(colors) <= th["C"]["max_colors_pass"] and not violations
              and len(strokes) <= th["C"]["max_stroke_widths_pass"])
    c_exc = (c_pass and len(colors) <= th["C"]["max_colors_excellent"]
             and len(strokes) <= th["C"]["max_stroke_widths_excellent"]
             and reuse >= th["C"]["group_reuse_excellent"])
    c_score = EXC if c_exc else (PASS if c_pass else FAIL)
    if len(colors) > th["C"]["max_colors_pass"]:
        signals.append("E-C-PALETTE-OVERFLOW")
    scores["C"] = _dim(c_score,
                       {"color_count": len(colors), "colors": colors,
                        "palette_violations": violations,
                        "stroke_width_count": len(strokes),
                        "stroke_widths": strokes, "group_reuse": reuse},
                       {"max_colors_pass": th["C"]["max_colors_pass"],
                        "max_colors_excellent": th["C"]["max_colors_excellent"],
                        "max_stroke_widths_pass": th["C"]["max_stroke_widths_pass"],
                        "max_stroke_widths_excellent": th["C"]["max_stroke_widths_excellent"],
                        "group_reuse_excellent": th["C"]["group_reuse_excellent"],
                        "frozen_palette": sorted(frozen)})
    # Perception is measured alongside the count, not instead of it. The count
    # stays the gate until the perceptual thresholds have been calibrated on
    # real samples; swapping a calibrated-looking number in front of an
    # unvalidated threshold would make the gate weaker while looking stronger.
    perc, perc_warn = _perceptual_block(colors, spec, th)
    scores["C"]["measured"]["perceptual"] = perc

    # ---- D: path cleanliness / editability (static structure) ----
    readable_names = all(not re.fullmatch(r"g\d+", gid) for gid in sig["g_ids"])
    # Reported, not gated. An absolute anchor band cannot mean the same thing
    # at 200x200 and 2000x2000, and none of the vector tooling surveyed scores
    # on an absolute count -- they use a relative share (short segments) or a
    # degeneracy test. Keeping the band in the config while it decides nothing
    # would be decoration; it is listed in the threshold block as deprecated.
    anchors_ok = _in_band(sig["anchor_count"], th["D"]["anchor_count_excellent"])
    short_ok = sig["short_segment_ratio"] <= th["D"]["short_segment_ratio_max"]
    rev_ok = sig["tangent_reversal_count"] <= th["D"]["tangent_reversal_max"]
    text_policy = bool(args.text_policy)
    d_pass = (not (sig["has_image"] or sig["has_base64"]) and not sig["forbidden"]
              and sig["degenerate_paths"] <= th["D"]["degenerate_paths"]
              and len(sig["g_ids"]) >= th["D"]["min_groups"])
    d_exc = d_pass and readable_names and short_ok and rev_ok and text_policy
    scores["D"] = _dim(EXC if d_exc else (PASS if d_pass else FAIL),
                       {"bitmap": sig["has_image"] or sig["has_base64"],
                        "forbidden": list(sig["forbidden"]),
                        "degenerate_paths": sig["degenerate_paths"],
                        "g_count": len(sig["g_ids"]), "g_ids": list(sig["g_ids"]),
                        "anchor_count": sig["anchor_count"],
                        "anchor_count_in_band": anchors_ok,
                        "short_segment_ratio": sig["short_segment_ratio"],
                        "tangent_reversal_count": sig["tangent_reversal_count"],
                        "dense_paths": sig["dense_paths"],
                        "short_segment_note": sig["short_segment_note"],
                        "semantic_group_names": readable_names,
                        "text_policy": args.text_policy or "not-declared",
                        "readback": "delegated_to_H1"},
                       {"min_groups": th["D"]["min_groups"],
                        "degenerate_paths": th["D"]["degenerate_paths"],
                        "short_segment_ratio_max": th["D"]["short_segment_ratio_max"],
                        "tangent_reversal_max": th["D"]["tangent_reversal_max"],
                        "anchor_count_excellent_deprecated":
                            th["D"]["anchor_count_excellent"],
                        "forbid": th["D"]["forbid"]})

    # ---- E: brand / style reuse (restricted; N/A when no reference) ----
    if not args.reference:
        scores["E"] = "N/A"
    else:
        ref = L.read_json(args.reference, expect_schema=REF_SCHEMA)
        produced = _svg_tokens(text, sig)
        jac = {}
        for tok in th["E"]["tokens"]:
            jac[tok] = round(_jaccard(produced.get(tok, []), ref.get(tok, [])), 6)
        avg = round(sum(jac.values()) / len(jac), 6) if jac else 0.0
        e_pass = avg >= th["E"]["jaccard_pass"]
        e_exc = (e_pass and avg >= th["E"]["jaccard_excellent"]
                 and jac.get("palette", 0.0) >= th["E"]["palette_jaccard_excellent"])
        scores["E"] = _dim(EXC if e_exc else (PASS if e_pass else FAIL),
                           {"jaccard": jac, "average": avg, "tokens": produced},
                           {"jaccard_pass": th["E"]["jaccard_pass"],
                            "jaccard_excellent": th["E"]["jaccard_excellent"],
                            "palette_jaccard_excellent": th["E"]["palette_jaccard_excellent"]})

    # ---- F: batch stability (injected from batch_check; N/A for single run) ----
    if args.batch_result:
        br = L.read_json(args.batch_result)
        f = br.get("F", {}) if isinstance(br.get("F"), dict) else br.get("data", {}).get("F", {})
        scores["F"] = f if f else "N/A"
    else:
        scores["F"] = "N/A"

    failed_dims, passed_dims = [], []
    for d in ("A", "B", "C", "D", "E", "F"):
        v = scores[d]
        if isinstance(v, dict):
            if v["score"] == FAIL:
                failed_dims.append(d)
            else:
                passed_dims.append(d)

    score_doc = {
        "schema": "vector-ai-studio/score-v1",
        "run_id": args.run_id,
        "round": args.round,
        "l2_degraded": l2,
        "capability_missing": l2,
        "capability_missing_value": cap,
        "scores": scores,
        "degraded_dims": degraded_dims,
        "failed_dims": failed_dims,
        "passed_dims": passed_dims,
        "signals": signals,
        "perceptual_warnings": perc_warn,
        "measured_summary": {
            "whitespace": sig["whitespace"],
            "centroid_offset_ratio": sig["centroid_offset_ratio"],
            "out_of_frame": sig["out_of_frame"],
            "color_count": len(colors),
            "stroke_width_count": len(strokes),
            "g_count": len(sig["g_ids"]),
            "degenerate_paths": sig["degenerate_paths"],
            "anchor_count": sig["anchor_count"],
            "short_segment_ratio": sig["short_segment_ratio"],
            "tangent_reversal_count": sig["tangent_reversal_count"],
            "artboard": sig["artboard"],
            "viewBox": sig["viewBox"],
        },
    }

    artifacts = {}
    if args.out:
        L.write_json_atomic(args.out, score_doc)
        artifacts["score"] = Path(args.out).name
    L.append_run_log(Path(args.out).parent if args.out else L.default_output_root(),
                     SCRIPT + " round=" + str(args.round) +
                     " failed=" + ",".join(failed_dims) + " l2=" + str(l2))

    has_zero = any(isinstance(v, dict) and v["score"] == FAIL for v in scores.values())
    errors = [L.err(s, "deterministic criterion signal") for s in signals]
    return L.emit(SCRIPT, status="ok", code=L.EXIT_FAIL if has_zero else L.EXIT_OK,
                  run_id=args.run_id,
                  data={"scores": scores, "failed_dims": failed_dims,
                        "passed_dims": passed_dims, "degraded_dims": degraded_dims,
                        "signals": signals, "l2_degraded": l2, "round": args.round},
                  errors=errors, artifacts=artifacts)


if __name__ == "__main__":
    sys.exit(L.run_cli(SCRIPT, main))

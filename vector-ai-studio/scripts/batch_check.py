#!/usr/bin/env python3
"""F: batch stability across N independently generated runs.

Each run converges on its own (up to K rounds, C2 early exit) before F is
aggregated. F statistics are read ONLY from f_source_runs/run-i/ - the C2
revision workspaces in c2_revisions/run-i/ are never backfilled into F.
The single-run --quick preview sets f_verified=false and must not claim a pass.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L

SCRIPT = "batch_check.py"


def _load(p):
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _run_h1(run_path):
    for name in ("h1.json", "structure_readback.json"):
        d = _load(run_path / name)
        if isinstance(d, dict):
            if "pass" in d:
                return bool(d["pass"])
            if d.get("ok") is True:
                return (d.get("raster") == 0 and d.get("placed") == 0
                        and isinstance(d.get("paths"), int) and d["paths"] > 0)
    return False  # a missing log is NEVER a default pass


def _run_generations(run_path):
    d = _load(run_path / "generation.json")
    if isinstance(d, dict) and isinstance(d.get("count"), int) and d["count"] >= 1:
        return d["count"]
    return 1


def _run_layout(runs_dir):
    """Prefer the v1.3 f_source_runs/ layout; fall back to flat run-* dirs."""
    fsrc = runs_dir / "f_source_runs"
    if fsrc.is_dir() and any(p.is_dir() for p in fsrc.glob("run-*")):
        return "isolated", sorted([p for p in fsrc.glob("run-*") if p.is_dir()],
                                  key=lambda p: p.name), (runs_dir / "c2_revisions")
    flat = sorted([p for p in runs_dir.glob("run-*") if p.is_dir()],
                  key=lambda p: p.name)
    return "flat", flat, (runs_dir / "c2_revisions")


def _fs_record(rp, lv):
    sp = rp / "score.json"
    sc = _load(sp)
    h1 = _run_h1(rp)
    if not isinstance(sc, dict):
        return {"run_id": rp.name, "score": {}, "h1": h1,
                "draft_sha256": "", "generation_count": _run_generations(rp),
                "missing": ["score.json"]}
    scores = sc.get("scores", {}) or {}
    ad_scores = [scores.get(d) for d in ("A", "B", "C", "D")]
    ad_numeric = [v["score"] for v in ad_scores if isinstance(v, dict)]
    rate = (round(sum(1 for s in ad_numeric if s >= lv["pass"]) / len(ad_numeric), 6)
            if ad_numeric else 0.0)
    cval = scores.get("C")
    palette = (cval.get("measured", {}) or {}).get("colors", []) if isinstance(cval, dict) else []
    draft = rp / "draft.svg"
    digest = L.file_sha256(draft) if draft.is_file() else L.sha_run_id(rp.name, L.file_sha256(sp))
    return {"run_id": rp.name, "score": sc.get("scores", {}),
            "h1": h1, "ad_pass_rate": rate, "palette": palette,
            "draft_sha256": digest,
            "generation_count": _run_generations(rp), "missing": []}


def _c2_records(c2_root):
    out = []
    if not c2_root or not c2_root.is_dir():
        return out
    for rev in sorted(c2_root.glob("run-*/round-*/revision.json")) + \
               sorted(c2_root.glob("run-*/revision.json")):
        d = _load(rev)
        if not isinstance(d, dict):
            continue
        rid = rev.relative_to(c2_root).parts[0]
        svg = rev.parent / "sanitized.svg"
        digest = d.get("draft_sha256") or (L.file_sha256(svg) if svg.is_file() else "")
        out.append({"run_id": rid, "round": d.get("from_round", d.get("round")),
                    "score": d.get("score_summary", d.get("score", "")),
                    "change_only": d.get("change_only", []),
                    "draft_sha256": digest,
                    "f_run_id": d.get("f_run_id"),
                    "counted_in_f": d.get("counted_in_f", False)})
    return out


def _isolation_problems(fsr, c2):
    """Hard constraint: C2 revisions must never be backfilled into F stats."""
    fs_ids = [str(r.get("run_id", "")) for r in fsr]
    fs_hashes = {str(r.get("draft_sha256")) for r in fsr if r.get("draft_sha256")}
    problems = []
    for r in c2:
        rid = str(r.get("run_id", ""))
        if rid not in fs_ids:
            problems.append("c2_revisions entry has no matching f_source run: " + rid)
        if r.get("f_run_id") is not None and str(r["f_run_id"]) != rid:
            problems.append("cross-run F backfill: " + rid + " -> " + str(r["f_run_id"]))
        if r.get("counted_in_f") is True:
            problems.append("C2 revision counted in F statistics: " + rid)
        if r.get("draft_sha256") and str(r["draft_sha256"]) in fs_hashes:
            problems.append("same SVG hash in c2_revisions and F source statistics: " + rid)
    return problems


def main():
    ap = argparse.ArgumentParser(description="aggregate F across independent runs")
    ap.add_argument("--runs-dir", required=True)
    ap.add_argument("--thresholds", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--run-id", default="")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    th = L.load_thresholds(args.thresholds or None)
    lv = th["score_levels"]
    runs_dir = Path(args.runs_dir)
    if not runs_dir.is_dir():
        raise L.ToolError("E-BATCH-INCOMPLETE", "runs-dir not found: " + runs_dir.name, L.EXIT_USAGE)

    layout, run_paths, c2_root = _run_layout(runs_dir)
    n_need = th["F"]["N_pass"]
    fsr = [_fs_record(rp, lv) for rp in run_paths]
    c2 = _c2_records(c2_root)
    iso_problems = _isolation_problems(fsr, c2)
    f_data_isolated = not iso_problems

    # F statistics are derived ONLY from f_source_runs records.
    per_run = []
    for r in fsr:
        per_run.append({"run": r["run_id"], "h1_pass": r["h1"],
                        "ad_pass_rate": r.get("ad_pass_rate", 0.0),
                        "palette": r.get("palette", []),
                        "draft_sha256": r.get("draft_sha256", ""),
                        "generation_count": r.get("generation_count", 1),
                        "missing": r.get("missing", [])})
    n = len(per_run)
    # P2-2: each run contributes its independent generation (>=1) plus one per
    # C2 revision round actually recorded in c2_revisions/run-i/. This is a real
    # sum over produced records, not a hardcoded "1 per run".
    c2_by_run = {}
    for r in c2:
        c2_by_run[r["run_id"]] = c2_by_run.get(r["run_id"], 0) + 1
    generation_count = sum(r.get("generation_count", 1) + c2_by_run.get(r["run_id"], 0)
                           for r in fsr)
    quick = args.quick or n < n_need

    if n < n_need and not args.quick:
        missing_runs = ["run-" + str(i + 1) for i in range(n, n_need)]
        return L.emit(SCRIPT, status="error", code=L.EXIT_USAGE,
                      run_id=args.run_id,
                      data={"found": n, "required": n_need, "missing_runs": missing_runs},
                      errors=[L.err("E-BATCH-INCOMPLETE",
                                    "missing independent runs: " + ",".join(missing_runs))])

    h1_rate = round(sum(1 for r in per_run if r["h1_pass"]) / n, 6) if n else 0.0
    ad_rate = round(sum(r["ad_pass_rate"] for r in per_run) / n, 6) if n else 0.0
    palettes = [tuple(sorted(r["palette"])) for r in per_run]
    palette_consistent = bool(palettes) and all(p == palettes[0] for p in palettes)

    if quick:
        f_val, f_verified = "N/A", False
        headline = th["orchestration"]["quick_mode"]["headline"]
    else:
        f_pass = (h1_rate >= th["F"]["h1_pass_rate"] and
                  ad_rate >= th["F"]["ad_pass_rate_pass"] and n >= n_need)
        f_exc = (f_pass and n >= th["F"]["N_excellent"] and
                 h1_rate >= th["F"]["h1_pass_rate"] and
                 ad_rate >= th["F"]["ad_pass_rate_excellent"] and palette_consistent)
        f_val = {"score": lv["excellent"] if f_exc else (lv["pass"] if f_pass else lv["fail"]),
                 "measured": {"N": n, "h1_pass_rate": h1_rate,
                              "ad_pass_rate": ad_rate,
                              "palette_consistent": palette_consistent},
                 "threshold": {"N_pass": th["F"]["N_pass"],
                               "N_excellent": th["F"]["N_excellent"],
                               "h1_pass_rate": th["F"]["h1_pass_rate"],
                               "ad_pass_rate_pass": th["F"]["ad_pass_rate_pass"],
                               "ad_pass_rate_excellent": th["F"]["ad_pass_rate_excellent"]},
                 "degraded": False}
        f_verified = f_data_isolated
        headline = "F 已按 N 个独立 run 统计" if f_verified else "F 数据隔离校验未通过"

    budget = L.generation_budget(th)
    doc = {
        "schema": "vector-ai-studio/batch-v1",
        "run_id": args.run_id,
        "quick": bool(quick),
        "layout": layout,
        "f_verified": f_verified,
        "f_data_isolated": f_data_isolated,
        "isolation_problems": iso_problems,
        "generation_count": generation_count,
        "generation_budget": budget,
        "N": n,
        "headline": headline,
        "F": f_val,
        "f_source_runs": fsr,
        "c2_revisions": c2,
        "per_run": per_run,
        "aggregate": {"h1_pass_rate": h1_rate, "ad_pass_rate": ad_rate,
                      "palette_consistent": palette_consistent},
    }
    artifacts = {}
    if args.out:
        L.write_json_atomic(args.out, doc)
        artifacts["batch"] = Path(args.out).name
    L.append_run_log(Path(args.out).parent if args.out else L.default_output_root(),
                     SCRIPT + " N=" + str(n) + " f_verified=" + str(f_verified) +
                     " isolated=" + str(f_data_isolated) + " gen=" + str(generation_count))

    errors = []
    if iso_problems:
        errors.append(L.err("E-F-DATA-ISOLATION", "; ".join(iso_problems)))
    if generation_count > budget["excellent"]:
        errors.append(L.err("E-BUDGET-EXCEEDED",
                            "generation_count exceeds derived upper bound " + str(budget)))

    return L.emit(SCRIPT, status="ok" if not errors else "fail",
                  code=L.EXIT_OK if not errors else L.EXIT_FAIL,
                  run_id=args.run_id,
                  data={"F": f_val, "f_verified": f_verified,
                        "f_data_isolated": f_data_isolated,
                        "isolation_problems": iso_problems,
                        "generation_count": generation_count,
                        "generation_budget": budget,
                        "quick": bool(quick), "N": n, "headline": headline,
                        "per_run": per_run, "f_source_runs": fsr,
                        "c2_revisions": c2, "aggregate": doc["aggregate"]},
                  errors=errors, artifacts=artifacts)


if __name__ == "__main__":
    sys.exit(L.run_cli(SCRIPT, main))

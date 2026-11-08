#!/usr/bin/env python3
"""Layered L1: pure-script selftest. Zero external deps, zero credentials, zero
Illustrator, zero renderer, zero network - MUST be runnable after install.

All sub-script calls use ABSOLUTE script paths while cwd is a temp dir, which
proves cwd independence. Real-machine checks live only in smoke_ai.py (L2).
"""

import argparse
import importlib.util
import json
import re
import shutil
import subprocess
import zlib
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L

SCRIPT = "selftest.py"
SCRIPTS = Path(__file__).resolve().parent
ROOT = L.skill_root()
FIX = L.fixtures_dir()
GOOD = FIX / "good"
BAD = FIX / "bad"

INLINE_FEATURES = ["def ", "#target illustrator", "param(", "New-Object -ComObject",
                   "import json", "import lib_common", "#!/usr/bin/env python"]
THRESHOLD_LITERALS = ["0.15", "0.25", "0.45", "0.6667", "0.70", "0.20", "0.10", "0.8", "0.6"]
CORE_SCRIPTS = ["score.py", "batch_check.py", "iterate.py", "aggregate_log.py"]
O3_BANNED = ["优秀", "Excellent"]
CLAIM_BANNED = ["达标", "优秀", "完美", "Excellent"]


def _call(name, args, cwd, flags=()):
    cmd = ([sys.executable] + list(flags) + [str(SCRIPTS / name)]
           + [str(a) for a in args])
    try:
        r = subprocess.run(cmd, cwd=str(cwd), timeout=90, capture_output=True,
                           text=True, check=False)
    except (OSError, subprocess.SubprocessError):
        return -1, {}, ""
    try:
        env = json.loads(r.stdout or "")
    except json.JSONDecodeError:
        env = {}
    return r.returncode, env, (r.stdout or "")


def _codes(env):
    return [e.get("code", "") for e in env.get("errors", []) if isinstance(e, dict)]


def _chain(work, cwd, svg=None):
    """Run the pure-script chain on `svg` (default: the circle-bearing sample).

    rect-only.svg -> builtin fallback is judged "faithful" -> non-L2, graded run.
    sample.svg    -> circles are rasterised as squares -> "limited" -> auto L2.
    """
    work.mkdir(parents=True, exist_ok=True)
    src_svg = Path(svg) if svg else (GOOD / "sample.svg")
    steps = [
        ("freeze_spec.py", ["--spec", str(GOOD / "spec.json"), "--validate",
                            "--out", str(work / "spec.json")], {0}),
        ("sanitize_svg.py", ["--in", str(src_svg),
                             "--out", str(work / "final.svg")], {0}),
        ("render_png.py", ["--in", str(work / "final.svg"),
                           "--out", str(work / "preview.png"), "--force-builtin"], {0}),
        ("llm_judge.py", ["--judge", str(GOOD / "judge.json"), "--validate",
                          "--spec", str(work / "spec.json"),
                          "--out", str(work / "judge.json")], {0}),
        ("score.py", ["--svg", str(work / "final.svg"), "--judge", str(work / "judge.json"),
                      "--spec", str(work / "spec.json"),
                      "--out", str(work / "score.json")], {0, 1}),
    ]
    info = []
    ok = True
    for name, args, want in steps:
        rc, env, _ = _call(name, args, cwd)
        good = (rc in want and env.get("schema") == L.SCHEMA and
                isinstance(env.get("code"), int) and env.get("code") == rc and
                isinstance(env.get("errors"), list) and isinstance(env.get("data"), dict))
        info.append({"script": name, "exit": rc, "status": env.get("status"), "ok": good})
        ok = ok and good
        if not good:
            # A failed step invalidates every later step. Stop here instead of
            # letting a downstream script crash on a missing artifact: a missing
            # input must turn this chain (and its assertions) red, never raise
            # E-INTERNAL and abort the whole selftest.
            break

    # preview.meta.json is left exactly as render_png.py wrote it. Its `fidelity`
    # field is computed by the rasteriser itself (rect-only + solid fill + no
    # stroke => "faithful", otherwise "limited"); never hand-write it here, that
    # would be fabricating preview evidence. A circle-bearing sample is therefore
    # "limited" and correctly flips to L2.
    # P2-1: pass a content-derived run-id so acceptance.run_id is never empty.
    # Same fixture text + version => same id, so the idempotency check holds.
    spec_text = ((work / "spec.json").read_text(encoding="utf-8")
                 if (work / "spec.json").is_file() else "")
    rid = L.sha_run_id("chain", spec_text, L.load_thresholds()["version"])
    rc, env, _ = _call("aggregate_log.py",
                       ["--run-dir", str(work), "--out", str(work / "acceptance.json"),
                        "--run-id", rid], cwd)
    good = (rc in {0, 1} and env.get("schema") == L.SCHEMA and
            isinstance(env.get("code"), int) and env.get("code") == rc and
            isinstance(env.get("errors"), list) and isinstance(env.get("data"), dict))
    info.append({"script": "aggregate_log.py", "exit": rc,
                 "status": env.get("status"), "ok": good})
    ok = ok and good
    return ok, info


def _jread(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _copy_if(src, dst):
    """Copy only when the source exists; a missing input must never raise."""
    if Path(src).is_file():
        shutil.copyfile(src, dst)
        return True
    return False


def main():
    ap = argparse.ArgumentParser(description="pure-script selftest")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--log-dir", default="")
    ap.add_argument("--fixtures", action="store_true")
    ap.add_argument("--run-id", default="")
    args = ap.parse_args()

    th = L.load_thresholds()
    msg = th["messages"]
    cap = L.capability_missing_value(th)
    rid = args.run_id or L.sha_run_id("selftest", th["version"])
    log_dir = Path(args.log_dir) if args.log_dir else (L.default_output_root() / "selftest" / rid)
    log_dir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="vector-ai-selftest-"))
    a = []

    def add(aid, ok, detail=""):
        a.append({"id": aid, "ok": bool(ok), "detail": str(detail)[:220]})

    # A1 envelope / exit codes + A3 idempotency + A4 cwd independence
    # Graded assertions (A6/A7/A10) need a non-L2 run, so the main chains use the
    # rect-only fixture: the builtin fallback renders it faithfully. A16 keeps the
    # circle-bearing sample to exercise the "limited preview -> L2" road.
    RECT = GOOD / "rect-only.svg"
    w1, w2 = tmp / "chain-a", tmp / "chain-b"
    ok1, chain = _chain(w1, tmp, svg=RECT)
    ok_b, _ = _chain(w2, tmp, svg=RECT)
    add("A1-envelope-exit-codes", ok1 and ok_b, "pure-script chain envelopes valid")
    add("A4-cwd-independent", ok1 and ok_b and tmp.resolve() != ROOT.resolve(),
        "all sub-scripts called by absolute path with temp cwd")

    cmp_files = ["spec.json", "final.svg", "judge.json", "score.json",
                 "acceptance.json", "preview.png", "preview.meta.json"]
    idempotent, detail = True, []
    for f in cmp_files:
        p1, p2 = w1 / f, w2 / f
        if not p1.is_file() or not p2.is_file() or p1.read_bytes() != p2.read_bytes():
            idempotent = False
            detail.append(f)
    add("A3-idempotent-byte-identical", idempotent,
        "differing: " + ",".join(detail) if detail else "judgment JSON byte-identical twice")

    # A2 single source of truth + no hardcoded thresholds
    ok2 = all(k in th for k in ("A", "B", "C", "D", "E", "F", "grade", "iterate",
                                "orchestration", "messages", "calibration_policy",
                                "calibration_log", "score_levels"))
    hard = []
    for s in CORE_SCRIPTS:
        src = (SCRIPTS / s).read_text(encoding="utf-8")
        hits = [t for t in THRESHOLD_LITERALS if t in src]
        if hits or "load_thresholds" not in src:
            hard.append(s + (":" + ",".join(hits) if hits else ":no-load_thresholds"))
    add("A2-thresholds-single-source", ok2 and not hard, ";".join(hard))

    # A5 inline source scan + script/command mapping
    skill_md = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```[^\n]*\n(.*?)```", skill_md, re.S)
    inline = sorted({f for b in blocks for f in INLINE_FEATURES if f in b})
    all_scripts = sorted({p.name for p in SCRIPTS.iterdir()
                          if p.suffix in (".py", ".jsx", ".ps1")})
    unmentioned = [s for s in all_scripts if ("scripts/" + s) not in skill_md]
    add("A5-no-inline-source-and-mapping", not inline and not unmentioned,
        "inline=" + ",".join(inline) + " unmentioned=" + ",".join(unmentioned))

    # A6 O3 outward template / generated note
    tpl = (L.templates_dir() / "delivery-note.template.md").read_text(encoding="utf-8")
    dn = w2 / "delivery-note.md"
    dn_text = dn.read_text(encoding="utf-8") if dn.is_file() else ""
    banned = [w for w in O3_BANNED if w in tpl or w in dn_text]
    add("A6-O3-no-excellent-wording", not banned, ",".join(banned))

    # A7 H4 credential-free default chain
    cred_reads = []
    for p in SCRIPTS.glob("*.py"):
        if re.search(r"os\.environ\s*(?:\[|\.get\()\s*[\"']RECRAFT", p.read_text(encoding="utf-8")):
            cred_reads.append(p.name)
    L.assert_no_credentials()
    add("A7-H4-no-credential-value-read", not cred_reads, ",".join(cred_reads))

    # A8 dual-runtime consistency: same input under the default interpreter and
    # under `-I` isolated mode must yield identical scores. pycache is redirected
    # into the temp dir so isolated mode cannot leave residue in the package.
    s1, s2 = _jread(w1 / "score.json"), _jread(w2 / "score.json")
    iso_out = w1 / "score-isolated.json"
    _call("score.py",
          ["--svg", str(w1 / "final.svg"), "--judge", str(w1 / "judge.json"),
           "--spec", str(w1 / "spec.json"), "--out", str(iso_out)],
          tmp, flags=["-I", "-X", "pycache_prefix=" + str(tmp / "pycache")])
    s_iso = _jread(iso_out)
    iso_equal = bool(s_iso) and s_iso.get("scores") == s1.get("scores")
    optional = {"cairosvg": importlib.util.find_spec("cairosvg") is not None,
                "pyyaml": importlib.util.find_spec("yaml") is not None,
                "svgo": shutil.which("svgo") is not None}
    add("A8-dual-runtime-consistent",
        bool(s1) and s1.get("scores") == s2.get("scores") and iso_equal,
        "default_vs_isolated_equal=" + str(iso_equal) +
        " optional=" + json.dumps(optional, sort_keys=True))

    # A9 calibration policy whitelist
    cp = th.get("calibration_policy", {})
    locked = cp.get("locked", [])
    ok9 = ("calibratable" in cp and "A.*" in locked and "iterate.K_max" in locked
           and "H1" in locked and "F.N_*" in locked)
    for rec in th.get("calibration_log", []) or []:
        if rec.get("loosening") and not rec.get("ruling_ref"):
            ok9 = False
    add("A9-calibration-policy", ok9, "locked=" + str(len(locked)))

    # A10 quick must not claim a pass. L2 outranks quick: an unreadable preview
    # is more fundamental than "F not verified", so either non-pass headline is
    # acceptable. Never tweak the work dir's SVG to dodge L2 here.
    _call("aggregate_log.py", ["--run-dir", str(w1), "--quick", "--run-id", "quick-a10",
                               "--out", str(w1 / "quick-acceptance.json")], tmp)
    qa = _jread(w1 / "quick-acceptance.json")
    ok10 = (qa.get("f_verified") is False and
            qa.get("grade") != msg["grade_pass"] and
            qa.get("headline") in (th["orchestration"]["quick_mode"]["headline"],
                                   msg["l2_headline"]))
    add("A10-quick-f-not-verified", ok10,
        "grade=" + str(qa.get("grade")) + " headline=" + str(qa.get("headline")))

    # A11 real fallback PNG + zero-byte placeholder rejected by H2
    png = (w1 / "preview.png").read_bytes() if (w1 / "preview.png").is_file() else b""
    meta = _jread(w1 / "preview.meta.json")
    real_png = (len(png) >= 24 and png[:8] == b"\x89PNG\r\n\x1a\n" and
                png[12:16] == b"IHDR" and png[-8:-4] == b"IEND" and
                meta.get("renderer") == "builtin-fallback" and meta.get("degraded") is True)
    zp = tmp / "zero-png"
    zp.mkdir(parents=True, exist_ok=True)
    for name in ("final.svg", "score.json"):
        if (w1 / name).is_file():
            shutil.copyfile(w1 / name, zp / name)
    (zp / "preview.png").write_bytes(b"")
    _call("aggregate_log.py", ["--run-dir", str(zp), "--out", str(zp / "acceptance.json")], tmp)
    za = _jread(zp / "acceptance.json")
    h2 = za.get("hard_gates", {}).get("H2", {})
    reject = (h2.get("pass") is False and
              any("preview.png" in m for m in h2.get("missing", [])))
    add("A11-fallback-png-strict-H2", real_png and reject,
        "real_png=" + str(real_png) + " zero_byte_rejected=" + str(reject))

    # A12 L2: no third grading tier, capability-missing status, delivery not blocked
    wl = tmp / "l2"
    wl.mkdir(parents=True, exist_ok=True)
    if (w1 / "spec.json").is_file():
        shutil.copyfile(w1 / "spec.json", wl / "spec.json")
    # A16 keeps the circle-bearing sample on purpose: the builtin fallback draws
    # circles as squares, so render_png judges the preview "limited" and aggregate
    # must auto-degrade to L2. The unreadable judge is the second, independent road
    # to the same status; both must land on capability_missing.
    _copy_if(GOOD / "sample.svg", wl / "final.svg")
    _call("render_png.py", ["--in", str(wl / "final.svg"),
                            "--out", str(wl / "preview.png"), "--force-builtin"], tmp)
    L.write_json_atomic(wl / "judge-src.json", {
        "schema": "vector-ai-studio/judge-v1", "readable": False,
        "items": [], "subject_conflict": False, "attributes": [], "align": []})
    _call("llm_judge.py", ["--judge", str(wl / "judge-src.json"), "--validate",
                           "--out", str(wl / "judge.json")], tmp)
    _call("score.py", ["--svg", str(wl / "final.svg"), "--judge", str(wl / "judge.json"),
                       "--spec", str(wl / "spec.json"), "--out", str(wl / "score.json")], tmp)
    _call("aggregate_log.py", ["--run-dir", str(wl),
                               "--out", str(wl / "acceptance.json")], tmp)
    la = _jread(wl / "acceptance.json")
    lblob = (wl / "acceptance.json").read_text(encoding="utf-8") if (wl / "acceptance.json").is_file() else ""
    l2_words = [w for w in CLAIM_BANNED if w in lblob.replace(msg["grade_fail"], "")]
    ldn = (wl / "delivery-note.md").read_text(encoding="utf-8") if (wl / "delivery-note.md").is_file() else ""
    outward_words = [w for w in CLAIM_BANNED if w in ldn]
    ok12 = (la.get("grade") == msg["l2_grade"] and
            la.get("headline") == msg["l2_headline"] and
            la.get("degraded") is True and
            la.get("needs_human_review") is True and
            la.get("reason") == msg["reason_capability_missing"] and
            la.get("capability_missing") is True and
            la.get("grading_entered") is True and
            la.get("scores", {}).get("A") == cap and
            la.get("scores", {}).get("B") == cap and
            not l2_words and not outward_words)
    add("A12-L2-not-pass-no-third-tier", ok12, "grade=" + str(la.get("grade")) +
        " banned=" + ",".join(l2_words + outward_words))

    # A14 runtime-derived generation budget + real generation_count + rejection
    budget = L.generation_budget(th)
    acc1 = _jread(w1 / "acceptance.json")
    ok14 = (budget == {"pass": th["F"]["N_pass"] * th["iterate"]["K_max"],
                       "excellent": th["F"]["N_excellent"] * th["iterate"]["K_max"]})
    ok14 = (ok14 and isinstance(acc1.get("generation_count"), int) and
            acc1["generation_count"] <= budget["excellent"] and
            acc1.get("generation_budget") == budget)
    rc14, env14, _ = _call("aggregate_log.py",
                           ["--validate-case", str(BAD / "bad_budget_exceeded.json")], tmp)
    ok14 = ok14 and rc14 == 1 and "E-BUDGET-EXCEEDED" in _codes(env14)
    add("A14-generation-budget-derived", ok14,
        "budget=" + json.dumps(budget, sort_keys=True) +
        " count=" + str(acc1.get("generation_count")))

    # A15 O4: f_source_runs / c2_revisions isolation, no cross-run F backfill
    iso = tmp / "o4-isolated"
    for i in range(1, th["F"]["N_pass"] + 1):
        run_id = "run-" + str(i)
        fs = iso / "f_source_runs" / run_id
        fs.mkdir(parents=True, exist_ok=True)
        _copy_if(w1 / "score.json", fs / "score.json")
        _copy_if(w1 / "final.svg", fs / "draft.svg")
        L.write_json_atomic(fs / "h1.json", {"pass": True})
        rev = iso / "c2_revisions" / run_id / "round-2"
        rev.mkdir(parents=True, exist_ok=True)
        L.write_json_atomic(rev / "revision.json",
                            {"run_id": run_id, "from_round": 2, "change_only": ["C"]})
    rc15, env15, _ = _call("batch_check.py",
                           ["--runs-dir", str(iso), "--out", str(iso / "batch.json")], tmp)
    bd = _jread(iso / "batch.json")
    iso_ok = (rc15 == 0 and bd.get("f_data_isolated") is True and
              len(bd.get("f_source_runs", [])) == th["F"]["N_pass"] and
              len(bd.get("c2_revisions", [])) == th["F"]["N_pass"] and
              isinstance(bd.get("generation_count"), int) and
              bd["generation_count"] <= budget["excellent"])
    rc15b, env15b, _ = _call("aggregate_log.py",
                             ["--validate-case", str(BAD / "bad_f_backfill.json")], tmp)
    neg_ok = rc15b == 1 and "E-F-DATA-ISOLATION" in _codes(env15b)
    # Same SVG hash must not appear both in c2_revisions and in F source stats.
    hc = _jread(GOOD / "good_o4_isolated.json")
    if hc.get("c2_revisions"):
        hc["c2_revisions"][0]["draft_sha256"] = hc["f_source_runs"][0]["draft_sha256"]
    hash_path = tmp / "hash-overlap.json"
    L.write_json_atomic(hash_path, hc)
    rc15c, env15c, _ = _call("aggregate_log.py",
                             ["--validate-case", str(hash_path)], tmp)
    hash_ok = rc15c == 1 and "E-F-DATA-ISOLATION" in _codes(env15c)
    add("A15-F-data-isolation", iso_ok and neg_ok and hash_ok,
        "positive=" + str(iso_ok) + " backfill_rejected=" + str(neg_ok) +
        " shared_hash_rejected=" + str(hash_ok) +
        " gen_count=" + str(bd.get("generation_count")))

    # A16 L2 anti-fabrication: A/B must be status enum, never numeric scores
    ls = _jread(wl / "score.json")
    ab_ok = (ls.get("scores", {}).get("A") == cap and
             ls.get("scores", {}).get("B") == cap and
             not isinstance(ls.get("scores", {}).get("A"), dict))
    rc16, env16, _ = _call("aggregate_log.py",
                           ["--validate-case", str(BAD / "bad_l2_fabricated.json")], tmp)
    ok16 = ab_ok and rc16 == 1 and "E-FABRICATED-SCORE" in _codes(env16)
    add("A16-L2-anti-fabrication", ok16,
        "ab_status=" + str(ab_ok) + " rejected=" + str("E-FABRICATED-SCORE" in _codes(env16)))

    # A17 quick mode must never participate in a pass judgement
    rc17, env17, _ = _call("aggregate_log.py",
                           ["--validate-case", str(BAD / "bad_quick_claimed_pass.json")], tmp)
    ok17 = rc17 == 1 and "E-QUICK-UNVERIFIED" in _codes(env17)
    add("A17-quick-not-graded", ok17, "rejected=" + str(ok17))

    # A18 F merged into scores -> the pass tier becomes mechanically reachable.
    # SYNTHETIC validator run: proves the F->scores wiring, not a real .ai.
    # The SVG is rect-only with solid fills and no stroke, so render_png's own
    # _builtin_fidelity() judges the fallback "faithful" and A/B stays numeric
    # (a circle-bearing SVG would be "limited" and correctly flip to L2).
    a18 = tmp / "a18"
    a18.mkdir(parents=True, exist_ok=True)
    rect_ok = RECT.is_file()
    if rect_ok:
        shutil.copyfile(RECT, a18 / "final.svg")
    # A missing fixture deliberately leaves final.svg absent: render_png then
    # returns exit 2 and this assertion goes red, instead of copyfile raising
    # FileNotFoundError and aborting the whole selftest as E-INTERNAL.
    _call("render_png.py", ["--in", str(a18 / "final.svg"),
                            "--out", str(a18 / "preview.png"), "--force-builtin"], tmp)
    meta18 = _jread(a18 / "preview.meta.json")
    _copy_if(GOOD / "judge.json", a18 / "judge.json")
    (a18 / "final.ai").write_bytes(
        b"synthetic-h1-file-existence-placeholder-not-a-real-ai\n")
    L.write_json_atomic(a18 / "structure_readback.json", {
        "ok": True, "raster": 0, "placed": 0, "paths": 5,
        "artboard_matches_viewBox": True})
    L.write_json_atomic(a18 / "selftest.json", {
        "status": "ok", "passed": 1, "failed": 0,
        "assertions": [{"id": "A0", "ok": True}]})
    L.write_json_atomic(a18 / "score.json", {
        "round": 2, "l2_degraded": False, "degraded_dims": [], "failed_dims": [],
        "scores": {"A": {"score": 2, "measured": {}, "threshold": {}},
                   "B": {"score": 2, "measured": {}, "threshold": {}},
                   "C": {"score": 1, "measured": {}, "threshold": {}},
                   "D": {"score": 1, "measured": {}, "threshold": {}},
                   "E": "N/A", "F": "N/A"}})
    L.write_json_atomic(a18 / "batch.json", {
        "schema": "vector-ai-studio/batch-v1", "quick": False,
        "f_verified": True, "f_data_isolated": True, "isolation_problems": [],
        "N": 3, "generation_count": 3,
        "F": {"score": 1, "measured": {"N": 3, "h1_pass_rate": 1.0,
                                       "ad_pass_rate": 0.8333,
                                       "palette_consistent": True},
              "threshold": {}, "degraded": False},
        "f_source_runs": [{"run_id": "run-1", "draft_sha256": "a" * 64},
                          {"run_id": "run-2", "draft_sha256": "b" * 64},
                          {"run_id": "run-3", "draft_sha256": "c" * 64}],
        "c2_revisions": [],
        "per_run": [{"run": "run-" + str(i), "h1_pass": True,
                     "ad_pass_rate": 0.85, "palette": ["#111", "#eee"]}
                    for i in (1, 2, 3)]})
    rc18, env18, _ = _call("aggregate_log.py",
                           ["--run-dir", str(a18), "--run-id", "a18", "--json"], tmp)
    d18 = env18.get("data", {}) or {}
    acc18 = _jread(a18 / "acceptance.json")
    f_dict = isinstance((acc18.get("scores") or {}).get("F"), dict)
    ok18 = (rect_ok and rc18 == 0 and acc18.get("grade") == msg["grade_pass"] and f_dict
            and acc18.get("needs_human_review") is True
            and meta18.get("fidelity") == "faithful")
    add("A18-F-merged-reachable", ok18,
        ("MISSING FIXTURE rect-only.svg; " if not rect_ok else "") +
        "synthetic rect-only run: fallback judged faithful by render_png itself; "
        "proves the F->scores wiring, not preview fidelity; "
        "fidelity=" + str(meta18.get("fidelity")) +
        " grade=" + str(acc18.get("grade")) +
        " needs_human_review=" + str(acc18.get("needs_human_review")) +
        " F_is_dict=" + str(f_dict) +
        " validation=" + str(d18.get("validation_problems")))

    # A18b a compliant full-mode pass-tier acceptance log passes validation
    rc18b, env18b, _ = _call("aggregate_log.py",
                             ["--validate-case", str(GOOD / "good_acceptance_reachable.json")], tmp)
    add("A18b-validator-accepts-pass", rc18b == 0 and not _codes(env18b),
        "codes=" + ",".join(_codes(env18b)))

    # A19 full track without C/D is a broken chain, never an L2 degradation
    rc19, env19, _ = _call("aggregate_log.py",
                           ["--validate-case", str(BAD / "bad_no_score.json")], tmp)
    ok19 = rc19 == 1 and "E-NO-SCORE" in _codes(env19)
    add("A19-full-track-no-score-rejected", ok19, "codes=" + ",".join(_codes(env19)))

    # A20 landing-only track: unevaluated (null), accepted, no tier word anywhere
    rc20, env20, _ = _call("aggregate_log.py",
                           ["--validate-case", str(GOOD / "good_landing_only.json")], tmp)
    lo_blob = (GOOD / "good_landing_only.json").read_text(encoding="utf-8")
    lo_words = [w for w in (CLAIM_BANNED + ["未验证"]) if w in lo_blob]
    ok20 = rc20 == 0 and not _codes(env20) and not lo_words
    add("A20-landing-only-unevaluated", ok20,
        "codes=" + ",".join(_codes(env20)) + " words=" + ",".join(lo_words))

    # A21 the exemption must be policed from both sides
    rc21a, env21a, _ = _call("aggregate_log.py",
                             ["--validate-case", str(BAD / "bad_exemption_abuse_full.json")], tmp)
    rc21b, env21b, _ = _call("aggregate_log.py",
                             ["--validate-case", str(BAD / "bad_exemption_abuse_lo.json")], tmp)
    ok21 = (rc21a == 1 and "E-EXEMPTION-ABUSE" in _codes(env21a) and
            rc21b == 1 and "E-EXEMPTION-ABUSE" in _codes(env21b))
    add("A21-exemption-abuse-both-ways", ok21,
        "full_track_exit=" + str(rc21a) + " lo_track_exit=" + str(rc21b))

    # A22 package hygiene: runtime output/ must never live inside the skill dir,
    # and no stray __pycache__ may be shipped. scripts/ bytecode cache is created
    # by this very run, so it is ignored here and excluded by the packager.
    stray = []
    if (ROOT / "output").exists():
        stray.append("output/")
    for p in ROOT.rglob("__pycache__"):
        if p.is_dir() and p.parent != SCRIPTS:
            stray.append(str(p.relative_to(ROOT)))
    add("A22-package-hygiene", not stray,
        ",".join(stray[:6]) if stray
        else "no output/ and no stray __pycache__ (scripts/ bytecode cache ignored)")

    # A23 L2 must carry all six score keys with the right per-key status.
    # Reverse side: dropping A or F must be rejected with E-NO-SCORE.
    l2_sc = la.get("scores") or {}
    l2_keys = set(l2_sc) >= {"A", "B", "C", "D", "E", "F"}
    l2_shape = (l2_sc.get("A") == cap and l2_sc.get("B") == cap
                and isinstance(l2_sc.get("C"), dict) and isinstance(l2_sc.get("D"), dict)
                and (l2_sc.get("E") == "N/A" or isinstance(l2_sc.get("E"), dict))
                and (l2_sc.get("F") == "N/A" or isinstance(l2_sc.get("F"), dict)))
    rc_mf, env_mf, _ = _call("aggregate_log.py",
                             ["--validate-case", str(BAD / "bad_missing_f.json")], tmp)
    rc_ma, env_ma, _ = _call("aggregate_log.py",
                             ["--validate-case", str(BAD / "bad_missing_a.json")], tmp)
    mf_rej = rc_mf == 1 and "E-NO-SCORE" in _codes(env_mf)
    ma_rej = rc_ma == 1 and "E-NO-SCORE" in _codes(env_ma)
    add("A23-L2-six-keys", l2_keys and l2_shape and mf_rej and ma_rej,
        "keys=" + str(sorted(l2_sc)) + " shape=" + str(l2_shape) +
        " missing_f_rejected=" + str(mf_rej) + " missing_a_rejected=" + str(ma_rej))

    # A24 a graded (full-track) log must carry every grade-required dimension.
    full_sc = acc18.get("scores") or {}
    req_missing = sorted({"A", "B", "C", "D", "F"} - set(full_sc))
    add("A24-full-required-dims", not req_missing and mf_rej and ma_rej,
        "keys=" + str(sorted(full_sc)) + " missing=" + str(req_missing))

    # A25 a passing (grade=达标) log must keep E visible: dropping E would hide
    # "E applicable but scored 0", which is the same fake-pass surface as a
    # missing graded dimension. Reverse-verified with bad_pass_missing_e.json.
    rc_me, env_me, _ = _call("aggregate_log.py",
                             ["--validate-case", str(BAD / "bad_pass_missing_e.json")], tmp)
    me_rej = rc_me == 1 and "E-NO-SCORE" in _codes(env_me)
    add("A25-pass-missing-e-rejected", me_rej,
        "pass_missing_e_rejected=" + str(me_rej) + " codes=" + ",".join(_codes(env_me)))

    # A26: the top-level contract fields and their metadata mirror must agree.
    # Two specifications read this file (internal contract.yaml wants them at
    # top level; the public Agent Skills spec puts author/version under
    # metadata). Keeping both copies is only safe if a machine compares them.
    def _fm():
        txt = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        body = txt.split("---", 2)[1]
        top, meta, cur = {}, {}, None
        for raw in body.splitlines():
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            if raw[0] in " \t":
                if cur:
                    k, _, v = raw.strip().partition(":")
                    meta[k.strip()] = v.strip().strip('"').strip("'")
                continue
            k, _, v = raw.partition(":")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if v == "":
                cur = k
            else:
                cur = None
                top[k] = v
        return top, meta

    _top, _meta = _fm()
    pairs = [(k, _top.get(k), _meta.get(k)) for k in
             ("author", "version", "description_zh", "description_en")]
    mirror_ok = all(m is not None and t == m for _, t, m in pairs)
    add("A26-frontmatter-metadata-mirror", mirror_ok,
        "pairs=" + str([(k, t == m) for k, t, m in pairs]) +
        " | 解析器仅支持扁平 YAML：若改用块标量(>- / |)等结构会在此判红，属预期")

    # A27: the published JSON Schema must accept a real log and reject broken
    # ones. A schema nobody validates against is documentation, not a contract.
    schema_path = ROOT / "references" / "schemas" / "acceptance.schema.json"
    acc_path = w1 / "acceptance.json"
    sch_ok = False
    detail = "schema or acceptance missing"
    if schema_path.is_file() and acc_path.is_file():
        rc_ok, env_ok, _ = _call("schema_check.py",
                                 [str(schema_path), str(acc_path), "--json"], tmp)
        acc_doc = json.loads(acc_path.read_text(encoding="utf-8"))

        bad_dir = tmp / "schema-negative"
        bad_dir.mkdir(parents=True, exist_ok=True)
        missing_mode = dict(acc_doc)
        missing_mode.pop("mode", None)
        bad_mode_path = bad_dir / "missing-mode.json"
        bad_mode_path.write_text(json.dumps(missing_mode, ensure_ascii=False), encoding="utf-8")
        rc_miss, _, _ = _call("schema_check.py",
                              [str(schema_path), str(bad_mode_path), "--json"], tmp)

        bad_grade = dict(acc_doc)
        bad_grade["grade"] = "优秀"
        bad_grade_path = bad_dir / "bad-grade.json"
        bad_grade_path.write_text(json.dumps(bad_grade, ensure_ascii=False), encoding="utf-8")
        rc_grade, _, _ = _call("schema_check.py",
                               [str(schema_path), str(bad_grade_path), "--json"], tmp)

        sch_ok = (rc_ok == 0 and rc_miss == 1 and rc_grade == 1)
        detail = ("valid_exit=" + str(rc_ok) +
                  " missing_mode_exit=" + str(rc_miss) +
                  " bad_grade_exit=" + str(rc_grade))
    add("A27-schema-accepts-real-rejects-broken", sch_ok, detail)

    # A28: every error code emitted by the scripts must be registered, and the
    # registry must not list codes nobody emits. Checked in both directions --
    # a one-way check lets stale entries accumulate, which is how a registry
    # stops being trusted.
    reg_path = ROOT / "references" / "error-codes.md"
    code_set = set()
    for sp in sorted((ROOT / "scripts").glob("*.py")):
        code_set |= set(re.findall(r'"(E-[A-Z0-9-]+)"', sp.read_text(encoding="utf-8")))
    reg_set = set()
    if reg_path.is_file():
        reg_set = set(re.findall(r"`(E-[A-Z0-9-]+)`", reg_path.read_text(encoding="utf-8")))
    reg_ok = bool(code_set) and code_set == reg_set
    add("A28-error-code-registry", reg_ok,
        "in_code=" + str(len(code_set)) + " in_registry=" + str(len(reg_set)) +
        " unregistered=" + ",".join(sorted(code_set - reg_set)[:6]) +
        " stale=" + ",".join(sorted(reg_set - code_set)[:6]))

    # A29: the measurement function must actually measure.
    #
    # Round 6 shipped a colour-operator counter that returned None on *every*
    # real deliverable because zlib.decompress() rejected the trailing CR/LF.
    # The function existed, was called, and produced a plausible-looking field
    # ("undecodable") -- and no assertion noticed.
    #
    # A well-formed synthetic stream is not enough to catch that: strict
    # decompression handles it fine. The sample below truncates the trailing
    # adler32 so the stream is incomplete, which is what Illustrator actually
    # writes. Strict decompression fails on it; decompressobj succeeds. That
    # difference is the whole point, so it is the sample this assertion uses.
    a29_ok, a29_detail = False, "not run"
    try:
        _spec = importlib.util.spec_from_file_location("land_ai_under_test",
                                                       ROOT / "scripts" / "land_ai.py")
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)

        payload = b"0.2 0.4 0.6 rg\n0 0 0 1 K\n"
        complete = zlib.compress(payload)
        truncated = complete[:-4]                      # drop adler32 -> incomplete
        blob = b"%PDF-1.6\nstream\n" + truncated + b"\nendstream\n"
        ops, stats = _mod._content_ops(blob)

        junk = b"%PDF-1.6\nstream\n" + b"\x00\x01\x02\x03" + b"\nendstream\n"
        ops_junk, stats_junk = _mod._content_ops(junk)

        a29_ok = (ops == {"rg": 1, "RG": 0, "k": 0, "K": 1} and
                  stats.get("decoded") == 1 and
                  ops_junk is None and stats_junk.get("decoded") == 0)
        a29_detail = ("incomplete_stream_ops=" + str(ops) +
                      " decoded=" + str(stats.get("decoded")) +
                      " junk_ops=" + str(ops_junk))
    except Exception as e:                      # noqa: BLE001 - report, do not crash
        a29_detail = type(e).__name__ + ": " + str(e)[:140]
    add("A29-content-ops-measurement", a29_ok, a29_detail)

    # optional: fixtures regression (now includes the six v1.3 samples)
    if args.fixtures:
        frc, fenv, _ = _call("run_fixtures.py", ["--json", "--log-dir", str(log_dir)], tmp)
        add("A13-fixtures-regression", frc == 0 and fenv.get("data", {}).get("all_ok") is True,
            "exit=" + str(frc))

    passed = sum(1 for x in a if x["ok"])
    failed = [x["id"] for x in a if not x["ok"]]
    doc = {
        "schema": "vector-ai-studio/selftest-v1",
        "status": "ok" if not failed else "fail",
        "run_id": rid,
        "passed": passed, "failed": len(failed),
        "assertions": a,
        "chain": chain,
        "optional_deps": optional,
        "generation_budget": budget,
        "cwd_independent": True,
        "note": "pure-script layer only; real-machine checks in smoke_ai.py",
    }
    L.write_json_atomic(log_dir / "selftest.json", doc)
    L.append_run_log(log_dir, SCRIPT + " passed=" + str(passed) + "/" + str(len(a)))
    return L.emit(SCRIPT, status=doc["status"],
                  code=L.EXIT_OK if not failed else L.EXIT_FAIL,
                  run_id=rid, data=doc,
                  errors=([L.err("E-SELFTEST", "failed assertions: " + ",".join(failed))]
                          if failed else []),
                  artifacts={"selftest": "selftest.json"})


if __name__ == "__main__":
    sys.exit(L.run_cli(SCRIPT, main))

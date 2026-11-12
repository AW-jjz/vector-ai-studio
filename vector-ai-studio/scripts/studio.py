#!/usr/bin/env python3
"""One command from a draft SVG to a landed .ai, with the stages composed
instead of remembered.

Why this exists: the ten-step workflow is correct but long, and every extra
hand-typed command is a place to skip a gate (forget --validate, forget
--judge, land an unsanitized draft). This script runs the same scripts in the
same order through their real CLIs, stops at the first blocking failure, and
writes studio.json so a human can see which stage said what.

It does NOT generate the design. The draft SVG and the judge file stay authored
by the host LLM; this is orchestration, not creation.

Stage order and stop policy:
  spec    freeze_spec --validate        stop on failure (bad spec is not a draft)
  clean   sanitize_svg                  stop on failure (forbidden constructs)
  render  render_png                    continue, degraded (preview is evidence,
                                        not a gate)
  legible legibility                    stop on failure (a mark that vanishes at
                                        16px is not landable); skipped when the
                                        preview came from the builtin fallback,
                                        because measuring a low-fidelity PNG
                                        would mislead
  score   llm_judge + score             stop on failure (do not land a failing
                                        design by accident)
  land    land_vc                       stop on failure / degraded without CLI

Exit codes follow the shared envelope: 0 all stages ok, 1 a criterion failed,
2 bad usage, 3 degraded (a capability is missing), 4 internal error.

Review pack mode (`--review-pack OUT --candidate RUN...`) builds a blind review
folder: anonymous case files, a CSV with empty score cells, and a separate
key.json. The scores are filled by a human; this script never writes a taste
score.
"""
import argparse
import json
import random
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L

SCRIPT = "studio.py"
HERE = Path(__file__).resolve().parent
# r70 #3: the ONE content-derived run id for this orchestrated run. Every
# stage receives it via --run-id, and steps 7-10 must pass the SAME id to
# selftest.py and aggregate_log.py so H3 can bind the self-test log to this
# run (design C-2). Set once at the top of _run_chain; never wall-clock.
_ORCH_RUN_ID = ""
STAGE_SCRIPTS = {
    "spec": "freeze_spec.py",
    "clean": "sanitize_svg.py",
    "render": "render_png.py",
    "legible": "legibility.py",
    "judge": "llm_judge.py",
    "score": "score.py",
    "land": "land_vc.py",
}


def content_run_id(args):
    """Content-derived run id for THIS run (r70, #3).

    Same spec + svg + thresholds version -> same id. No nonce, no wall-clock,
    no signature: a time/random id would break the byte-idempotency of the
    downstream selftest.json / acceptance.json judgment chain, and a signature
    key would introduce credentials into the zero-credential default chain.
    The orchestrator passes this ONE id to every stage and, in steps 7-10, to
    selftest.py and aggregate_log.py, so H3 can bind the self-test log to the
    very run whose acceptance it guards (design C-2, the three-way pair).
    """
    parts = ["studio", L.load_thresholds()["version"]]
    for p in (getattr(args, "spec", "") or "", getattr(args, "svg", "") or ""):
        try:
            parts.append(Path(p).read_text(encoding="utf-8"))
        except (OSError, UnicodeError):
            parts.append(str(p))
    return L.sha_run_id(*parts)


def _call(stage, args, timeout=1800):
    """Run one stage through its real CLI and return (rc, envelope).

    Importing the modules instead would let a stage drift from the command the
    docs tell humans to run. Subprocess keeps one code path for both. r70 #3:
    _run_chain appends the SAME content-derived --run-id to every stage's args,
    so a stage's envelope, selftest.json and acceptance.json share one run id.
    The signature stays (stage, args, timeout) to keep the selftest A50 stub,
    which replaces this function with a 3-arg fake, working unchanged.
    """
    cmd = [sys.executable, str(HERE / STAGE_SCRIPTS[stage]), "--json"] + args
    # r70 #3: thread the orchestration run id to every stage. A stage that
    # later writes a log does so under the same id the H3 gate expects.
    if _ORCH_RUN_ID:
        cmd += ["--run-id", _ORCH_RUN_ID]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    try:
        env = json.loads(p.stdout)
    except json.JSONDecodeError:
        env = {"status": "error", "code": p.returncode,
               "errors": [L.err("E-INTERNAL",
                                "stage produced no JSON envelope: " +
                                (p.stderr or p.stdout or "")[-300:])]}
    return p.returncode, env


def _stage_row(name, rc, env):
    return {"stage": name, "status": env.get("status", "error"),
            "code": rc,
            "errors": [e.get("code") for e in (env.get("errors") or [])],
            "detail": " ".join((e.get("msg") or "") for e in (env.get("errors") or []))[:400]}


def _run_chain(args, out):
    global _ORCH_RUN_ID
    # r70 #3: derive ONE content-based run id for this run. getattr keeps the
    # A50 harness (a hand-built Namespace without run_id) working.
    _ORCH_RUN_ID = getattr(args, "run_id", "") or content_run_id(args)
    stages = []
    rd = out / "round-1"
    rd.mkdir(parents=True, exist_ok=True)

    # 1. spec: a frozen, validated copy is the only spec later stages may read
    rc, env = _call("spec", ["--spec", args.spec, "--validate",
                                  "--out", str(out / "spec.json")])
    stages.append(_stage_row("spec", rc, env))
    if rc != 0:
        return _finish(out, stages, rc, env)

    # 2. clean
    sanitized = rd / "sanitized.svg"
    rc, env = _call("clean", ["--in", args.svg, "--out", str(sanitized)])
    stages.append(_stage_row("clean", rc, env))
    if rc != 0:
        return _finish(out, stages, rc, env)

    # 3. render: a missing renderer is recorded, not fatal
    rc_preview, env_preview = _call("render",
                                           ["--in", str(sanitized),
                                            "--out", str(rd / "preview.png")])
    stages.append(_stage_row("render", rc_preview, env_preview))

    # 4. judge + score only when the host supplied a reading of the preview
    # A built-in fallback preview still exits 0; the degradation lives in the
    # envelope's status, so the flag must read that, not the process code.
    degraded = env_preview.get("status") != "ok"

    # 3b. legibility: only on a real preview. The builtin fallback is a
    # low-fidelity stand-in, so a 16px measurement of it would report on the
    # fallback rather than on the design.
    if not degraded and (rd / "preview.png").is_file():
        rc, env = _call("legible", ["--png", str(rd / "preview.png"),
                                           "--out", str(rd / "legibility.json")])
        stages.append(_stage_row("legible", rc, env))
        if rc == 1:
            return _finish(out, stages, rc, env)
        degraded = degraded or rc == 3
    else:
        stages.append({"stage": "legible", "status": "skipped", "code": 0,
                       "errors": [],
                       "detail": "preview is not from a real renderer: the 16px "
                                 "check would measure the fallback, not the design"})

    if args.judge:
        rc, env = _call("judge", ["--judge", args.judge, "--validate",
                                         "--spec", str(out / "spec.json")])
        stages.append(_stage_row("judge", rc, env))
        if rc != 0:
            return _finish(out, stages, rc, env)
        score_args = ["--svg", str(sanitized), "--judge", args.judge,
                      "--spec", str(out / "spec.json"),
                      "--out", str(rd / "score.json")]
        if args.reference:
            score_args += ["--reference", args.reference]
        rc, env = _call("score", score_args)
        stages.append(_stage_row("score", rc, env))
        if rc != 0:
            return _finish(out, stages, rc, env)
    else:
        stages.append({"stage": "judge", "status": "skipped", "code": 0,
                       "errors": [],
                       "detail": "no --judge given: A/B stay N/A, no score written"})
        degraded = True

    # 5. land
    rc, env = _call("land", ["--svg", str(sanitized), "--out", str(out),
                                    "--profile", args.profile])
    stages.append(_stage_row("land", rc, env))
    if rc != 0:
        return _finish(out, stages, rc, env)
    return _finish(out, stages, 3 if degraded else 0, env)


def _finish(out, stages, code, last_env):
    status = "ok" if code == 0 else ("degraded" if code == 3 else
                                     ("error" if code >= 2 else "fail"))
    L.write_json_atomic(out / "studio.json", {
        "schema": "vector-ai-studio/studio-v1",
        "status": status,
        # r70 #3: steps 7-10 MUST reuse this exact id for selftest.py and
        # aggregate_log.py -- H3 binds selftest.json.run_id to that --run-id.
        "run_id": _ORCH_RUN_ID,
        "stages": stages,
        "note": "full grading still runs steps 7-10 (batch_check + aggregate_log); "
                "this chain proves one draft can be cleaned, previewed, scored and landed; "
                "pass --run-id from studio.json.run_id to selftest.py and aggregate_log.py",
    })
    errors = [e for e in (last_env.get("errors") or [])]
    if not errors and code in (1, 2, 4):
        # A failing stage that reports no specific code (score.py returns 1 on a
        # zero-scoring dimension) still has to say which stage stopped the chain.
        failed = [s["stage"] for s in stages if s["code"] != 0]
        errors = [L.err("E-STUDIO-STAGE",
                        "stage stopped the chain: " +
                        (", ".join(failed) or "unknown"))]
    degradation = None
    if code == 3:
        degradation = {"level": "L4",
                       "reason": "a stage ran with a fallback or a missing capability",
                       "substitute": "stage-level status in studio.json"}
    return L.emit(SCRIPT, status=status, code=code,
                  data={"stages": stages, "run_id": _ORCH_RUN_ID}, errors=errors,
                  degradation=degradation, run_id=_ORCH_RUN_ID)


def _preview_of(run_dir):
    for rel in ("round-1/preview.png", "preview.png"):
        p = run_dir / rel
        if p.is_file():
            return p
    return None


def _svg_of(run_dir):
    for rel in ("round-1/sanitized.svg", "final.svg"):
        p = run_dir / rel
        if p.is_file():
            return p
    return None


def _review_pack(args):
    """Anonymous cases + empty score sheet + separate key. No taste scores here."""
    out = Path(args.review_pack)
    out.mkdir(parents=True, exist_ok=True)
    items = []
    for cand in args.candidate:
        d = Path(cand)
        svg, png = _svg_of(d), _preview_of(d)
        if not svg or not png:
            raise L.ToolError("E-USAGE",
                              "candidate has no sanitized SVG or preview: " + str(d),
                              L.EXIT_USAGE)
        items.append((svg, png))
    order = list(range(len(items)))
    random.Random(args.seed).shuffle(order)
    key, rows = [], []
    for i, idx in enumerate(order, 1):
        case = "case-%02d" % i
        svg, png = items[idx]
        shutil.copyfile(svg, out / (case + ".svg"))
        shutil.copyfile(png, out / (case + ".png"))
        key.append({"case": case, "source": str(args.candidate[idx])})
        rows.append(case + ",,,,,,")
    L.write_json_atomic(out / "key.json",
                        {"schema": "vector-ai-studio/review-key-v1",
                         "seed": args.seed, "cases": key,
                         "warning": "do not hand this file to the reviewer"})
    sheet = ("case,concept,hierarchy,proportion,detail,durability,notes\n" +
             "\n".join(rows) + "\n")
    L.write_text_atomic(out / "review-sheet.csv", sheet)
    L.write_text_atomic(out / "README.md",
                        "# 盲评包\n\n"
                        "给评审人的只有：本说明、`case-XX.svg`、`case-XX.png`、"
                        "`review-sheet.csv`。不要提供 `key.json`。\n\n"
                        "评分：五维各 0-2，锚点见 `references/design-language.md` "
                        "第 10 节。空白格由评审人填写；脚本不写分数。\n\n"
                        "回填后把 `review-sheet.csv` 与 `key.json` 一起归档，"
                        "这组数据才是「真实产出证据」。\n")
    return L.emit(SCRIPT, status="ok", code=0,
                  data={"cases": len(items), "out": str(out)},
                  artifacts={"review_pack": str(out)})


def _opts():
    ap = argparse.ArgumentParser(description="one-command vector pipeline")
    ap.add_argument("--spec", default="", help="spec JSON to validate and freeze")
    ap.add_argument("--svg", default="", help="draft SVG to clean")
    ap.add_argument("--out", default="", help="run directory")
    ap.add_argument("--judge", default="", help="binary judge JSON (optional)")
    ap.add_argument("--reference", default="", help="style reference SVG for E")
    ap.add_argument("--profile", default="screen",
                    choices=("screen", "print", "handoff"))
    ap.add_argument("--review-pack", default="", help="build a blind review folder")
    ap.add_argument("--candidate", action="append", default=[],
                    help="run dir for --review-pack, repeatable")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--run-id", default="",
                    help="content-derived run id shared with selftest.py and "
                         "aggregate_log.py; derived from spec+svg+version when omitted")
    ap.add_argument("--json", action="store_true")
    return ap


def main():
    args = _opts().parse_args()
    if args.review_pack:
        if len(args.candidate) < 2:
            raise L.ToolError("E-USAGE",
                              "--review-pack needs at least two --candidate",
                              L.EXIT_USAGE)
        return _review_pack(args)
    if not (args.spec and args.svg and args.out):
        raise L.ToolError("E-USAGE", "--spec, --svg and --out are required",
                          L.EXIT_USAGE)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    return L.run_cli(SCRIPT, lambda: _run_chain(args, out))


if __name__ == "__main__":
    # F2 (r71): argument parsing and the usage checks in main() raise
    # L.ToolError BEFORE the inner run_cli around _run_chain exists, so they
    # must be caught at the entry: E-USAGE -> 9-field envelope + exit 2, no
    # traceback. The inner run_cli keeps covering the chain itself.
    sys.exit(L.run_cli(SCRIPT, main))

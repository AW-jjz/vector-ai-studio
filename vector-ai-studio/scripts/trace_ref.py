#!/usr/bin/env python3
"""Vectorise a supplied reference photo — the "I have a picture, give me an .ai"
route that the generated-artwork pipeline does not cover.

Two traps this wrapper encodes so they are not rediscovered every time:

* `ignoreWhite` must stay false. With it on, the tracer treats every pure-white
  area as background and punches it out — which includes the specular highlight
  inside a pearl or a crystal, leaving a black hole where the highlight was.
* The trace parameters are nested one level down (`{"params": {...}}`). Passing
  them flat is rejected by the CLI with "unknown parameter".

Photo-derived artwork should go through delight.py first: a traced photo keeps
the studio lighting, and printed on a real object that becomes a second, wrong
light.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L

SCRIPT = "trace_ref.py"
CANDIDATES = [
    r"E:/Vector craft/vectorcraft-0.22.0/vectorcraft-0.22.0-windows-x64-portable/vectorcraft-cli.exe",
    r"C:/Program Files/VectorCraft/vectorcraft-cli.exe",
]


def find_cli():
    env = os.environ.get("VECTORCRAFT_CLI")
    if env and Path(env).is_file():
        return Path(env)
    for c in CANDIDATES:
        if Path(c).is_file():
            return Path(c)
    for name in ("vectorcraft-cli", "vectorcraft-cli.exe"):
        found = shutil.which(name)
        if found:
            return Path(found)
    return None


def require_cli():
    """Raise loudly when the tracer is absent -- a missing engine is never a
    success with a smaller file."""
    cli = find_cli()
    if cli is None:
        raise L.ToolError(
            "E-CAPABILITY-MISSING",
            "VectorCraft CLI not found; set VECTORCRAFT_CLI or install it",
            L.EXIT_MISSING)
    return cli


def trace_params(colors, paths, corners, noise, ignore_white):
    # nested on purpose: the CLI reads the trace settings under "params"
    return {"params": {"mode": "color", "palette": "limited",
                       "colors": colors, "paths": paths,
                       "corners": corners, "noise": noise,
                       "ignoreWhite": bool(ignore_white)}}


def _last_json(stdout, want_step):
    out = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("step") == want_step or d.get("command") == want_step:
            out = d
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--colors", type=int, default=96)
    ap.add_argument("--paths", type=int, default=90)
    ap.add_argument("--corners", type=int, default=20)
    ap.add_argument("--noise", type=int, default=2)
    ap.add_argument("--simplify", type=int, default=0,
                    help="0 disables; 0-100 is the Simplify slider")
    ap.add_argument("--ignore-white", action="store_true",
                    help="NOT recommended: punches highlights out of pearls/gems")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    for name, v, lo, hi in (("colors", args.colors, 2, 256),
                            ("paths", args.paths, 0, 100),
                            ("corners", args.corners, 0, 100),
                            ("noise", args.noise, 0, 100)):
        if not (lo <= v <= hi):
            raise L.ToolError("E-USAGE", "%s must be %d..%d" % (name, lo, hi), L.EXIT_USAGE)

    src = Path(args.src)
    if not src.is_file():
        raise L.ToolError("E-SCHEMA", "input not found: " + src.name, L.EXIT_USAGE)
    if src.suffix.lower() not in (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp"):
        raise L.ToolError("E-USAGE", "trace needs a raster input", L.EXIT_USAGE)

    cli = require_cli()

    warnings = []
    if args.ignore_white:
        warnings.append("ignoreWhite=true punches specular highlights out of pearls "
                        "and crystals, leaving black holes where the highlight was")

    cmd = [str(cli), "run", "--in", str(src),
           "--cmd", "select.all", "--params", "{}",
           "--cmd", "imageTrace.makeAndExpand",
           "--params", json.dumps(trace_params(args.colors, args.paths, args.corners,
                                               args.noise, args.ignore_white))]
    if args.simplify > 0:
        cmd += ["--cmd", "object.path.simplify",
                "--params", json.dumps({"level": args.simplify})]
    cmd += ["--export", args.out]

    p = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if p.returncode != 0:
        raise L.ToolError("E-TRACE", "trace failed: " +
                          (p.stderr or p.stdout)[-400:], L.EXIT_ERROR)

    trace = _last_json(p.stdout, "imageTrace.makeAndExpand") or {}
    r = trace.get("result") or {}
    simp = _last_json(p.stdout, "object.path.simplify") or {}

    dst = Path(args.out)
    if not dst.is_file():
        raise L.ToolError("E-TRACE", "no output produced", L.EXIT_ERROR)

    return L.emit(SCRIPT, status="ok", code=0, warnings=warnings, data={
        "paths": r.get("paths"),
        "anchors": r.get("anchors"),
        "colors": r.get("colors"),
        "anchors_after_simplify": (simp.get("result") or {}).get("after"),
        "params": {"colors": args.colors, "paths": args.paths,
                   "corners": args.corners, "noise": args.noise,
                   "ignoreWhite": bool(args.ignore_white),
                   "simplify": args.simplify},
        "bytes": dst.stat().st_size,
    }, artifacts={"ai": dst.name})


if __name__ == "__main__":
    sys.exit(L.run_cli(SCRIPT, main))

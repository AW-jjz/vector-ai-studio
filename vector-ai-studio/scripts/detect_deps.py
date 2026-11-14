#!/usr/bin/env python3
"""C5 / L2 / L4 / H4: real-machine dependency probe.

Every item is actually probed on this machine; nothing is copied from a design
document. There is no Adobe dependency and no COM probe: the landing engine is
VectorCraft (vectorcraft-cli), located the same way the landers locate it.
Credential env NAMES are checked for existence only - values are never read or
printed.
"""

import argparse
import importlib.util
import os
import platform
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L
from trace_ref import find_cli as find_vectorcraft

SCRIPT = "detect_deps.py"
LLM_FLAG_ENV = "VECTOR_AI_LLM_IMAGE_READING"


def _dep(name, status, detail, substitute, optional=False, counts=True):
    return {"name": name, "status": status, "detail": detail,
            "substitute": substitute, "optional": optional,
            "counts_for_missing": counts}


def main():
    ap = argparse.ArgumentParser(description="probe real-machine dependencies")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default="")
    ap.add_argument("--run-id", default="")
    args = ap.parse_args()

    deps = []
    deps.append(_dep("python", "FOUND",
                     "Python " + platform.python_version() + " on " + platform.system(),
                     "standard library only; no replacement needed"))

    cli = find_vectorcraft()
    deps.append(_dep("vectorcraft_cli", "FOUND" if cli else "MISSING",
                     ("vectorcraft-cli found: " + cli.name) if cli
                     else "vectorcraft-cli not found via VECTORCRAFT_CLI, known install "
                          "paths, or PATH",
                     "install VectorCraft (or set VECTORCRAFT_CLI); without it H1 fails "
                     "and only SVG + PNG can be delivered"))

    deps.append(_dep("renderer_resvg",
                     "FOUND" if shutil.which("resvg") else "MISSING",
                     "resvg on PATH" if shutil.which("resvg") else "resvg not on PATH",
                     "optional enhancement; builtin PNG fallback available", optional=True))
    cairo = L.cairosvg_probe()
    deps.append(_dep("renderer_cairosvg",
                     "FOUND" if cairo["available"] else "MISSING",
                     cairo["detail"],
                     "optional enhancement; builtin PNG fallback available", optional=True))

    flag = os.environ.get(LLM_FLAG_ENV)
    if flag == "1":
        llm = ("FOUND", "host image reading confirmed via " + LLM_FLAG_ENV)
    elif flag == "0":
        llm = ("MISSING", "host image reading reported unavailable via " + LLM_FLAG_ENV)
    else:
        llm = ("MISSING", "host image reading NOT verified; treated as MISSING per L2 policy; set " +
               LLM_FLAG_ENV + " only when the host can actually read the rendered PNG")
    deps.append(_dep("host_llm_image_reading", llm[0], llm[1],
                     "L2: A/B = capability_missing status, human final review; C/D/F and H1-H4 still scored",
                     optional=True, counts=(llm[0] == "MISSING")))

    creds = L.assert_no_credentials()
    for name, present in creds.items():
        deps.append(_dep("credential_" + name, "FOUND" if present else "MISSING",
                         "environment variable name present: " + str(present) +
                         " (value never read)",
                         "default chain is credential-free; L3 only when user supplies it",
                         optional=True, counts=False))

    missing = [d["name"] for d in deps if d["status"] == "MISSING" and d["counts_for_missing"]]
    counts = {s: sum(1 for d in deps if d["status"] == s)
              for s in ("FOUND", "MISSING", "UNVERIFIED")}
    # r72: report whether the found engine build is pinned in
    # engine.lock.json. This is a REPORT, not a gate: the landers enforce the
    # lock (E-ENGINE-DRIFT); the probe only tells the operator what to expect
    # before a run starts. drift=true here means the regression on record does
    # not cover the binary on disk.
    engine_lock = None
    if cli:
        try:
            version = L.engine_version(cli)
            lock = L.check_engine_lock(cli, version)
            engine_lock = {"locked": bool(lock.get("locked")),
                           "drift": bool(lock.get("drift")),
                           "platform": lock.get("platform"),
                           "sha256": lock.get("sha256"),
                           "expected": lock.get("expected"),
                           "note": lock.get("note")}
        except (OSError, ValueError):
            engine_lock = {"locked": False, "drift": False,
                           "note": "engine version could not be read; pin status unknown"}
    doc = {
        "schema": "vector-ai-studio/deps-v1",
        "run_id": args.run_id,
        "python_version": platform.python_version(),
        "system": platform.system(),
        "dependencies": deps,
        "missing": missing,
        "credential_audit": creds,
        "engine_lock": engine_lock,
        "summary": counts,
    }
    artifacts = {}
    if args.out:
        L.write_json_atomic(args.out, doc)
        artifacts["deps"] = Path(args.out).name
    L.append_run_log(Path(args.out).parent if args.out else L.default_output_root(),
                     SCRIPT + " found=" + str(counts["FOUND"]) +
                     " missing=" + str(counts["MISSING"]) +
                     " unverified=" + str(counts["UNVERIFIED"]))

    code = L.EXIT_MISSING if missing else L.EXIT_OK
    return L.emit(SCRIPT, status="degraded" if missing else "ok", code=code,
                  run_id=args.run_id,
                  data={"dependencies": deps, "missing": missing,
                        "credential_audit": creds, "engine_lock": engine_lock,
                        "summary": counts},
                  degradation=({"level": "L4", "reason": "one or more dependencies MISSING",
                                "substitute": "see dependencies[].substitute"}
                               if missing else None),
                  artifacts=artifacts)


if __name__ == "__main__":
    sys.exit(L.run_cli(SCRIPT, main))

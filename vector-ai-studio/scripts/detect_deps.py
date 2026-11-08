#!/usr/bin/env python3
"""C5 / L2 / L4 / H4: real-machine dependency probe.

Every item is actually probed on this machine; nothing is copied from a design
document. COM is UNVERIFIED by default (no side effects); --probe-com performs
a real PowerShell COM creation with a timeout. Credential env NAMES are checked
for existence only - values are never read or printed.
"""

import argparse
import glob
import importlib.util
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L

SCRIPT = "detect_deps.py"
LLM_FLAG_ENV = "VECTOR_AI_LLM_IMAGE_READING"


def _dep(name, status, detail, substitute, optional=False, counts=True):
    return {"name": name, "status": status, "detail": detail,
            "substitute": substitute, "optional": optional,
            "counts_for_missing": counts}


def _find_illustrator(override):
    if override:
        p = Path(override)
        return ("FOUND" if p.is_file() else "MISSING",
                "user override: " + p.name + (" exists" if p.is_file() else " not found"))
    pf = os.environ.get("ProgramFiles", "C:/Program Files")
    patterns = [
        str(Path(pf) / "Adobe" / "Adobe Illustrator *" /
            "Support Files" / "Contents" / "Windows" / "Illustrator.exe"),
        str(Path(pf) / "Adobe" / "Adobe Illustrator *" / "Illustrator.exe"),
        "C:/Program Files (x86)/Adobe/Adobe Illustrator */Support Files/Contents/Windows/Illustrator.exe",
    ]
    found = []
    for pat in patterns:
        found.extend(glob.glob(pat))
    if found:
        return ("FOUND", "Illustrator.exe located in an Adobe Illustrator installation "
                         "(exact path intentionally not stored in JSON)")
    return ("MISSING", "no Illustrator.exe under the standard Adobe install locations")


def _probe_com(attempts=2, timeout=90):
    """Create the COM object for real. Returns (ok, detail).

    ok is True / False / None:
      True   COM creation succeeded
      False  COM creation raised (definitively unavailable)
      None   we could not tell (timeout) -- this is NOT the same as missing

    The distinction matters. Illustrator's cold start takes 15-20s on a normal
    machine and longer on a loaded one; the first version used a 20s timeout and
    reported TimeoutExpired as MISSING, so the whole chain degraded to L4 on a
    slow machine even though Illustrator was installed and drivable. A timeout
    proves nothing about availability, so after retrying it is reported as
    UNVERIFIED rather than as a missing dependency.
    """
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        return (False, "no shell available on PATH")
    code = ("try { $a = New-Object -ComObject Illustrator.Application; "
            "'COM_OK'; $a.Quit() } catch { 'COM_FAIL' }")
    last = ""
    for i in range(attempts):
        try:
            r = subprocess.run([exe, "-NoProfile", "-ExecutionPolicy", "Bypass",
                                "-Command", code], timeout=timeout,
                               capture_output=True, text=True, check=False)
            out = (r.stdout or "").strip()
            if "COM_OK" in out:
                return (True, "COM_OK" if i == 0 else
                        "COM_OK (attempt " + str(i + 1) + " of " + str(attempts) + ")")
            if "COM_FAIL" in out:
                return (False, out[:200] or "COM_FAIL")
            last = out[:200] or "no stdout"
        except subprocess.TimeoutExpired:
            last = ("timeout after " + str(timeout) + "s (attempt " +
                    str(i + 1) + "/" + str(attempts) + ")")
        except (OSError, subprocess.SubprocessError) as e:
            last = type(e).__name__ + ": " + str(e)[:120]
    return (None, last)


def main():
    ap = argparse.ArgumentParser(description="probe real-machine dependencies")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default="")
    ap.add_argument("--probe-com", action="store_true")
    ap.add_argument("--illustrator-exe", default="")
    ap.add_argument("--run-id", default="")
    args = ap.parse_args()

    deps = []
    deps.append(_dep("python", "FOUND",
                     "Python " + platform.python_version() + " on " + platform.system(),
                     "standard library only; no replacement needed"))

    exe_status, exe_detail = _find_illustrator(args.illustrator_exe)
    deps.append(_dep("illustrator_exe", exe_status, exe_detail,
                     "install Adobe Illustrator, or pass --illustrator-exe"))

    pw = shutil.which("powershell") or shutil.which("pwsh")
    deps.append(_dep("powershell", "FOUND" if pw else "MISSING",
                     "PowerShell present" if pw else "PowerShell not on PATH",
                     "Windows PowerShell is required for the COM bridge"))

    if args.probe_com:
        ok, detail = _probe_com()
        # ok is tri-state: True / False / None (None = timeout, unknown).
        # Never let a timeout masquerade as MISSING.
        status = "FOUND" if ok is True else ("UNVERIFIED" if ok is None else "MISSING")
        deps.append(_dep("illustrator_com", status,
                         "real COM creation: " + detail,
                         "L4: H1 fails, deliver SVG+PNG only; or use semi-manual JSX path"))
    else:
        deps.append(_dep("illustrator_com", "UNVERIFIED",
                         "COM automation NOT verified (read-only default probe). "
                         "Run --probe-com or scripts/smoke_ai.py for a real attempt.",
                         "L4 unless verified; semi-manual JSX path is user-selected only",
                         counts=False))

    deps.append(_dep("renderer_resvg",
                     "FOUND" if shutil.which("resvg") else "MISSING",
                     "resvg on PATH" if shutil.which("resvg") else "resvg not on PATH",
                     "optional enhancement; builtin PNG fallback available", optional=True))
    deps.append(_dep("renderer_cairosvg",
                     "FOUND" if importlib.util.find_spec("cairosvg") else "MISSING",
                     "cairosvg importable" if importlib.util.find_spec("cairosvg")
                     else "cairosvg not installed",
                     "optional enhancement; builtin PNG fallback available", optional=True))
    headless = next((b for b in ("chromium", "chrome", "msedge", "google-chrome")
                     if shutil.which(b)), None)
    deps.append(_dep("renderer_headless", "FOUND" if headless else "MISSING",
                     (headless + " on PATH") if headless else "no headless browser on PATH",
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

    deps.append(_dep("node", "FOUND" if shutil.which("node") else "MISSING",
                     "node on PATH" if shutil.which("node") else "node not on PATH",
                     "optional enhancement; not in the default chain", optional=True))
    deps.append(_dep("svgo", "FOUND" if shutil.which("svgo") else "MISSING",
                     "svgo on PATH" if shutil.which("svgo") else "svgo not on PATH",
                     "optional enhancement; sanitize_svg.py builtin minimal implementation",
                     optional=True))

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
    doc = {
        "schema": "vector-ai-studio/deps-v1",
        "run_id": args.run_id,
        "python_version": platform.python_version(),
        "system": platform.system(),
        "com_probe_performed": bool(args.probe_com),
        "dependencies": deps,
        "missing": missing,
        "credential_audit": creds,
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
                        "credential_audit": creds, "summary": counts,
                        "com_probe_performed": bool(args.probe_com)},
                  degradation=({"level": "L4", "reason": "one or more dependencies MISSING",
                                "substitute": "see dependencies[].substitute"}
                               if missing else None),
                  artifacts=artifacts)


if __name__ == "__main__":
    sys.exit(L.run_cli(SCRIPT, main))

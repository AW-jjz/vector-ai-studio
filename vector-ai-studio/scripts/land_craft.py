#!/usr/bin/env python3
"""Land an SVG as a native .ai (and, for print, a PDF/X master) using only
VectorCraft. No Adobe, no COM, no Windows-only bridge.

Why this exists: Adobe does not offer Creative Cloud services on adobe.com in
mainland China, and a headless pipeline must not depend on a desktop app being
licensed, activated and reachable. VectorCraft is a clean-room, MIT/Apache-2.0
Illustrator-class engine with a headless CLI; its 933-command catalogue covers
what the old JSX/COM path did and considerably more.

Output contract mirrors land_vc.py so downstream (aggregate_log.py, the H1 gate,
the QA reports) can consume either engine without special cases:

  final.svg               source SVG copied into the run dir
  final.ai                PDF-compatible native .ai (editable working file)
  print.pdf               PDF/X-1a / X-3 / X-4 master (print profile only)
  preview.png             raster preview
  deliverable.json        what the client signs off
  structure_readback.json H1 evidence, read back from the written .ai by `info`

Known limits, declared rather than hidden:
  * VectorCraft writes PDF-compatible .ai and legacy version-7 .ai (layers,
    groups, type), but NOT Adobe's proprietary AIPrivateData. A client who
    requires Adobe-native editing data gets SVG + PDF/X instead.
  * `handoff` exports a layered PDF; the .ai keeps SVG groups because the
    engine has no group-to-layer command.
  * A PDF/X-1a is untagged DeviceCMYK plus an embedded output intent by design;
    use pdfX3/pdfX4 for ICC-tagged colours.
"""
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L
import lib_pdf as LP           # one PDF reader for both craft landers
from trace_ref import require_cli, find_cli  # single source of truth for the CLI locator

SCRIPT = "land_craft.py"
STANDARDS = ("none", "pdfX1a", "pdfX3", "pdfX4")
PROFILES = ("screen", "print", "handoff")


def _num(s):
    return re.findall(r"-?\d+(?:\.\d+)?", s or "")


def viewbox_of(svg_path):
    text = svg_path.read_text(encoding="utf-8")
    m = re.search(r'viewBox\s*=\s*["\']([^"\']+)["\']', text)
    if not m:
        raise L.ToolError("E-USAGE", "SVG has no viewBox", L.EXIT_USAGE)
    v = [float(x) for x in _num(m.group(1))]
    if len(v) < 4:
        raise L.ToolError("E-USAGE", "viewBox needs 4 numbers", L.EXIT_USAGE)
    return [v[0], v[1], v[2], v[3]]


def _run(cmd, timeout=1800):
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    if p.returncode != 0:
        raise L.ToolError("E-RENDER", "craft failed: " +
                          (p.stderr or p.stdout or "")[-400:], L.EXIT_ERROR)


def _pdf_text(blob):
    # PDF literal strings escape ( ) and \; undo that before reporting
    return (blob.replace(b"\\\\", b"\\")
                .replace(b"\\(", b"(").replace(b"\\)", b")")).decode("latin1")


def _pdf_facts(path, warnings=None):
    raw = path.read_bytes()
    facts = {"bytes": len(raw), "pdf_header": raw[:8].decode("latin1")}
    # Declared colour resources come from lib_pdf's counter: one file, one number.
    facts.update(LP._object_counts(raw))
    for key in (b"/GTS_PDFXVersion", b"/OutputCondition",
                b"/Trapped", b"/TrimBox", b"/BleedBox", b"/MediaBox"):
        facts[key.decode().lstrip("/")] = len(re.findall(re.escape(key), raw))
    for box in (b"TrimBox", b"BleedBox", b"MediaBox"):
        m = re.findall(rb"/" + box + rb"\s*\[([^\]]+)\]", raw)
        facts[box.decode().lower() + "_rect"] = m[0].decode("latin1").strip() if m else None
    m = re.search(rb"/GTS_PDFXVersion\s*\(((?:\\.|[^\\()])*)\)", raw)
    facts["gts_pdfx_value"] = _pdf_text(m.group(1)) if m else None
    m = re.search(rb"/OutputConditionIdentifier\s*\(((?:\\.|[^\\()])*)\)", raw)
    facts["output_condition"] = _pdf_text(m.group(1)) if m else None
    facts["output_intent_objects"] = len(re.findall(rb"/Type\s*/OutputIntent\b", raw))

    # Selection is lib_pdf's too: page /Contents refs plus /Subtype /Form
    # XObjects. Selecting differently per engine is how the same PDF ended up
    # with two different operator counts.
    streams, n_refs, n_objects = LP._page_streams(raw, warnings)
    ops, decoded = LP._count_ops(streams)
    # None means "nothing decoded", and it must stay None. Filling zeros here
    # made an unmeasured page indistinguishable from a page with no colour
    # operator, so a caller could assert "no CMYK in this file" from a
    # measurement that never happened. land_vc reports null on this path; the
    # two engines have to agree on the difference.
    facts["content_ops"] = ops
    # Only the page content streams and Form XObjects are page description;
    # ICC profiles, font programs and CMaps are binary blobs that happen to
    # contain k/K bytes, and are not evidence of a CMYK operator.
    facts["content_ops_scope"] = "page /Contents refs + /Subtype /Form streams only"
    facts["content_streams"] = {"objects": n_objects, "refs": n_refs, "decoded": decoded}
    return facts


def _structure(ai_path):
    # `info` emits one JSON document (not JSON lines), so parse the whole stdout
    p = subprocess.run([str(find_cli()), "info", str(ai_path)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=600)
    if p.returncode != 0:
        raise L.ToolError("E-RENDER", "info failed: " +
                          (p.stderr or "")[-300:], L.EXIT_ERROR)
    return json.loads(p.stdout)


def _record_failure(run_dir, note):
    """Fail landing + no stale readback or preview, so a failed print run cannot
    inherit the previous run's H1 pass or its picture. Mirrors land_vc.py."""
    cleared = LP._drop_stale_readback(run_dir)
    preview_cleared = LP._drop_stale_preview(run_dir)
    try:
        cur = json.loads((run_dir / "landing.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        cur = {}
    if cur.get("status") == "fail":
        return
    try:
        L.write_json_atomic(run_dir / "landing.json",
                            LP._landing("none", "fail", note, False, False,
                                        {"stale_readback_removed": cleared,
                                         "stale_preview_removed": preview_cleared}))
    except OSError:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--svg", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--profile", choices=PROFILES, default="screen")
    ap.add_argument("--bleed-mm", type=float, default=3.0)
    ap.add_argument("--pdf-standard", choices=STANDARDS, default="none")
    ap.add_argument("--icc-cmyk", help="CMYK output-intent profile name")
    ap.add_argument("--ppi", type=int, default=72)
    ap.add_argument("--run-id", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    try:
        return _land(args, out)
    except BaseException:
        _record_failure(out, "print landing aborted by an exception")
        raise


def _land(args, out):
    cli = require_cli()          # E-CAPABILITY-MISSING, never a silent success
    # r72: same rule as land_vc -- the engine's bytes decide the print master,
    # so a build that IS pinned but differs is a hard stop (E-ENGINE-DRIFT);
    # an unpinned build is only a note in the run log.
    from trace_ref import require_locked_engine as _locked
    engine_lock = _locked(cli)
    if not engine_lock.get("locked"):
        L.append_run_log(out, SCRIPT + " engine not pinned: " +
                         str(engine_lock.get("note", "")))
    src = Path(args.svg)
    if not src.is_file():
        raise L.ToolError("E-SCHEMA", "SVG not found: " + src.name, L.EXIT_USAGE)
    if args.profile == "print" and args.pdf_standard == "none":
        args.pdf_standard = "pdfX1a"
    if args.pdf_standard != "none" and args.profile != "print":
        raise L.ToolError("E-USAGE", "PDF/X needs --profile print", L.EXIT_USAGE)

    vb = viewbox_of(src)
    ai = out / "final.ai"
    png = out / "preview.png"
    pdf = out / "print.pdf"
    svg_out = out / "final.svg"
    shutil.copyfile(src, svg_out)

    # Same rule as land_vc: clear the targets before the engine runs, so a CLI
    # that exits 0 without writing cannot leave the previous run's .ai / PDF /
    # preview standing in as this run's output. A locked old file is refused,
    # not ignored.
    if not LP._drop_outputs(out, ("final.ai", "print.pdf", "preview.png",
                                  "preview.meta.json")):
        raise L.ToolError("E-OUTPUT-STALE",
                          "previous outputs are locked; refusing to run with stale "
                          "files in place", L.EXIT_ERROR)

    bleed_pt = round(args.bleed_mm / 25.4 * 72.0, 3)
    cmd = [str(cli), "run", "--in", str(src)]
    if args.profile == "print":
        cmd += ["--cmd", "file.documentColorMode",
                "--params", json.dumps({"mode": "cmyk", "convert": True})]
    if args.profile == "print" and args.bleed_mm > 0:
        cmd += ["--cmd", "document.setup",
                "--params", json.dumps({"bleed": bleed_pt})]

    pdf_params = None
    if args.pdf_standard != "none":
        marks = {"trim": True, "registration": True,
                 "colorBars": True, "pageInfo": True,
                 "kind": "japanese", "weight": 0.25, "offset": max(9.0, bleed_pt + 0.5)}
        pdf_params = {
            "path": str(pdf),
            "standard": args.pdf_standard,
            "bleed": {"top": bleed_pt, "bottom": bleed_pt,
                      "left": bleed_pt, "right": bleed_pt},
            "marks": marks,
            "preserveEditing": False,
        }
        if args.profile == "handoff":
            pdf_params["createLayers"] = True
        if args.icc_cmyk:
            pdf_params["output"] = {"conversion": "destination",
                                   "profiles": "destination",
                                   "outputIntent": args.icc_cmyk}
        cmd += ["--cmd", "document.exportPdf", "--params", json.dumps(pdf_params)]
    elif args.profile == "handoff":
        # layered PDF for hand-off, no PDF/X conversion
        cmd += ["--cmd", "document.exportPdf",
                "--params", json.dumps({"path": str(pdf), "createLayers": True})]

    cmd += ["--export", str(ai), "--export", str(png), "--ppi", str(args.ppi)]
    _run(cmd)

    for f in (ai, png, svg_out):
        if not f.is_file() or f.stat().st_size == 0:
            raise L.ToolError("E-RENDER", "missing artefact: " + f.name, L.EXIT_ERROR)
    if (args.pdf_standard != "none" or args.profile == "handoff") and not pdf.is_file():
        raise L.ToolError("E-RENDER", "missing print PDF", L.EXIT_ERROR)

    info = _structure(ai)
    kinds = info.get("kinds") or {}
    art = (info.get("artboards") or [{}])[0].get("rect") or [0, 0, 0, 0]
    raster = sum(v for k, v in kinds.items() if "raster" in k.lower() or "image" in k.lower())
    placed = sum(v for k, v in kinds.items() if "placed" in k.lower() or "link" in k.lower())
    paths = kinds.get("Path", 0) + kinds.get("Compound Path", 0)
    dims_ok = abs((art[2] - art[0]) - vb[2]) <= 1 and abs((art[3] - art[1]) - vb[3]) <= 1
    h1 = bool(raster == 0 and placed == 0 and paths > 0 and dims_ok)
    struct = {
        "schema": "vector-ai-studio/structure-readback-v1",
        "ok": h1,
        "engine": "vectorcraft",
        "source": "vectorcraft-cli info (reopen)",
        "raster": raster, "placed": placed, "paths": paths,
        "artboard": "%gx%g" % (art[2] - art[0], art[3] - art[1]),
        "viewBox": "_".join("%g" % v for v in vb),
        "artboard_matches_viewBox": bool(dims_ok),
        "kinds": kinds,
        "color_mode": (info.get("info") or {}).get("colorMode"),
        "layers": kinds.get("Layer", 0),
        "automation": "auto",
    }
    (out / "structure_readback.json").write_text(json.dumps(struct, ensure_ascii=False, indent=2), encoding="utf-8")

    warnings = [
        "VectorCraft writes its own /AIPrivateData1 rewrite stream (not Adobe's "
        "proprietary editing data); clients requiring Adobe-native editing data "
        "get SVG + PDF/X instead",
    ]
    pdf_facts = _pdf_facts(pdf, warnings) if pdf.is_file() else None
    ai_bytes = ai.read_bytes()
    observed = {
        "bytes": len(ai_bytes),
        "sha256": hashlib.sha256(ai_bytes).hexdigest(),
        "pdf_compatible": ai_bytes[:8].startswith(b"%PDF-"),
        "pdf_header": ai_bytes[:8].decode("latin1"),
        "fonts_embedded": (len(re.findall(rb"/FontFile(?![0-9])", ai_bytes))
                           + ai_bytes.count(b"/FontFile2") + ai_bytes.count(b"/FontFile3")),
        "has_ai_private": b"/AIPrivateData1" in ai_bytes,
        "has_aiprivatedata1_marker": b"/AIPrivateData1" in ai_bytes,
        "output_condition": (pdf_facts or {}).get("output_condition"),
        "color_mode": (info.get("info") or {}).get("colorMode"),
        "artboard_pt": [art[2] - art[0], art[3] - art[1]],
        "artboard_matches_viewBox": bool(dims_ok),
        "layers": kinds.get("Layer", 0),
        "paths": paths,
    }
    if pdf_facts:
        observed["print_pdf"] = pdf_facts

    unverified = []
    if args.pdf_standard != "none" and pdf_facts:
        if pdf_facts.get("GTS_PDFXVersion", 0) == 0 or pdf_facts.get("output_intent", 0) == 0:
            unverified.append("requested " + args.pdf_standard +
                              " but /GTS_PDFXVersion or /OutputIntent is absent")
        if not pdf_facts.get("trimbox_rect") or not pdf_facts.get("bleedbox_rect"):
            unverified.append("TrimBox / BleedBox missing from the print PDF")
        _ops = pdf_facts.get("content_ops")
        if _ops is None:
            unverified.append("print PDF content streams could not be decoded: "
                              "colour operators were not measured")
        elif args.pdf_standard == "pdfX1a":
            if _ops["k"] + _ops["K"] == 0:
                unverified.append("print PDF has no CMYK content operators")
        elif not sum(_ops.values()):
            unverified.append("print PDF has no colour operators in its page content")
        if args.pdf_standard == "pdfX1a" and pdf_facts.get("device_rgb", 0) > 0:
            unverified.append("PDF/X-1a still declares DeviceRGB")
    if args.profile == "handoff":
        warnings.append("handoff: the .ai keeps SVG groups (engine has no group-to-layer command); "
                        "the PDF carries layers")
    if not h1:
        unverified.append("H1 structure readback failed")

    L.write_json_atomic(out / "deliverable.json", {
        "schema": "vector-ai-studio/deliverable-v1",
        "file": ai.name,
        "engine": "vectorcraft",
        "engine_version": subprocess.run([str(cli), "--version"], capture_output=True,
                                         text=True, encoding="utf-8", errors="replace",
                                         timeout=60).stdout.strip(),
        "profile": args.profile,
        "save_options": {"pdf_standard": args.pdf_standard,
                         "pdf_compatible": True,
                         "embed_icc": bool(args.icc_cmyk),
                         "cmyk": args.profile == "print",
                         "bleed_mm": args.bleed_mm if args.profile == "print" else 0,
                         "bleed_pt": bleed_pt if args.profile == "print" else 0,
                         "color_mode": "cmyk" if args.profile == "print" else "rgb",
                         "layers_from_groups": args.profile == "handoff",
                         "output_intent": (pdf_facts or {}).get("output_condition")
                                          or (args.icc_cmyk or "document default"),
                         "output_intent_requested": args.icc_cmyk or "document default",
                         "printers_marks": args.pdf_standard != "none"},
        "observed": observed,
        "structure": {"open_paths": paths, "raster": raster, "placed": placed,
                      "layers": kinds.get("Layer", 0),
                      "artboard": "%gx%g" % (art[2] - art[0], art[3] - art[1]),
                      "viewBox": "_".join("%g" % v for v in vb)},
        "warnings": warnings,
        "unverified": unverified,
        "h1_pass": h1,
        "verify_yourself": ["head -c 8 final.ai   # %PDF-",
                           "sha256sum final.ai  # " + observed["sha256"]],
    })

    # A measured H1 failure is a result, not an exception: raising here would
    # make _record_failure delete the readback this very run just measured, and
    # the only evidence of why the print master failed would be gone. Mirrors
    # land_vc.py: degraded landing, exit 1, readback kept.
    L.write_json_atomic(out / "landing.json",
                        LP._landing("vectorcraft", "ok" if h1 else "degraded",
                                    "" if h1 else "structure readback did not satisfy H1",
                                    True, h1,
                                    substitute=("inspect the source artboard/fonts; "
                                                "the engine itself was found and ran")))
    return L.emit(SCRIPT, status="ok" if h1 else "degraded",
                  code=0 if h1 else L.EXIT_FAIL, run_id=args.run_id, data={
        "profile": args.profile, "pdf_standard": args.pdf_standard,
        "h1_pass": h1, "paths": paths, "raster": raster, "placed": placed,
        "artboard": [art[2] - art[0], art[3] - art[1]], "viewBox": vb,
        "pdf_x": {"standard": args.pdf_standard,
                  "gts_pdfx": (pdf_facts or {}).get("GTS_PDFXVersion", 0),
                  "gts_pdfx_value": (pdf_facts or {}).get("gts_pdfx_value"),
                  "output_intents": (pdf_facts or {}).get("output_intent", 0),
                  "output_intent_objects": (pdf_facts or {}).get("output_intent_objects", 0),
                  "output_condition": (pdf_facts or {}).get("output_condition"),
                  "trimbox": (pdf_facts or {}).get("trimbox_rect"),
                  "bleedbox": (pdf_facts or {}).get("bleedbox_rect"),
                  # null when nothing decoded: 0 would read as "measured, none".
                  "cmyk_ops": None if (pdf_facts or {}).get("content_ops") is None
                              else pdf_facts["content_ops"].get("k", 0) + pdf_facts["content_ops"].get("K", 0),
                  "colour_ops": None if (pdf_facts or {}).get("content_ops") is None
                                else sum(pdf_facts["content_ops"].values())},
        "unverified": unverified,
        "bytes": {"ai": ai.stat().st_size, "pdf": pdf.stat().st_size if pdf.is_file() else 0},
    }, warnings=warnings, artifacts={"ai": ai.name, "print_pdf": pdf.name,
                                     "structure": "structure_readback.json",
                                     "deliverable": "deliverable.json"})


if __name__ == "__main__":
    sys.exit(L.run_cli(SCRIPT, main))

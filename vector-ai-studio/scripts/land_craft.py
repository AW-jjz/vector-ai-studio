#!/usr/bin/env python3
"""Land an SVG as a native .ai (and, for print, a PDF/X master) using only
VectorCraft. No Adobe, no COM, no Windows-only bridge.

Why this exists: Adobe does not offer Creative Cloud services on adobe.com in
mainland China, and a headless pipeline must not depend on a desktop app being
licensed, activated and reachable. VectorCraft is a clean-room, MIT/Apache-2.0
Illustrator-class engine with a headless CLI; its 933-command catalogue covers
what the old JSX/COM path did and considerably more.

Output contract mirrors land_ai.py so downstream (aggregate_log.py, the H1 gate,
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
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L
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
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    rows = []
    for line in (p.stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if p.returncode != 0:
        raise L.ToolError("E-RENDER", "craft failed: " +
                          (p.stderr or p.stdout or "")[-400:], L.EXIT_ERROR)
    return rows


def _step(rows, want, key="command"):
    for r in rows:
        if r.get(key) == want or r.get("step") == want:
            return r
    return {}


def _pdf_objects(raw):
    """object number -> body. A segment ends at the next object header, never
    at an `endobj` string that may occur inside compressed data."""
    starts = [(int(m.group(1)), m.start(), m.end())
              for m in re.finditer(rb"(?m)^(\d+)\s+0\s+obj\b", raw)]
    out = {}
    for i, (num, _start, body_start) in enumerate(starts):
        end = starts[i + 1][1] if i + 1 < len(starts) else len(raw)
        seg = raw[body_start:end]
        cut = seg.rfind(b"endobj")
        out[num] = seg[:cut] if cut >= 0 else seg
    return out


def _stream_bytes(body):
    m = re.search(rb"stream\r?\n", body)
    if not m:
        return None
    end = body.rfind(b"endstream")
    return body[m.end():end] if end >= m.end() else None


def _inflate(blob):
    try:
        return zlib.decompress(blob)
    except zlib.error:
        try:
            return zlib.decompressobj().decompress(blob)
        except zlib.error:
            return None


def _strip_strings(data):
    data = re.sub(rb"\((?:\\.|[^\\()])*\)", b"()", data)
    return re.sub(rb"<[0-9A-Fa-f\s]*>", b"<>", data)


def _pdf_text(blob):
    # PDF literal strings escape ( ) and \; undo that before reporting
    return (blob.replace(b"\\\\", b"\\")
                .replace(b"\\(", b"(").replace(b"\\)", b")")).decode("latin1")


def _pdf_facts(path):
    raw = path.read_bytes()
    facts = {"bytes": len(raw), "pdf_header": raw[:8].decode("latin1")}
    for key in (b"/GTS_PDFXVersion", b"/OutputIntent", b"/OutputCondition",
                b"/Trapped", b"/TrimBox", b"/BleedBox", b"/MediaBox",
                b"/DeviceCMYK", b"/DeviceRGB", b"/ICCBased"):
        # /OutputIntents is the catalog array; counting it as an intent object
        # double-counts the file's single intent.
        pat = re.escape(key) + (rb"(?![s])" if key == b"/OutputIntent" else b"")
        facts[key.decode().lstrip("/")] = len(re.findall(pat, raw))
    for box in (b"TrimBox", b"BleedBox", b"MediaBox"):
        m = re.findall(rb"/" + box + rb"\s*\[([^\]]+)\]", raw)
        facts[box.decode().lower() + "_rect"] = m[0].decode("latin1").strip() if m else None
    m = re.search(rb"/GTS_PDFXVersion\s*\(((?:\\.|[^\\()])*)\)", raw)
    facts["gts_pdfx_value"] = _pdf_text(m.group(1)) if m else None
    m = re.search(rb"/OutputConditionIdentifier\s*\(((?:\\.|[^\\()])*)\)", raw)
    facts["output_condition"] = _pdf_text(m.group(1)) if m else None
    facts["output_intent_objects"] = len(re.findall(rb"/Type\s*/OutputIntent\b", raw))

    objs = _pdf_objects(raw)
    wanted = set()
    for num, body in objs.items():
        head = body.split(b"stream", 1)[0]
        if re.search(rb"/Type\s*/Page\b", head):
            for mm in re.finditer(rb"/Contents\s+(\d+)\s+0\s+R", head):
                wanted.add(int(mm.group(1)))
            arr = re.search(rb"/Contents\s*\[([^\]]*)\]", head)
            if arr:
                for mm in re.finditer(rb"(\d+)\s+0\s+R", arr.group(1)):
                    wanted.add(int(mm.group(1)))
        if re.search(rb"/Subtype\s*/Form\b", head):
            wanted.add(num)
    ops = {k: 0 for k in ("k", "K", "rg", "RG", "g", "G", "sc", "SC", "scn", "SCN")}
    decoded = 0
    for num in sorted(wanted):
        blob = _stream_bytes(objs.get(num, b""))
        data = _inflate(blob) if blob is not None else None
        if data is None:
            continue
        decoded += 1
        data = _strip_strings(data)
        for op in ops:
            ops[op] += len(re.findall(rb"(?<![\w.])" + op.encode() + rb"(?![\w])", data))
    facts["content_ops"] = ops
    # Only the page content streams and Form XObjects are page description;
    # ICC profiles, font programs and CMaps are binary blobs that happen to
    # contain k/K bytes, and are not evidence of a CMYK operator.
    facts["content_ops_scope"] = "page /Contents refs + /Subtype /Form streams only"
    facts["content_streams"] = {"objects": len(wanted), "decoded": decoded}
    facts["streams_decoded"] = decoded
    return facts


def _structure(ai_path):
    # `info` emits one JSON document (not JSON lines), so parse the whole stdout
    p = subprocess.run([str(find_cli()), "info", str(ai_path)],
                       capture_output=True, text=True, timeout=600)
    if p.returncode != 0:
        raise L.ToolError("E-RENDER", "info failed: " +
                          (p.stderr or "")[-300:], L.EXIT_ERROR)
    return json.loads(p.stdout)


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

    cli = require_cli()          # E-CAPABILITY-MISSING, never a silent success
    src = Path(args.svg)
    if not src.is_file():
        raise L.ToolError("E-SCHEMA", "SVG not found: " + src.name, L.EXIT_USAGE)
    if args.profile == "print" and args.pdf_standard == "none":
        args.pdf_standard = "pdfX1a"
    if args.pdf_standard != "none" and args.profile != "print":
        raise L.ToolError("E-USAGE", "PDF/X needs --profile print", L.EXIT_USAGE)

    vb = viewbox_of(src)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ai = out / "final.ai"
    png = out / "preview.png"
    pdf = out / "print.pdf"
    svg_out = out / "final.svg"
    shutil.copyfile(src, svg_out)

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
    rows = _run(cmd)

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

    pdf_facts = _pdf_facts(pdf) if pdf.is_file() else None
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
    warnings = [
        "VectorCraft writes its own /AIPrivateData1 rewrite stream (not Adobe's "
        "proprietary editing data); clients requiring Adobe-native editing data "
        "get SVG + PDF/X instead",
    ]
    if args.pdf_standard != "none" and pdf_facts:
        if pdf_facts.get("GTS_PDFXVersion", 0) == 0 or pdf_facts.get("OutputIntent", 0) == 0:
            unverified.append("requested " + args.pdf_standard +
                              " but /GTS_PDFXVersion or /OutputIntent is absent")
        if not pdf_facts.get("trimbox_rect") or not pdf_facts.get("bleedbox_rect"):
            unverified.append("TrimBox / BleedBox missing from the print PDF")
        if args.pdf_standard == "pdfX1a":
            if pdf_facts["content_ops"]["k"] + pdf_facts["content_ops"]["K"] == 0:
                unverified.append("print PDF has no CMYK content operators")
        elif not sum(pdf_facts["content_ops"].values()):
            unverified.append("print PDF has no colour operators in its page content")
        if args.pdf_standard == "pdfX1a" and pdf_facts.get("DeviceRGB", 0) > 0:
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
                                         text=True, timeout=60).stdout.strip(),
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

    if not h1:
        raise L.ToolError("E-H1-STRUCTURE",
                          "structure readback failed: " + json.dumps(struct)[:300], L.EXIT_FAIL)

    return L.emit(SCRIPT, status="ok", code=0, data={
        "profile": args.profile, "pdf_standard": args.pdf_standard,
        "h1_pass": h1, "paths": paths, "raster": raster, "placed": placed,
        "artboard": [art[2] - art[0], art[3] - art[1]], "viewBox": vb,
        "pdf_x": {"standard": args.pdf_standard,
                  "gts_pdfx": (pdf_facts or {}).get("GTS_PDFXVersion", 0),
                  "gts_pdfx_value": (pdf_facts or {}).get("gts_pdfx_value"),
                  "output_intents": (pdf_facts or {}).get("OutputIntent", 0),
                  "output_intent_objects": (pdf_facts or {}).get("output_intent_objects", 0),
                  "output_condition": (pdf_facts or {}).get("output_condition"),
                  "trimbox": (pdf_facts or {}).get("trimbox_rect"),
                  "bleedbox": (pdf_facts or {}).get("bleedbox_rect"),
                  "cmyk_ops": (pdf_facts or {}).get("content_ops", {}).get("k", 0) +
                              (pdf_facts or {}).get("content_ops", {}).get("K", 0),
                  "colour_ops": sum((pdf_facts or {}).get("content_ops", {}).values())},
        "unverified": unverified,
        "bytes": {"ai": ai.stat().st_size, "pdf": pdf.stat().st_size if pdf.is_file() else 0},
    }, warnings=warnings, artifacts={"ai": ai.name, "print_pdf": pdf.name,
                                     "structure": "structure_readback.json",
                                     "deliverable": "deliverable.json"})


if __name__ == "__main__":
    sys.exit(L.run_cli(SCRIPT, main))

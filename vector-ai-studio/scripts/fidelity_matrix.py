#!/usr/bin/env python3
"""fidelity_matrix.py — M3 / r75：把「faithful」从声明变成测量。

Before this, the builtin fallback's "faithful" verdict rested on a STATIC
sniff (`render_png._element_fidelity`): "every element is an axis-aligned
solid rect" -- and nobody ever measured whether what the builtin DRAWS
matches what a real rasteriser shows. This script measures it:

  * one PROBE per feature (rect-solid, rect-stroked, circle, ellipse, line,
    path-fill, path-stroke, gradient, text, transform, opacity);
  * each probe is rendered TWICE at the same size:
      - by the builtin fallback (render_png._builtin_raster, production code),
      - by a Pillow reference renderer (a REAL second implementation drawing
        the same geometry);
  * the two rasters are compared pixel by pixel -> per-feature rows:
        consistent  the builtin matches the reference (within the declared
                    tolerance, see TOLERANCE below)
        divergent   it does not -- the classifier's "limited" for this
                    feature is CONFIRMED by measurement.

What this changes in the product: the per-element claim
(`render_png._element_fidelity`) is no longer self-attested. A94 asserts that
every feature the classifier claims FAITHFUL is measured CONSISTENT by this
matrix -- the load-bearing direction (a claim of faithful that measurement
contradicts is a lie shipped to the user). The other direction (measured
consistent -> the claim may be UPGRADED later) is recorded in the matrix as
an upgrade candidate, not enforced: staying limited is fail-closed, never
dishonest.

`--check` re-measures in memory and byte-compares with the committed matrix
(scripts/_contract.py pattern: E-FIDELITY-DRIFT when someone edits the
committed matrix without re-measuring). Pillow is required (it is the
reference renderer); without it the measurement is a declared skip, never a
pass.

    python scripts/fidelity_matrix.py --json              # measure + write
    python scripts/fidelity_matrix.py --check --json      # re-measure, compare
"""

import argparse
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L
import render_png as R
import _contract as C

SCRIPT = "fidelity_matrix.py"
MATRIX_PATH = L.skill_root() / "references" / "fidelity" / "capability-matrix.json"

# Declared tolerance (r75, measured on the probes below):
#   * solid shapes: the builtin fills the bounding box with a 1px overshoot on
#     the right/bottom edges (render_png._builtin_raster x1/y1 "+1") and does
#     not anti-alias, while the Pillow reference draws the exact rectangle with
#     hard edges. The INTERIOR (bbox shrunk by 2px) must match EXACTLY; the
#     edge band may differ. Overall differing-pixel ratio must stay <= 0.02.
#   * every other feature must measure divergent (the classifier claims
#     limited for them; a measured match would be an upgrade candidate).
EDGE_BAND = 2
OVERALL_TOL = 0.02

# Each probe: feature name, the SVG source, and the Pillow reference draw.
# Artboard is 100x100 for every probe (scale = 1 -> 100x100 rasters).
COLOR = "#D3122A"
PROBES = [
    ("rect-solid",
     '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100">'
     '<rect x="20" y="20" width="40" height="30" fill="' + COLOR + '"/></svg>',
     "rect_solid"),
    ("rect-stroked",
     '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100">'
     '<rect x="20" y="20" width="40" height="30" fill="' + COLOR + '" stroke="#111111" stroke-width="4"/></svg>',
     "rect_stroked"),
    ("circle",
     '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100">'
     '<circle cx="50" cy="50" r="25" fill="' + COLOR + '"/></svg>',
     "circle"),
    ("ellipse",
     '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100">'
     '<ellipse cx="50" cy="50" rx="30" ry="20" fill="' + COLOR + '"/></svg>',
     "ellipse"),
    ("line",
     '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100">'
     '<line x1="15" y1="50" x2="85" y2="50" stroke="' + COLOR + '" stroke-width="6"/></svg>',
     "line"),
    ("path-fill",
     '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100">'
     '<path d="M20 20 L60 20 L60 50 Z" fill="' + COLOR + '"/></svg>',
     "path_fill"),
    ("path-stroke",
     '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100">'
     '<path d="M20 20 L60 20 L60 50" fill="none" stroke="' + COLOR + '" stroke-width="5"/></svg>',
     "path_stroke"),
    ("gradient",
     '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100">'
     '<defs><linearGradient id="g"><stop offset="0" stop-color="#D3122A"/>'
     '<stop offset="1" stop-color="#F5B915"/></linearGradient></defs>'
     '<rect x="20" y="20" width="40" height="30" fill="url(#g)"/></svg>',
     "gradient"),
    ("text",
     '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100">'
     '<text x="20" y="50" font-size="18" fill="' + COLOR + '">ABC</text></svg>',
     "text"),
    ("transform",
     '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100">'
     '<rect x="25" y="25" width="40" height="30" fill="' + COLOR + '" '
     'transform="rotate(45 50 50)"/></svg>',
     "transform"),
    ("opacity",
     '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100">'
     '<rect x="20" y="20" width="40" height="30" fill="' + COLOR + '" fill-opacity="0.4"/></svg>',
     "opacity"),
]


def _pil_reference(kind, w, h):
    """A real second renderer: Pillow draws the probe's geometry directly."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (w, h), (255, 255, 255))
    dr = ImageDraw.Draw(img)
    col = (211, 18, 42)  # COLOR, RGB
    if kind == "rect_solid":
        dr.rectangle((20, 20, 60 - 1, 50 - 1), fill=col)
    elif kind == "rect_stroked":
        # SVG stroke is CENTRED on the path: half outside, half inside. Pillow's
        # rectangle(outline=, width=) draws the outline INSIDE the box, which
        # made the old reference 2 px narrower on every side and inflated the
        # divergence. Draw the outer edge and the visible fill explicitly.
        dr.rectangle((18, 18, 61, 51), fill=(17, 17, 17))
        dr.rectangle((22, 22, 57, 47), fill=col)
    elif kind == "circle":
        dr.ellipse((25, 25, 75 - 1, 75 - 1), fill=col)
    elif kind == "ellipse":
        dr.ellipse((20, 30, 80 - 1, 70 - 1), fill=col)
    elif kind == "line":
        dr.line((15, 50, 85, 50), fill=col, width=6)
    elif kind == "path_fill":
        dr.polygon((20, 20, 60, 20, 60, 50), fill=col)
    elif kind == "path_stroke":
        dr.line((20, 20, 60, 20), fill=col, width=5)
        dr.line((60, 20, 60, 50), fill=col, width=5)
    elif kind == "gradient":
        for x in range(20, 60):
            t = (x - 20) / 40.0
            c = (int(211 + (245 - 211) * t), int(18 + (185 - 18) * t), int(42 + (21 - 42) * t))
            dr.line((x, 20, x, 50), fill=c)
    elif kind == "text":
        from PIL import ImageFont
        try:
            fnt = ImageFont.load_default(size=18)
        except TypeError:                     # Pillow < 10
            fnt = ImageFont.load_default()
        dr.text((20, 36), "ABC", fill=col, font=fnt)
    elif kind == "transform":
        import math
        # rotate(45 50 50): SVG's matrix is [cos -sin; sin cos] with y DOWN, so
        # a positive angle turns clockwise on screen. The old reference used
        # -pi/4 and produced a mirrored bbox; cairosvg (and SVG) go the other
        # way. The sign is the bug, not the geometry.
        pts = [(25, 25), (65, 25), (65, 55), (25, 55)]
        a = 3.14159265358979 / 4.0
        cx, cy = 50.0, 50.0
        rot = [(cx + (x - cx) * math.cos(a) - (y - cy) * math.sin(a),
                cy + (x - cx) * math.sin(a) + (y - cy) * math.cos(a)) for x, y in pts]
        dr.polygon(rot, fill=col)
    elif kind == "opacity":
        base = (255, 255, 255)
        a = 0.4
        blended = tuple(int(base[i] * (1 - a) + col[i] * a) for i in range(3))
        dr.rectangle((20, 20, 60 - 1, 50 - 1), fill=blended)
    return img


def _builtin_png_for(svg_text):
    """Render the probe with the PRODUCTION builtin rasteriser; also return
    the analyzed sig so the row's CLAIM is the classifier's real verdict on
    the real elements (not a hand-built element dict -- the r75 first run
    showed hand-built claims mislabel half the probes)."""
    sig = L.analyze_svg(svg_text)
    if not sig.get("parse_ok"):
        return None, None, sig.get("error"), sig
    w, h, data = R._builtin_raster(sig)
    return w, h, data, sig


def _pixels_builtin(png_bytes):
    """Decode the builtin's own PNG (filter-0 RGBA, as _encode_png writes)."""
    import struct, zlib
    data = png_bytes
    i = 8
    w = h = 0
    idat = b""
    while i < len(data):
        ln = struct.unpack(">I", data[i:i + 4])[0]
        typ = data[i + 4:i + 8]
        body = data[i + 8:i + 8 + ln]
        if typ == b"IHDR":
            w, h = struct.unpack(">II", body[:8])
        elif typ == b"IDAT":
            idat += body
        i += 12 + ln
    raw = zlib.decompress(idat)
    stride = w * 4
    px = bytearray()
    pos = 0
    for _ in range(h):
        ft = raw[pos]
        pos += 1
        if ft != 0:
            raise ValueError("unexpected PNG filter type %d" % ft)
        px += raw[pos:pos + stride]
        pos += stride
    return w, h, px


def _compare(builtin_img, ref_img):
    """Pixel comparison -> (diff_ratio, interior_ok). Interior = the bbox of
    any difference shrunk by EDGE_BAND; exactness required there."""
    from PIL import Image, ImageChops
    a = builtin_img.convert("RGB")
    b = ref_img.convert("RGB")
    if a.size != b.size:
        a = a.resize(b.size)
    diff = ImageChops.difference(a, b)
    bboxpx = list(diff.getdata())
    total = len(bboxpx)
    differing = sum(1 for p in bboxpx if max(p) > 0)
    ratio = differing / float(total) if total else 0.0
    # interior exactness: any difference STRICTLY INSIDE the difference bbox
    # (shrunk by EDGE_BAND) breaks the solid-shape claim
    db = diff.getbbox()
    interior_ok = True
    if db:
        x0, y0, x1, y1 = db
        x0 += EDGE_BAND
        y0 += EDGE_BAND
        x1 -= EDGE_BAND
        y1 -= EDGE_BAND
        if x1 > x0 and y1 > y0:
            inner = diff.crop((x0, y0, x1, y1))
            interior_ok = all(max(p) == 0 for p in inner.getdata())
    return ratio, interior_ok, (db or (0, 0, 0, 0))


def measure():
    """The measured matrix (deterministic; no wall-clock)."""
    try:
        from PIL import Image  # noqa: F401
        has_pil = True
    except ImportError:
        has_pil = False
    rows = []
    for feature, svg, kind in PROBES:
        if not has_pil:
            rows.append({"feature": feature, "claim": None, "measured": "skipped",
                         "diff_ratio": None, "interior_ok": None,
                         "note": "Pillow not installed -- measurement is a declared skip"})
            continue
        w, h, png, sig = _builtin_png_for(svg)
        if png is None:
            rows.append({"feature": feature, "measured": "skipped",
                         "note": "builtin raster failed"})
            continue
        builtin_img = Image.open(io.BytesIO(png))
        ref_img = _pil_reference(kind, w, h)
        ratio, interior_ok, db = _compare(builtin_img, ref_img)
        # the claim is the classifier's REAL verdict on the probe's real sig
        claim = R._builtin_fidelity(sig)
        consistent = (ratio <= OVERALL_TOL and interior_ok)
        measured = "consistent" if consistent else "divergent"
        rows.append({"feature": feature, "claim": claim, "measured": measured,
                     "diff_ratio": round(ratio, 6),
                     "interior_ok": interior_ok,
                     "diff_bbox": list(db),
                     "note": ("edge band only (1px overshoot is a known measured "
                              "defect of the builtin)" if feature == "rect-solid" else "")})
    return {"schema": "vector-ai-studio/fidelity-matrix-v1",
            "criteria_version": C.CRITERIA_VERSION,
            "tolerance": {"edge_band_px": EDGE_BAND, "overall_ratio_max": OVERALL_TOL,
                          "declared": "solid shapes: interior exact; edges may differ "
                                      "(builtin overshoots 1px and does not anti-alias)"},
            "rows": rows}


def main():
    ap = argparse.ArgumentParser(description="measure the builtin renderer's fidelity per feature")
    ap.add_argument("--check", action="store_true",
                    help="re-measure in memory and compare with the committed matrix")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    doc = measure()
    text = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    if any(r.get("measured") == "skipped" for r in doc["rows"]):
        # Pillow is the reference renderer; without it there is no measurement.
        # A drift check against a matrix measured on a machine that HAS the
        # reference would be a false red -- declared skip, never a pass, and
        # never writing an unmeasured matrix over a measured one.
        if args.check:
            return L.emit(SCRIPT, status="degraded", code=L.EXIT_MISSING,
                          data={"rows": len(doc.get("rows") or []),
                                "reason": "Pillow missing: measurement is a declared skip"},
                          degradation={"level": "L4", "reason": "Pillow missing",
                                       "substitute": "install pillow to measure the capability matrix"})
        return L.emit(SCRIPT, status="degraded", code=L.EXIT_MISSING,
                      data={"rows": len(doc.get("rows") or []),
                            "reason": "Pillow missing: not writing an unmeasured matrix"},
                      degradation={"level": "L4", "reason": "Pillow missing",
                                   "substitute": "install pillow to measure the capability matrix"})

    if args.check:
        if not MATRIX_PATH.is_file():
            raise L.ToolError("E-FIDELITY-DRIFT",
                              "committed matrix missing: " + str(MATRIX_PATH), L.EXIT_FAIL)
        committed = MATRIX_PATH.read_text(encoding="utf-8")
        if committed != text:
            raise L.ToolError("E-FIDELITY-DRIFT",
                              "capability matrix drifted: re-run scripts/fidelity_matrix.py "
                              "and commit the new measurement", L.EXIT_FAIL)
        # the load-bearing direction: every feature the classifier claims
        # FAITHFUL must be measured CONSISTENT
        bad = [r["feature"] for r in doc["rows"]
               if r.get("claim") == C.FIDELITY.FAITHFUL and r.get("measured") != "consistent"]
        if bad:
            raise L.ToolError("E-FIDELITY-DRIFT",
                              "classifier claims FAITHFUL but measurement disagrees: "
                              + ",".join(bad), L.EXIT_FAIL)
        return L.emit(SCRIPT, status="ok", code=L.EXIT_OK,
                      data={"rows": len(doc["rows"]), "drift": False,
                            "claims_backed": True})

    MATRIX_PATH.parent.mkdir(parents=True, exist_ok=True)
    L.write_text_atomic(MATRIX_PATH, text)
    divergent = [r["feature"] for r in doc["rows"] if r.get("measured") == "divergent"]
    return L.emit(SCRIPT, status="ok", code=L.EXIT_OK,
                  data={"matrix": str(MATRIX_PATH), "rows": len(doc["rows"]),
                        "consistent": len(doc["rows"]) - len(divergent),
                        "divergent": divergent})


if __name__ == "__main__":
    sys.exit(L.run_cli(SCRIPT, main))

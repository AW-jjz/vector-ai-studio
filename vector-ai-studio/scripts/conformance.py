#!/usr/bin/env python3
"""Check a mark against the mechanically judgeable half of its brief.

Why: a blind reviewer never sees the brief, so need-fit cannot be scored on the
review sheet. Most of it, however, is not a matter of taste -- the canvas size,
the colour set, the colour-area caps, the allowed stroke widths, the element
kinds, the element count, the minimum margin and the forbidden constructs are
all readable from the SVG without a human. This script settles those and leaves
only the genuinely human questions (does the claim hold, is the hierarchy clear,
does the tone fit) to the reviewer.

The rules file is the structured form of the brief's hard constraints. It does
not parse prose: a human writes the numbers once, the script checks them every
round, so a later edit cannot quietly drift away from what was asked.

Exit codes follow the shared envelope contract: 0 all checks pass, 1 one or more
checks fail (E-CONFORMANCE), 2 bad usage or malformed rules (E-USAGE/E-SCHEMA).
"""
import argparse
import json
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L

SCRIPT = "conformance.py"
NS = "{http://www.w3.org/2000/svg}"
SHAPES = ("rect", "circle", "ellipse", "line", "polygon", "polyline", "path")
FORBID_TAGS = {"image": NS + "image", "text": NS + "text",
               "gradient": (NS + "linearGradient", NS + "radialGradient"),
               "filter": NS + "filter", "clipPath": NS + "clipPath",
               "mask": NS + "mask", "use": NS + "use"}
FORBID_ATTRS = ("filter", "clip-path", "mask")


def _tag(el):
    return el.tag.replace(NS, "")


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _area(el):
    t = _tag(el)
    if t == "rect":
        return _f(el.get("width")) * _f(el.get("height"))
    if t == "circle":
        return math.pi * _f(el.get("r")) ** 2
    if t == "ellipse":
        return math.pi * _f(el.get("rx")) * _f(el.get("ry"))
    if t == "line":
        return math.hypot(_f(el.get("x2")) - _f(el.get("x1")),
                          _f(el.get("y2")) - _f(el.get("y1"))) * _f(el.get("stroke-width"), 1.0)
    if t in ("polygon", "polyline"):
        pts = [tuple(map(float, p.split(","))) for p in (el.get("points") or "").split()]
        if len(pts) < 3:
            return 0.0
        s = sum(pts[i][0] * pts[(i + 1) % len(pts)][1] -
                pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts)))
        return abs(s) / 2
    return 0.0


def _bbox(el, expand):
    t = _tag(el)
    pad = expand / 2.0 if el.get("stroke-width") else 0.0
    if t == "rect":
        x, y = _f(el.get("x")), _f(el.get("y"))
        box = (x, y, x + _f(el.get("width")), y + _f(el.get("height")))
    elif t == "circle":
        cx, cy, r = _f(el.get("cx")), _f(el.get("cy")), _f(el.get("r"))
        box = (cx - r, cy - r, cx + r, cy + r)
    elif t == "ellipse":
        cx, cy = _f(el.get("cx")), _f(el.get("cy"))
        box = (cx - _f(el.get("rx")), cy - _f(el.get("ry")),
               cx + _f(el.get("rx")), cy + _f(el.get("ry")))
    elif t == "line":
        box = (_f(el.get("x1")), _f(el.get("y1")), _f(el.get("x2")), _f(el.get("y2")))
    else:
        pts = [tuple(map(float, p.split(","))) for p in (el.get("points") or "").split()]
        if not pts:
            return None
        box = (min(p[0] for p in pts), min(p[1] for p in pts),
               max(p[0] for p in pts), max(p[1] for p in pts))
    return (box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad)


def _colour(el, key):
    v = (el.get(key) or "").strip().lower()
    if not v or v in ("none", "transparent") or v.startswith("url("):
        return None
    return v


def _measure(svg, canvas):
    els = [e for e in svg.iter() if _tag(e) in SHAPES]
    colours, widths, per_colour, boxes = set(), set(), {}, []
    for e in els:
        fill, stroke = _colour(e, "fill"), _colour(e, "stroke")
        for c in (fill, stroke):
            if c:
                colours.add(c)
        if stroke:
            widths.add(_f(e.get("stroke-width"), 1.0))
        owner = fill or stroke
        if owner:
            per_colour[owner] = per_colour.get(owner, 0.0) + _area(e)
        box = _bbox(e, _f(e.get("stroke-width")))
        if box:
            boxes.append(box)
    bbox = ([min(b[0] for b in boxes), min(b[1] for b in boxes),
             max(b[2] for b in boxes), max(b[3] for b in boxes)] if boxes else None)
    total = float(canvas[0] * canvas[1])
    forbid = {k: 0 for k in FORBID_TAGS}
    for e in svg.iter():
        t = _tag(e)
        for key, tag in FORBID_TAGS.items():
            if isinstance(tag, tuple):
                if e.tag in tag:
                    forbid[key] += 1
            elif e.tag == tag:
                forbid[key] += 1
        for attr in FORBID_ATTRS:
            if e.get(attr):
                forbid["filter" if attr == "filter" else attr] += 1
    return {
        "elements": len(els),
        "types": sorted({_tag(e) for e in els}),
        "colours": sorted(colours),
        "stroke_widths": sorted(widths),
        "area_ratio": {k: round(v / total, 4) for k, v in sorted(per_colour.items())},
        "bbox": [round(v, 2) for v in bbox] if bbox else None,
        "margins": ([round(bbox[0], 2), round(bbox[1], 2),
                     round(canvas[0] - bbox[2], 2), round(canvas[1] - bbox[3], 2)]
                    if bbox else None),
        "forbidden": forbid,
    }


def main():
    ap = argparse.ArgumentParser(description="brief conformance for the mechanical half")
    ap.add_argument("--svg", required=True)
    ap.add_argument("--rules", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--run-id", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    svg_path, rules_path = Path(args.svg), Path(args.rules)
    for p in (svg_path, rules_path):
        if not p.is_file():
            raise L.ToolError("E-USAGE", "file not found: " + p.name, L.EXIT_USAGE)
    try:
        rules = json.loads(rules_path.read_text(encoding="utf-8"))
    except Exception as e:                      # noqa: BLE001
        raise L.ToolError("E-SCHEMA", "rules unreadable: " + str(e)[:120], L.EXIT_USAGE)
    if rules.get("schema") != "vector-ai-studio/conformance-v1":
        raise L.ToolError("E-SCHEMA", "rules schema must be vector-ai-studio/conformance-v1",
                          L.EXIT_USAGE)
    canvas = rules.get("canvas") or [240, 240]
    try:
        root = ET.fromstring(svg_path.read_text(encoding="utf-8"))
    except Exception as e:                      # noqa: BLE001
        raise L.ToolError("E-XML", "SVG unreadable: " + str(e)[:120], L.EXIT_USAGE)

    m = _measure(root, canvas)
    checks = []

    def check(cid, ok, rule, measured):
        checks.append({"id": cid, "ok": bool(ok), "rule": rule, "measured": measured})

    vb = (root.get("viewBox") or "").split()
    if vb:
        check("CANVAS", [round(_f(x), 2) for x in vb] == [0.0, 0.0, float(canvas[0]), float(canvas[1])],
              "viewBox 0 0 %d %d" % (canvas[0], canvas[1]), " ".join(vb))
    for key in ("width", "height"):
        if root.get(key):
            check("CANVAS-" + key.upper(), _f(root.get(key)) == float(canvas[0 if key == "width" else 1]),
                  str(canvas[0 if key == "width" else 1]), root.get(key))

    allowed = {c.lower() for c in rules.get("colors_allowed", [])}
    if allowed:
        extra = sorted(set(m["colours"]) - allowed)
        check("COLOUR-ALLOWED", not extra, "colours within " + ",".join(sorted(allowed)),
              "extra " + ",".join(extra) if extra else "none")
    if rules.get("colors_max") is not None:
        check("COLOUR-COUNT", len(m["colours"]) <= int(rules["colors_max"]),
              "<= %d colours" % int(rules["colors_max"]), str(len(m["colours"])))
    for colour, cap in (rules.get("area_ratio_max") or {}).items():
        got = m["area_ratio"].get(colour.lower(), 0.0)
        check("AREA-" + colour.lower().replace("#", ""), got <= float(cap),
              "%s area <= %.4f" % (colour.lower(), float(cap)), "%.4f" % got)
    if rules.get("stroke_widths_allowed") is not None:
        want = sorted({float(v) for v in rules["stroke_widths_allowed"]})
        check("STROKE-WIDTHS", m["stroke_widths"] == want,
              "stroke widths " + str(want), str(m["stroke_widths"]))
    if rules.get("types_allowed") is not None:
        want = sorted(rules["types_allowed"])
        check("ELEMENT-TYPES", m["types"] == want, "types " + str(want), str(m["types"]))
    span = rules.get("elements") or {}
    if span:
        lo, hi = int(span.get("min", 0)), int(span.get("max", 10 ** 6))
        check("ELEMENT-COUNT", lo <= m["elements"] <= hi,
              "%d..%d elements" % (lo, hi), str(m["elements"]))
    if rules.get("bbox_margin_min") is not None and m["margins"]:
        worst = min(m["margins"])
        check("MARGIN", worst >= float(rules["bbox_margin_min"]),
              "margin >= %s" % rules["bbox_margin_min"], "%s (min %s)" % (m["margins"], worst))
    for key in rules.get("forbid", []):
        check("FORBID-" + key.upper(), m["forbidden"].get(key, 0) == 0,
              "no " + key, str(m["forbidden"].get(key, 0)))

    fails = [c["id"] for c in checks if not c["ok"]]
    unchecked = [k for k in ("canvas", "colors_allowed", "colors_max", "area_ratio_max",
                             "stroke_widths_allowed", "types_allowed", "elements",
                             "bbox_margin_min", "forbid") if k not in rules]
    doc = {"schema": "vector-ai-studio/conformance-v1-result",
           "svg": svg_path.name, "rules": rules_path.name,
           "measured": m, "checks": checks, "unchecked": unchecked,
           "verdicts": ([{"id": "OK", "msg": "all %d mechanical checks pass" % len(checks),
                          "action": ""}] if not fails else
                        [{"id": "CONFORMANCE-FAIL", "msg": "failed: " + ", ".join(fails),
                          "action": "改图或改需求；不要把不符合写成通过"}])}
    if args.out:
        L.write_json_atomic(Path(args.out), doc)
    if fails:
        return L.emit(SCRIPT, status="ok", code=L.EXIT_FAIL, run_id=args.run_id, data=doc,
                      errors=[L.err("E-CONFORMANCE",
                                    "mechanical brief checks failed: " + ", ".join(fails))],
                      artifacts={"conformance": Path(args.out).name} if args.out else {})
    return L.emit(SCRIPT, status="ok", code=L.EXIT_OK, run_id=args.run_id, data=doc,
                  artifacts={"conformance": Path(args.out).name} if args.out else {})


if __name__ == "__main__":
    sys.exit(L.run_cli(SCRIPT, main))

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
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime
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
# Attribute -> the forbid key it counts against. clip-path used to be its own
# key here while `forbid` was initialised from FORBID_TAGS, so a clip-path
# attribute raised KeyError and the whole run crashed with E-INTERNAL.
FORBID_ATTRS = {"filter": "filter", "clip-path": "clipPath", "mask": "mask"}
# Every key the script understands. A key it does not know used to be ignored
# silently, so a typo ("stroke_width_allowed") looked like an enforced rule and
# an invented forbid name ("texts") produced a check that could never fail.
RULES_KEYS = {"schema", "canvas", "colors_allowed", "colors_max", "area_ratio_max",
              "stroke_widths_allowed", "types_allowed", "elements",
              "bbox_margin_min", "forbid", "source_ledger", "licenses_allowed"}


def _tag(el):
    return el.tag.replace(NS, "")


_UNIT_PX = {"": 1.0, "px": 1.0, "pt": 96.0 / 72.0, "mm": 96.0 / 25.4,
            "cm": 96.0 / 2.54, "in": 96.0}


def _len(v):
    """Absolute length in user units, or None when it is not absolute.

    A percentage needs the viewport it resolves against and an em needs a font
    size; neither is modelled here. Returning 0.0 for them (and for any typo)
    made a 50%-wide rect measure as empty and a 2px stroke as 1.0 -- the
    default width -- so a cap passed on a number that was never rendered.
    Callers must treat None as unmeasurable, never as zero.
    """
    m = re.fullmatch(r"([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*([a-z]*)",
                     str(v).strip().lower())
    if not m or m.group(2) not in _UNIT_PX:
        return None
    return float(m.group(1)) * _UNIT_PX[m.group(2)]


def _dim(el, key):
    """A geometry attribute as user units; a missing one is 0, as in SVG."""
    v = el.get(key)
    return 0.0 if v is None else _len(v)


def _points(el):
    """`points` as [(x, y), ...], or None when a pair is not two numbers.

    SVG allows "10,20 30,40" and "10 20 30 40" and any mix; splitting on commas
    alone raised IndexError on the space form. A percent or a unit makes the
    pair non-absolute, so the geometry is unmeasurable rather than zero.
    """
    raw = el.get("points") or ""
    nums = [float(t) for t in re.findall(
        r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", raw)]
    rest = re.sub(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?", "", raw)
    if rest.strip(" ,\t\r\n") or len(nums) % 2:
        return None
    return [(nums[i], nums[i + 1]) for i in range(0, len(nums), 2)]


def _geom(el):
    """(fill area, perimeter) of a shape; None where not computable.

    The perimeter exists so a stroke can be measured as ink (perimeter x
    width) instead of being reported as its fill area -- r66 found the old
    number was 0.6944 where the band was 0.1389. Joins and caps are ignored,
    which is stated in the docs; the ellipse uses Ramanujan's approximation.
    """
    t = _tag(el)
    if t == "rect":
        w, h = _dim(el, "width"), _dim(el, "height")
        if w is None or h is None:
            return None, None
        return w * h, 2.0 * (w + h)
    if t == "circle":
        r = _dim(el, "r")
        if r is None:
            return None, None
        return math.pi * r * r, 2.0 * math.pi * r
    if t == "ellipse":
        a, b = _dim(el, "rx"), _dim(el, "ry")
        if a is None or b is None:
            return None, None
        per = math.pi * (3.0 * (a + b) - math.sqrt((3.0 * a + b) * (a + 3.0 * b)))
        return math.pi * a * b, per
    if t == "line":
        x1, y1, x2, y2 = (_dim(el, k) for k in ("x1", "y1", "x2", "y2"))
        if None in (x1, y1, x2, y2):
            return None, None
        return 0.0, math.hypot(x2 - x1, y2 - y1)
    if t in ("polygon", "polyline"):
        pts = _points(el)
        if pts is None:
            return None, None
        if len(pts) < 2:
            return 0.0, 0.0
        edges = sum(math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
                    for i in range(len(pts) - 1))
        if t == "polygon" and len(pts) >= 3:
            edges += math.hypot(pts[0][0] - pts[-1][0], pts[0][1] - pts[-1][1])
            s = sum(pts[i][0] * pts[(i + 1) % len(pts)][1] -
                    pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts)))
            return abs(s) / 2.0, edges
        return 0.0, edges
    return None, None


def _bbox(el, expand, has_stroke):
    t = _tag(el)
    pad = expand / 2.0 if has_stroke else 0.0
    if t == "rect":
        x, y = _dim(el, "x"), _dim(el, "y")
        w, h = _dim(el, "width"), _dim(el, "height")
        if None in (x, y, w, h):
            return None
        box = (x, y, x + w, y + h)
    elif t == "circle":
        cx, cy, r = _dim(el, "cx"), _dim(el, "cy"), _dim(el, "r")
        if None in (cx, cy, r):
            return None
        box = (cx - r, cy - r, cx + r, cy + r)
    elif t == "ellipse":
        cx, cy = _dim(el, "cx"), _dim(el, "cy")
        a, b = _dim(el, "rx"), _dim(el, "ry")
        if None in (cx, cy, a, b):
            return None
        box = (cx - a, cy - b, cx + a, cy + b)
    elif t == "line":
        box = tuple(_dim(el, k) for k in ("x1", "y1", "x2", "y2"))
        if None in box:
            return None
    else:
        pts = _points(el)
        if not pts:
            return None
        box = (min(p[0] for p in pts), min(p[1] for p in pts),
               max(p[0] for p in pts), max(p[1] for p in pts))
    return (box[0] - pad, box[1] - pad, box[2] + pad, box[3] + pad)


def _colour(val):
    """Normalized paint, or None when nothing is painted.

    normalize_color is the toolkit's one colour normalizer, so #F00,
    rgb(255,0,0) and #ff0000 compare equal to each other and to the rules file.
    currentColor stays literal here so _resolve can substitute the computed
    color instead of dropping the fill.
    """
    v = (val or "").strip().lower()
    if not v or v in ("none", "transparent") or v.startswith("url("):
        return None
    if v == "currentcolor":
        return v
    return L.normalize_color(v)


# ---------------------------------------------------------------- CSS subset
# A <style> sheet is interpreted, but only over a subset small enough to state
# here and to refuse everything else: type / class / id compounds, descendant
# and child combinators, comma groups, and the declarations below. A sheet that
# uses anything outside the subset fails the run instead of being half applied
# -- a half-applied sheet reports numbers that were never rendered.
CSS_PROPS = ("fill", "stroke", "stroke-width", "color", "visibility",
             "fill-opacity", "stroke-opacity")
CSS_DECLS_ALLOWED = {"fill", "stroke", "stroke-width", "stroke-linecap",
                     "stroke-linejoin", "stroke-miterlimit", "fill-rule",
                     "color", "visibility", "fill-opacity", "stroke-opacity"}
# Definitions are declared but never painted, and a display:none subtree is
# equally absent from the picture. Counting either one inflates colours,
# element count and colour area, so both are excluded from measurement.
NON_RENDERED = {"defs", "symbol", "clippath", "mask", "pattern", "marker"}


def _own(el, key):
    """Own declared value: inline style first, then the presentation attribute."""
    val = _inline(el).get(key)
    if val is not None:
        return val[0]
    return el.get(key)


def _is_zero(val):
    """0, 0.0, 0% and 0px are all zero; anything unparseable is not.

    "0%" used to fall through the float() attempt and count as non-zero, so a
    fully transparent fill-opacity="0%" still counted as ink.
    """
    if val is None:
        return False
    s = str(val).strip().lower()
    if s.endswith("%"):
        s = s[:-1]
        try:
            return float(s) == 0.0
        except ValueError:
            return False
    return _len(s) == 0.0


def _chain(el, parents, pred):
    cur = el
    while cur is not None:
        if pred(cur):
            return True
        cur = parents.get(cur)
    return False


def _renders(el, parents):
    """False for definitions, display:none subtrees and opacity:0 subtrees."""
    if _chain(el, parents, lambda e: _tag(e).lower() in NON_RENDERED):
        return False
    if _chain(el, parents, lambda e: (_own(e, "display") or "").strip().lower() == "none"):
        return False
    return not _chain(el, parents, lambda e: _is_zero(_own(e, "opacity")))


def _transformed(el, parents):
    def has(e):
        v = (_own(e, "transform") or "").strip().lower()
        return bool(v) and v != "none"
    return _chain(el, parents, has)


def _resolve(colour, vals):
    """currentColor takes the computed color; without one it stays a literal."""
    if colour == "currentcolor":
        c = _colour(vals.get("color"))
        if c:
            return c
    return colour


def _inline(el):
    """style="" as {property: (value, important)}; one parser, in lib_common."""
    return L.parse_style(el.get("style"))


def _compound(tok):
    m = re.fullmatch(r"(\*|[A-Za-z_][A-Za-z0-9_-]*)?((?:[.#][A-Za-z0-9_-]+)*)", tok)
    if not m:
        raise ValueError("unsupported selector: " + tok)
    ids, classes = [], []
    for part in re.findall(r"[.#][A-Za-z0-9_-]+", m.group(2) or ""):
        (ids if part[0] == "#" else classes).append(part[1:])
    return (m.group(1) or "", tuple(classes), tuple(ids))


def _selector(text):
    toks = text.replace(">", " > ").split()
    if not toks:
        raise ValueError("empty selector")
    chain, comb = [], " "
    for tok in toks:
        if tok == ">":
            if comb == ">":
                raise ValueError("dangling combinator")
            comb = ">"
            continue
        if any(c in tok for c in "+~:|[]()"):
            raise ValueError("unsupported selector: " + tok)
        chain.append((comb, _compound(tok)))
        comb = " "
    if comb == ">":
        raise ValueError("dangling combinator")
    spec = (sum(len(c[1][2]) for c in chain),
            sum(len(c[1][1]) for c in chain),
            sum(1 for c in chain if c[1][0] and c[1][0] != "*"))
    return chain, spec


def _parse_sheet(text, order):
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    text = text.replace("<![CDATA[", " ").replace("]]>", " ")
    if "@" in text:
        raise ValueError("at-rules are not supported")
    out = []
    for chunk in text.split("}"):
        if not chunk.strip():
            continue
        if "{" not in chunk:
            raise ValueError("malformed rule: " + chunk.strip()[:60])
        pre, _, body = chunk.partition("{")
        decls = {}
        for part in body.split(";"):
            if not part.strip():
                continue
            if ":" not in part:
                raise ValueError("malformed declaration: " + part.strip()[:60])
            k, _, v = part.partition(":")
            key = k.strip().lower()
            if key not in CSS_DECLS_ALLOWED:
                raise ValueError("property not measured: " + key)
            val = v.strip()
            imp = False
            if val.lower().endswith("!important"):
                imp = True
                val = val[:-len("!important")].strip()
            decls[key] = (val, imp)
        for sel in pre.split(","):
            chain, spec = _selector(sel)
            out.append((chain, spec, order, decls))
            order += 1
    return out, order


def _sheets(svg):
    """Parse every <style>, stopping at the first thing the subset cannot state."""
    rules, order = [], 0
    for e in svg.iter():
        if _tag(e) != "style":
            continue
        typ = (e.get("type") or "text/css").strip().lower()
        if typ not in ("", "text/css"):
            raise ValueError("style type is not text/css: " + typ)
        media = (e.get("media") or "all").strip().lower()
        if media not in ("", "all"):
            raise ValueError("media is not supported: " + media)
        parsed, order = _parse_sheet("".join(e.itertext()), order)
        rules.extend(parsed)
    return rules


def _match_compound(el, comp):
    typ, classes, ids = comp
    if typ and typ != "*" and _tag(el) != typ:
        return False
    if classes and not set(classes) <= set((el.get("class") or "").split()):
        return False
    if ids and el.get("id") not in ids:
        return False
    return True


def _matches(el, chain, idx, parents):
    if not _match_compound(el, chain[idx][1]):
        return False
    if idx == 0:
        return True
    p = parents.get(el)
    if chain[idx][0] == ">":
        return p is not None and _matches(p, chain, idx - 1, parents)
    while p is not None:
        if _matches(p, chain, idx - 1, parents):
            return True
        p = parents.get(p)
    return False


def _computed(svg, rules, parents):
    """Computed fill / stroke / stroke-width per element.

    The cascade is the real one: !important beats normal, inline beats the
    sheet, the sheet beats the presentation attribute, and the attribute beats
    inheritance. All three properties are inherited, so an undeclared value is
    taken from the parent's computed value.
    """
    vals = {}
    for el in svg.iter():
        inline = _inline(el)
        got = {}
        for key in CSS_PROPS:
            best = None
            for chain, spec, order, decls in rules:
                if key not in decls or not _matches(el, chain, len(chain) - 1, parents):
                    continue
                val, imp = decls[key]
                rank = (1 if imp else 0, 1, spec, order)
                if best is None or rank > best[0]:
                    best = (rank, val)
            if key in inline:
                val, imp = inline[key]
                rank = (1 if imp else 0, 2, (0, 0, 0), 10 ** 9)
                if best is None or rank > best[0]:
                    best = (rank, val)
            attr = el.get(key)
            if attr is not None:
                rank = (0, 0, (-1, -1, -1), 0)
                if best is None or rank > best[0]:
                    best = (rank, attr)
            got[key] = best[1] if best else None
        parent = parents.get(el)
        inherited = vals.get(parent, {}) if parent is not None else {}
        for key in CSS_PROPS:
            if got[key] is None:
                got[key] = inherited.get(key)
        vals[el] = got
    return vals


def _measure(svg, canvas):
    # Presentation attributes inherit: an icon library writes stroke and
    # stroke-width once on the root <svg> and every path reads them. Measuring
    # only the element's own attributes reported "no stroke, no width" for
    # such files, so a legal 2pt icon failed a 2pt rule.
    #
    # Inline style="" is read too, and it outranks the presentation attribute
    # on the same element. A <style> sheet is interpreted over the subset at
    # the top of this file; anything outside the subset refuses the whole
    # sheet rather than letting a half-applied rule report wrong numbers.
    parents = {c: p for p in svg.iter() for c in p}
    sheets = [e for e in svg.iter() if _tag(e) == "style"]
    style_error, rules = "", []
    if sheets:
        try:
            rules = _sheets(svg)
        except ValueError as e:
            style_error = str(e)[:120]
    comp = _computed(svg, rules, parents)

    def _visible(e):
        if not _renders(e, parents):
            return False
        return (comp.get(e, {}).get("visibility") or "").strip().lower() not in (
            "hidden", "collapse")

    els = [e for e in svg.iter() if _tag(e) in SHAPES and _visible(e)]
    colours, widths, per_colour, boxes = set(), set(), {}, []
    area_unknown, bbox_unknown, painted_shapes = {}, "", 0
    widths_unknown = False
    for e in els:
        vals = comp.get(e, {})
        raw_fill = vals.get("fill")
        # An undeclared fill is black, not absent: a rect without a fill
        # attribute is a black rect. Reading it as unpainted failed PAINTED and
        # dropped the colour, while every renderer painted black. Spelled as hex
        # because the rules files and the rest of the measurement use hex.
        fill = _colour("#000000" if raw_fill is None else raw_fill)
        if _tag(e) == "line":
            fill = None          # a line has no interior; its fill never paints
        stroke = _resolve(_colour(vals.get("stroke")), vals)
        fill = _resolve(fill, vals)
        if fill and _is_zero(vals.get("fill-opacity")):
            fill = None
        if stroke and _is_zero(vals.get("stroke-opacity")):
            stroke = None
        for c in (fill, stroke):
            if c:
                colours.add(c)
        raw_width = vals.get("stroke-width")
        width = 1.0 if raw_width is None else _len(raw_width)
        moved = _transformed(e, parents)
        fill_area, perimeter = _geom(e)
        # A path and a non-absolute length are both unmeasurable, but for
        # different reasons; say which one instead of reporting "path" for a
        # rect that declared width="50%".
        why = "path" if _tag(e) == "path" else "unit"
        if fill:
            if fill_area is None:
                area_unknown[fill] = why
            else:
                painted = fill_area
                if stroke and perimeter is not None and width is not None:
                    # Half the stroke band lies over the fill, so only the half
                    # outside adds painted area to the fill colour.
                    painted = max(0.0, fill_area - perimeter * width / 2.0)
                per_colour[fill] = per_colour.get(fill, 0.0) + painted
        if stroke:
            if width is None:
                # A declared width the script cannot read (percent, em) must
                # not silently become 1.0 and pass a 2pt rule.
                widths_unknown = True
                area_unknown[stroke] = "stroke-width"
            elif perimeter is None:
                area_unknown[stroke] = why
                widths.add(width)
            else:
                widths.add(width)
                per_colour[stroke] = per_colour.get(stroke, 0.0) + perimeter * width
        if fill or stroke:
            painted_shapes += 1
        if moved:
            bbox_unknown = "transform"
            for c in (fill, stroke):
                if c:
                    area_unknown[c] = "transform"
            continue
        if stroke and width is None:
            bbox_unknown = bbox_unknown or "stroke-width"
            continue
        box = _bbox(e, width or 0.0, bool(stroke))
        if box:
            boxes.append(box)
        else:
            if fill or stroke:
                bbox_unknown = bbox_unknown or "path"
                for c in (fill, stroke):
                    if c:
                        area_unknown[c] = area_unknown.get(c, "path")
    bbox = ([min(b[0] for b in boxes), min(b[1] for b in boxes),
             max(b[2] for b in boxes), max(b[3] for b in boxes)] if boxes else None)
    total = float(canvas[0] * canvas[1])
    forbid = {k: 0 for k in FORBID_TAGS}
    for e in svg.iter():
        for key, tag in FORBID_TAGS.items():
            if isinstance(tag, tuple):
                if e.tag in tag:
                    forbid[key] += 1
            elif e.tag == tag:
                forbid[key] += 1
        for attr, key in FORBID_ATTRS.items():
            if e.get(attr):
                forbid[key] += 1
    return {
        "elements": len(els),
        "painted": painted_shapes,
        "types": sorted({_tag(e) for e in els}),
        "colours": sorted(colours),
        "style_sheets": len(sheets),
        "style_rules": len(rules),
        "style_error": style_error,
        "stroke_widths": sorted(widths),
        "stroke_widths_unmeasured": widths_unknown,
        "area_ratio": {k: round(v / total, 4) for k, v in sorted(per_colour.items())},
        "area_unmeasured": area_unknown,
        "bbox_unmeasured": bbox_unknown,
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

    # A rule the script cannot see is worse than a rule that fails: it reports
    # nothing at all, so the run stays green while the brief is unmet. Reject
    # instead, at usage level, before any measurement is reported.
    unknown = sorted(set(rules) - RULES_KEYS)
    if unknown:
        raise L.ToolError("E-SCHEMA", "unknown rules keys: " + ", ".join(unknown),
                          L.EXIT_USAGE)
    forbid_want = rules.get("forbid") or []
    if isinstance(forbid_want, list):
        bad_forbid = [str(k) for k in forbid_want if k not in FORBID_TAGS]
        if bad_forbid:
            raise L.ToolError(
                "E-SCHEMA", "unknown forbid constructs: " + ", ".join(bad_forbid)
                + " (known: " + ", ".join(sorted(FORBID_TAGS)) + ")", L.EXIT_USAGE)
    span_rules = rules.get("elements")
    if isinstance(span_rules, dict):
        bad_span = sorted(set(span_rules) - {"min", "max"})
        if bad_span:
            raise L.ToolError("E-SCHEMA", "unknown elements keys: " + ", ".join(bad_span),
                              L.EXIT_USAGE)
        for span_key in ("min", "max"):
            if span_key in span_rules:
                v = span_rules[span_key]
                if isinstance(v, bool) or not isinstance(v, int):
                    raise L.ToolError(
                        "E-SCHEMA", "rules.elements.%s must be an integer" % span_key,
                        L.EXIT_USAGE)

    # A malformed number used to raise ValueError while the checks were being
    # built, and the CLI reported the crash as E-INTERNAL (exit 4). A bad rules
    # file is a usage error: name the key and exit 2 before measuring anything.
    def _num_ok(v):
        return not isinstance(v, bool) and isinstance(v, (int, float))

    def _need_num(key, values):
        if not all(_num_ok(v) for v in values):
            raise L.ToolError("E-SCHEMA", "rules.%s must be numeric" % key, L.EXIT_USAGE)

    canvas = rules.get("canvas")
    if canvas is None:
        canvas = [240, 240]
    if not (isinstance(canvas, list) and len(canvas) == 2):
        raise L.ToolError("E-SCHEMA", "rules.canvas must be [width, height]", L.EXIT_USAGE)
    _need_num("canvas", canvas)
    if rules.get("colors_max") is not None:
        cm = rules["colors_max"]
        if isinstance(cm, bool) or not isinstance(cm, int) or cm < 0:
            raise L.ToolError("E-SCHEMA", "rules.colors_max must be a non-negative integer",
                              L.EXIT_USAGE)
    if rules.get("area_ratio_max") is not None:
        if not isinstance(rules["area_ratio_max"], dict):
            raise L.ToolError("E-SCHEMA", "rules.area_ratio_max must be an object",
                              L.EXIT_USAGE)
        _need_num("area_ratio_max", list(rules["area_ratio_max"].values()))
    if rules.get("stroke_widths_allowed") is not None:
        if not isinstance(rules["stroke_widths_allowed"], list):
            raise L.ToolError("E-SCHEMA", "rules.stroke_widths_allowed must be a list",
                              L.EXIT_USAGE)
        _need_num("stroke_widths_allowed", rules["stroke_widths_allowed"])
    if rules.get("bbox_margin_min") is not None:
        _need_num("bbox_margin_min", [rules["bbox_margin_min"]])
    for list_key in ("colors_allowed", "types_allowed"):
        if rules.get(list_key) is not None and not isinstance(rules[list_key], list):
            raise L.ToolError("E-SCHEMA", "rules.%s must be a list" % list_key, L.EXIT_USAGE)

    try:
        root = ET.fromstring(svg_path.read_text(encoding="utf-8"))
    except Exception as e:                      # noqa: BLE001
        raise L.ToolError("E-XML", "SVG unreadable: " + str(e)[:120], L.EXIT_USAGE)

    m = _measure(root, canvas)
    checks = []

    def check(cid, ok, rule, measured):
        checks.append({"id": cid, "ok": bool(ok), "rule": rule, "measured": measured})

    # A <style> sheet is interpreted only inside the stated subset. Inside it,
    # the measurement is the rendered one; outside it, the sheet is refused
    # whole -- a half-applied sheet reports numbers that were never rendered,
    # which is the failure this check exists to prevent.
    if m["style_sheets"]:
        check("STYLE-SHEETS", not m["style_error"],
              "stylesheet inside the supported subset (fill/stroke/stroke-width)",
              m["style_error"] or ("%d sheet(s), %d rule(s) applied"
                                   % (m["style_sheets"], m["style_rules"])))

    # A file where nothing visible is painted has no conformance to report:
    # every rule below would be evaluated against an empty picture. r66 found
    # that a fully transparent document came out green once opacity was read.
    if not m["painted"]:
        check("PAINTED", False, "at least one visible shape", "no visible ink")

    # viewBox allows commas and any whitespace between the four numbers, and a
    # comma form used to collapse into one unparseable token and go red.
    vb = [t for t in re.split(r"[\s,]+", (root.get("viewBox") or "").strip()) if t]
    if vb:
        want_vb = [0.0, 0.0, float(canvas[0]), float(canvas[1])]
        check("CANVAS", [_len(x) for x in vb] == want_vb,
              "viewBox 0 0 %d %d" % (canvas[0], canvas[1]), " ".join(vb))
    else:
        # The rule is written as a viewBox. Without one the canvas cannot be
        # checked as stated, and silence here used to read as "canvas fine".
        check("CANVAS", False, "viewBox 0 0 %d %d" % (canvas[0], canvas[1]), "no viewBox")
    for key in ("width", "height"):
        if root.get(key):
            want = float(canvas[0 if key == "width" else 1])
            check("CANVAS-" + key.upper(), _len(root.get(key)) == want,
                  str(canvas[0 if key == "width" else 1]), root.get(key))

    # Colors compare after normalization, the same one _measure applies, so a
    # #F00 fill matches a #ff0000 rule instead of failing on spelling.
    allowed = {c for c in (_colour(x) for x in rules.get("colors_allowed", [])) if c}
    if allowed:
        extra = sorted(set(m["colours"]) - allowed)
        check("COLOUR-ALLOWED", not extra, "colours within " + ",".join(sorted(allowed)),
              "extra " + ",".join(extra) if extra else "none")
    if rules.get("colors_max") is not None:
        check("COLOUR-COUNT", len(m["colours"]) <= int(rules["colors_max"]),
              "<= %d colours" % int(rules["colors_max"]), str(len(m["colours"])))
    for colour, cap in (rules.get("area_ratio_max") or {}).items():
        key = _colour(colour) or str(colour).strip().lower()
        got = m["area_ratio"].get(key, 0.0)
        why = m["area_unmeasured"].get(key, "")
        if why:
            # A cap on a colour whose coverage this script cannot compute is
            # not a pass. Reporting 0.0 here was the worst false green found in
            # r66: a path covering the whole canvas passed a 1% cap.
            check("AREA-" + colour.lower().replace("#", ""), False,
                  "%s area <= %.4f" % (colour.lower(), float(cap)),
                  "unmeasurable: " + why)
        else:
            check("AREA-" + colour.lower().replace("#", ""), got <= float(cap),
                  "%s area <= %.4f" % (colour.lower(), float(cap)), "%.4f" % got)
    if rules.get("stroke_widths_allowed") is not None:
        want = sorted({float(v) for v in rules["stroke_widths_allowed"]})
        if m["stroke_widths_unmeasured"]:
            # A declared width in % or em used to be read as the SVG default of
            # 1.0, so a 2px stroke measured as 1 and failed a 2pt rule while a
            # 1pt rule passed. Unmeasurable is a failure, not a guess.
            check("STROKE-WIDTHS", False, "stroke widths " + str(want),
                  "unmeasurable: a declared stroke-width is not absolute")
        else:
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
    if rules.get("bbox_margin_min") is not None:
        if m["bbox_unmeasured"]:
            # A transformed or path-only drawing has no trustworthy bounding
            # box here, and the old code quietly measured the untransformed one.
            check("MARGIN", False, "margin >= %s" % rules["bbox_margin_min"],
                  "unmeasurable: " + m["bbox_unmeasured"])
        elif m["margins"]:
            worst = min(m["margins"])
            check("MARGIN", worst >= float(rules["bbox_margin_min"]),
                  "margin >= %s" % rules["bbox_margin_min"],
                  "%s (min %s)" % (m["margins"], worst))
        else:
            # A margin rule on a drawing with nothing in it cannot be verified.
            check("MARGIN", False, "margin >= %s" % rules["bbox_margin_min"],
                  "no drawable geometry")
    for key in rules.get("forbid", []):
        check("FORBID-" + key.upper(), m["forbidden"].get(key, 0) == 0,
              "no " + key, str(m["forbidden"].get(key, 0)))

    # Source ledger: reference material never enters the artwork, but a component
    # that does enter must be traceable to a URL and a licence. Anything carrying
    # data-source in the SVG has to match an output entry, so a pasted-in shape
    # cannot ride along unrecorded.
    if rules.get("source_ledger"):
        lp = Path(rules["source_ledger"])
        if not lp.is_absolute():
            lp = rules_path.parent / lp
        if not lp.is_file():
            raise L.ToolError("E-USAGE", "source ledger not found: " + lp.name, L.EXIT_USAGE)
        try:
            ledger = json.loads(lp.read_text(encoding="utf-8"))
        except Exception as e:                  # noqa: BLE001
            raise L.ToolError("E-SCHEMA", "ledger unreadable: " + str(e)[:120], L.EXIT_USAGE)
        if ledger.get("schema") != "vector-ai-studio/source-ledger-v1":
            raise L.ToolError("E-SCHEMA",
                              "ledger schema must be vector-ai-studio/source-ledger-v1",
                              L.EXIT_USAGE)
        entries = ledger.get("entries") or []
        bad = [str(i) for i, e in enumerate(entries)
               if e.get("channel") not in ("reference", "output") or not e.get("license")
               or not e.get("fetched") or not e.get("use")
               or (e.get("channel") == "output" and e.get("in_output") is not True)]
        check("LEDGER-FIELDS", not bad,
              "each entry has channel/license/fetched/use; output entries set in_output",
              "bad " + ",".join(bad) if bad else "none")
        ids = {e.get("id") for e in root.iter() if e.get("id")}
        out = [e for e in entries if e.get("channel") == "output"]
        missing = [str(e.get("id")) for e in out
                   if e.get("element") and e.get("element") not in ids]
        check("LEDGER-ELEMENT", not missing, "output element id exists in the SVG",
              "missing " + ",".join(missing) if missing else "none")
        allowed_lic = {str(x).lower() for x in rules.get("licenses_allowed", [])}
        if allowed_lic:
            bad_lic = [str(e.get("id")) for e in out
                       if str(e.get("license") or "").lower() not in allowed_lic]
            check("LEDGER-LICENSE", not bad_lic,
                  "output licence within " + ",".join(sorted(allowed_lic)),
                  "bad " + ",".join(bad_lic) if bad_lic else "none")
        marks = sorted({e.get("data-source") for e in root.iter() if e.get("data-source")})
        known = {e.get("url") for e in out if e.get("url")}
        unreg = [m for m in marks if m not in known]
        check("LEDGER-DATASOURCE", not unreg,
              "every data-source element has an output entry",
              "unregistered " + ",".join(unreg) if unreg else "none")

        # A licence name is an assertion; it is only checkable against a source.
        # Recording "cc0" tells a reviewer nothing unless the ledger also says
        # where that claim came from and when a human read it. Aggregators make
        # this load-bearing: the same icon can be tagged CC0 on one site and
        # CC BY-SA at its origin. Own work carries no external licence, so it is
        # exempt rather than forced to invent a URL.
        need = [e for e in out if str(e.get("license") or "").lower() != "own"]
        bad_src = [str(e.get("id")) for e in need
                   if not str(e.get("license_source") or "").strip()]
        check("LICENSE-SOURCE", not bad_src,
              "%d non-own output entries cite a licence URL" % len(need),
              "missing " + ",".join(bad_src) if bad_src else "none")
        bad_ver = []
        for e in need:
            v = str(e.get("license_verified_at") or "").strip()
            try:
                datetime.strptime(v, "%Y-%m-%d")
            except ValueError:
                bad_ver.append(str(e.get("id")))
        # Format only. Expiry needs "today", which would make the same input
        # score differently a year later; that is a human review step, not a gate.
        check("LICENSE-VERIFIED", not bad_ver,
              "non-own entries record licence_verified_at as YYYY-MM-DD",
              "bad " + ",".join(bad_ver) if bad_ver else "none")

    fails = [c["id"] for c in checks if not c["ok"]]
    unchecked = [k for k in ("canvas", "colors_allowed", "colors_max", "area_ratio_max",
                             "stroke_widths_allowed", "types_allowed", "elements",
                             "bbox_margin_min", "forbid", "source_ledger",
                             "licenses_allowed") if k not in rules]
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

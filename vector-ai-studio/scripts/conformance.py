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
FORBID_ATTRS = ("filter", "clip-path", "mask")
# Every key the script understands. A key it does not know used to be ignored
# silently, so a typo ("stroke_width_allowed") looked like an enforced rule and
# an invented forbid name ("texts") produced a check that could never fail.
RULES_KEYS = {"schema", "canvas", "colors_allowed", "colors_max", "area_ratio_max",
              "stroke_widths_allowed", "types_allowed", "elements",
              "bbox_margin_min", "forbid", "source_ledger", "licenses_allowed"}


def _tag(el):
    return el.tag.replace(NS, "")


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _area(el, stroke_width=0.0):
    t = _tag(el)
    if t == "rect":
        return _f(el.get("width")) * _f(el.get("height"))
    if t == "circle":
        return math.pi * _f(el.get("r")) ** 2
    if t == "ellipse":
        return math.pi * _f(el.get("rx")) * _f(el.get("ry"))
    if t == "line":
        return math.hypot(_f(el.get("x2")) - _f(el.get("x1")),
                          _f(el.get("y2")) - _f(el.get("y1"))) * _f(stroke_width, 1.0)
    if t in ("polygon", "polyline"):
        pts = [tuple(map(float, p.split(","))) for p in (el.get("points") or "").split()]
        if len(pts) < 3:
            return 0.0
        s = sum(pts[i][0] * pts[(i + 1) % len(pts)][1] -
                pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts)))
        return abs(s) / 2
    return 0.0


def _bbox(el, expand, has_stroke):
    t = _tag(el)
    pad = expand / 2.0 if has_stroke else 0.0
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


def _colour(val):
    v = (val or "").strip().lower()
    if not v or v in ("none", "transparent") or v.startswith("url("):
        return None
    return v


# ---------------------------------------------------------------- CSS subset
# A <style> sheet is interpreted, but only over a subset small enough to state
# here and to refuse everything else: type / class / id compounds, descendant
# and child combinators, comma groups, and the declarations below. A sheet that
# uses anything outside the subset fails the run instead of being half applied
# -- a half-applied sheet reports numbers that were never rendered.
CSS_PROPS = ("fill", "stroke", "stroke-width", "color", "visibility",
             "fill-opacity", "stroke-opacity")
CSS_DECLS_ALLOWED = {"fill", "stroke", "stroke-width", "stroke-linecap",
                     "stroke-linejoin", "stroke-miterlimit", "fill-rule"}
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
    try:
        return float(str(val).strip()) == 0.0
    except (TypeError, ValueError):
        return False


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


def _area_state(el, width):
    """Painted area, or the reason it cannot be measured."""
    if _tag(el) == "path":
        return 0.0, "path"
    return _area(el, width), ""


def _inline(el):
    """style="" as {property: (value, important)}."""
    out = {}
    for part in (el.get("style") or "").split(";"):
        if ":" not in part:
            continue
        k, _, v = part.partition(":")
        val = v.strip()
        imp = False
        if val.lower().endswith("!important"):
            imp = True
            val = val[:-len("!important")].strip()
        out[k.strip().lower()] = (val, imp)
    return out


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
    area_unknown, bbox_unknown = {}, ""
    for e in els:
        vals = comp.get(e, {})
        fill, stroke = _colour(vals.get("fill")), _colour(vals.get("stroke"))
        fill, stroke = _resolve(fill, vals), _resolve(stroke, vals)
        if fill and _is_zero(vals.get("fill-opacity")):
            fill = None
        if stroke and _is_zero(vals.get("stroke-opacity")):
            stroke = None
        for c in (fill, stroke):
            if c:
                colours.add(c)
        width = _f(vals.get("stroke-width"), 1.0)
        if stroke:
            widths.add(width)
        owner = fill or stroke
        moved = _transformed(e, parents)
        if owner:
            amount, why = _area_state(e, width)
            per_colour[owner] = per_colour.get(owner, 0.0) + amount
            if why:
                area_unknown[owner] = why
            if moved:
                area_unknown[owner] = "transform"
        if moved:
            bbox_unknown = "transform"
            continue
        box = _bbox(e, width, bool(stroke))
        if box:
            boxes.append(box)
        elif owner:
            area_unknown[owner] = "path"
            bbox_unknown = bbox_unknown or "path"
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
        for attr in FORBID_ATTRS:
            if e.get(attr):
                forbid["filter" if attr == "filter" else attr] += 1
    return {
        "elements": len(els),
        "types": sorted({_tag(e) for e in els}),
        "colours": sorted(colours),
        "style_sheets": len(sheets),
        "style_rules": len(rules),
        "style_error": style_error,
        "stroke_widths": sorted(widths),
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

    canvas = rules.get("canvas") or [240, 240]
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

    vb = (root.get("viewBox") or "").split()
    if vb:
        check("CANVAS", [round(_f(x), 2) for x in vb] == [0.0, 0.0, float(canvas[0]), float(canvas[1])],
              "viewBox 0 0 %d %d" % (canvas[0], canvas[1]), " ".join(vb))
    else:
        # The rule is written as a viewBox. Without one the canvas cannot be
        # checked as stated, and silence here used to read as "canvas fine".
        check("CANVAS", False, "viewBox 0 0 %d %d" % (canvas[0], canvas[1]), "no viewBox")
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
        why = m["area_unmeasured"].get(colour.lower(), "")
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

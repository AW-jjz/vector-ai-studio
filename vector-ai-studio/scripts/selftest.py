#!/usr/bin/env python3
"""Layered L1: pure-script selftest. Zero external deps, zero credentials, zero
Adobe, zero renderer, zero network - MUST be runnable after install.

All sub-script calls use ABSOLUTE script paths while cwd is a temp dir, which
proves cwd independence. Real-machine checks live only in smoke_ai.py (L2).
"""

import argparse
import importlib.util
import os
import json
import re
import shutil
import subprocess
import zlib
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L

SCRIPT = "selftest.py"
SCRIPTS = Path(__file__).resolve().parent
ROOT = L.skill_root()
FIX = L.fixtures_dir()
GOOD = FIX / "good"
BAD = FIX / "bad"
# The rect-only fixture: the builtin fallback renders it faithfully, so the
# graded assertions can run without a real renderer.
RECT = GOOD / "rect-only.svg"

INLINE_FEATURES = ["def ", "param(", "import json", "import lib_common",
                   "#!/usr/bin/env python"]
THRESHOLD_LITERALS = ["0.15", "0.25", "0.45", "0.6667", "0.70", "0.20", "0.10", "0.8", "0.6"]
CORE_SCRIPTS = ["score.py", "batch_check.py", "iterate.py", "aggregate_log.py"]
O3_BANNED = ["优秀", "Excellent"]
CLAIM_BANNED = ["达标", "优秀", "完美", "Excellent"]

# Single source of truth (r70): both of these now live in lib_common.py so
# aggregate_log.py (the H3 judge) and selftest.py (the H3 evidence writer)
# share ONE policy instead of each keeping a copy. The local names are kept
# so every existing assertion / A76 / A60 reference stays untouched.
# See L.EXPECTED_ASSERTIONS / L.CAPABILITY_SKIPS for the bump rule and the
# registration condition; add or remove an assertion -> bump L's number.
EXPECTED_ASSERTIONS = L.EXPECTED_ASSERTIONS
CAPABILITY_SKIPS = L.CAPABILITY_SKIPS


def _call(name, args, cwd, flags=()):
    cmd = ([sys.executable] + list(flags) + [str(SCRIPTS / name)]
           + [str(a) for a in args])
    try:
        r = subprocess.run(cmd, cwd=str(cwd), timeout=90, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", check=False)
    except (OSError, subprocess.SubprocessError):
        return -1, {}, ""
    try:
        env = json.loads(r.stdout or "")
    except json.JSONDecodeError:
        env = {}
    return r.returncode, env, (r.stdout or "")


def _codes(env):
    return [e.get("code", "") for e in env.get("errors", []) if isinstance(e, dict)]


def _chain(work, cwd, svg=None):
    """Run the pure-script chain on `svg` (default: the circle-bearing sample).

    rect-only.svg -> builtin fallback is judged "faithful" -> non-L2, graded run.
    sample.svg    -> circles are rasterised as squares -> "limited" -> auto L2.
    """
    work.mkdir(parents=True, exist_ok=True)
    src_svg = Path(svg) if svg else (GOOD / "sample.svg")
    steps = [
        ("freeze_spec.py", ["--spec", str(GOOD / "spec.json"), "--validate",
                            "--out", str(work / "spec.json")], {0}),
        ("sanitize_svg.py", ["--in", str(src_svg),
                             "--out", str(work / "final.svg")], {0}),
        ("render_png.py", ["--in", str(work / "final.svg"),
                           "--out", str(work / "preview.png"), "--force-builtin"], {0}),
        ("llm_judge.py", ["--judge", str(GOOD / "judge.json"), "--validate",
                          "--spec", str(work / "spec.json"),
                          "--out", str(work / "judge.json")], {0}),
        ("score.py", ["--svg", str(work / "final.svg"), "--judge", str(work / "judge.json"),
                      "--spec", str(work / "spec.json"),
                      "--out", str(work / "score.json")], {0, 1}),
    ]
    info = []
    ok = True
    for name, args, want in steps:
        rc, env, _ = _call(name, args, cwd)
        good = (rc in want and env.get("schema") == L.SCHEMA and
                isinstance(env.get("code"), int) and env.get("code") == rc and
                isinstance(env.get("errors"), list) and isinstance(env.get("data"), dict))
        info.append({"script": name, "exit": rc, "status": env.get("status"), "ok": good})
        ok = ok and good
        if not good:
            # A failed step invalidates every later step. Stop here instead of
            # letting a downstream script crash on a missing artifact: a missing
            # input must turn this chain (and its assertions) red, never raise
            # E-INTERNAL and abort the whole selftest.
            break

    # preview.meta.json is left exactly as render_png.py wrote it. Its `fidelity`
    # field is computed by the rasteriser itself (rect-only + solid fill + no
    # stroke => "faithful", otherwise "limited"); never hand-write it here, that
    # would be fabricating preview evidence. A circle-bearing sample is therefore
    # "limited" and correctly flips to L2.
    # P2-1: pass a content-derived run-id so acceptance.run_id is never empty.
    # Same fixture text + version => same id, so the idempotency check holds.
    spec_text = ((work / "spec.json").read_text(encoding="utf-8")
                 if (work / "spec.json").is_file() else "")
    rid = L.sha_run_id("chain", spec_text, L.load_thresholds()["version"])
    rc, env, _ = _call("aggregate_log.py",
                       ["--run-dir", str(work), "--out", str(work / "acceptance.json"),
                        "--run-id", rid], cwd)
    good = (rc in {0, 1} and env.get("schema") == L.SCHEMA and
            isinstance(env.get("code"), int) and env.get("code") == rc and
            isinstance(env.get("errors"), list) and isinstance(env.get("data"), dict))
    info.append({"script": "aggregate_log.py", "exit": rc,
                 "status": env.get("status"), "ok": good})
    ok = ok and good
    return ok, info


def _jread(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _landing(where):
    """landing.json for `where`, or {} when the run wrote none."""
    return _jread(Path(where) / "landing.json")


def _copy_if(src, dst):
    """Copy only when the source exists; a missing input must never raise."""
    if Path(src).is_file():
        shutil.copyfile(src, dst)
        return True
    return False


def _type_ok(val, spec):
    t = spec.get("type")
    if t is None:
        return True
    for n in ([t] if isinstance(t, str) else list(t)):
        if n == "object" and isinstance(val, dict): return True
        if n == "array" and isinstance(val, list): return True
        if n == "string" and isinstance(val, str): return True
        if n == "boolean" and isinstance(val, bool): return True
        if n == "integer" and isinstance(val, int) and not isinstance(val, bool): return True
        if n == "number" and isinstance(val, (int, float)) and not isinstance(val, bool): return True
        if n == "null" and val is None: return True
    return False


# Dotted paths whose sub-objects are contract too. `observed.content_ops` sat
# outside this list for a round: the top-level shape matched while six of its
# ten keys were undeclared, and nothing was red.
NESTED_PATHS = ("save_options", "observed", "structure", "source_probe",
                "observed.content_ops", "observed.content_streams",
                "observed.print_pdf", "observed.print_pdf.content_ops",
                "observed.print_pdf.content_streams")

# Declared once, written by one engine only. Declaring them is right -- they
# exist in a real manifest -- so the other engine's diff must not call them
# missing.
CRAFT_ONLY_KEYS = ("h1_pass",)


def _schema_props(schema, dotted):
    node = schema
    for part in dotted.split("."):
        node = (node.get("properties") or {}).get(part) or {}
    return node.get("properties") or {}


def _schema_diff(doc, schema, check_missing=True, optional=()):
    """Payload vs declaration, in both directions.

    check_missing=False for an engine whose manifest legitimately omits fields
    another engine owns; `optional` names the individual keys that are declared
    once and written by only one engine (vectorcraft's h1_pass). Undeclared keys
    are always checked: "the code emits something the contract does not
    describe" has to be red on every engine, not only on whichever one happened
    to be in the diff.
    """
    props = schema.get("properties") or {}
    extra = sorted(set(doc) - set(props))
    missing = (sorted(k for k in props if k not in doc and k not in optional)
               if check_missing else [])
    typebad = sorted(k for k in props if k in doc and not _type_ok(doc[k], props[k]))
    nested = {}
    for path in NESTED_PATHS:
        sub = _schema_props(schema, path)
        if not sub:
            continue
        val = doc
        for part in path.split("."):
            val = val.get(part) if isinstance(val, dict) else None
        if val is None:
            continue
        if not isinstance(val, dict):
            nested[path] = "not-an-object"
            continue
        nx = sorted(set(val) - set(sub))
        if nx:
            nested[path] = {"extra": nx}
    return extra, missing, typebad, nested


def _check_conformance(tmp, add):
    """A56, A59, A61-A75: the mechanical half of brief conformance.

    Extracted from main() so this block can be read and changed without
    scrolling past two thousand lines of unrelated assertions. It owns no
    state beyond the temp dir and the assertion sink. A54 and A55 are NOT in
    here -- r60 said they were, which was wrong: they live in
    _check_failed_dimension and _check_empty_review_sheet, and only their
    execution order (A54, A55, then this) puts them next to each other.
    A68-A70 are the r67 additions: stroke ink, a picture with no ink, and
    the three shipped examples. A72-A75 are the r69 additions: unmeasurable
    lengths, colour identity and default fill, SVG syntax variants, and
    malformed rule numbers.
    """
    # ---- A56: brief conformance is checked, and unchecked rules stay visible --
    # The review sheet cannot judge need-fit (no brief is shown to reviewers), so
    # the mechanical half is settled by script. Two things must hold: a colour
    # outside the allowed set turns the run red with E-CONFORMANCE, and a rule
    # key that is absent is reported in `unchecked` instead of looking passed.
    try:
        _d56 = tmp / "a56"
        _d56.mkdir()
        _rules56 = _d56 / "rules.json"
        _rules56.write_text(json.dumps({
            "schema": "vector-ai-studio/conformance-v1",
            "canvas": [240, 240],
            "colors_allowed": ["#111111"],
            "colors_max": 1,
            "stroke_widths_allowed": [10],
            "types_allowed": ["circle", "rect"],
            "elements": {"min": 2, "max": 2},
            "bbox_margin_min": 10,
            "forbid": ["image", "gradient", "text"]}), encoding="utf-8")
        _rules56b = _d56 / "rules-partial.json"
        _rules56b.write_text(json.dumps({
            "schema": "vector-ai-studio/conformance-v1",
            "colors_allowed": ["#111111"]}), encoding="utf-8")
        _rules56c = _d56 / "rules-bad.json"
        _rules56c.write_text(json.dumps({"schema": "not-ours"}), encoding="utf-8")
        _svg56 = _d56 / "good.svg"
        _svg56.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 240">'
            '<circle cx="120" cy="120" r="82" fill="none" stroke="#111111" stroke-width="10"/>'
            '<rect x="105" y="130" width="30" height="30" fill="#111111"/></svg>',
            encoding="utf-8")
        _svg56b = _d56 / "extra-colour.svg"
        _svg56b.write_text(
            _svg56.read_text(encoding="utf-8").replace(
                "</svg>", '<rect x="0" y="0" width="8" height="8" fill="#c8362c"/></svg>'),
            encoding="utf-8")
        _rc56g, _env56g, _ = _call("conformance.py", ["--svg", str(_svg56),
                                                      "--rules", str(_rules56),
                                                      "--json"], tmp)
        _rc56b, _env56b, _ = _call("conformance.py", ["--svg", str(_svg56b),
                                                      "--rules", str(_rules56),
                                                      "--json"], tmp)
        _rc56p, _env56p, _ = _call("conformance.py", ["--svg", str(_svg56),
                                                      "--rules", str(_rules56b),
                                                      "--json"], tmp)
        _rc56s, _env56s, _ = _call("conformance.py", ["--svg", str(_svg56),
                                                      "--rules", str(_rules56c),
                                                      "--json"], tmp)
        _chk56g = (_env56g.get("data") or {}).get("checks") or []
        _unch56 = (_env56p.get("data") or {}).get("unchecked") or []
        _ids56b = [c.get("id") for c in (_env56b.get("data") or {}).get("checks") or []
                   if not c.get("ok")]
        add("A56-conformance-checks-brief-and-reports-unchecked",
            bool(_rc56g == 0 and _chk56g and all(c.get("ok") for c in _chk56g)
                 and _rc56b == 1 and "E-CONFORMANCE" in _codes(_env56b)
                 and "COLOUR-ALLOWED" in _ids56b
                 and _rc56p == 0 and "canvas" in _unch56
                 and _rc56s == 2 and "E-SCHEMA" in _codes(_env56s)),
            "good_rc=" + str(_rc56g) + " bad_rc=" + str(_rc56b)
            + " bad_ids=" + str(_ids56b) + " unchecked=" + str(_unch56)
            + " schema_rc=" + str(_rc56s))
    except Exception as e:                      # noqa: BLE001
        add("A56-conformance-checks-brief-and-reports-unchecked", False,
            type(e).__name__ + ": " + str(e)[:140])

    # ---- A59: presentation attributes are inherited, and "none" ends it -------
    # Icon libraries write stroke and stroke-width once on the root <svg>. Read
    # per element, such a file measured "no stroke, no width" and every stroke
    # rule went red on legal artwork. Inheriting is correct; inheriting through
    # an explicit stroke="none" is not, so both directions are asserted.
    try:
        _d59 = tmp / "a59"
        _d59.mkdir()
        _rules59 = _d59 / "rules.json"
        _rules59.write_text(json.dumps({
            "schema": "vector-ai-studio/conformance-v1",
            "colors_allowed": ["#111111"],
            "stroke_widths_allowed": [10],
            "types_allowed": ["circle", "rect"],
            "elements": {"min": 2, "max": 2},
            "bbox_margin_min": 10}), encoding="utf-8")
        _svg59 = _d59 / "inherited.svg"
        _svg59.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 240"'
            ' stroke="#111111" stroke-width="10" fill="none">'
            '<circle cx="120" cy="120" r="82"/>'
            '<rect x="105" y="130" width="30" height="30" fill="#111111"'
            ' stroke="none"/></svg>', encoding="utf-8")
        _svg59b = _d59 / "stroke-none.svg"
        _svg59b.write_text(
            _svg59.read_text(encoding="utf-8").replace(
                '<circle cx="120" cy="120" r="82"/>',
                '<circle cx="120" cy="120" r="82" stroke="none"/>'),
            encoding="utf-8")
        _rc59, _env59, _ = _call("conformance.py", ["--svg", str(_svg59),
                                                    "--rules", str(_rules59),
                                                    "--json"], tmp)
        _rc59b, _env59b, _ = _call("conformance.py", ["--svg", str(_svg59b),
                                                      "--rules", str(_rules59),
                                                      "--json"], tmp)
        _chk59 = {c.get("id"): c for c in (_env59.get("data") or {}).get("checks") or []}
        _ids59b = [c.get("id") for c in (_env59b.get("data") or {}).get("checks") or []
                   if not c.get("ok")]
        add("A59-inherited-presentation-attrs",
            bool(_rc59 == 0 and _chk59 and all(c.get("ok") for c in _chk59.values())
                 and _chk59.get("STROKE-WIDTHS", {}).get("measured") == str([10.0])
                 and _rc59b == 1 and "STROKE-WIDTHS" in _ids59b),
            "rc=" + str(_rc59) + " widths="
            + str(_chk59.get("STROKE-WIDTHS", {}).get("measured"))
            + " none_rc=" + str(_rc59b) + " none_ids=" + str(_ids59b))
    except Exception as e:                      # noqa: BLE001
        add("A59-inherited-presentation-attrs", False,
            type(e).__name__ + ": " + str(e)[:140])

    # ---- A61: inline style is read, outranks the attribute, and inherits -----
    # A59 covered attributes only. Real icon sets ship style="stroke-width:2",
    # and reading that as "no width" is the same false red A59 closed, one
    # syntax further along. Three cases, each of which fails if prop() stops
    # looking at style: style on the element, style on the root (inherited),
    # and style overriding a conflicting attribute on the same element plus
    # stroke:none in style ending the inheritance.
    try:
        _d61 = tmp / "a61"
        _d61.mkdir()
        _rules61 = _d61 / "rules.json"
        _rules61.write_text(json.dumps({
            "schema": "vector-ai-studio/conformance-v1",
            "colors_allowed": ["#111111"],
            "stroke_widths_allowed": [10],
            "types_allowed": ["rect"],
            "elements": {"min": 1, "max": 1}}), encoding="utf-8")
        _base61 = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 240"{root}>'
                   '<rect x="20" y="20" width="200" height="200"{el}/></svg>')
        _svg61a = _d61 / "style-on-element.svg"
        _svg61a.write_text(_base61.format(
            root="", el=' style="fill:#111111;stroke:#111111;stroke-width:10"'),
            encoding="utf-8")
        _svg61b = _d61 / "style-inherited.svg"
        _svg61b.write_text(_base61.format(
            root=' style="stroke:#111111;stroke-width:10" fill="none"',
            el=""), encoding="utf-8")
        _svg61c = _d61 / "style-wins-and-none.svg"
        _svg61c.write_text(_base61.format(
            root="", el=' fill="#111111" stroke="#111111" stroke-width="2"'
                        ' style="stroke-width:10;stroke:none"'), encoding="utf-8")

        def _run61(p):
            rc, env, _ = _call("conformance.py", ["--svg", str(p), "--rules",
                                                  str(_rules61), "--json"], tmp)
            chk = {c.get("id"): c for c in (env.get("data") or {}).get("checks") or []}
            bad = [c.get("id") for c in (env.get("data") or {}).get("checks") or []
                   if not c.get("ok")]
            return rc, chk, bad

        _rc61a, _chk61a, _bad61a = _run61(_svg61a)
        _rc61b, _chk61b, _bad61b = _run61(_svg61b)
        _rc61c, _chk61c, _bad61c = _run61(_svg61c)
        add("A61-inline-style-is-read-and-inherited",
            bool(_rc61a == 0 and not _bad61a
                 and _chk61a.get("STROKE-WIDTHS", {}).get("measured") == str([10.0])
                 and _rc61b == 0 and not _bad61b
                 and _chk61b.get("STROKE-WIDTHS", {}).get("measured") == str([10.0])
                 and _rc61c == 1 and _bad61c == ["STROKE-WIDTHS"]),
            "element_rc=" + str(_rc61a) + " inherited_rc=" + str(_rc61b)
            + " widths=" + str(_chk61b.get("STROKE-WIDTHS", {}).get("measured"))
            + " none_rc=" + str(_rc61c) + " none_bad=" + str(_bad61c))
    except Exception as e:                      # noqa: BLE001
        add("A61-inline-style-is-read-and-inherited", False,
            type(e).__name__ + ": " + str(e)[:140])

    # ---- A62: a <style> sheet is applied, or refused whole -------------------
    # r63 made any sheet a red flag; r65 interprets the stated subset instead.
    # Four files: one sheet that must be applied (10 measured, not the 2 in the
    # attribute), the same sheet against a 2-only rule (sheet wins over the
    # attribute, so STROKE-WIDTHS fails at 10), a pseudo-class selector outside
    # the subset (refused whole, widths stay 2 -- no partial application), and
    # two sheets whose rule count must be both counted and applied.
    try:
        _d62 = tmp / "a62"
        _d62.mkdir()
        _rules62 = _d62 / "rules-10.json"
        _rules62.write_text(json.dumps({
            "schema": "vector-ai-studio/conformance-v1",
            "colors_allowed": ["#111111"],
            "stroke_widths_allowed": [10],
            "types_allowed": ["rect"],
            "elements": {"min": 1, "max": 1}}), encoding="utf-8")
        _rules62b = _d62 / "rules-2.json"
        _rules62b.write_text(json.dumps({
            "schema": "vector-ai-studio/conformance-v1",
            "stroke_widths_allowed": [2]}), encoding="utf-8")
        _head62 = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 240">'
        _rect62 = ('<rect class="k" x="20" y="20" width="200" height="200"'
                   ' fill="#111111" stroke="#111111" stroke-width="2"/>')
        _sheet62 = '<style>.k{stroke-width:10}</style>'

        def _run62(name, body, rules=None):
            p = _d62 / (name + ".svg")
            p.write_text(_head62 + body + "</svg>", encoding="utf-8")
            rc, env, _ = _call("conformance.py", ["--svg", str(p), "--rules",
                                                  str(rules or _rules62), "--json"], tmp)
            dat = env.get("data") or {}
            chk = {c.get("id"): c for c in dat.get("checks") or []}
            bad = [c.get("id") for c in dat.get("checks") or [] if not c.get("ok")]
            return rc, dat.get("measured") or {}, chk, bad

        _rc62a, _m62a, _chk62a, _bad62a = _run62("applied", _sheet62 + _rect62)
        _rc62b, _m62b, _ch62b, _bad62b = _run62("beats-attribute",
                                                _sheet62 + _rect62, _rules62b)
        _rc62c, _m62c, _ch62c, _bad62c = _run62(
            "pseudo", '<style>.k:hover{stroke-width:10}</style>' + _rect62, _rules62b)
        _rc62d, _m62d, _ch62d, _bad62d = _run62(
            "two-sheets", _sheet62 + '<style>rect{stroke-width:10}</style>' + _rect62)
        add("A62-style-sheets-are-applied-or-refused",
            bool(_rc62a == 0 and not _bad62a and _m62a.get("style_sheets") == 1
                 and _m62a.get("style_rules") == 1
                 and _m62a.get("stroke_widths") == [10.0]
                 and _chk62a.get("STYLE-SHEETS", {}).get("ok") is True
                 and _rc62b == 1 and _bad62b == ["STROKE-WIDTHS"]
                 and _m62b.get("stroke_widths") == [10.0]
                 and _rc62c == 1 and _bad62c == ["STYLE-SHEETS"]
                 and _m62c.get("stroke_widths") == [2.0]
                 and _rc62d == 0 and not _bad62d
                 and _m62d.get("style_sheets") == 2
                 and _m62d.get("style_rules") == 2
                 and _m62d.get("stroke_widths") == [10.0]),
            "applied_rc=" + str(_rc62a) + " widths=" + str(_m62a.get("stroke_widths"))
            + " rules=" + str(_m62a.get("style_rules"))
            + " attr_rc=" + str(_rc62b) + " attr_bad=" + str(_bad62b)
            + " pseudo_rc=" + str(_rc62c) + " pseudo_bad=" + str(_bad62c)
            + " pseudo_widths=" + str(_m62c.get("stroke_widths"))
            + " two_sheets=" + str(_m62d.get("style_sheets"))
            + " two_rules=" + str(_m62d.get("style_rules")))
    except Exception as e:                      # noqa: BLE001
        add("A62-style-sheets-are-applied-or-refused", False,
            type(e).__name__ + ": " + str(e)[:140])

    # ---- A63: a rule the script cannot read is an error, not a pass ----------
    # r63 closed the stylesheet-shaped version of this: input the script cannot
    # interpret must not come out green. Three more shapes of the same defect
    # were still there: a misspelled rule key (silently ignored), an invented
    # forbid name (produced a check that can never fail), and two rules that
    # were skipped when the drawing or canvas gave them nothing to measure.
    try:
        _d63 = tmp / "a63"
        _d63.mkdir()
        _svg63 = _d63 / "one-rect.svg"
        _svg63.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 240">'
            '<rect x="20" y="20" width="200" height="200" fill="#111111"/></svg>',
            encoding="utf-8")
        _svg63b = _d63 / "empty.svg"
        _svg63b.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 240"></svg>',
            encoding="utf-8")
        _svg63c = _d63 / "no-canvas.svg"
        _svg63c.write_text('<svg xmlns="http://www.w3.org/2000/svg"></svg>',
                           encoding="utf-8")

        def _rules63(name, extra):
            p = _d63 / (name + ".json")
            doc = {"schema": "vector-ai-studio/conformance-v1"}
            doc.update(extra)
            p.write_text(json.dumps(doc), encoding="utf-8")
            return p

        def _run63(svg, rules):
            rc, env, _ = _call("conformance.py", ["--svg", str(svg), "--rules",
                                                  str(rules), "--json"], tmp)
            dat = env.get("data") or {}
            bad = [c.get("id") for c in dat.get("checks") or [] if not c.get("ok")]
            return rc, _codes(env), bad

        _rc63a, _cd63a, _ = _run63(
            _svg63, _rules63("typo-key", {"stroke_width_allowed": [10]}))
        _rc63b, _cd63b, _ = _run63(
            _svg63, _rules63("typo-forbid", {"forbid": ["texts"]}))
        _rc63c, _cd63c, _ = _run63(
            _svg63, _rules63("typo-elements", {"elements": {"min": 1, "max": 1, "exactly": 1}}))
        _rc63d, _cd63d, _bad63d = _run63(
            _svg63b, _rules63("margin", {"canvas": [240, 240], "bbox_margin_min": 10}))
        _rc63e, _cd63e, _bad63e = _run63(
            _svg63c, _rules63("canvas", {"canvas": [240, 240]}))
        add("A63-unreadable-rules-fail-closed",
            bool(_rc63a == 2 and "E-SCHEMA" in _cd63a
                 and _rc63b == 2 and "E-SCHEMA" in _cd63b
                 and _rc63c == 2 and "E-SCHEMA" in _cd63c
                 and _rc63d == 1 and _bad63d == ["PAINTED", "MARGIN"]
                 and _rc63e == 1 and _bad63e == ["PAINTED", "CANVAS"]),
            "typo_key=" + str(_rc63a) + " typo_forbid=" + str(_rc63b)
            + " typo_span=" + str(_rc63c) + " empty_bad=" + str(_bad63d)
            + " nocanvas_bad=" + str(_bad63e))
    except Exception as e:                      # noqa: BLE001
        add("A63-unreadable-rules-fail-closed", False,
            type(e).__name__ + ": " + str(e)[:140])

    # ---- A64: the sheet follows the cascade, not just the first match --------
    # Specificity (#id > .class > type), !important over inline, and the two
    # combinators. Each case would pass if the implementation simply took the
    # last matching declaration, so the four together pin the ordering.
    try:
        _d64 = tmp / "a64"
        _d64.mkdir()
        _rules64 = _d64 / "rules.json"
        _rules64.write_text(json.dumps({"schema": "vector-ai-studio/conformance-v1"}),
                            encoding="utf-8")
        _head64 = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 240">'
        _rect64 = ('<rect id="i" class="k" x="20" y="20" width="200" height="200"'
                   ' fill="#111111" stroke="#111111" stroke-width="2"/>')

        def _run64(name, body):
            p = _d64 / (name + ".svg")
            p.write_text(_head64 + body + "</svg>", encoding="utf-8")
            rc, env, _ = _call("conformance.py", ["--svg", str(p), "--rules",
                                                  str(_rules64), "--json"], tmp)
            dat = env.get("data") or {}
            return rc, (dat.get("measured") or {}).get("stroke_widths")

        _rc64a, _w64a = _run64(
            "specificity",
            "<style>rect{stroke-width:3} .k{stroke-width:7} #i{stroke-width:5}</style>"
            + _rect64)
        _rc64b, _w64b = _run64(
            "important",
            "<style>.k{stroke-width:5 !important}</style>"
            + _rect64.replace('stroke-width="2"', 'stroke-width="2" style="stroke-width:2"'))
        _rc64c, _w64c = _run64(
            "child",
            "<style>g > rect{stroke-width:9}</style><g><a>" + _rect64 + "</a></g>")
        _rc64d, _w64d = _run64(
            "descendant",
            "<style>g rect{stroke-width:9}</style><g><a>" + _rect64 + "</a></g>")
        add("A64-style-cascade-follows-specificity",
            bool(_rc64a == 0 and _w64a == [5.0]
                 and _rc64b == 0 and _w64b == [5.0]
                 and _rc64c == 0 and _w64c == [2.0]
                 and _rc64d == 0 and _w64d == [9.0]),
            "spec=" + str(_w64a) + " important=" + str(_w64b)
            + " child=" + str(_w64c) + " descendant=" + str(_w64d))
    except Exception as e:                      # noqa: BLE001
        add("A64-style-cascade-follows-specificity", False,
            type(e).__name__ + ": " + str(e)[:140])

    # ---- A65: what does not render is not measured ---------------------------
    # Definitions, display:none subtrees, visibility:hidden, opacity:0 and
    # fill-opacity:0 were all counted as painted content, inflating the colour
    # set, the element count and the colour area. Each is excluded now, and a
    # control run proves the exclusion is per-construct rather than a blanket.
    try:
        _d65 = tmp / "a65"
        _d65.mkdir()
        _rules65 = _d65 / "rules.json"
        _rules65.write_text(json.dumps({
            "schema": "vector-ai-studio/conformance-v1",
            "canvas": [240, 240],
            "colors_allowed": ["#111111"],
            "area_ratio_max": {"#111111": 0.1},
            "elements": {"min": 1, "max": 2}}), encoding="utf-8")
        _head65 = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 240">'
        _hidden65 = (
            '<defs><rect width="10" height="10" fill="#c8362c"/></defs>'
            '<g display="none"><rect x="0" y="0" width="20" height="20" fill="#c8362c"/></g>'
            '<g visibility="hidden"><rect x="20" y="0" width="20" height="20" fill="#c8362c"/></g>'
            '<g opacity="0"><rect x="40" y="0" width="20" height="20" fill="#c8362c"/></g>'
            '<rect x="0" y="0" width="240" height="240" fill="#111111" fill-opacity="0"/>')
        _shown65 = ('<rect x="100" y="100" width="40" height="40" fill="#111111"/>')

        def _run65(name, body):
            p = _d65 / (name + ".svg")
            p.write_text(_head65 + body + "</svg>", encoding="utf-8")
            rc, env, _ = _call("conformance.py", ["--svg", str(p), "--rules",
                                                  str(_rules65), "--json"], tmp)
            dat = env.get("data") or {}
            m = dat.get("measured") or {}
            bad = [c.get("id") for c in dat.get("checks") or [] if not c.get("ok")]
            return rc, m, bad

        _rc65a, _m65a, _bad65a = _run65("hidden", _hidden65 + _shown65)
        _rc65b, _m65b, _bad65b = _run65(
            "shown", _hidden65.replace('<g display="none">', '<g>') + _shown65)
        add("A65-unrendered-content-is-not-measured",
            bool(_rc65a == 0 and not _bad65a and _m65a.get("colours") == ["#111111"]
                 and _m65a.get("elements") == 2
                 and _rc65b == 1 and "COLOUR-ALLOWED" in _bad65b),
            "hidden_rc=" + str(_rc65a) + " colours=" + str(_m65a.get("colours"))
            + " elements=" + str(_m65a.get("elements"))
            + " shown_rc=" + str(_rc65b) + " shown_bad=" + str(_bad65b))
    except Exception as e:                      # noqa: BLE001
        add("A65-unrendered-content-is-not-measured", False,
            type(e).__name__ + ": " + str(e)[:140])

    # ---- A66: geometry the script cannot compute fails the rule --------------
    # A path contributed 0.0 to area_ratio, so a full-canvas path passed a 1%
    # cap; a translate() was ignored, so a rectangle moved off the canvas kept
    # a healthy margin. Both now fail with "unmeasurable", while a path or a
    # plain rectangle with no such rule stays green (no blanket ban).
    try:
        _d66 = tmp / "a66"
        _d66.mkdir()
        _head66 = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 240">'

        def _rules66(name, extra):
            p = _d66 / (name + ".json")
            doc = {"schema": "vector-ai-studio/conformance-v1", "canvas": [240, 240]}
            doc.update(extra)
            p.write_text(json.dumps(doc), encoding="utf-8")
            return p

        def _run66(name, body, rules):
            p = _d66 / (name + ".svg")
            p.write_text(_head66 + body + "</svg>", encoding="utf-8")
            rc, env, _ = _call("conformance.py", ["--svg", str(p), "--rules",
                                                  str(rules), "--json"], tmp)
            dat = env.get("data") or {}
            m = dat.get("measured") or {}
            bad = [c.get("id") for c in dat.get("checks") or [] if not c.get("ok")]
            return rc, m, bad

        _path66 = '<path d="M0 0H240V240H0Z" fill="#111111"/>'
        _moved66 = ('<rect x="10" y="10" width="20" height="20" fill="#111111"'
                    ' transform="translate(-30,-30)"/>')
        _still66 = '<rect x="10" y="10" width="20" height="20" fill="#111111"/>'
        _r66a = _rules66("area", {"area_ratio_max": {"#111111": 0.01}})
        _r66b = _rules66("margin", {"bbox_margin_min": 5})
        _r66c = _rules66("plain", {})
        _rc66a, _m66a, _bad66a = _run66("path-area", _path66, _r66a)
        _rc66b, _m66b, _bad66b = _run66("path-plain", _path66, _r66c)
        _rc66c, _m66c, _bad66c = _run66("moved", _moved66, _r66b)
        _rc66d, _m66d, _bad66d = _run66("still", _still66, _r66b)
        add("A66-unmeasurable-geometry-fails-the-rule",
            bool(_rc66a == 1 and _bad66a == ["AREA-111111"]
                 and _m66a.get("area_unmeasured", {}).get("#111111") == "path"
                 and _rc66b == 0 and not _bad66b
                 and _rc66c == 1 and _bad66c == ["MARGIN"]
                 and _m66c.get("bbox_unmeasured") == "transform"
                 and _rc66d == 0 and not _bad66d),
            "path_rc=" + str(_rc66a) + " path_bad=" + str(_bad66a)
            + " why=" + str(_m66a.get("area_unmeasured"))
            + " plain_path_rc=" + str(_rc66b)
            + " moved_rc=" + str(_rc66c) + " moved_bad=" + str(_bad66c)
            + " why_bbox=" + str(_m66c.get("bbox_unmeasured"))
            + " still_rc=" + str(_rc66d))
    except Exception as e:                      # noqa: BLE001
        add("A66-unmeasurable-geometry-fails-the-rule", False,
            type(e).__name__ + ": " + str(e)[:140])

    # ---- A67: currentColor resolves through color ----------------------------
    # Comparing the literal "currentcolor" made a correct file red (color
    # #111111) and let a wrong one pass (color #c8362c against a palette that
    # only names the keyword). With no color declared the literal stays, since
    # the used value is then genuinely unknown here.
    try:
        _d67 = tmp / "a67"
        _d67.mkdir()

        def _rules67(name, colours):
            p = _d67 / (name + ".json")
            p.write_text(json.dumps({
                "schema": "vector-ai-studio/conformance-v1",
                "canvas": [240, 240],
                "colors_allowed": colours}), encoding="utf-8")
            return p

        def _run67(name, colour_attr, colours):
            p = _d67 / (name + ".svg")
            p.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 240"%s>'
                '<rect x="100" y="100" width="40" height="40" fill="currentColor"/>'
                '</svg>' % colour_attr, encoding="utf-8")
            rc, env, _ = _call("conformance.py", ["--svg", str(p), "--rules",
                                                  str(_rules67(name, colours)), "--json"], tmp)
            dat = env.get("data") or {}
            m = dat.get("measured") or {}
            bad = [c.get("id") for c in dat.get("checks") or [] if not c.get("ok")]
            return rc, m, bad

        _rc67a, _m67a, _bad67a = _run67("resolved", ' color="#111111"', ["#111111"])
        _rc67b, _m67b, _bad67b = _run67("resolved-off-palette", ' color="#c8362c"',
                                        ["currentcolor"])
        _rc67c, _m67c, _bad67c = _run67("unknown", "", ["currentcolor"])
        add("A67-currentcolor-resolves-through-color",
            bool(_rc67a == 0 and not _bad67a and _m67a.get("colours") == ["#111111"]
                 and _rc67b == 1 and _bad67b == ["COLOUR-ALLOWED"]
                 and _m67b.get("colours") == ["#c8362c"]
                 and _rc67c == 0 and _m67c.get("colours") == ["currentcolor"]),
            "resolved_rc=" + str(_rc67a) + " colours=" + str(_m67a.get("colours"))
            + " off_rc=" + str(_rc67b) + " off_bad=" + str(_bad67b)
            + " off_colours=" + str(_m67b.get("colours"))
            + " unknown_colours=" + str(_m67c.get("colours")))
    except Exception as e:                      # noqa: BLE001
        add("A67-currentcolor-resolves-through-color", False,
            type(e).__name__ + ": " + str(e)[:140])

    # ---- A68: a stroke is measured as ink, not as its fill area --------------
    # r66 kept the old number on purpose (0.6944 for a band that is 0.1389) and
    # this round replaces it with perimeter x width. Four cases: a 10-wide
    # outline passes a 20% cap, the same outline at 40 wide fails it, a filled
    # and stroked rectangle splits the two colours (fill loses the half of the
    # band that lies over it), and a circle uses its circumference.
    try:
        _d68 = tmp / "a68"
        _d68.mkdir()
        _head68 = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 240">'

        def _run68(name, body, caps):
            svg = _d68 / (name + ".svg")
            rules = _d68 / (name + ".json")
            svg.write_text(_head68 + body + "</svg>", encoding="utf-8")
            doc = {"schema": "vector-ai-studio/conformance-v1", "canvas": [240, 240],
                   "area_ratio_max": caps}
            rules.write_text(json.dumps(doc), encoding="utf-8")
            rc, env, _ = _call("conformance.py", ["--svg", str(svg), "--rules",
                                                  str(rules), "--json"], tmp)
            dat = env.get("data") or {}
            m = dat.get("measured") or {}
            bad = [c.get("id") for c in dat.get("checks") or [] if not c.get("ok")]
            return rc, m, bad

        _rc68a, _m68a, _bad68a = _run68(
            "band10", '<rect x="20" y="20" width="200" height="200" fill="none"'
                      ' stroke="#111111" stroke-width="10"/>', {"#111111": 0.2})
        _rc68b, _m68b, _bad68b = _run68(
            "band40", '<rect x="20" y="20" width="200" height="200" fill="none"'
                      ' stroke="#111111" stroke-width="40"/>', {"#111111": 0.2})
        _rc68c, _m68c, _bad68c = _run68(
            "split", '<rect x="20" y="20" width="200" height="200" fill="#111111"'
                     ' stroke="#c8362c" stroke-width="10"/>',
            {"#111111": 0.7, "#c8362c": 0.2})
        _rc68d, _m68d, _bad68d = _run68(
            "circle", '<circle cx="120" cy="120" r="80" fill="none"'
                      ' stroke="#111111" stroke-width="10"/>', {"#111111": 0.1})
        add("A68-stroke-is-measured-as-ink",
            bool(_rc68a == 0 and not _bad68a
                 and _m68a.get("area_ratio") == {"#111111": 0.1389}
                 and _rc68b == 1 and _bad68b == ["AREA-111111"]
                 and _rc68c == 0 and not _bad68c
                 and _m68c.get("area_ratio") == {"#111111": 0.625, "#c8362c": 0.1389}
                 and _rc68d == 0 and not _bad68d
                 and _m68d.get("area_ratio") == {"#111111": 0.0873}),
            "band10=" + str(_m68a.get("area_ratio")) + " band40_rc=" + str(_rc68b)
            + " split=" + str(_m68c.get("area_ratio"))
            + " circle=" + str(_m68d.get("area_ratio"))
            + " bad=" + str(_bad68a + _bad68b + _bad68c + _bad68d))
    except Exception as e:                      # noqa: BLE001
        add("A68-stroke-is-measured-as-ink", False,
            type(e).__name__ + ": " + str(e)[:140])

    # ---- A69: a picture with no visible ink fails before any rule ------------
    # r66 made opacity readable, which exposed this: an all-transparent file
    # measured nothing and every rule passed vacuously. PAINTED turns that into
    # a red.
    try:
        _d69 = tmp / "a69"
        _d69.mkdir()
        _rules69 = _d69 / "rules.json"
        _rules69.write_text(json.dumps({
            "schema": "vector-ai-studio/conformance-v1", "canvas": [240, 240]}),
            encoding="utf-8")

        def _run69(name, body):
            p = _d69 / (name + ".svg")
            p.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 240">'
                         + body + "</svg>", encoding="utf-8")
            rc, env, _ = _call("conformance.py", ["--svg", str(p), "--rules",
                                                  str(_rules69), "--json"], tmp)
            dat = env.get("data") or {}
            m = dat.get("measured") or {}
            bad = [c.get("id") for c in dat.get("checks") or [] if not c.get("ok")]
            return rc, m, bad

        _rc69a, _m69a, _bad69a = _run69(
            "transparent",
            '<rect width="240" height="240" fill="#111111" fill-opacity="0"/>')
        _rc69b, _m69b, _bad69b = _run69(
            "visible", '<rect x="20" y="20" width="200" height="200" fill="#111111"/>')
        add("A69-a-picture-with-no-ink-fails",
            bool(_rc69a == 1 and _bad69a == ["PAINTED"]
                 and _m69a.get("painted") == 0
                 and _rc69b == 0 and not _bad69b and _m69b.get("painted") == 1),
            "empty_rc=" + str(_rc69a) + " empty_bad=" + str(_bad69a)
            + " painted=" + str(_m69a.get("painted"))
            + " visible_rc=" + str(_rc69b))
    except Exception as e:                      # noqa: BLE001
        add("A69-a-picture-with-no-ink-fails", False,
            type(e).__name__ + ": " + str(e)[:140])

    # ---- A70: the three shipped examples still pass their own rules ----------
    # They are the only end-to-end samples a user opens first, and nothing was
    # asserting them; a change to measurement could have made them red quietly.
    try:
        _ex = ROOT / "examples"
        _bad70 = []
        _rows = []
        for _name in ("01-bookshop", "02-coffee", "03-devtool"):
            _dir = _ex / _name
            _rc, _env, _ = _call("conformance.py", ["--svg", str(_dir / "final.svg"),
                                                    "--rules", str(_dir / "conformance.json"),
                                                    "--json"], tmp)
            _ids = [c.get("id") for c in (_env.get("data") or {}).get("checks") or []
                    if not c.get("ok")]
            _rows.append(_name + ":" + str(_rc))
            if _rc != 0 or _ids:
                _bad70.append(_name + " " + str(_ids))
        add("A70-shipped-examples-pass-their-rules", not _bad70,
            " ".join(_rows) + (" bad=" + str(_bad70) if _bad70 else ""))
    except Exception as e:                      # noqa: BLE001
        add("A70-shipped-examples-pass-their-rules", False,
            type(e).__name__ + ": " + str(e)[:140])

    # ---- A71: one inline-style parser for the whole toolkit ------------------
    # lib_common's structure reader and conformance's cascade each carried their
    # own split(";") loop; only conformance understood !important. Both now use
    # L.parse_style, so this pins the API and the cascade it feeds.
    try:
        _d71 = tmp / "a71"
        _d71.mkdir()
        _svg71 = _d71 / "important.svg"
        _rules71 = _d71 / "rules.json"
        _svg71.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 240">'
            '<rect x="20" y="20" width="200" height="200" fill="#c8362c"'
            ' style="fill:#111111 !important"/></svg>', encoding="utf-8")
        _rules71.write_text(json.dumps({
            "schema": "vector-ai-studio/conformance-v1", "canvas": [240, 240],
            "colors_allowed": ["#111111"]}), encoding="utf-8")
        _rc71, _env71, _ = _call("conformance.py", ["--svg", str(_svg71),
                                                    "--rules", str(_rules71),
                                                    "--json"], tmp)
        _m71 = (_env71.get("data") or {}).get("measured") or {}
        _bad71 = [c.get("id") for c in (_env71.get("data") or {}).get("checks") or []
                  if not c.get("ok")]
        _api71 = L.parse_style("fill:#111; stroke: #c8362c !important; fill:#222")
        add("A71-one-inline-style-parser",
            bool(_api71 == {"fill": ("#222", False),
                            "stroke": ("#c8362c", True)}
                 and _rc71 == 0 and not _bad71
                 and _m71.get("colours") == ["#111111"]),
            "api=" + str(_api71) + " rc=" + str(_rc71)
            + " colours=" + str(_m71.get("colours")) + " bad=" + str(_bad71))
    except Exception as e:                      # noqa: BLE001
        add("A71-one-inline-style-parser", False,
            type(e).__name__ + ": " + str(e)[:140])

    # ---- A72: a length the script cannot read is not zero --------------------
    # The old _f() returned 0.0 for "50%" and the default 1.0 for "2px", so a
    # half-canvas rect passed a 1% area cap and a 2px stroke failed a 2pt rule.
    # A percentage stays unmeasurable (and fails a cap); px and the absolute CSS
    # units convert; an unreadable declared width fails the width rule.
    try:
        _d72 = tmp / "a72"
        _d72.mkdir()

        def _run72(name, attrs, body, rules):
            _svg = _d72 / (name + ".svg")
            _rp = _d72 / (name + ".json")
            _svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" ' + attrs + ">"
                            + body + "</svg>", encoding="utf-8")
            _rp.write_text(json.dumps(rules), encoding="utf-8")
            _rc, _env, _ = _call("conformance.py", ["--svg", str(_svg), "--rules",
                                                    str(_rp), "--json"], tmp)
            _dat = _env.get("data") or {}
            return (_rc, _dat.get("measured") or {},
                    [c.get("id") for c in _dat.get("checks") or [] if not c.get("ok")])
        _b72 = {"schema": "vector-ai-studio/conformance-v1", "canvas": [240, 240]}
        _rc72a, _m72a, _bad72a = _run72(
            "percent", 'viewBox="0 0 240 240"',
            '<rect x="0" y="0" width="50%" height="100%" fill="#111111"/>',
            dict(_b72, area_ratio_max={"#111111": 0.01}))
        _rc72b, _m72b, _bad72b = _run72(
            "px", 'viewBox="0 0 240 240"',
            '<rect x="20" y="20" width="200" height="200" fill="none"'
            ' stroke="#111111" stroke-width="2px"/>',
            dict(_b72, stroke_widths_allowed=[2]))
        _rc72c, _m72c, _bad72c = _run72(
            "em", 'viewBox="0 0 240 240"',
            '<rect x="20" y="20" width="200" height="200" fill="none"'
            ' stroke="#111111" stroke-width="2em"/>',
            dict(_b72, stroke_widths_allowed=[2]))
        add("A72-unmeasurable-length-is-not-zero",
            bool(_rc72a == 1 and _bad72a == ["AREA-111111"]
                 and _m72a.get("area_unmeasured") == {"#111111": "unit"}
                 and _rc72b == 0 and not _bad72b
                 and _m72b.get("stroke_widths") == [2.0]
                 and _rc72c == 1 and _bad72c == ["STROKE-WIDTHS"]
                 and _m72c.get("stroke_widths_unmeasured") is True),
            "percent_rc=" + str(_rc72a) + " why=" + str(_m72a.get("area_unmeasured"))
            + " px_rc=" + str(_rc72b) + " widths=" + str(_m72b.get("stroke_widths"))
            + " em_rc=" + str(_rc72c) + " em_bad=" + str(_bad72c))
    except Exception as e:                      # noqa: BLE001
        add("A72-unmeasurable-length-is-not-zero", False,
            type(e).__name__ + ": " + str(e)[:140])

    # ---- A73: colour identity, default fill, and 0% opacity ------------------
    # #F00 and rgb(255,0,0) are the same paint as #ff0000 and used to fail an
    # allowed-set written in hex. An undeclared fill is black, not absent, and
    # fill-opacity="0%" is zero ink. A <line> has no interior, so its default
    # fill must not add a colour a renderer would never show.
    try:
        _d73 = tmp / "a73"
        _d73.mkdir()

        def _run73(name, body, rules):
            _svg = _d73 / (name + ".svg")
            _rp = _d73 / (name + ".json")
            _svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 240">'
                            + body + "</svg>", encoding="utf-8")
            _rp.write_text(json.dumps(rules), encoding="utf-8")
            _rc, _env, _ = _call("conformance.py", ["--svg", str(_svg), "--rules",
                                                    str(_rp), "--json"], tmp)
            _dat = _env.get("data") or {}
            return (_rc, _dat.get("measured") or {},
                    [c.get("id") for c in _dat.get("checks") or [] if not c.get("ok")])
        _b73 = {"schema": "vector-ai-studio/conformance-v1", "canvas": [240, 240]}
        _rc73a, _m73a, _bad73a = _run73(
            "short-hex", '<rect x="20" y="20" width="200" height="200" fill="#F00"/>',
            dict(_b73, colors_allowed=["#ff0000"]))
        _rc73b, _m73b, _bad73b = _run73(
            "rgb", '<rect x="20" y="20" width="200" height="200" fill="rgb(255, 0, 0)"/>',
            dict(_b73, colors_allowed=["#ff0000"]))
        _rc73c, _m73c, _bad73c = _run73(
            "default-fill", '<rect x="20" y="20" width="200" height="200"/>',
            dict(_b73, colors_allowed=["#000000"]))
        _rc73d, _m73d, _bad73d = _run73(
            "zero-percent", '<rect x="20" y="20" width="200" height="200"'
                            ' fill="#111111" fill-opacity="0%"/>', dict(_b73))
        _rc73e, _m73e, _bad73e = _run73(
            "line", '<line x1="20" y1="120" x2="220" y2="120" stroke="#111111"'
                    ' stroke-width="4"/>',
            dict(_b73, colors_allowed=["#111111"], colors_max=1))
        add("A73-colour-identity-and-default-fill",
            bool(_rc73a == 0 and not _bad73a and _m73a.get("colours") == ["#ff0000"]
                 and _rc73b == 0 and not _bad73b and _m73b.get("colours") == ["#ff0000"]
                 and _rc73c == 0 and not _bad73c and _m73c.get("colours") == ["#000000"]
                 and _m73c.get("painted") == 1
                 and _rc73d == 1 and _bad73d == ["PAINTED"] and _m73d.get("painted") == 0
                 and _rc73e == 0 and not _bad73e and _m73e.get("colours") == ["#111111"]),
            "hex=" + str(_m73a.get("colours")) + " rgb=" + str(_m73b.get("colours"))
            + " default=" + str(_m73c.get("colours"))
            + " zero_rc=" + str(_rc73d) + " zero_bad=" + str(_bad73d)
            + " line=" + str(_m73e.get("colours")) + " " + str(_bad73e))
    except Exception as e:                      # noqa: BLE001
        add("A73-colour-identity-and-default-fill", False,
            type(e).__name__ + ": " + str(e)[:140])

    # ---- A74: the syntax variants a real file uses ---------------------------
    # Space-separated points crashed with IndexError; a comma viewBox and a
    # "240px" root size went red on a legal file; a clip-path attribute raised
    # KeyError in the forbid counter. Percent pairs are unmeasurable, not zero.
    try:
        _d74 = tmp / "a74"
        _d74.mkdir()

        def _run74(name, attrs, body, rules):
            _svg = _d74 / (name + ".svg")
            _rp = _d74 / (name + ".json")
            _svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" ' + attrs + ">"
                            + body + "</svg>", encoding="utf-8")
            _rp.write_text(json.dumps(rules), encoding="utf-8")
            _rc, _env, _ = _call("conformance.py", ["--svg", str(_svg), "--rules",
                                                    str(_rp), "--json"], tmp)
            _dat = _env.get("data") or {}
            return (_rc, _dat.get("measured") or {},
                    [c.get("id") for c in _dat.get("checks") or [] if not c.get("ok")])
        _b74 = {"schema": "vector-ai-studio/conformance-v1", "canvas": [240, 240]}
        _rc74a, _m74a, _bad74a = _run74(
            "points-space", 'viewBox="0 0 240 240"',
            '<polygon points="20 20 220 20 220 220" fill="#111111"/>',
            dict(_b74, colors_allowed=["#111111"], bbox_margin_min=10))
        _rc74b, _m74b, _bad74b = _run74(
            "comma-px", 'viewBox="0,0,240,240" width="240px" height="240px"',
            '<rect x="20" y="20" width="200" height="200" fill="#111111"/>', dict(_b74))
        _rc74c, _m74c, _bad74c = _run74(
            "clip-path-attr", 'viewBox="0 0 240 240"',
            '<rect x="20" y="20" width="200" height="200" fill="#111111"'
            ' clip-path="url(#c)"/>', dict(_b74, forbid=["clipPath"]))
        _rc74d, _m74d, _bad74d = _run74(
            "points-percent", 'viewBox="0 0 240 240"',
            '<polygon points="10%,20% 30%,40% 50%,20%" fill="#111111"/>',
            dict(_b74, area_ratio_max={"#111111": 0.5}))
        add("A74-syntax-variants-are-measured",
            bool(_rc74a == 0 and not _bad74a
                 and _m74a.get("area_ratio") == {"#111111": 0.3472}
                 and _rc74b == 0 and not _bad74b
                 and _rc74c == 1 and _bad74c == ["FORBID-CLIPPATH"]
                 and _rc74d == 1 and _bad74d == ["AREA-111111"]),
            "space=" + str(_m74a.get("area_ratio")) + " " + str(_bad74a)
            + " comma=" + str(_rc74b) + " " + str(_bad74b)
            + " clip=" + str(_bad74c) + " pct=" + str(_bad74d))
    except Exception as e:                      # noqa: BLE001
        add("A74-syntax-variants-are-measured", False,
            type(e).__name__ + ": " + str(e)[:140])

    # ---- A75: a malformed rule number is a usage error, not a crash ----------
    # int("two") inside the check builder raised ValueError, which the CLI
    # reported as E-INTERNAL (exit 4). A bad rules file must name the key and
    # exit 2, the same as every other rules-schema failure.
    try:
        _d75 = tmp / "a75"
        _d75.mkdir()
        _svg75 = _d75 / "ok.svg"
        _svg75.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 240">'
            '<rect x="20" y="20" width="200" height="200" fill="#111111"'
            ' stroke="#111111" stroke-width="2"/></svg>', encoding="utf-8")

        def _run75(name, extra):
            _rp = _d75 / (name + ".json")
            _doc = {"schema": "vector-ai-studio/conformance-v1", "canvas": [240, 240]}
            _doc.update(extra)
            _rp.write_text(json.dumps(_doc), encoding="utf-8")
            _rc, _env, _ = _call("conformance.py", ["--svg", str(_svg75), "--rules",
                                                    str(_rp), "--json"], tmp)
            return _rc, _codes(_env)
        _c75 = [_run75("colors_max", {"colors_max": "two"}),
                _run75("cap", {"area_ratio_max": {"#111111": "lots"}}),
                _run75("width", {"stroke_widths_allowed": ["thick"]}),
                _run75("span", {"elements": {"min": "2"}})]
        add("A75-malformed-rule-numbers-exit-2",
            all(rc == 2 and "E-SCHEMA" in codes for rc, codes in _c75),
            "runs=" + str(_c75))
    except Exception as e:                      # noqa: BLE001
        add("A75-malformed-rule-numbers-exit-2", False,
            type(e).__name__ + ": " + str(e)[:140])



def _check_stale_preview(tmp, add, rect):
    """A47: a failed preview render must clear the previous picture and its meta.

    Extracted from main(); `rect` is the rect-only fixture the caller owns.
    """
    # ---- A47: a failed preview render must not leave the previous picture ----
    # H2 only asks whether preview.png parses, so a stale PNG from an earlier
    # design passed H2 for a run whose own render failed -- and the stale
    # preview.meta.json described a renderer that never ran this time.
    # _ensure_preview deletes both before rendering. Both halves are asserted:
    # failure clears them, success replaces the picture instead of returning
    # True beside the old one.
    try:
        _spec47 = importlib.util.spec_from_file_location("lib_pdf_a47",
                                                         SCRIPTS / "lib_pdf.py")
        _lp47 = importlib.util.module_from_spec(_spec47)
        _spec47.loader.exec_module(_lp47)
        _p47 = tmp / "a47-preview"
        _p47.mkdir()
        _junk47 = b"\x89PNG\r\n\x1a\nstale-from-previous-run"
        (_p47 / "preview.png").write_bytes(_junk47)
        (_p47 / "preview.meta.json").write_text('{"renderer": "stale"}', encoding="utf-8")
        _fail47 = _lp47._ensure_preview(_p47, tmp / "no-such.svg")
        _cleared47 = (not (_p47 / "preview.png").exists()
                      and not (_p47 / "preview.meta.json").exists())
        _ok47 = _lp47._ensure_preview(_p47, rect)
        _fresh47 = bool(_ok47 and (_p47 / "preview.png").is_file()
                        and (_p47 / "preview.png").read_bytes() != _junk47
                        and (_p47 / "preview.meta.json").is_file())
        add("A47-failed-preview-clears-stale-png",
            bool(_fail47 is False and _cleared47 and _fresh47),
            "fail_returns=" + str(_fail47) + " cleared=" + str(_cleared47)
            + " rerender=" + str(_ok47) + " replaced=" + str(_fresh47))
    except Exception as e:                      # noqa: BLE001
        add("A47-failed-preview-clears-stale-png", False,
            type(e).__name__ + ": " + str(e)[:140])


def _check_archetypes_legal(tmp, add):
    """Extracted from main(): one assertion block, no state of its own."""
    # ---- A49: the design-language archetypes must be legal vectors ----------
    # The archetypes are the only stand-in for "what good looks like". If one of
    # them uses a bitmap, a filter or a single group, the reference undermines
    # the rule it is meant to demonstrate -- and nobody notices by eye.
    try:
        _arch = sorted((ROOT / "references" / "archetypes").glob("*.svg"))
        _bad49 = []
        for _a in _arch:
            _p49 = subprocess.run(
                [sys.executable, str(SCRIPTS / "sanitize_svg.py"),
                 "--in", str(_a), "--out", str(tmp / ("a49-" + _a.name)), "--json"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=120)
            try:
                _e49 = json.loads(_p49.stdout)
            except json.JSONDecodeError:
                _bad49.append(_a.name + ":no-envelope")
                continue
            _c49 = (_e49.get("data") or {}).get("static_checks") or {}
            _vb = _c49.get("viewBox") or []
            _bb = _c49.get("ink_bbox") or []
            _inside = (len(_vb) == 4 and len(_bb) == 4
                       and _bb[0] >= _vb[0] - 1 and _bb[1] >= _vb[1] - 1
                       and _bb[2] <= _vb[0] + _vb[2] + 1
                       and _bb[3] <= _vb[1] + _vb[3] + 1)
            if not (_p49.returncode == 0 and _c49.get("xml_ok")
                    and not _c49.get("bitmap") and not _c49.get("forbidden")
                    and _c49.get("degenerate_paths") == 0
                    and (_c49.get("g_count") or 0) >= 3
                    and len(_c49.get("colors") or []) <= 5
                    and len(_c49.get("stroke_widths") or []) <= 2
                    and _inside):
                _bad49.append(_a.name)
        add("A49-design-archetypes-are-legal-vectors",
            bool(len(_arch) >= 10 and not _bad49),
            "files=" + str(len(_arch)) + " bad=" + str(_bad49))
    except Exception as e:                      # noqa: BLE001
        add("A49-design-archetypes-are-legal-vectors", False,
            type(e).__name__ + ": " + str(e)[:140])


def _check_review_pack(tmp, add):
    """Extracted from main(): one assertion block, no state of its own."""
    # ---- A51: the review pack must be anonymous and reproducible -----------
    # A blind review is only blind if the sheet cannot name the candidate and
    # the mapping lives in a separate file. Same seed must give the same pack,
    # or a re-run silently produces a different experiment.
    try:
        import contextlib
        import io

        _spec51 = importlib.util.spec_from_file_location("studio_a51",
                                                         SCRIPTS / "studio.py")
        _st51 = importlib.util.module_from_spec(_spec51)
        _spec51.loader.exec_module(_st51)
        _cands51 = []
        for _i in (1, 2):
            _d51 = tmp / ("a51-c" + str(_i))
            (_d51 / "round-1").mkdir(parents=True)
            (_d51 / "round-1" / "sanitized.svg").write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"/>',
                encoding="utf-8")
            (_d51 / "round-1" / "preview.png").write_bytes(b"\x89PNG\r\n\x1a\ncase")
            _cands51.append(str(_d51))
        _packs51 = []
        for _j in (1, 2):
            _pk51 = tmp / ("a51-pack" + str(_j))
            with contextlib.redirect_stdout(io.StringIO()):
                _rc51 = _st51._review_pack(argparse.Namespace(
                    review_pack=str(_pk51), candidate=_cands51, seed=11))
            _packs51.append(_pk51)
        _k51 = json.loads((_packs51[0] / "key.json").read_text(encoding="utf-8"))
        _k51b = json.loads((_packs51[1] / "key.json").read_text(encoding="utf-8"))
        _rows51 = (_packs51[0] / "review-sheet.csv").read_text(
            encoding="utf-8").splitlines()
        _sources51 = sorted(c["source"] for c in _k51["cases"])
        _anon51 = all(str(c) not in ln for ln in _rows51 for c in _cands51)
        _empty51 = (len(_rows51) == 3
                    and all(ln.endswith(",,,,,,") for ln in _rows51[1:]))
        _readme51 = (_packs51[0] / "README.md").read_text(encoding="utf-8")
        _key_kept51 = all(str(c) not in _readme51 for c in _cands51)
        add("A51-review-pack-anonymous-and-deterministic",
            bool(_rc51 == 0 and len(_k51["cases"]) == 2
                 and _sources51 == sorted(_cands51)
                 and _k51["cases"] == _k51b["cases"]
                 and _anon51 and _empty51 and _key_kept51),
            "cases=" + str(len(_k51.get("cases") or []))
            + " anon=" + str(_anon51) + " empty=" + str(_empty51)
            + " same_seed=" + str(_k51["cases"] == _k51b["cases"]))
    except Exception as e:                      # noqa: BLE001
        add("A51-review-pack-anonymous-and-deterministic", False,
            type(e).__name__ + ": " + str(e)[:140])


def _check_deal_history(tmp, add):
    """Extracted from main(): one assertion block, no state of its own."""
    # ---- A52: the deal must not repeat itself or reset its history silently --
    # The point of deal.py is that the next round cannot look like the last two.
    # A deck with no memory just reproduces the model's default look with extra
    # steps, so the exclusion window is pinned against real rounds: three deals
    # in a row, then the same seed replayed on the same history.
    try:
        _spec52 = ROOT / "examples" / "01-bookshop" / "spec.json"
        _h52 = tmp / "a52-history.json"
        _pre3 = tmp / "a52-pre3.json"
        _rounds52, _rcs52 = [], []
        for _i in range(3):
            if _i == 2 and _h52.is_file():
                _pre3.write_bytes(_h52.read_bytes())
            _o52 = tmp / ("a52-round%d.json" % (_i + 1))
            _rc52, _env52, _ = _call("deal.py", [
                "--spec", str(_spec52), "--out", str(_o52),
                "--history", str(_h52), "--seed", "7", "--json"], tmp)
            _doc52 = json.loads(_o52.read_text(encoding="utf-8"))
            _rcs52.append(_rc52)
            _rounds52.append([d["archetype"] for d in _doc52["directions"]])
        _distinct52 = all(len(r) == 3 and len(set(r)) == 3 for r in _rounds52)
        _norep52 = (not (set(_rounds52[1]) & set(_rounds52[0]))
                    and not (set(_rounds52[2])
                             & (set(_rounds52[0]) | set(_rounds52[1]))))
        _o52r = tmp / "a52-replay.json"
        _h52r = tmp / "a52-history-replay.json"
        _h52r.write_bytes(_pre3.read_bytes())
        _call("deal.py", ["--spec", str(_spec52), "--out", str(_o52r),
                          "--history", str(_h52r), "--seed", "7", "--json"], tmp)
        _rep52 = [d["archetype"] for d in json.loads(
            _o52r.read_text(encoding="utf-8"))["directions"]]
        _same52 = _rep52 == _rounds52[2]
        _hbad = tmp / "a52-bad-history.json"
        _hbad.write_text('{"schema": "nope", "rounds": []}', encoding="utf-8")
        _before52 = _hbad.read_bytes()
        _rc52x, _env52x, _ = _call("deal.py", ["--spec", str(_spec52),
                                               "--history", str(_hbad), "--json"], tmp)
        _kept52 = _hbad.read_bytes() == _before52
        add("A52-deal-no-repeat-and-no-silent-reset",
            bool(_rcs52 == [0, 0, 0] and _distinct52 and _norep52 and _same52
                 and _rc52x == L.EXIT_USAGE and "E-SCHEMA" in _codes(_env52x)
                 and _kept52),
            "rc=" + str(_rcs52) + " distinct=" + str(_distinct52)
            + " no_repeat=" + str(_norep52) + " replay_same=" + str(_same52)
            + " corrupt_rc=" + str(_rc52x) + " kept=" + str(_kept52))
    except Exception as e:                      # noqa: BLE001
        add("A52-deal-no-repeat-and-no-silent-reset", False,
            type(e).__name__ + ": " + str(e)[:140])


def _check_failed_dimension(tmp, add):
    """Extracted from main(): one assertion block, no state of its own."""
    # ---- A54: every failed dimension must name itself and its next action ----
    # A number tells a reader that something failed and nothing else. The
    # verdict table turns each failure into one sentence with an action, so the
    # assertion is a contract: one verdict per failed dimension, ids drawn from
    # the registered table, and a clean run that says PASS rather than nothing.
    try:
        _common54 = ["--judge", str(GOOD / "judge.json"),
                     "--spec", str(GOOD / "spec.json"), "--json"]
        _rc54g, _env54g, _ = _call("score.py", ["--svg", str(GOOD / "sample.svg")]
                                   + _common54, tmp)
        _rc54b, _env54b, _ = _call("score.py", ["--svg", str(BAD / "palette_overflow.svg")]
                                   + _common54, tmp)
        _g54 = (_env54g.get("data") or {}).get("verdicts") or []
        _b54 = (_env54b.get("data") or {}).get("verdicts") or []
        _f54 = (_env54b.get("data") or {}).get("failed_dims") or []
        _spec54 = importlib.util.spec_from_file_location("score_a54",
                                                        SCRIPTS / "score.py")
        _sc54 = importlib.util.module_from_spec(_spec54)
        _spec54.loader.exec_module(_sc54)
        _reg54 = set(_sc54.VERDICT_ACTIONS) - {"PASS"}
        _clean54 = (len(_g54) == 1 and _g54[0].get("id") == "PASS"
                    and not _g54[0].get("action"))
        _named54 = (bool(_f54) and len(_b54) == len(_f54)
                    and all(v.get("dim") in _f54 and v.get("id") in _reg54
                            and v.get("msg") and v.get("action")
                            for v in _b54))
        add("A54-failed-dimension-names-its-action",
            bool(_rc54g == 0 and _clean54 and _rc54b == 1 and _named54),
            "good=" + str([v.get("id") for v in _g54])
            + " bad=" + str([v.get("id") for v in _b54])
            + " failed_dims=" + str(_f54))
    except Exception as e:                      # noqa: BLE001
        add("A54-failed-dimension-names-its-action", False,
            type(e).__name__ + ": " + str(e)[:140])


def _check_empty_review_sheet(tmp, add):
    """Extracted from main(): one assertion block, no state of its own."""
    # ---- A55: an empty review sheet must not pass as evidence -----------------
    # r48 and r49 shipped a review sheet whose score cells were all blank, and
    # "the file exists" was the only thing anyone could check. A blank cell is
    # not a zero, and one reviewer is not a panel, so the script must refuse
    # both and name which one it refused.
    try:
        _pack55 = tmp / "a55"
        _pack55.mkdir()
        _keyp55 = _pack55 / "key.json"
        _keyp55.write_text(json.dumps({
            "schema": "vector-ai-studio/review-key-v1", "seed": 7,
            "cases": [{"case": "case-01", "source": "run-a"},
                      {"case": "case-02", "source": "run-b"},
                      {"case": "case-03", "source": "run-c"}]}), encoding="utf-8")
        _head55 = "case,concept,hierarchy,proportion,detail,durability,notes"
        _empty55 = _pack55 / "empty.csv"
        _empty55.write_text("\n".join([_head55, "case-01,,,,,,", "case-02,,,,,,",
                                       "case-03,,,,,,"]) + "\n", encoding="utf-8")
        _rows55 = {
            "a": ["case-01,2,2,1,2,2,ok", "case-02,1,2,1,1,2,ok",
                  "case-03,2,1,1,2,1,ok"],
            "b": ["case-01,2,1,1,2,1,x", "case-02,1,2,1,1,2,y",
                  "case-03,2,1,1,2,2,z"],
            "hole": ["case-01,2,1,1,2,1,x", "case-02,1,2,,1,2,y",
                     "case-03,2,1,1,2,2,z"],
        }
        _paths55 = {}
        for _k, _rows in _rows55.items():
            _p55 = _pack55 / ("rev-" + _k + ".csv")
            _p55.write_text("\n".join([_head55] + _rows) + "\n", encoding="utf-8")
            _paths55[_k] = str(_p55)

        _rc55e, _env55e, _ = _call("review.py", ["--sheet", str(_empty55),
                                                 "--key", str(_keyp55),
                                                 "--reviewer-kind", "external",
                                                 "--json"], tmp)
        _rc55h, _env55h, _ = _call("review.py", ["--sheet", _paths55["hole"],
                                                 "--key", str(_keyp55),
                                                 "--reviewer-kind", "external",
                                                 "--json"], tmp)
        _rc55g, _env55g, _ = _call("review.py", ["--sheet", _paths55["a"],
                                                 "--sheet", _paths55["b"],
                                                 "--key", str(_keyp55),
                                                 "--reviewer-kind", "external",
                                                 "--json"], tmp)
        _rc55i, _env55i, _ = _call("review.py", ["--sheet", _paths55["a"],
                                                 "--key", str(_keyp55),
                                                 "--reviewer-kind", "internal",
                                                 "--json"], tmp)
        _rc55m, _env55m, _ = _call("review.py", ["--sheet", _paths55["a"],
                                                 "--sheet", _paths55["b"],
                                                 "--key", str(_keyp55),
                                                 "--reviewer-kind", "machine",
                                                 "--json"], tmp)
        # r70 #4: the SAME sheet passed twice must be rejected as a duplicate
        # (E-REVIEW-INVALID) and never count as two independent reviewers.
        # Folded into A55 itself (no new assertion id -> no EXPECTED_ASSERTIONS
        # churn); design §2.2 #4 explicitly allows extending A55.
        _rc55d, _env55d, _ = _call("review.py", ["--sheet", _paths55["a"],
                                                 "--sheet", _paths55["a"],
                                                 "--key", str(_keyp55),
                                                 "--reviewer-kind", "external",
                                                 "--json"], tmp)
        _u55d = ((_env55d.get("data") or {}).get("evidence") or {}).get("usable_for_claim")
        _g55 = _env55g.get("data") or {}
        _u55i = ((_env55i.get("data") or {}).get("evidence") or {}).get("usable_for_claim")
        _u55m = ((_env55m.get("data") or {}).get("evidence") or {}).get("usable_for_claim")
        add("A55-review-sheet-empty-is-not-evidence",
            bool(_rc55e == 1 and "E-REVIEW-EMPTY" in _codes(_env55e)
                 and _rc55h == 1 and "E-REVIEW-INVALID" in _codes(_env55h)
                 and _rc55g == 0
                 and (_g55.get("evidence") or {}).get("usable_for_claim") is True
                 and (_g55.get("agreement") or {}).get("rate") == 0.8
                 and [c["verdict"] for c in _g55.get("cases") or []] == ["pass"] * 3
                 and _u55i is False and _u55m is False
                 and _rc55d == 1 and "E-REVIEW-INVALID" in _codes(_env55d)
                 and _u55d is False),
            "empty_rc=" + str(_rc55e) + " hole_rc=" + str(_rc55h)
            + " good_rc=" + str(_rc55g) + " agreement="
            + str((_g55.get("agreement") or {}).get("rate"))
            + " internal=" + str(_u55i) + " machine=" + str(_u55m)
            + " dup_rc=" + str(_rc55d) + " dup_codes=" + ",".join(_codes(_env55d))
            + " dup_usable=" + str(_u55d))
    except Exception as e:                      # noqa: BLE001
        add("A55-review-sheet-empty-is-not-evidence", False,
            type(e).__name__ + ": " + str(e)[:140])


def _check_ledger_trace(tmp, add):
    """Extracted from main(): one assertion block, no state of its own."""
    # ---- A57: a component that enters the artwork must be traceable -----------
    # Reference material never enters the SVG; a component that does needs a URL,
    # a licence and an element id. Three failures must be caught: a data-source
    # with no ledger entry, a licence outside the allowed set, and an element id
    # that is not in the file.
    try:
        _d57 = tmp / "a57"
        _d57.mkdir()
        _base57 = {"schema": "vector-ai-studio/conformance-v1",
                   "colors_allowed": ["#111111"],
                   "source_ledger": "ledger.json",
                   "licenses_allowed": ["own", "cc0"]}
        _rules57 = _d57 / "rules.json"
        _rules57.write_text(json.dumps(_base57), encoding="utf-8")
        _svg57 = _d57 / "good.svg"
        _svg57.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 240">'
            '<rect id="tile" x="40" y="40" width="60" height="60" fill="#111111"/></svg>',
            encoding="utf-8")
        _svg57b = _d57 / "marked.svg"
        _svg57b.write_text(
            _svg57.read_text(encoding="utf-8").replace(
                'id="tile"', 'id="tile" data-source="https://example.com/tile"'),
            encoding="utf-8")
        _own57 = [{"id": "own-01", "channel": "output", "url": "", "license": "own",
                   "fetched": "2026-11-10", "use": "self-drawn", "in_output": True,
                   "element": None}]
        _paths57 = {}
        for _k, _entries in {
            "own": _own57,
            "licensed": [{"id": "s1", "channel": "output", "url": "https://example.com/tile",
                          "license": "cc0", "fetched": "2026-11-10", "use": "tile",
                          "in_output": True, "element": "tile",
                          "license_source": "https://example.com/licence",
                          "license_verified_at": "2026-11-10"}],
            "bad-licence": [{"id": "s1", "channel": "output", "url": "https://example.com/tile",
                             "license": "all-rights-reserved", "fetched": "2026-11-10",
                             "use": "tile", "in_output": True, "element": "tile"}],
            "ghost": [{"id": "s1", "channel": "output", "url": "https://example.com/tile",
                       "license": "cc0", "fetched": "2026-11-10", "use": "tile",
                       "in_output": True, "element": "ghost"}],
        }.items():
            _p57 = _d57 / ("ledger-" + _k + ".json")
            _p57.write_text(json.dumps({"schema": "vector-ai-studio/source-ledger-v1",
                                        "entries": _entries}), encoding="utf-8")
            _paths57[_k] = _p57

        def _with_ledger(path):
            r57 = dict(_base57)
            r57["source_ledger"] = path.name
            p57 = _d57 / ("rules-" + path.stem + ".json")
            p57.write_text(json.dumps(r57), encoding="utf-8")
            return str(p57)

        _rc57g, _env57g, _ = _call("conformance.py",
                                   ["--svg", str(_svg57), "--rules",
                                    _with_ledger(_paths57["own"]), "--json"], tmp)
        _rc57m, _env57m, _ = _call("conformance.py",
                                   ["--svg", str(_svg57b), "--rules",
                                    _with_ledger(_paths57["own"]), "--json"], tmp)
        _rc57l, _env57l, _ = _call("conformance.py",
                                   ["--svg", str(_svg57b), "--rules",
                                    _with_ledger(_paths57["bad-licence"]), "--json"], tmp)
        _rc57e, _env57e, _ = _call("conformance.py",
                                   ["--svg", str(_svg57), "--rules",
                                    _with_ledger(_paths57["ghost"]), "--json"], tmp)
        _rc57o, _env57o, _ = _call("conformance.py",
                                   ["--svg", str(_svg57b), "--rules",
                                    _with_ledger(_paths57["licensed"]), "--json"], tmp)
        _ids57m = [c["id"] for c in (_env57m.get("data") or {}).get("checks") or []
                   if not c.get("ok")]
        _ids57l = [c["id"] for c in (_env57l.get("data") or {}).get("checks") or []
                   if not c.get("ok")]
        _ids57e = [c["id"] for c in (_env57e.get("data") or {}).get("checks") or []
                   if not c.get("ok")]
        add("A57-source-ledger-tracks-what-enters-the-artwork",
            bool(_rc57g == 0 and _rc57o == 0
                 and _rc57m == 1 and "LEDGER-DATASOURCE" in _ids57m
                 and "E-CONFORMANCE" in _codes(_env57m)
                 and _rc57l == 1 and "LEDGER-LICENSE" in _ids57l
                 and _rc57e == 1 and "LEDGER-ELEMENT" in _ids57e),
            "own_rc=" + str(_rc57g) + " licensed_rc=" + str(_rc57o)
            + " unmarked=" + str(_ids57m) + " licence=" + str(_ids57l)
            + " ghost=" + str(_ids57e))
    except Exception as e:                      # noqa: BLE001
        add("A57-source-ledger-tracks-what-enters-the-artwork", False,
            type(e).__name__ + ": " + str(e)[:140])


def _check_licence_claim(tmp, add):
    """Extracted from main(): one assertion block, no state of its own."""
    # ---- A58: a licence name without a source is an unverifiable claim ------
    # "cc0" is an assertion, not evidence. It becomes checkable only when the
    # ledger also records where the claim came from and when a human read it,
    # because aggregators routinely tag a file with a different licence than its
    # origin. Own work carries no external licence, so it is exempt rather than
    # forced to invent a URL -- A57's own-case already proves that exemption.
    try:
        _d58 = tmp / "a58"
        _d58.mkdir()
        (_d58 / "rules.json").write_text(json.dumps(
            {"schema": "vector-ai-studio/conformance-v1",
             "colors_allowed": ["#111111"],
             "source_ledger": "ledger.json",
             "licenses_allowed": ["own", "cc0"]}), encoding="utf-8")
        _svg58 = _d58 / "marked.svg"
        _svg58.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 240 240">'
            '<rect id="tile" data-source="https://example.com/tile" x="40" y="40"'
            ' width="60" height="60" fill="#111111"/></svg>', encoding="utf-8")
        _base58 = {"id": "s1", "channel": "output", "url": "https://example.com/tile",
                   "license": "cc0", "fetched": "2026-11-10", "use": "tile",
                   "in_output": True, "element": "tile",
                   "license_source": "https://example.com/licence",
                   "license_verified_at": "2026-11-10"}

        def _run58(entry):
            (_d58 / "ledger.json").write_text(json.dumps(
                {"schema": "vector-ai-studio/source-ledger-v1", "entries": [entry]}),
                encoding="utf-8")
            _r, _e, _ = _call("conformance.py",
                              ["--svg", str(_svg58), "--rules",
                               str(_d58 / "rules.json"), "--json"], tmp)
            _bad = [c["id"] for c in (_e.get("data") or {}).get("checks") or []
                    if not c.get("ok")]
            return _r, _bad

        _nos58 = dict(_base58)
        _nos58.pop("license_source")
        _bad58 = dict(_base58)
        _bad58["license_verified_at"] = "2026/11/10"
        _r58a, _b58a = _run58(dict(_base58))
        _r58b, _b58b = _run58(_nos58)
        _r58c, _b58c = _run58(_bad58)
        add("A58-licence-claim-needs-source-and-date",
            bool(_r58a == 0 and not _b58a
                 and _r58b == 1 and "LICENSE-SOURCE" in _b58b
                 and _r58c == 1 and "LICENSE-VERIFIED" in _b58c),
            "ok=" + str(_r58a) + str(_b58a) + " nosrc=" + str(_r58b) + str(_b58b)
            + " baddate=" + str(_r58c) + str(_b58c))
    except Exception as e:                      # noqa: BLE001
        add("A58-licence-claim-needs-source-and-date", False,
            type(e).__name__ + ": " + str(e)[:140])


def _stage_pure_chain(tmp, add, th):
    """A1-A11: one pure-script chain, measured twice.

    The chain is the fixture every later gate reads, so it is built once
    here and its two work dirs are handed back to main(). `msg` is taken
    from the same thresholds object the caller loaded -- no second copy.
    """
    msg = th["messages"]
    # A1 envelope / exit codes + A3 idempotency + A4 cwd independence
    # Graded assertions (A6/A7/A10) need a non-L2 run, so the main chains use the
    # rect-only fixture: the builtin fallback renders it faithfully. A16 keeps the
    # circle-bearing sample to exercise the "limited preview -> L2" road.
    w1, w2 = tmp / "chain-a", tmp / "chain-b"
    ok1, chain = _chain(w1, tmp, svg=RECT)
    ok_b, _ = _chain(w2, tmp, svg=RECT)
    add("A1-envelope-exit-codes", ok1 and ok_b, "pure-script chain envelopes valid")
    add("A4-cwd-independent", ok1 and ok_b and tmp.resolve() != ROOT.resolve(),
        "all sub-scripts called by absolute path with temp cwd")

    cmp_files = ["spec.json", "final.svg", "judge.json", "score.json",
                 "acceptance.json", "preview.png", "preview.meta.json"]
    idempotent, detail = True, []
    for f in cmp_files:
        p1, p2 = w1 / f, w2 / f
        if not p1.is_file() or not p2.is_file() or p1.read_bytes() != p2.read_bytes():
            idempotent = False
            detail.append(f)
    add("A3-idempotent-byte-identical", idempotent,
        "differing: " + ",".join(detail) if detail else "judgment JSON byte-identical twice")

    # A2 single source of truth + no hardcoded thresholds
    ok2 = all(k in th for k in ("A", "B", "C", "D", "E", "F", "grade", "iterate",
                                "orchestration", "messages", "calibration_policy",
                                "calibration_log", "score_levels"))
    hard = []
    for s in CORE_SCRIPTS:
        src = (SCRIPTS / s).read_text(encoding="utf-8")
        hits = [t for t in THRESHOLD_LITERALS if t in src]
        if hits or "load_thresholds" not in src:
            hard.append(s + (":" + ",".join(hits) if hits else ":no-load_thresholds"))
    add("A2-thresholds-single-source", ok2 and not hard, ";".join(hard))

    # A5 inline source scan + script/command mapping
    skill_md = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```[^\n]*\n(.*?)```", skill_md, re.S)
    inline = sorted({f for b in blocks for f in INLINE_FEATURES if f in b})
    all_scripts = sorted(p.name for p in SCRIPTS.iterdir() if p.suffix == ".py")
    unmentioned = [s for s in all_scripts if ("scripts/" + s) not in skill_md]
    add("A5-no-inline-source-and-mapping", not inline and not unmentioned,
        "inline=" + ",".join(inline) + " unmentioned=" + ",".join(unmentioned))

    # A6 O3 outward template / generated note
    tpl = (L.templates_dir() / "delivery-note.template.md").read_text(encoding="utf-8")
    dn = w2 / "delivery-note.md"
    dn_text = dn.read_text(encoding="utf-8") if dn.is_file() else ""
    banned = [w for w in O3_BANNED if w in tpl or w in dn_text]
    add("A6-O3-no-excellent-wording", not banned, ",".join(banned))

    # A7 H4 credential-free default chain
    cred_reads = []
    for p in SCRIPTS.glob("*.py"):
        if re.search(r"os\.environ\s*(?:\[|\.get\()\s*[\"']RECRAFT", p.read_text(encoding="utf-8")):
            cred_reads.append(p.name)
    L.assert_no_credentials()
    add("A7-H4-no-credential-value-read", not cred_reads, ",".join(cred_reads))

    # A8 dual-runtime consistency: same input under the default interpreter and
    # under `-I` isolated mode must yield identical scores. pycache is redirected
    # into the temp dir so isolated mode cannot leave residue in the package.
    s1, s2 = _jread(w1 / "score.json"), _jread(w2 / "score.json")
    iso_out = w1 / "score-isolated.json"
    _call("score.py",
          ["--svg", str(w1 / "final.svg"), "--judge", str(w1 / "judge.json"),
           "--spec", str(w1 / "spec.json"), "--out", str(iso_out)],
          tmp, flags=["-I", "-X", "pycache_prefix=" + str(tmp / "pycache")])
    s_iso = _jread(iso_out)
    iso_equal = bool(s_iso) and s_iso.get("scores") == s1.get("scores")
    optional = {"cairosvg": importlib.util.find_spec("cairosvg") is not None,
                "pyyaml": importlib.util.find_spec("yaml") is not None}
    add("A8-dual-runtime-consistent",
        bool(s1) and s1.get("scores") == s2.get("scores") and iso_equal,
        "default_vs_isolated_equal=" + str(iso_equal) +
        " optional=" + json.dumps(optional, sort_keys=True))

    # A9 calibration policy whitelist
    cp = th.get("calibration_policy", {})
    locked = cp.get("locked", [])
    ok9 = ("calibratable" in cp and "A.*" in locked and "iterate.K_max" in locked
           and "H1" in locked and "F.N_*" in locked)
    for rec in th.get("calibration_log", []) or []:
        if rec.get("loosening") and not rec.get("ruling_ref"):
            ok9 = False
    add("A9-calibration-policy", ok9, "locked=" + str(len(locked)))

    # A10 quick must not claim a pass. L2 outranks quick: an unreadable preview
    # is more fundamental than "F not verified", so either non-pass headline is
    # acceptable. Never tweak the work dir's SVG to dodge L2 here.
    _call("aggregate_log.py", ["--run-dir", str(w1), "--quick", "--run-id", "quick-a10",
                               "--out", str(w1 / "quick-acceptance.json")], tmp)
    qa = _jread(w1 / "quick-acceptance.json")
    ok10 = (qa.get("f_verified") is False and
            qa.get("grade") != msg["grade_pass"] and
            qa.get("headline") in (th["orchestration"]["quick_mode"]["headline"],
                                   msg["l2_headline"]))
    add("A10-quick-f-not-verified", ok10,
        "grade=" + str(qa.get("grade")) + " headline=" + str(qa.get("headline")))

    # A11 real fallback PNG + zero-byte placeholder rejected by H2
    png = (w1 / "preview.png").read_bytes() if (w1 / "preview.png").is_file() else b""
    meta = _jread(w1 / "preview.meta.json")
    real_png = (len(png) >= 24 and png[:8] == b"\x89PNG\r\n\x1a\n" and
                png[12:16] == b"IHDR" and png[-8:-4] == b"IEND" and
                meta.get("renderer") == "builtin-fallback" and meta.get("degraded") is True)
    zp = tmp / "zero-png"
    zp.mkdir(parents=True, exist_ok=True)
    for name in ("final.svg", "score.json"):
        if (w1 / name).is_file():
            shutil.copyfile(w1 / name, zp / name)
    (zp / "preview.png").write_bytes(b"")
    _call("aggregate_log.py", ["--run-dir", str(zp), "--out", str(zp / "acceptance.json")], tmp)
    za = _jread(zp / "acceptance.json")
    h2 = za.get("hard_gates", {}).get("H2", {})
    reject = (h2.get("pass") is False and
              any("preview.png" in m for m in h2.get("missing", [])))
    add("A11-fallback-png-strict-H2", real_png and reject,
        "real_png=" + str(real_png) + " zero_byte_rejected=" + str(reject))

    return w1, w2, chain, optional


def _stage_print_route(tmp, add):
    """A42-A46: the supplied photo -> print file route.

    Codifies what one real job taught: a traced photo keeps the studio
    lighting, a vector has no physical size, and a tracer hides its settings
    one level down. Every assertion names its own id in the except handler, so
    one missing dependency reports five honest failures instead of aborting the
    run."""

    # ---- A42/A43/A44: the "supplied photo -> print file" route -------------
    # Codifies what one real job taught: a traced photo keeps the studio
    # lighting, a vector has no physical size, and the tracer hides its settings
    # one level down and punches highlights out if told to ignore white.
    try:
        try:
            from PIL import Image as _Im
        except ImportError:
            # Pillow is optional (render_png / delight use it when present). The
            # print route cannot be exercised without it, so say "not evaluated"
            # instead of turning a missing optional dep into five red assertions.
            for _id in ("A42-delight-flattens-lighting",
                        "A43-export-print-dimensions",
                        "A44-trace-ref-contract",
                        "A45-uniform-error-envelope",
                        "A46-craft-only-landing"):
                add(_id, False, "Pillow not installed", skipped=True)
            return

        def _run_script(name, argv):
            _p = subprocess.run([sys.executable, str(ROOT / "scripts" / name)] + argv,
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=1200)
            try:
                return _p.returncode, json.loads(_p.stdout)
            except Exception:                      # noqa: BLE001
                return _p.returncode, {}

        # A42: delight's contract is now deterministic, not heuristic.
        #  (a) a lit ramp still flattens; (b) with an explicit mask, a blown
        #  specular is repaired; (c) WITHOUT a mask, EVERY near-white pixel is
        #  byte-identical for all five geometries QA found (axial slit,
        #  diagonal slit, curved slit, thin highlight, bridged highlight).
        #  The third check is the root-cause guarantee: no geometry can be a
        #  counterexample because unclassified pixels are never touched.
        import math as _math
        _pw, _ph = 420, 300
        _ramp = _Im.new("RGB", (_pw, _ph), (255, 255, 255))
        _rp = _ramp.load()
        for _y in range(20, _ph - 20):
            for _x in range(20, _pw - 20):
                _k = 0.55 + 0.60 * (_x - 20) / float(_pw - 40)
                _rp[_x, _y] = (int(198 * _k), int(34 * _k), int(40 * _k))
        _a42 = tmp / "a42-ramp.png"; _ramp.save(_a42)
        _rc1, _d1 = _run_script("delight.py",
                                ["--in", str(_a42), "--out", str(tmp / "a42-flat.png"),
                                 "--strength", "1.0", "--blur", "40", "--sat", "1.0", "--json"])
        _before = [_rp[x, 150][0] for x in range(60, _pw - 60)]
        _flat = _Im.open(tmp / "a42-flat.png").convert("RGB")
        _after = [_flat.load()[x, 150][0] for x in range(60, _pw - 60)]
        _r_before, _r_after = max(_before) - min(_before), max(_after) - min(_after)
        _flat_ok = (_rc1 == 0 and _r_before > 40 and _r_after <= 0.40 * _r_before)
        _warn_ok = bool((_d1.get("data") or {}).get("mask_mode") == "conservative")

        # explicit mask repairs a blown specular, and does so for a thin one too
        _masked_ok = True
        for _nm, _im in (("bridged", None), ("thin", None)):
            _sp = _Im.new("RGB", (240, 160), (255, 255, 255))
            _spp = _sp.load()
            for _y in range(40, 120):
                for _x in range(40, 200):
                    _spp[_x, _y] = (198, 34, 40)
            if _nm == "bridged":
                for _y in range(70, 100):
                    for _x in range(100, 140): _spp[_x, _y] = (255, 255, 255)
                for _y in range(0, 70):
                    for _x in range(110, 130): _spp[_x, _y] = (255, 255, 255)
            else:
                for _y in range(60, 110):
                    for _x in range(116, 126): _spp[_x, _y] = (255, 255, 255)
                for _y in range(0, 60):
                    for _x in range(110, 130): _spp[_x, _y] = (255, 255, 255)
            _src = tmp / ("a42-%s.png" % _nm); _sp.save(_src)
            _mk = _Im.new("L", _sp.size, 0)
            _ImDraw = __import__("PIL.ImageDraw", fromlist=["Draw"])
            _ImDraw.Draw(_mk).rectangle([40, 40, 199, 119], fill=255)
            _mkp = tmp / ("a42-%s-mask.png" % _nm); _mk.save(_mkp)
            _dsto = tmp / ("a42-%s-masked.png" % _nm)
            _rc, _dj = _run_script("delight.py",
                                   ["--in", str(_src), "--out", str(_dsto), "--mask", str(_mkp),
                                    "--strength", "0", "--blur", "20", "--json"])
            _c = _Im.open(_dsto).convert("RGB").load()[120, 85]
            _masked_ok = _masked_ok and (_rc == 0
                        and (_dj.get("data") or {}).get("mask_mode") == "explicit"
                        and (_dj.get("data") or {}).get("specular_repaired", 0) > 0
                        and _c[0] > 120 and _c[0] > _c[2] + 30)

        # conservative mode: near-white pixels never change, for every geometry
        _geoms = {}
        _g0 = _Im.new("RGB", (240, 160), (255, 255, 255)); _q = _g0.load()
        for _y in list(range(40, 70)) + list(range(90, 120)):
            for _x in range(40, 200): _q[_x, _y] = (198, 34, 40)
        _geoms["axial"] = _g0
        _g1 = _Im.new("RGB", (240, 160), (198, 34, 40)); _q = _g1.load()
        for _y in range(160):
            for _x in range(240):
                if abs(_x - _y) <= 6: _q[_x, _y] = (255, 255, 255)
        _geoms["diagonal"] = _g1
        _g2 = _Im.new("RGB", (240, 160), (198, 34, 40)); _q = _g2.load()
        for _x in range(240):
            _cy = 80 + 30 * _math.sin(_x / 28.0)
            for _y in range(160):
                if abs(_y - _cy) <= 6: _q[_x, _y] = (255, 255, 255)
        _geoms["curved"] = _g2
        _g3 = _Im.new("RGB", (240, 160), (255, 255, 255)); _q = _g3.load()
        for _y in range(40, 120):
            for _x in range(40, 200): _q[_x, _y] = (198, 34, 40)
        for _y in range(60, 110):
            for _x in range(116, 126): _q[_x, _y] = (255, 255, 255)
        for _y in range(0, 60):
            for _x in range(110, 130): _q[_x, _y] = (255, 255, 255)
        _geoms["thin"] = _g3
        _g4 = _Im.new("RGB", (240, 160), (255, 255, 255)); _q = _g4.load()
        for _y in range(40, 120):
            for _x in range(40, 200): _q[_x, _y] = (198, 34, 40)
        for _y in range(70, 100):
            for _x in range(100, 140): _q[_x, _y] = (255, 255, 255)
        for _y in range(0, 70):
            for _x in range(110, 130): _q[_x, _y] = (255, 255, 255)
        _geoms["bridged"] = _g4
        _invariant_ok = True
        _dye_report = []
        for _gn, _gi in _geoms.items():
            _gs = tmp / ("a42-g-%s.png" % _gn); _gi.save(_gs)
            _go = tmp / ("a42-g-%s-out.png" % _gn)
            _rc, _ = _run_script("delight.py",
                                 ["--in", str(_gs), "--out", str(_go),
                                  "--strength", "0.65", "--blur", "20", "--json"])
            _a, _b = _gi.load(), _Im.open(_go).convert("RGB").load()
            _dye = sum(1 for _y in range(160) for _x in range(240)
                       if min(_a[_x, _y]) >= 250 and _a[_x, _y] != _b[_x, _y])
            _dye_report.append("%s:%d" % (_gn, _dye))
            _invariant_ok = _invariant_ok and _rc == 0 and _dye == 0
        add("A42-delight-flattens-lighting", _flat_ok and _warn_ok and _masked_ok and _invariant_ok,
            "row-range " + str(_r_before) + "->" + str(_r_after)
            + " masked=" + str(_masked_ok) + " near-white-dyed=" + ",".join(_dye_report))

        # A43: physical size, device resolution and bleed must land exactly
        _rc3, _d3 = _run_script("export_print.py",
                                ["--in", str(_a42), "--out", str(tmp / "a43.jpg"),
                                 "--height-mm", "100", "--dpi", "300",
                                 "--bleed-mm", "3", "--json"])
        _e = _d3.get("data") or {}
        _bleed = int(round(3 / 25.4 * 300))
        _th = int(round(100 / 25.4 * 300))
        _tw = int(round(100 * (_pw / float(_ph)) / 25.4 * 300))
        _rc4, _ = _run_script("export_print.py",
                              ["--in", str(_a42), "--out", str(tmp / "a43b.jpg"),
                               "--height-mm", "100", "--dpi", "5", "--json"])
        # a full-bleed red job must not get a white bleed band (QA23 P2-1)
        _red = _Im.new("RGB", (200, 150), (198, 34, 40))
        _a43r = tmp / "a43-fullred.png"
        _red.save(_a43r)
        _rc6, _d6 = _run_script("export_print.py",
                                ["--in", str(_a43r), "--out", str(tmp / "a43-red.jpg"),
                                 "--height-mm", "100", "--dpi", "300", "--bleed-mm", "3",
                                 "--json"])
        _corner = _Im.open(tmp / "a43-red.jpg").convert("RGB").load()[2, 2]
        _bleed_ok = (_rc6 == 0 and _corner[0] > 120 and _corner[0] > _corner[2] + 40)
        # corrupt input must produce the standard envelope, not a traceback (P2-2)
        _bad = tmp / "a43-broken.png"
        _bad.write_bytes(b"\x89PNG\r\n\x1a\nnot-a-png")
        _rc7, _d7 = _run_script("export_print.py",
                                ["--in", str(_bad), "--out", str(tmp / "a43-bad.jpg"),
                                 "--height-mm", "100", "--json"])
        _env_ok = (_rc7 == L.EXIT_ERROR and _d7.get("code") == L.EXIT_ERROR
                   and any(x.get("code") == "E-INTERNAL" for x in _d7.get("errors", [])))
        _ok43 = (_rc3 == 0
                 and _e.get("trim_px") == [_tw, _th]
                 and _e.get("canvas_px") == [_tw + 2 * _bleed, _th + 2 * _bleed]
                 and _e.get("dpi") == 300
                 and _rc4 == L.EXIT_USAGE
                 and _bleed_ok and _env_ok)
        add("A43-export-print-dimensions", _ok43,
            "trim=" + str(_e.get("trim_px")) + " canvas=" + str(_e.get("canvas_px"))
            + " bad-dpi-exit=" + str(_rc4)
            + " red-corner=" + str(_corner) + " corrupt=" + str(_rc7))

        # A44: nested settings + a missing engine must never read as success
        _spec2 = importlib.util.spec_from_file_location(
            "trace_ref_under_test", ROOT / "scripts" / "trace_ref.py")
        _tr = importlib.util.module_from_spec(_spec2)
        _spec2.loader.exec_module(_tr)
        _nested = _tr.trace_params(64, 85, 20, 2, False)
        _nesting_ok = ("params" in _nested and "colors" not in _nested
                       and _nested["params"].get("ignoreWhite") is False)
        _orig = _tr.find_cli
        _tr.find_cli = lambda: None
        _missing = False
        try:
            _tr.require_cli()
        except L.ToolError as _ex:
            _missing = (_ex.exit_code == L.EXIT_MISSING
                        and _ex.code == "E-CAPABILITY-MISSING")
        finally:
            _tr.find_cli = _orig
        add("A44-trace-ref-contract", _nesting_ok and _missing,
            "nested=" + str(_nesting_ok) + " missing-engine=" + str(_missing))

        _corrupt = tmp / "a45-corrupt.png"
        _corrupt.write_bytes(b"\x89PNG\r\n\x1a\nnot-a-png")
        _envs = []
        for _nm, _argv in (("delight.py", ["--in", str(_corrupt), "--out", str(tmp / "a45-d.png")]),
                           ("export_print.py", ["--in", str(_corrupt), "--out",
                                                str(tmp / "a45-e.jpg"), "--height-mm", "100"]),
                           ("trace_ref.py", ["--in", str(_corrupt), "--out", str(tmp / "a45-t.ai")])):
            _rc, _env = _run_script(_nm, _argv + ["--json"])
            _err_codes = [x.get("code") for x in _env.get("errors", [])]
            if _nm == "trace_ref.py" and _orig() is None:
                # Without the tracing engine the capability gate fires before
                # the file is opened, so E-CAPABILITY-MISSING / exit 3 is the
                # correct envelope here. Demanding E-TRACE made this assertion
                # depend on the machine rather than on the code.
                _case_ok = (_rc == L.EXIT_MISSING
                            and _env.get("code") == L.EXIT_MISSING
                            and "E-CAPABILITY-MISSING" in _err_codes)
            else:
                _case_ok = (_rc == L.EXIT_ERROR and _env.get("code") == L.EXIT_ERROR
                            and any(c in ("E-INTERNAL", "E-TRACE") for c in _err_codes))
            _envs.append((_nm, _case_ok))
        _env_ok = all(ok for _, ok in _envs)
        add("A45-uniform-error-envelope", _env_ok,
            ";".join("%s:%s" % (n, ok) for n, ok in _envs))

        # A46: the Craft-only landing path must really produce a PDF/X master
        # with an output intent, bleed boxes and CMYK content -- not just claim
        # it in the deliverable. Skipped (as a skip, never as a pass) when the
        # VectorCraft CLI is absent.
        _lc_svg = tmp / "a46.svg"
        _lc_svg.write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 100" width="200" height="100">'
            '<rect x="0" y="0" width="200" height="100" fill="#D3122A"/>'
            '<circle cx="100" cy="50" r="30" fill="#F5B915"/></svg>', encoding="utf-8")
        _lc_out = tmp / "a46-run"
        _rc9, _d9 = _run_script("land_craft.py",
                                ["--svg", str(_lc_svg), "--out", str(_lc_out),
                                 "--profile", "print", "--bleed-mm", "3",
                                 "--pdf-standard", "pdfX1a", "--run-id", "a46-craft",
                                 "--json"])
        _x = _d9.get("data") or {}
        if _d9.get("errors") and any(e.get("code") == "E-CAPABILITY-MISSING"
                                    for e in _d9["errors"]):
            add("A46-craft-only-landing", None, "VectorCraft CLI unavailable", skipped=True)
        else:
            _px = _x.get("pdf_x") or {}
            _ok46 = (_rc9 == 0 and _x.get("h1_pass") is True
                     and _d9.get("run_id") == "a46-craft"
                     and _x.get("raster") == 0 and _x.get("placed") == 0
                     and (_x.get("paths") or 0) > 0
                     and _px.get("gts_pdfx", 0) >= 1
                     and _px.get("output_intents", 0) >= 1
                     and bool(_px.get("trimbox")) and bool(_px.get("bleedbox"))
                     # two shapes on a 200x100 artboard: page content carries
                     # tens of operators. Counting ICC/font streams as page
                     # content inflates this into the hundreds (P3-6), so the
                     # ceiling is what makes the guard non-vacuous.
                     and 0 < _px.get("cmyk_ops", 0) <= 100
                     and _px.get("colour_ops", 0) > 0
                     and not _x.get("unverified"))
            # the delivery manifest is the client-facing contract: it must
            # pass the schema shipped in the same package, not only look right
            _sch = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "schema_check.py"),
                 str(ROOT / "references" / "schemas" / "deliverable.schema.json"),
                 str(_lc_out / "deliverable.json"), "--json"],
                capture_output=True, text=True, encoding="utf-8", errors="replace")
            _ok46 = _ok46 and _sch.returncode == 0
            # Passing the schema is not enough: it never forbade extra keys, so
            # the Craft manifest could emit a whole block nobody declared (it
            # did: h1_pass, print_pdf, five save_options) and still validate.
            # Diff it here, where a real Craft manifest is already on disk.
            _sj = json.loads((_lc_out / "deliverable.json").read_text(encoding="utf-8"))
            _ce, _, _ct, _cn = _schema_diff(
                _sj, json.loads((ROOT / "references" / "schemas" /
                                 "deliverable.schema.json").read_text(encoding="utf-8")),
                check_missing=False)
            _ok46 = _ok46 and not (_ce or _ct or _cn)
            add("A46-craft-only-landing", _ok46,
                "h1=" + str(_x.get("h1_pass")) + " paths=" + str(_x.get("paths"))
                + " GTS=" + str(_px.get("gts_pdfx")) + " OI=" + str(_px.get("output_intents"))
                + " cmyk=" + str(_px.get("cmyk_ops")) + " schema=" + str(_sch.returncode == 0)
                + " run_id=" + str(_d9.get("run_id"))
                + " undeclared=" + str(_ce or "-") + " typebad=" + str(_ct or "-")
                + " nested=" + str(_cn or "-")
                + " unverified=" + str(_x.get("unverified")))
    except Exception as e:                          # noqa: BLE001
        for _id in ("A42-delight-flattens-lighting",
                    "A43-export-print-dimensions",
                    "A44-trace-ref-contract",
                    "A45-uniform-error-envelope",
                    "A46-craft-only-landing"):
            if _id not in add.seen:
                add(_id, False, type(e).__name__ + ": " + str(e)[:140])


def _stage_legibility(tmp, add):
    """A53: the 16px / grey / silhouette legibility check must fail things.

    Four synthetic cases are rendered and measured. A check that never goes red
    is decoration, so the cases include a low-contrast pair and a mud-coloured
    palette that a naive implementation would pass."""

    # ---- A53: the 16px / grey / silhouette check must actually fail things ---
    # Three failures are invisible at preview size: ink that vanishes at 16px,
    # ink that fills the tile, and a foreground too close to its background.
    # Each PNG below is generated with a known answer, so this tests the
    # measurement, not the code path. When Pillow is absent the script must say
    # so (exit 3 + E-RENDERER-MISSING) instead of guessing from the SVG.
    try:
        import contextlib
        import io
        import struct

        def _png53(name, paint):
            w = h = 240
            raw = b"".join(b"\x00" + b"".join(bytes(paint(x, y))
                                              for x in range(w))
                           for y in range(h))

            def _chunk(tag, data):
                return (struct.pack(">I", len(data)) + tag + data
                        + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))
            blob = (b"\x89PNG\r\n\x1a\n"
                    + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                    + _chunk(b"IDAT", zlib.compress(raw, 6))
                    + _chunk(b"IEND", b""))
            p = tmp / (name + ".png")
            p.write_bytes(blob)
            return p

        def _disk53(fill):
            def paint(x, y):
                d2 = (x - 120) ** 2 + (y - 120) ** 2
                return fill if d2 <= 90 ** 2 else (255, 255, 255)
            return paint

        def _ring53(x, y):
            d2 = (x - 120) ** 2 + (y - 120) ** 2
            return (0, 0, 0) if 89 ** 2 <= d2 <= 90 ** 2 else (255, 255, 255)

        def _grid53(x, y):
            return (0, 0, 0) if x % 3 == 0 or y % 3 == 0 else (255, 255, 255)

        _cases53 = [("a53-good", _disk53((17, 17, 17)), 0, "OK"),
                    ("a53-low", _disk53((208, 208, 208)), 1, "LOW-CONTRAST"),
                    ("a53-mud", _grid53, 1, "MUD"),
                    ("a53-ring", _ring53, 1, "COLLAPSED")]
        try:
            import PIL  # noqa: F401
            _has53 = True
        except ImportError:
            _has53 = False
        _bad53 = []
        if _has53:
            for _n53, _paint53, _want_rc53, _want53 in _cases53:
                _p53 = _png53(_n53, _paint53)
                _rc53, _env53, _ = _call("legibility.py", ["--png", str(_p53), "--json"], tmp)
                _ids53 = {v.get("id") for v in
                          ((_env53.get("data") or {}).get("verdicts") or [])}
                if _rc53 != _want_rc53 or _want53 not in _ids53:
                    _bad53.append(_n53 + ":rc" + str(_rc53) + ":" + str(sorted(_ids53)))

        _spec53 = importlib.util.spec_from_file_location("legibility_a53",
                                                         SCRIPTS / "legibility.py")
        _lg53 = importlib.util.module_from_spec(_spec53)
        _spec53.loader.exec_module(_lg53)

        class _BlockPIL:
            def find_spec(self, name, path=None, target=None):
                if name == "PIL" or name.startswith("PIL."):
                    raise ImportError("blocked by A53")
                return None

        _blocker53 = _BlockPIL()
        _saved53 = {k: sys.modules.pop(k) for k in list(sys.modules)
                    if k == "PIL" or k.startswith("PIL.")}
        _buf53 = io.StringIO()
        _argv53 = sys.argv
        sys.meta_path.insert(0, _blocker53)
        sys.argv = ["legibility.py", "--png", str(tmp / "a53-good.png")]
        try:
            with contextlib.redirect_stdout(_buf53):
                _rc53d = _lg53.main()
        finally:
            sys.argv = _argv53
            sys.meta_path.remove(_blocker53)
            sys.modules.update(_saved53)
        try:
            _env53d = json.loads(_buf53.getvalue() or "{}")
        except json.JSONDecodeError:
            _env53d = {}
        _deg53 = (_rc53d == L.EXIT_MISSING and _env53d.get("status") == "degraded"
                  and "E-RENDERER-MISSING" in _codes(_env53d))
        if _has53:
            add("A53-legibility-fails-what-the-eye-misses",
                bool(not _bad53 and _deg53),
                "cases=" + str([c[0] for c in _cases53]) + " bad=" + str(_bad53)
                + " degraded=" + str(_deg53))
        else:
            # The four measurement cases need Pillow to read the PNGs. Without
            # it they cannot run, and calling that a failure is wrong: the
            # dependency-free promise is about the test suite, not about
            # legibility.py. The degraded path is still exercised and reported.
            add("A53-legibility-fails-what-the-eye-misses", False,
                "Pillow missing; measurement cases not evaluated; degraded="
                + str(_deg53), skipped=True)
    except Exception as e:                      # noqa: BLE001
        add("A53-legibility-fails-what-the-eye-misses", False,
            type(e).__name__ + ": " + str(e)[:140])


def _stage_studio_chain(tmp, add):
    """A50: one command must run the stages in order, and stop honestly.

    studio.py exists so a human does not have to remember six commands, which
    means its value is entirely in the order, the stop on failure and the
    degraded exit. All three are pinned here against a fake stage runner."""

    # ---- A50: one command must run the stages in order and stop honestly ----
    # studio.py exists so a human does not have to remember six commands. The
    # risk is that it quietly skips a gate or keeps going after one fails, so
    # the stage sequence, the stop on failure and the degraded exit are pinned
    # with a stubbed runner -- no engine, no renderer, no network.
    try:
        import contextlib
        import io

        _spec50 = importlib.util.spec_from_file_location("studio_a50",
                                                         SCRIPTS / "studio.py")
        _st50 = importlib.util.module_from_spec(_spec50)
        _spec50.loader.exec_module(_st50)
        _out50 = tmp / "a50"
        _out50.mkdir()
        _calls50 = []
        _state50 = {"fail_spec": False, "land_missing": False,
                    "real_preview": False, "legible_fail": False}

        def _fake50(stage, args, timeout=1800):
            _calls50.append((stage, list(args)))
            if _state50["fail_spec"] and stage == "spec":
                return 1, {"status": "fail",
                           "errors": [{"code": "E-EMPTY-SPEC", "msg": "empty"}]}
            if stage == "render":
                if _state50["real_preview"]:
                    _o = list(args)[list(args).index("--out") + 1]
                    Path(_o).write_bytes(b"\x89PNG\r\n\x1a\nstub-preview")
                    return 0, {"status": "ok", "errors": []}
                return 0, {"status": "degraded", "errors": []}
            if stage == "legible" and _state50["legible_fail"]:
                return 1, {"status": "ok",
                           "errors": [{"code": "COLLAPSED",
                                       "msg": "no ink left at 16px"}]}
            if stage == "land" and _state50["land_missing"]:
                return 3, {"status": "degraded",
                           "errors": [{"code": "E-CAPABILITY-MISSING", "msg": "no cli"}]}
            return 0, {"status": "ok", "errors": []}

        _orig50 = _st50._call
        _buf50 = io.StringIO()
        try:
            _st50._call = _fake50
            _args50 = argparse.Namespace(spec="s.json", svg="d.svg", out=str(_out50),
                                         judge="", reference="", profile="screen")
            with contextlib.redirect_stdout(_buf50):
                _rc50 = _st50._run_chain(_args50, _out50)
            _sj50 = json.loads((_out50 / "studio.json").read_text(encoding="utf-8"))
            _order50 = [s["stage"] for s in _sj50["stages"]]
            _land50 = [a for (st, a) in _calls50 if st == "land"]
            _clean50 = [a for (st, a) in _calls50 if st == "clean"]
            _landed_sanitized = bool(_land50 and any("sanitized.svg" in x
                                                     for x in _land50[0]))
            _goto_land = bool(_clean50 and any("d.svg" in x for x in _clean50[0]))

            # Second half: a blocking spec must end the chain at the first stage
            _state50["fail_spec"] = True
            _calls50.clear()
            _out50b = tmp / "a50b"
            _out50b.mkdir()
            _buf50b = io.StringIO()
            with contextlib.redirect_stdout(_buf50b):
                _rc50b = _st50._run_chain(argparse.Namespace(
                    spec="s.json", svg="d.svg", out=str(_out50b),
                    judge="", reference="", profile="screen"), _out50b)
            _order50b = [st for (st, a) in _calls50]

            # Third half: the engine is missing. Land reports EXIT_MISSING and
            # the chain must pass 3 through instead of flattening it to 0/1.
            _state50["fail_spec"] = False
            _state50["land_missing"] = True
            _calls50.clear()
            _out50c = tmp / "a50c"
            _out50c.mkdir()
            _buf50c = io.StringIO()
            with contextlib.redirect_stdout(_buf50c):
                _rc50c = _st50._run_chain(argparse.Namespace(
                    spec="s.json", svg="d.svg", out=str(_out50c),
                    judge="", reference="", profile="screen"), _out50c)
            _env50c = {}
            try:
                _env50c = json.loads(_buf50c.getvalue() or "{}")
            except json.JSONDecodeError:
                _env50c = {}
            _codes50c = [e.get("code") for e in (_env50c.get("errors") or [])]

            # Fourth half: a real preview that fails the 16px check must stop
            # before judge and land -- a mark nobody can read at 16px is not
            # landable just because it is legal vector.
            _state50["land_missing"] = False
            _state50["real_preview"] = True
            _state50["legible_fail"] = True
            _calls50.clear()
            _out50d = tmp / "a50d"
            _out50d.mkdir()
            _buf50d = io.StringIO()
            with contextlib.redirect_stdout(_buf50d):
                _rc50d = _st50._run_chain(argparse.Namespace(
                    spec="s.json", svg="d.svg", out=str(_out50d),
                    judge="j.json", reference="", profile="screen"), _out50d)
            _order50d = [st for (st, a) in _calls50]
            add("A50-studio-chain-orders-and-stops",
                bool(_rc50 == 3 and _order50 == ["spec", "clean", "render",
                                                 "legible", "judge", "land"]
                     and _landed_sanitized and _goto_land
                     and _sj50["status"] == "degraded"
                     and _rc50b == 1 and _order50b == ["spec"]
                     and _rc50c == 3 and "E-CAPABILITY-MISSING" in _codes50c
                     and _rc50d == 1 and _order50d == ["spec", "clean", "render",
                                                       "legible"]),
                "rc=" + str(_rc50) + " order=" + str(_order50)
                + " land_args_ok=" + str(_landed_sanitized)
                + " clean_args_ok=" + str(_goto_land)
                + " stop_rc=" + str(_rc50b) + " stop=" + str(_order50b)
                + " missing_rc=" + str(_rc50c) + " codes=" + str(_codes50c)
                + " legible_fail_rc=" + str(_rc50d) + " order=" + str(_order50d))
        finally:
            _st50._call = _orig50
    except Exception as e:                      # noqa: BLE001
        add("A50-studio-chain-orders-and-stops", False,
            type(e).__name__ + ": " + str(e)[:140])


def _stage_silent_cli(tmp, add):
    """A48: a silent CLI must not inherit the previous run's outputs.

    A CLI that exits 0 without writing anything is not a success: the old
    final.ai and preview.png would be read as this run's product. The reverse
    case is pinned too -- when the cleanup itself cannot delete a locked file,
    the run must refuse to continue instead of reporting success."""

    # ---- A48: a CLI that exits 0 without writing must not inherit outputs ----
    # "the file is there" was the only evidence that the engine produced
    # anything, and a previous run's final.ai / preview.png satisfied it. A CLI
    # that reports success and writes nothing is a silent failure, not a crash,
    # so the check has to be "was it written now", which the landers enforce by
    # clearing their targets before invoking the engine. This drives land_vc's
    # real _run with a patched runner that claims success and writes nothing.
    try:
        import contextlib
        import io

        _spec48 = importlib.util.spec_from_file_location("land_vc_a48",
                                                         SCRIPTS / "land_vc.py")
        _lv48 = importlib.util.module_from_spec(_spec48)
        _spec48.loader.exec_module(_lv48)
        _d48 = tmp / "a48"
        _d48.mkdir()
        (_d48 / "final.ai").write_bytes(b"%PDF-1.4 previous-run")
        (_d48 / "preview.png").write_bytes(b"\x89PNG\r\n\x1a\nprevious-run")
        (_d48 / "preview.meta.json").write_text('{"renderer": "previous"}', encoding="utf-8")
        _lv48.find_cli = lambda explicit: Path(sys.executable)
        _lv48.run_cli = lambda *a, **k: (0, "", "")   # claims success, writes nothing
        _a48 = argparse.Namespace(svg=str(GOOD / "rect-only.svg"), out=str(_d48),
                                  profile="screen", vc="", run_id="a48", json=True)
        _buf48 = io.StringIO()
        with contextlib.redirect_stdout(_buf48):
            _rc48 = _lv48._run(_a48, _d48)
        _l48 = _landing(_d48)
        _gone48 = (not (_d48 / "final.ai").exists()
                   and not (_d48 / "preview.png").exists())

        # Second half: the cleanup itself fails (a locked file). Continuing would
        # read the old file as this run's, so the run must refuse with a code --
        # not warn and carry on.
        _d48b = tmp / "a48b"
        _d48b.mkdir()
        (_d48b / "final.ai").write_bytes(b"%PDF-1.4 locked-previous-run")
        _orig48 = _lv48.LP._drop_outputs
        _buf48b = io.StringIO()
        try:
            _lv48.LP._drop_outputs = lambda d, n: False
            _a48b = argparse.Namespace(svg=str(GOOD / "rect-only.svg"), out=str(_d48b),
                                       profile="screen", vc="", run_id="a48b", json=True)
            with contextlib.redirect_stdout(_buf48b):
                _rc48b = _lv48._run(_a48b, _d48b)
        finally:
            _lv48.LP._drop_outputs = _orig48
        _env48b = {}
        try:
            _env48b = json.loads(_buf48b.getvalue() or "{}")
        except json.JSONDecodeError:
            _env48b = {}
        _codes48b = [e.get("code") for e in (_env48b.get("errors") or []) if isinstance(e, dict)]
        _l48b = _landing(_d48b)
        _locked48 = (_rc48b == L.EXIT_ERROR and "E-OUTPUT-STALE" in _codes48b
                     and _l48b.get("status") == "fail")
        add("A48-silent-cli-cannot-inherit-outputs",
            bool(_rc48 != 0 and _gone48 and _l48.get("status") == "fail"
                 and _locked48),
            "rc=" + str(_rc48) + " old_gone=" + str(_gone48)
            + " landing=" + str(_l48.get("status"))
            + " locked_rc=" + str(_rc48b) + " locked_codes=" + str(_codes48b))
    except Exception as e:                      # noqa: BLE001
        add("A48-silent-cli-cannot-inherit-outputs", False,
            type(e).__name__ + ": " + str(e)[:140])


def _stage_p2_survivors(tmp, add):
    """A36-A41: the P2 items that survived five QA rounds.

    Each assertion is written so that reverting its fix turns exactly that
    assertion red; a guard that cannot fail is decoration. All of them drive the
    shipping seam (land_vc / land_craft / the font probe) rather than the
    helpers behind it, because the original defects lived in the wiring, and
    guards aimed at the helpers stayed green when the wiring was reverted."""

    # A36-A38: the three P2 items that survived five QA rounds (report
    # QA报告-第17轮.md §6). Each assertion is written so that reverting the
    # fix makes exactly that assertion red -- a guard that cannot fail is
    # decoration, not evidence. All three drive land_vc._build_deliverable,
    # the seam main() itself calls: the original defects lived in the wiring
    # (hardcoded icc, a second write after construction, `.split()[-1]`), and
    # guards aimed at the helpers stayed green when the wiring was reverted.
    # A36 additionally checked a second engine's request/claim symmetry through
    # its JSX template. r45 removed that engine and the template, so the guard
    # below drives the shipping chain and mutation-tests the profile constant.
    try:
        _s36b = importlib.util.spec_from_file_location("r24_land_vc", SCRIPTS / "land_vc.py")
        _lv36 = importlib.util.module_from_spec(_s36b)
        _s36b.loader.exec_module(_lv36)

        _obs36 = {"has_ai_private": True, "content_ops": {}, "content_streams": {},
                  "color_mode": "cmyk", "device_cmyk": 0, "device_rgb": 1,
                  "iccbased": 0, "output_intent": 0, "artboard_pt": [200, 200],
                  "sha256": "0" * 64}
        _kv36 = {"layers": "5", "notes": ""}

        # A36: a print delivery asks for an embedded ICC and never claims the
        # request was honoured. The request must come from PROFILE_DEFAULTS,
        # not from a literal, and the unverified bucket must name the missing
        # /OutputIntent. The mutation below replaces the old AST scan: flipping
        # the constant and re-running the seam proves the value flows through.
        _icc_print = _lv36.PROFILE_DEFAULTS["print"]["icc"] is True
        _icc_screen = _lv36.PROFILE_DEFAULTS["screen"]["icc"] is False
        _d_vc = _lv36._build_deliverable(tmp / "a.ai", "print",
                                         _lv36.PROFILE_DEFAULTS["print"],
                                         _obs36, _kv36,
                                         "vectorcraft-cli 0.22.0", [], None)
        _u_vc = [x for x in _d_vc["unverified"] if "OutputIntent" in x]
        _saved36 = _lv36.PROFILE_DEFAULTS["print"]["icc"]
        _lv36.PROFILE_DEFAULTS["print"]["icc"] = False
        try:
            _d_mut36 = _lv36._build_deliverable(tmp / "a.ai", "print",
                                                _lv36.PROFILE_DEFAULTS["print"],
                                                _obs36, _kv36,
                                                "vectorcraft-cli 0.22.0", [], None)
        finally:
            _lv36.PROFILE_DEFAULTS["print"]["icc"] = _saved36
        _mut36 = (_d_mut36["save_options"]["embed_icc"] is False
                  and not any("OutputIntent" in x for x in _d_mut36["unverified"]))
        _ok36 = (bool(_u_vc) and _d_vc["save_options"]["embed_icc"] is True
                 and _icc_print and _icc_screen and _mut36
                 and _d_vc["profile"] == "print"
                 and not any("未嵌入 ICC" in x for x in _d_vc["warnings"]))
        add("A36-icc-request-not-claimed", _ok36,
            "print unverified=" + str(len(_u_vc))
            + " embed_icc=" + str(_d_vc["save_options"]["embed_icc"])
            + " profile_defaults=" + str((_icc_print, _icc_screen))
            + " mutation_follows=" + str(_mut36))

        # A37: a warning emitted by three steps must appear once, and the
        # deliverable must carry that exact de-duplicated list (not a second
        # copy written after construction).
        # The steps are built by the parser that reads the CLI's own output,
        # because that is where the warnings enter the chain. QA round 19's M43
        # dropped `result.warnings` inside parse_jsonl and every assertion
        # stayed green while tool_warnings silently became [].
        _jsonl37 = "\n".join([
            '{"step": "import", "result": {"warnings": ["font missing: Helvetica"]}}',
            '{"step": "save", "result": {"warnings": ["font missing: Helvetica", '
            '"no output intent"]}}',
            "not a json line",
        ])
        _steps37 = _lv36.parse_jsonl(_jsonl37)
        _w37 = _lv36._collect_export_warnings(_steps37)
        # Through the seam: covers the de-duplication AND the `tool_warnings=`
        # wiring. Feeding the helper directly left the wiring blind, which is
        # precisely where the r23 defect lived (a second write after the fact).
        _d37 = _lv36._build_deliverable(tmp / "a.ai", "print",
                                        _lv36.PROFILE_DEFAULTS["print"],
                                        _obs36, _kv36,
                                        "vectorcraft-cli 0.22.0", _steps37, None)
        _ok37 = (_w37 == ["font missing: Helvetica", "no output intent"]
                 and len(_steps37) == 2          # the non-JSON line is not a step
                 and _d37["tool_warnings"] == _w37
                 and len(_d37["tool_warnings"]) == len(set(_d37["tool_warnings"])))
        add("A37-tool-warnings-deduplicated", _ok37,
            "in deliverable.json: tool_warnings=" + str(_d37["tool_warnings"]))

        # A38: a version string with a trailing build/platform suffix must
        # yield the dotted version, and an unparseable one must say unknown.
        _cases38 = [("vectorcraft-cli 0.22.0 (build 20261101 win-x64)", "0.22.0"),
                    ("tool 1.2", "1.2"), ("no version here", "unknown"),
                    ("", "unknown")]
        # Through the seam: the field a client reads is deliverable["engine_version"].
        # Calling the parser directly stays green even if the wiring goes back to
        # `.split()[-1]` -- and that call site was where the r23 defect lived.
        _got38 = []
        for _txt, _ in _cases38:
            _d38 = _lv36._build_deliverable(tmp / "a.ai", "print",
                                            _lv36.PROFILE_DEFAULTS["print"],
                                            _obs36, _kv36, _txt, [], None)
            _got38.append((_txt, _d38.get("engine_version")))
        _ok38 = all(g == e for (_, g), (_, e) in zip(_got38, _cases38))
        add("A38-engine-version-parser", _ok38,
            "parse=" + str(_got38))

        # A39: probe_source is the only place the CLI's "which fonts did you
        # substitute" answer enters the manifest -- invisible in file size, path
        # counts and layer counts alike. Two rounds of QA broke the first
        # version of this guard: M44 (return unavailable unconditionally) was
        # caught, but the payload was invented ({"name", "status":
        # "substituted"}), so land_vc's consumer -- `if f.get("missing")` -- had
        # no coverage at all (QA round 20, M50/M51), and the two error branches
        # could be flipped to available=True unnoticed (M45/M53). The payload
        # below is a trimmed copy of real `vectorcraft info` output.
        _real_run_cli39 = _lv36.run_cli

        def _fake39(payload, rc=0):
            def _run(cli, argv, timeout=300):
                return rc, payload, ""
            return _run

        _font39 = {"family": "DefinitelyMissingFontXYZ", "style": "Regular",
                   "status": "missing", "missing": True, "missingGlyphs": 0,
                   "objects": 1, "runs": 1,
                   "resolved": {"family": "Source Sans 3", "style": "Regular"}}
        _payload39 = json.dumps({"fonts": [_font39],
                                 "importWarnings": ["font substituted: DefinitelyMissingFontXYZ"],
                                 "info": {"objects": 12, "colorMode": "CMYK"}})
        try:
            _lv36.run_cli = _fake39(_payload39)
            _p39 = _lv36.probe_source(tmp / "vectorcraft-cli", tmp / "a.svg")
            _w39 = _lv36._font_warnings(_p39)
            # The CLI omits `resolved` when it could not name a replacement; the
            # fallback text is then what a client reads, so it is asserted too.
            _lv36.run_cli = _fake39(json.dumps({
                "fonts": [{"family": "NoResolveFont", "status": "missing", "missing": True}],
                "importWarnings": [], "info": {"objects": 1, "colorMode": "CMYK"}}))
            _p39b = _lv36.probe_source(tmp / "vectorcraft-cli", tmp / "a.svg")
            _w39b = _lv36._font_warnings(_p39b)
            _lv36.run_cli = _fake39("boom", rc=1)
            _rc39 = _lv36.probe_source(tmp / "vectorcraft-cli", tmp / "a.svg")
            _lv36.run_cli = _fake39("this is not json")
            _js39 = _lv36.probe_source(tmp / "vectorcraft-cli", tmp / "a.svg")
        finally:
            _lv36.run_cli = _real_run_cli39
        # Matched by regex + groups, not by exact text: QA round 21 found that
        # a punctuation change turns an exact-string check red for a reason no
        # client would ever notice. The substance -- which font, replaced by
        # what -- is what the assertion is about.
        _pat39 = r"源文件字体缺失：(.+?)，产物中已用 (.+?) 替代"
        _m39 = re.search(_pat39, _w39[0]) if _w39 else None
        _m39b = re.search(_pat39, _w39b[0]) if _w39b else None
        _ok39 = (_p39.get("available") is True
                 and _p39.get("fonts") == [_font39]
                 and _p39.get("import_warnings") == ["font substituted: DefinitelyMissingFontXYZ"]
                 and _p39.get("objects") == 12
                 and _p39.get("color_mode") == "CMYK"
                 and _m39 is not None
                 and _m39.group(1) == "DefinitelyMissingFontXYZ"
                 and _m39.group(2) == "Source Sans 3"
                 and _m39b is not None
                 and _m39b.group(1) == "NoResolveFont"
                 and _m39b.group(2) == "未知替代字体"
                 and _rc39.get("available") is False
                 and _js39.get("available") is False)
        add("A39-font-probe-carried", _ok39,
            "available=" + str(_p39.get("available"))
            + " fonts=" + str(len(_p39.get("fonts") or []))
            + " warn=" + str(len(_w39))
            + " fallback=" + str(len(_w39b))
            + " family=" + str(_m39.group(1) if _m39 else None)
            + " rc_branch=" + str(_rc39.get("available"))
            + " nonjson=" + str(_js39.get("available")))

        # (r45) A40's AST scan was retired with the JSX template it guarded.
        # The replacement below guards the same property behaviourally: a
        # failed landing must not leave the previous run's H1 evidence behind.
        _hyg = tmp / "a40-hygiene"
        _hygc = tmp / "a40-hygiene-craft"

        def _stale40(where):
            d = where or _hyg
            d.mkdir(parents=True, exist_ok=True)
            (d / "structure_readback.json").write_text(json.dumps({
                "schema": "vector-ai-studio/structure-readback-v1",
                "ok": True, "raster": 0, "placed": 0, "paths": 5,
                "artboard_matches_viewBox": True,
                "source": "vectorcraft-info"}), encoding="utf-8")
            (d / "final.ai").write_bytes(b"%PDF-1.4 stale-from-previous-run")
            # H2 only asks whether preview.png parses, so a failed run that keeps
            # the previous design's picture still looks complete. The seed makes
            # that observable: every failure exit below must clear it.
            (d / "preview.png").write_bytes(b"\x89PNG\r\n\x1a\nstale-picture")
            (d / "preview.meta.json").write_text('{"renderer": "stale"}', encoding="utf-8")

        def _prev40_gone(where):
            d = where or _hyg
            return not (d / "preview.png").exists() and not (d / "preview.meta.json").exists()

        def _sr40_gone(where):
            p = (where or _hyg) / "structure_readback.json"
            if not p.is_file():
                return True
            try:
                return json.loads(p.read_text(encoding="utf-8")).get("ok") is False
            except (OSError, json.JSONDecodeError):
                return False

        # (a) engine absent: degraded L4, stale evidence removed
        _stale40(_hyg)
        _rc40a, _, _ = _call("land_vc.py",
                             ["--svg", str(GOOD / "sample.svg"), "--out", str(_hyg),
                              "--vc", str(tmp / "definitely-no-cli.exe"), "--json"], tmp)
        _l40a = _landing(_hyg)
        _ok40a = (_rc40a == 3 and _sr40_gone(_hyg) and _prev40_gone(_hyg)
                  and _l40a.get("status") == "degraded"
                  and _l40a.get("stale_readback_removed") is True
                  and _l40a.get("stale_preview_removed") is True)

        # (b) source SVG missing: fail landing, stale evidence removed
        _stale40(_hyg)
        _rc40b, _, _ = _call("land_vc.py",
                             ["--svg", str(tmp / "missing.svg"),
                              "--out", str(_hyg), "--json"], tmp)
        _l40b = _landing(_hyg)
        _ok40b = (_rc40b == 2 and _sr40_gone(_hyg) and _prev40_gone(_hyg)
                  and _l40b.get("status") == "fail")

        # (c) the print sibling has the same hygiene on its failure exit.
        # Its own directory on purpose: sharing (b)'s would let (b)'s fail
        # landing satisfy this clause even if land_craft wrote nothing, which is
        # exactly the bug this assertion exists to catch. Exit is 2 (no SVG) or
        # 3 (no CLI on this machine); 0 must never appear.
        _stale40(_hygc)
        _rc40c, _, _ = _call("land_craft.py",
                             ["--svg", str(tmp / "missing.svg"),
                              "--out", str(_hygc), "--json"], tmp)
        _l40c = _landing(_hygc)
        _ok40c = (_rc40c in (2, 3) and _sr40_gone(_hygc) and _prev40_gone(_hygc)
                  and _l40c.get("status") == "fail"
                  and _l40c.get("stale_readback_removed") is True
                  and _l40c.get("stale_preview_removed") is True)
        add("A40-failed-landing-clears-stale-evidence",
            bool(_ok40a and _ok40b and _ok40c),
            "vc_no_cli=" + str(_ok40a) + " vc_no_svg=" + str(_ok40b)
            + " craft_no_svg=" + str(_ok40c))


        # A41: E is the one dimension that is a set comparison with its own
        # thresholds, and until r28 every fixture recorded E="N/A" -- the branch
        # that computes Jaccard had never run in any test (QA round 20 found the
        # gap while reviewing what E covers). Three cases: an exact reference
        # must score excellent, a disjoint one must fail, no reference must stay
        # N/A (L5: restricted capability, not a silent pass).
        # The reference is written out by hand from reading sample.svg, NOT
        # produced by score.py's own extractor: QA round 21 showed that a
        # reference built from _svg_tokens() passes even when the extractor is
        # broken (both sides rot together). The expected Jaccard values below
        # are hand-computed from the intersection/union definition.
        _svg41 = FIX / "good" / "sample.svg"
        _exact41 = {"schema": "vector-ai-studio/reference-v1",
                    "palette": ["#111111", "#2f6fed", "#ffb020", "#ffffff"],
                    "stroke_width": ["3.0"], "corner_radius": [], "font_family": []}
        # 2 of 4 palette colours hit, stroke identical, the two empty buckets
        # deliberately non-empty in the reference so their Jaccard is 0.
        _partial41 = {"schema": "vector-ai-studio/reference-v1",
                      "palette": ["#2f6fed", "#ffb020"], "stroke_width": ["3.0"],
                      "corner_radius": ["4.0"], "font_family": ["SomeFont"]}
        # Everything identical except the palette (2 of 4): the average 0.875 is
        # above the excellent threshold, so only the palette rule can keep this
        # from being excellent. QA round 21's D7 deleted that rule and went
        # unnoticed.
        _half41 = {"schema": "vector-ai-studio/reference-v1",
                   "palette": ["#2f6fed", "#ffb020"], "stroke_width": ["3.0"],
                   "corner_radius": [], "font_family": []}
        _ref41 = tmp / "ref_exact.json"
        _pre41 = tmp / "ref_partial.json"
        _pha41 = tmp / "ref_half.json"
        L.write_json_atomic(_ref41, _exact41)
        L.write_json_atomic(_pre41, _partial41)
        L.write_json_atomic(_pha41, _half41)
        _common41 = ["--svg", str(_svg41), "--judge", str(FIX / "good" / "judge.json"),
                     "--spec", str(FIX / "good" / "spec.json"), "--json"]
        _e41 = {}
        for _name41, _ref41p in (("exact", _ref41), ("partial", _pre41), ("half", _pha41)):
            _rc41, _env41, _ = _call("score.py", _common41 + ["--reference", str(_ref41p)], tmp)
            _e41[_name41] = ((_env41.get("data") or {}).get("scores") or {}).get("E")
        _rc41n, _env41n, _ = _call("score.py", _common41, tmp)
        _e41["none"] = ((_env41n.get("data") or {}).get("scores") or {}).get("E")
        _th41 = L.load_thresholds()
        _lv41 = _th41["score_levels"]
        _ex41 = _e41["exact"] if isinstance(_e41["exact"], dict) else {}
        _pa41 = _e41["partial"] if isinstance(_e41["partial"], dict) else {}
        _ha41 = _e41["half"] if isinstance(_e41["half"], dict) else {}
        _jac41 = (_ex41.get("measured") or {}).get("jaccard", {})
        _n_tok41 = len(_th41["E"]["tokens"])
        _ok41 = (_ex41.get("score") == _lv41["excellent"]
                 and len(_jac41) == _n_tok41 == 4
                 and set(_jac41.values()) == {1.0}
                 # partial: 2/4 palette = 0.5, stroke = 1.0, two empty buckets = 0
                 and (_pa41.get("measured") or {}).get("jaccard") == {
                     "palette": 0.5, "stroke_width": 1.0,
                     "corner_radius": 0.0, "font_family": 0.0}
                 and abs(float((_pa41.get("measured") or {}).get("average", -1)) - 0.375) < 1e-9
                 and _pa41.get("score") == _lv41["fail"]
                 # half: average 0.875 >= excellent threshold, palette 0.5 < 1.0
                 # -> must be pass, not excellent
                 and _ha41.get("score") == _lv41["pass"]
                 and _e41["none"] == "N/A")
        add("A41-brand-reference-jaccard", _ok41,
            "E exact=" + str(_ex41.get("score")) + " partial=" + str(_pa41.get("score"))
            + " half=" + str(_ha41.get("score")) + " none=" + str(_e41["none"])
            + " tokens=" + str(len(_jac41)))
    except Exception as e:                      # noqa: BLE001
        # Only fill in assertions that never ran. Re-adding an ID that already
        # produced a result would give one guard two contradictory verdicts.
        for _id in ("A36-icc-request-not-claimed",
                    "A37-tool-warnings-deduplicated",
                    "A38-engine-version-parser",
                    "A39-font-probe-carried",
                    "A40-failed-landing-clears-stale-evidence",
                    "A41-brand-reference-jaccard"):
            if _id not in add.seen:
                add(_id, False, type(e).__name__ + ": " + str(e)[:140])


def _stage_alt_engine(tmp, add):
    """A30-A31: the alternate engine's artefacts must survive the chain.

    A hardcoded engine version, a missing structure_readback.json or missing
    final.svg / preview.png all left every other gate green while the chain was
    broken end to end. When the optional CLI is absent the assertions report
    skipped, never passed -- unless a real run was explicitly requested, in
    which case a known miss must outrank an unknown."""

    # A30: the alternate engine's artefacts must survive the DOWNSTREAM chain.
    #
    # The defects this guards against were all invisible to the other 30
    # assertions: a hardcoded engine version, a missing structure_readback.json
    # (which makes aggregate_log mark H1 "unverified" for a perfectly good
    # file), and missing final.svg/preview.png (which makes H2 fail). Every
    # gate was green while the chain was broken end to end.
    #
    # When the optional CLI is absent the check reports skipped, never passed.
    a30_ok, a30_detail, a30_skip = False, "not run", False
    try:
        _spec2 = importlib.util.spec_from_file_location("land_vc_under_test",
                                                        ROOT / "scripts" / "land_vc.py")
        _lv = importlib.util.module_from_spec(_spec2)
        _spec2.loader.exec_module(_lv)
        _cli = _lv.find_cli("")
        if _cli is None:
            a30_skip, a30_ok = True, True
            a30_detail = "skipped: vectorcraft-cli not found (optional engine)"
        else:
            # A SQUARE sample cannot detect a transposed or scaled artboard:
            # 200x200 -> [200,200] either way. Generate a non-square one so the
            # width/height comparison in this assertion actually discriminates.
            wide = tmp / "wide.svg"
            wide.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 300 200" '
                'width="300" height="200">'
                '<rect x="10" y="10" width="120" height="80" fill="#2f6fed"/>'
                '<circle cx="220" cy="100" r="60" fill="#ffb020"/></svg>',
                encoding="utf-8")
            vdir = tmp / "vc-chain"
            rc_l, env_l, _ = _call("land_vc.py",
                                   ["--svg", str(wide),
                                    "--out", str(vdir), "--profile", "print",
                                    "--run-id", "a30", "--json"], tmp)
            rc_a, env_a, _ = _call("aggregate_log.py",
                                   ["--run-dir", str(vdir), "--run-id", "a30", "--json"], tmp)
            # aggregate_log puts the acceptance fields at data level; older
            # envelopes nested them under "acceptance". Accept both rather than
            # betting on one shape -- betting on one shape is exactly how this
            # assertion first reported a false red.
            _data = env_a.get("data") or {}
            _acc = _data.get("acceptance") or _data
            _gates = _acc.get("hard_gates") or _data.get("hard_gates") or {}
            h1 = (_gates.get("H1") or {}).get("pass") is True
            h2 = (_gates.get("H2") or {}).get("pass") is True
            # Read the artefact back and check the *emitted* numbers. The
            # in-code guard checked the raw rect, so reverting only the value
            # that gets written left every gate green with artboard=[200,-200].
            _sr = {}
            try:
                _sr = json.loads((vdir / "structure_readback.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
            _ab = _sr.get("artboard") or []
            _vb = _sr.get("viewBox") or []
            ab_positive = (len(_ab) == 2 and all(isinstance(x, (int, float)) and x > 0 for x in _ab))
            # "> 0" only catches sign errors. A transposed or scaled value still
            # passes it, and nothing compared the emitted numbers to the source
            # dimensions -- so a 2% inflation or a swap was invisible to every
            # gate. Compare the artefact against viewBox with the tolerance the
            # in-code dims_ok already uses; that kills sign, transpose, scale and
            # unit errors at once without inventing a new threshold.
            ab_matches_vb = (len(_ab) == 2 and len(_vb) == 4 and
                             abs(_ab[0] - _vb[2]) <= 1 and abs(_ab[1] - _vb[3]) <= 1)
            deliv = (env_l.get("data") or {}).get("deliverable") or {}
            ver = str(deliv.get("engine_version") or "")
            # A version string that is not what the binary reports means the
            # manifest is guessing again.
            ver_ok = bool(ver) and ver != "unknown"
            a30_ok = (rc_l == 0 and h1 and h2 and ver_ok and ab_positive and ab_matches_vb)
            a30_detail = ("land=" + str(rc_l) + " h1=" + str(h1) + " h2=" + str(h2) +
                          " version=" + (ver or "?") +
                          " artboard=" + str(_ab) + " viewBox=" + str(_vb) +
                          " positive=" + str(ab_positive) +
                          " matches_viewBox=" + str(ab_matches_vb) +
                          " agg_exit=" + str(rc_a))
    except Exception as e:                      # noqa: BLE001
        a30_detail = type(e).__name__ + ": " + str(e)[:140]
    add("A30-vectorcraft-chain-h1-h2", a30_ok, a30_detail, skipped=a30_skip)

    # A31: the manifest must not drift from its own schema, in BOTH directions.
    #
    # Round 8 found 13 fields emitted but undeclared on one engine and absent on
    # the other, and the schema still validated because it never forbade extra
    # keys. A contract that validates while omitting half the payload is worse
    # than no contract: it certifies nothing.
    #
    # r45 removed the synthetic half that built an Illustrator manifest through
    # its own constructor. That half exercised the constructor, not the chain
    # feeding it: deleting `engine` from the real chain still left it green.
    # The only manifest that ships now is the one land_vc writes, so that is
    # what gets diffed -- produced by a real run, not by hand.
    a31_ok, a31_detail, a31_skip = False, "not run", False
    try:
        _schema = json.loads((ROOT / "references" / "schemas" /
                              "deliverable.schema.json").read_text(encoding="utf-8"))
        _real_missing = os.environ.get("VECTOR_AI_STUDIO_REAL_AI") == "1"




        # --- the only shipping manifest: produced by a real land_vc run ---
        vc_extra = vc_missing = vc_typebad = vc_nested = []
        if _cli is None:
            # A missing engine normally downgrades this assertion to a skip,
            # which is honest: the manifest genuinely was not produced. But
            # "real coverage was requested and did not happen" is a known miss,
            # not an unknown, so failure has to outrank skip here.
            a31_skip = not _real_missing
            a31_ok = not _real_missing
            a31_detail = ("vectorcraft skipped (no CLI)"
                          + (" but REAL_AI was requested" if _real_missing else ""))
        else:
            vdir2 = tmp / "schema-diff-vc"
            _call("land_vc.py", ["--svg", str(GOOD / "sample.svg"),
                                 "--out", str(vdir2), "--profile", "print",
                                 "--run-id", "a31", "--json"], tmp)
            vc_doc = json.loads((vdir2 / "deliverable.json").read_text(encoding="utf-8"))
            vc_extra, vc_missing, vc_typebad, vc_nested = _schema_diff(
                vc_doc, _schema, optional=CRAFT_ONLY_KEYS)
            # The bidirectional diff only compares keys and primitive types; it
            # does not evaluate `const`, so a wrong or deleted engine still read
            # as conformant. Run the published validator as well.
            _rc31, _, _ = _call("schema_check.py",
                                [str(ROOT / "references" / "schemas" /
                                     "deliverable.schema.json"),
                                 str(vdir2 / "deliverable.json"), "--json"], tmp)
            # The run asked for --profile print: that request must reach the
            # manifest. A hardcoded "screen" profile (or dropping the prof
            # argument) would leave every key present and still be wrong.
            _prof31 = (vc_doc.get("profile") == "print"
                       and (vc_doc.get("save_options") or {}).get("cmyk") is True
                       and vc_doc.get("engine") == "vectorcraft")
            a31_ok = (_rc31 == 0 and _prof31
                      and not (vc_extra or vc_missing or vc_typebad or vc_nested))
            a31_detail = ("vc extra=" + str(vc_extra or "-") +
                          " missing=" + str(vc_missing or "-") +
                          " typebad=" + str(vc_typebad or "-") +
                          " nested=" + str(vc_nested or "-") +
                          " schema_check=" + str(_rc31) +
                          " profile_wired=" + str(_prof31) +
                          " engine=" + str(vc_doc.get("engine")))
    except Exception as e:                      # noqa: BLE001
        a31_detail = type(e).__name__ + ": " + str(e)[:140]
    add("A31-deliverable-schema-no-drift", a31_ok, a31_detail, skipped=a31_skip)


# ---- error-code literal scanning (A28 / A80, r71) ---------------------------
# A code literal counts when it is quoted AND immediately closed by a quote or
# followed by a colon (E-X: message, without quotes around the code), so
# warnings written as plain strings (E-REGRESSION: ...) are visible to the
# registry check too -- the old quote-delimited pattern could only see fully
# quoted codes. The E-XXX placeholder is a documentation convention, never a
# real code, and stays out of the scan; prose ("E- mail") never matches
# because the pattern requires uppercase alphanumerics right after "E-".
_CODE_PLACEHOLDERS = frozenset({"E-XXX"})
_CODE_RE = re.compile(r"""["'](E-[A-Z0-9-]+)(?::|["'])""")


def scan_code_literals(src):
    """Error-code literals in one source text, placeholder-free."""
    return set(_CODE_RE.findall(src)) - _CODE_PLACEHOLDERS


def registry_codes():
    """Registered live codes from error-codes.md (已废弃 rows are display-only)."""
    reg_path = ROOT / "references" / "error-codes.md"
    reg_set = set()
    if reg_path.is_file():
        for line in reg_path.read_text(encoding="utf-8").splitlines():
            if "已废弃" in line:
                continue
            reg_set |= set(re.findall(r"`(E-[A-Z0-9-]+)`", line))
    return reg_set


def _stage_contract_registry(tmp, add, w1):
    """A26-A28: the contract documents must agree with the artefacts.

    The SKILL.md frontmatter mirrors author/version under metadata, the
    published JSON Schema must accept a real log and reject broken ones, and
    every error code the scripts emit must be registered -- checked in both
    directions, because a one-way check lets stale rows accumulate."""

    # A26: the top-level contract fields and their metadata mirror must agree.
    # Two specifications read this file (internal contract.yaml wants them at
    # top level; the public Agent Skills spec puts author/version under
    # metadata). Keeping both copies is only safe if a machine compares them.
    def _fm():
        txt = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        body = txt.split("---", 2)[1]
        top, meta, cur = {}, {}, None
        for raw in body.splitlines():
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            if raw[0] in " \t":
                if cur:
                    k, _, v = raw.strip().partition(":")
                    meta[k.strip()] = v.strip().strip('"').strip("'")
                continue
            k, _, v = raw.partition(":")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if v == "":
                cur = k
            else:
                cur = None
                top[k] = v
        return top, meta

    _top, _meta = _fm()
    pairs = [(k, _top.get(k), _meta.get(k)) for k in
             ("author", "version", "description_zh", "description_en")]
    mirror_ok = all(m is not None and t == m for _, t, m in pairs)
    add("A26-frontmatter-metadata-mirror", mirror_ok,
        "pairs=" + str([(k, t == m) for k, t, m in pairs]) +
        " | 解析器仅支持扁平 YAML：若改用块标量(>- / |)等结构会在此判红，属预期")

    # A27: the published JSON Schema must accept a real log and reject broken
    # ones. A schema nobody validates against is documentation, not a contract.
    schema_path = ROOT / "references" / "schemas" / "acceptance.schema.json"
    acc_path = w1 / "acceptance.json"
    sch_ok = False
    detail = "schema or acceptance missing"
    if schema_path.is_file() and acc_path.is_file():
        rc_ok, _, _ = _call("schema_check.py",
                            [str(schema_path), str(acc_path), "--json"], tmp)
        acc_doc = json.loads(acc_path.read_text(encoding="utf-8"))

        bad_dir = tmp / "schema-negative"
        bad_dir.mkdir(parents=True, exist_ok=True)
        missing_mode = dict(acc_doc)
        missing_mode.pop("mode", None)
        bad_mode_path = bad_dir / "missing-mode.json"
        bad_mode_path.write_text(json.dumps(missing_mode, ensure_ascii=False), encoding="utf-8")
        rc_miss, _, _ = _call("schema_check.py",
                              [str(schema_path), str(bad_mode_path), "--json"], tmp)

        bad_grade = dict(acc_doc)
        bad_grade["grade"] = "优秀"
        bad_grade_path = bad_dir / "bad-grade.json"
        bad_grade_path.write_text(json.dumps(bad_grade, ensure_ascii=False), encoding="utf-8")
        rc_grade, _, _ = _call("schema_check.py",
                               [str(schema_path), str(bad_grade_path), "--json"], tmp)

        sch_ok = (rc_ok == 0 and rc_miss == 1 and rc_grade == 1)
        detail = ("valid_exit=" + str(rc_ok) +
                  " missing_mode_exit=" + str(rc_miss) +
                  " bad_grade_exit=" + str(rc_grade))
    add("A27-schema-accepts-real-rejects-broken", sch_ok, detail)

    # A28: every error code emitted by the scripts must be registered, and the
    # registry must not list codes nobody emits. Checked in both directions --
    # a one-way check lets stale entries accumulate, which is how a registry
    # stops being trusted.
    reg_path = ROOT / "references" / "error-codes.md"
    code_set = set()
    for sp in sorted((ROOT / "scripts").glob("*.py")):
        _src = sp.read_text(encoding="utf-8")
        # Either quote style counts, and a quoted code followed by a colon
        # (E-X: message) counts too -- the strict fully-quoted pattern was
        # blind to codes carried in plain-string warnings, which is how
        # E-REGRESSION stayed unregistered for a whole round.
        code_set |= scan_code_literals(_src)
    # The registry keeps retired codes on purpose (downstream code may still
    # branch on them), so a row marked 已废弃 is documentation, not a claim
    # that some script can emit it. Counting it as live made the two sets
    # impossible to satisfy at once.
    reg_set = registry_codes()
    reg_ok = bool(code_set) and code_set == reg_set
    add("A28-error-code-registry", reg_ok,
        "in_code=" + str(len(code_set)) + " in_registry=" + str(len(reg_set)) +
        " unregistered=" + ",".join(sorted(code_set - reg_set)[:6]) +
        " stale=" + ",".join(sorted(reg_set - code_set)[:6]))

    # A80 (F4 / r71): the widened scan must actually catch an unregistered
    # code, must not swallow the E-XXX placeholder, and must leave prose
    # alone. The sample is assembled from pieces so this file's own source
    # contains no quoted E-NEW-CODE literal (A28 scans selftest.py as well).
    _p1, _p2, _p3 = "E-", "NEW-CODE", "XXX:"
    _sample = ("x = err(\"" + _p1 + _p2 + "\", \"m\")   # see \"" + _p1 + _p3 +
               " placeholder, and E- mail prose")
    _got80 = scan_code_literals(_sample)
    _unreg80 = _got80 - registry_codes()
    _reg80 = scan_code_literals("'E-SCHEMA'") - registry_codes()
    # The expected set is assembled at runtime: a quoted E-NEW-CODE literal in
    # this file's source would itself be scanned by A28 above.
    _new_code = _p1 + _p2
    add("A80-registry-scan-catches-unregistered",
        bool(_got80 == {_new_code} and _unreg80 == {_new_code}
             and _reg80 == set()),
        "got=" + ",".join(sorted(_got80)) +
        " unregistered=" + ",".join(sorted(_unreg80)) +
        " registered_clean=" + str(_reg80 == set()))


def _stage_smoke_rating(tmp, add):
    """A81 (F1-B / r71): smoke's verdict must follow the acceptance verdict.

    The whole smoke chain needs a real engine, but the rating decision itself
    is a pure function (smoke_ai.rate_acceptance) -- pin it directly. An
    internal verdict that is not a clean pass must never surface as smoke's
    self-reported ok; that silent flip was the F1 defect."""

    # ---- A81: 内部评级非 clean pass 时不得自报 ok ----
    try:
        _spec81 = importlib.util.spec_from_file_location("smoke_a81",
                                                         SCRIPTS / "smoke_ai.py")
        _sm81 = importlib.util.module_from_spec(_spec81)
        _spec81.loader.exec_module(_sm81)
        _pass = "达标"
        _ok81 = _sm81.rate_acceptance(0, "ok", _pass, _pass) == ("ok", None)
        _f1 = _sm81.rate_acceptance(1, "fail", "不达标", _pass)[0] == "fail"
        # The exact F1 shape: H1 passed (exit could even be 0 for a
        # non-grading path) but the grade is not the pass grade -> never ok.
        _f2 = _sm81.rate_acceptance(0, "ok", "不达标", _pass)[0] != "ok"
        _f3 = _sm81.rate_acceptance(2, "error", None, _pass)[0] != "ok"
        # aggregate_log's own L2 verdict (status "degraded", exit 1) is an
        # honest degradation for smoke too, mirroring the same vocabulary.
        _s, _d = _sm81.rate_acceptance(1, "degraded", "不达标", _pass,
                                       {"level": "L2", "reason": "r"})
        _d1 = _s == "degraded" and isinstance(_d, dict) and _d.get("level") == "L2"
        _d2 = _sm81.rate_acceptance(3, "degraded", None, _pass)[0] == "degraded"
        add("A81-smoke-rating-follows-acceptance",
            bool(_ok81 and _f1 and _f2 and _f3 and _d1 and _d2),
            "ok_pass=%s fail_exit1=%s fail_grade=%s fail_exit2=%s"
            " degraded_l2=%s degraded_exit3=%s"
            % (_ok81, _f1, _f2, _f3, _d1, _d2))
    except Exception as e:                      # noqa: BLE001
        add("A81-smoke-rating-follows-acceptance", False,
            type(e).__name__ + ": " + str(e)[:140])


def _stage_perceptual(tmp, add):
    """A33-A35: the perceptual layer, driven end to end.

    The reference vectors must still reproduce, the contrast path must catch a
    dichromatic collapse that WCAG alone passes, and a dense path must keep its
    evidence in the file a reviewer opens -- not merely in a library return
    value. A32 is deliberately absent: A3 already byte-compares seven artefacts
    across two runs, so a second assertion for the same property would inflate
    the count without adding coverage."""

    # Determinism: NOT added as a new assertion. QA asked for "same input twice,
    # identical numbers" when the perceptual layer brought floating point in.
    # A3-idempotent-byte-identical already runs the whole chain twice on one
    # input and byte-compares seven artefacts including score.json, which is
    # strictly stronger than re-running score.py alone.
    #
    # This was verified rather than assumed: injecting a set-ordered field into
    # score.json (string hash randomisation reorders it per process) turned BOTH
    # a candidate A32 and the existing A3 red. A second assertion for a property
    # the first one already catches would inflate the count without adding
    # coverage -- exactly the sort of decorative guard this suite exists to
    # avoid. A3 had simply never been mutation-tested before.

    # A33: the perceptual primitives still reproduce their published reference
    # vectors. Deliberately a re-run of the module's own self-check rather than
    # a copy of its numbers: a matrix edited in the module would otherwise be
    # checked against a fixture carrying the same mistake.
    try:
        import perceptual as _P
        problems = _P.selfcheck()
        add("A33-perceptual-reference-vectors", not problems,
            ("dE2000 max err %.3e over %d pairs; 4 contrast anchors; "
             "2 CVD pins; 9 matrix row sums = 1"
             % (_P.sharma_max_error(), len(_P.SHARMA_VECTORS)))
            if not problems else "; ".join(problems[:3]))
    except Exception as e:                      # noqa: BLE001
        add("A33-perceptual-reference-vectors", False,
            type(e).__name__ + ": " + str(e)[:140])

    # A34: the contrast path actually runs, and it catches the case that
    # motivated the whole perceptual layer.
    #
    # Without a declared pair the check reports not_applicable, which is honest
    # but means the code is never exercised -- a guard that no default run
    # reaches is a guard nobody would notice breaking. This drives a real pair
    # through score.py: pure red on black. WCAG alone passes it at 5.25:1; the
    # dichromatic observer sees 1.26:1. If the simulated-observation layer is
    # ever dropped or the matrices rot, this goes red.
    try:
        spec34 = json.loads((GOOD / "spec.json").read_text(encoding="utf-8"))
        spec34["contrast_pairs"] = [{"fg": "#FF0000", "bg": "#000000",
                                     "role": "meaning"}]
        p34 = tmp / "a34-spec.json"
        p34.write_text(json.dumps(spec34), encoding="utf-8")
        out34 = tmp / "a34-score.json"
        _call("score.py", ["--svg", str(GOOD / "sample.svg"),
                           "--judge", str(GOOD / "judge.json"),
                           "--spec", str(p34), "--out", str(out34),
                           "--run-id", "a34", "--json"], tmp)
        s34 = json.loads(out34.read_text(encoding="utf-8"))
        c34 = s34["scores"]["C"]["measured"]["perceptual"]["contrast"]
        pair = (c34.get("pairs") or [{}])[0]
        caught = (c34.get("status") == "warn"
                  and pair.get("status") == "fail"
                  and pair.get("min", 9) < 3.0
                  and pair.get("normal", 0) > 4.5)
        add("A34-contrast-catches-cvd-collapse", caught,
            "normal=" + str(pair.get("normal")) + " min=" + str(pair.get("min"))
            + " status=" + str(c34.get("status")))
    except Exception as e:                      # noqa: BLE001
        add("A34-contrast-catches-cvd-collapse", False,
            type(e).__name__ + ": " + str(e)[:140])

    # A35: a path that trips the short-segment escape valve must still leave
    # evidence -- in the FILE, not merely in the library return value.
    #
    # The escape valve (borrowed from fontspector) exists so that genuinely
    # detailed artwork is not punished for being detailed. The first
    # implementation wrote `short += count if not dense else 0`, which kept the
    # path out of the ratio -- correct -- and ALSO threw away the count, so
    # "a circle cut into 200 chords" and "nothing to report" looked identical.
    #
    # The first version of THIS assertion called analyze_svg() directly and
    # checked its return dict. That proved the library kept the numbers while
    # score.json still dropped them: the fix had reached the function and
    # stopped there. Same shape as the failures this project keeps closing --
    # measured, but not in any file a reader opens. So it now runs score.py
    # end to end and reads score.json, which is what a reviewer actually gets.
    try:
        import math as _m
        _n = 200
        _d = "M" + " L".join(
            "%.4f,%.4f" % (100 + 70 * _m.cos(2 * _m.pi * i / _n),
                           100 + 70 * _m.sin(2 * _m.pi * i / _n))
            for i in range(_n)) + " Z"
        _svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 200">'
                '<g id="a"><path d="' + _d + '" fill="none" stroke="#000"/></g></svg>')
        _p35 = tmp / "a35-flattened.svg"
        _p35.write_text(_svg, encoding="utf-8")
        _out35 = tmp / "a35-score.json"
        _call("score.py", ["--svg", str(_p35),
                           "--judge", str(GOOD / "judge.json"),
                           "--spec", str(GOOD / "spec.json"),
                           "--out", str(_out35),
                           "--run-id", "a35", "--json"], tmp)
        _d35 = json.loads(_out35.read_text(encoding="utf-8"))["scores"]["D"]["measured"]
        _turns = _d35.get("dense_turns_deg") or []
        kept = (_d35.get("dense_paths") == 1
                and _d35.get("dense_short_segments") == _n
                and len(_turns) == 1 and _turns[0] < 5.0
                and (_d35.get("dense_corner_fracs") or [1])[0] == 0.0)
        add("A35-dense-path-keeps-evidence", kept,
            "in score.json: dense=" + str(_d35.get("dense_paths"))
            + " kept_short=" + str(_d35.get("dense_short_segments"))
            + " turns=" + str(_turns)
            + " corner=" + str(_d35.get("dense_corner_fracs")))
    except Exception as e:                      # noqa: BLE001
        add("A35-dense-path-keeps-evidence", False,
            type(e).__name__ + ": " + str(e)[:140])


def _stage_measurement(tmp, add):
    """A29: the measurement function must actually measure.

    A well-formed synthetic stream is not enough to prove it: strict zlib
    decompression accepts that too. The sample below truncates the trailing
    adler32 the way Illustrator writes it, which is the difference between a
    counter that works and one that silently reports "undecodable"."""

    # A29: the measurement function must actually measure.
    #
    # Round 6 shipped a colour-operator counter that returned None on *every*
    # real deliverable because zlib.decompress() rejected the trailing CR/LF.
    # The function existed, was called, and produced a plausible-looking field
    # ("undecodable") -- and no assertion noticed.
    #
    # A well-formed synthetic stream is not enough to catch that: strict
    # decompression handles it fine. The sample below truncates the trailing
    # adler32 so the stream is incomplete, which is what Illustrator actually
    # writes. Strict decompression fails on it; decompressobj succeeds. That
    # difference is the whole point, so it is the sample this assertion uses.
    a29_ok, a29_detail = False, "not run"
    try:
        _spec = importlib.util.spec_from_file_location("lib_pdf_under_test",
                                                       ROOT / "scripts" / "lib_pdf.py")
        _mod = importlib.util.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)

        payload = b"0.2 0.4 0.6 rg\n0 0 0 1 K\n"
        complete = zlib.compress(payload)
        truncated = complete[:-4]                      # drop adler32 -> incomplete
        body = (b"<< /Length 99 /Filter /FlateDecode >>\nstream\n" +
                truncated + b"\nendstream\n")
        ops, decoded = _mod._count_ops([body])

        # Declared Flate, undecodable: None, not a guessed number.
        junk = (b"<< /Length 4 /Filter /FlateDecode >>\nstream\n"
                b"\x00\x01\x02\x03\nendstream\n")
        ops_junk, decoded_junk = _mod._count_ops([junk])

        # Stored uncompressed, which the PDF format allows. This read as
        # "decode failure" and the whole page went unmeasured.
        plain = b"<< /Length 22 >>\nstream\n1 0 0 0 k\n0 0 0 1 K\nendstream\n"
        ops_plain, dec_plain = _mod._count_ops([plain])

        # Inline image samples are binary, not operators. The end is computed
        # from the dict's /W /H /BPC /CS, not from the first whitespace " EI ":
        # this sample contains those two bytes, and scanning for them ended the
        # image early so the tail of the bitmap was counted (k=2, true value 1).
        inline_sample = b"\x01\x02 EI k\x03\x00\x00\x00\x00"      # 12 = 3*1*4*8/8
        inline = (b"<< /Length 99 >>\nstream\n"
                  b"0 0 0 1 k\nq BI /W 3 /H 1 /BPC 8 /CS /CMYK ID " +
                  inline_sample + b" EI Q\n"
                  b"endstream\n")
        ops_inline, dec_inline = _mod._count_ops([inline])

        # A dict this reader cannot size is unmeasured, never half-counted: the
        # sample would otherwise leak operators, and truncating the tail would
        # hide the ones after it.
        inline_unknown = (b"<< /Length 99 >>\nstream\n"
                          b"0 0 0 1 k\nq BI /W 3 /H 1 /F /AHx ID \x01\x02 EI\n"
                          b"endstream\n")
        ops_iunk, dec_iunk = _mod._count_ops([inline_unknown])

        # `/BI` is a name, not an inline image start. Excluding only word
        # characters matched it, jumped to the next " EI ", and lost the k
        # operator sitting between the two.
        slash_bi = (b"<< /Length 99 >>\nstream\n"
                    b"/BI 0 0 0 1 k\n/ID x EI\n0 0 0 1 K\nendstream\n")
        ops_bi, dec_bi = _mod._count_ops([slash_bi])

        # Scope guard: counting every decodable stream inflated the number ~40x
        # on real files because ICC profiles and font programs contain k/K
        # bytes. A page must only count its /Contents, so this fixture puts a
        # decoy stream (20 k operators) in an object the page never references.
        decoy = zlib.compress(b"0 0 0 1 k\n" * 20)
        pdf = (b"%PDF-1.6\n"
               b"1 0 obj\n<< /Type /Page /Contents 3 0 R >>\nendobj\n"
               b"3 0 obj\n" + body + b"endobj\n"
               b"5 0 obj\n<< /Subtype /Image >>\nstream\n" + decoy +
               b"\nendstream\nendobj\n")
        scoped, scoped_stats = _mod._content_ops(pdf)

        # Page dictionary packed into an /ObjStm: there is no `N 0 obj` header
        # for it, so the whole file used to read as "0 objects" -- a silent miss
        # that looked exactly like a clean run.
        inner = b"<< /Type /Page /Contents 7 0 R >>"
        packed = b"6 0 " + inner                    # one pair: object 6 at 0
        objstm = zlib.compress(packed)
        pdf_os = (b"%PDF-1.6\n"
                  b"5 0 obj\n<< /Type /ObjStm /N 1 /First 4 /Length " +
                  str(len(objstm)).encode() + b" /Filter /FlateDecode >>\nstream\n" +
                  objstm + b"\nendstream\nendobj\n"
                  b"7 0 obj\n<< /Length 13 >>\nstream\n0 0 0 1 K\nendstream\nendobj\n")
        scoped_os, stats_os = _mod._content_ops(pdf_os)

        # /N smaller than the packed pair count. The page dictionary sat in the
        # pair /N said did not exist, so it was never read: the file reported
        # zero page streams and no operator -- a miss indistinguishable from a
        # clean run. The packed count is authoritative; the mismatch is warned.
        decoy_dict = b"<< /Type /Metadata >>"                 # 20 bytes
        page_dict = b"<< /Type /Page /Contents 10 0 R >>"
        packed2 = (b"6 0 7 " + str(len(decoy_dict)).encode() + b" " +
                   decoy_dict + page_dict)
        objstm2 = zlib.compress(packed2)
        warn_os = []
        pdf_os2 = (b"%PDF-1.6\n"
                   b"5 0 obj\n<< /Type /ObjStm /N 1 /First 10 /Length " +
                   str(len(objstm2)).encode() + b" /Filter /FlateDecode >>\nstream\n" +
                   objstm2 + b"\nendstream\nendobj\n"
                   b"10 0 obj\n<< /Length 13 >>\nstream\n0 0 0 1 K\nendstream\nendobj\n")
        scoped_n, stats_n = _mod._content_ops(pdf_os2, warn_os)

        # Hand-computed reference, not score.py's own extractor: comparing the
        # engine against itself passes on any bug the two share. Counted by
        # reading the stream: two k, two K, one rg, nothing else.
        fixed = (b"<< /Length 99 >>\nstream\n"
                 b"1 0 0 0 k\n0 0.5 0 0 k\n0 0 0 1 K\n0 0 1 rg\nq 0 0 0 1 K Q\n"
                 b"endstream\n")
        ops_fixed, _dec_fixed = _mod._count_ops([fixed])
        expect_fixed = {"k": 2, "K": 2, "rg": 1, "RG": 0, "g": 0, "G": 0,
                        "sc": 0, "SC": 0, "scn": 0, "SCN": 0}

        # 1/2/4 bpc has two readings: one packed bit stream, or rows padded to
        # a byte boundary. Here both ends land on a whitespace " EI ", so the
        # file does not say which one produced it. Taking either is a guess;
        # the stream is reported unmeasured instead.
        subbit = (b"<< /Length 99 >>\nstream\n"
                  b"q BI /W 3 /H 2 /BPC 1 /CS /G ID \x00k EI Q\n0 0 0 1 k\n"
                  b"endstream\n")
        ops_sub, dec_sub = _mod._count_ops([subbit])

        # A /Contents reference pointing at a missing object: refs and objects
        # must not collapse into one number, or a page that was never read
        # reports the same count as a page that was measured.
        pdf_ref = (b"%PDF-1.6\n"
                   b"3 0 obj\n<< /Type /Page /Contents [4 0 R 9 0 R] >>\nendobj\n"
                   b"4 0 obj\n<< /Length 13 >>\nstream\n0 0 0 1 K\nendstream\nendobj\n")
        scoped_ref, stats_ref = _mod._content_ops(pdf_ref)

        # None means "nothing decoded". Normalise to empty so a real failure
        # reports its numbers instead of dying on a subscript and hiding which
        # clause broke.
        ops = ops or {}
        ops_plain = ops_plain or {}
        ops_inline = ops_inline or {}
        ops_bi = ops_bi or {}
        scoped = scoped or {}
        scoped_os = scoped_os or {}
        scoped_stats = scoped_stats if isinstance(scoped_stats, dict) else {}
        stats_os = stats_os if isinstance(stats_os, dict) else {}
        scoped_n = scoped_n or {}
        scoped_ref = scoped_ref or {}
        stats_ref = stats_ref if isinstance(stats_ref, dict) else {}
        stats_n = stats_n if isinstance(stats_n, dict) else {}

        # The Craft path must report the same numbers on the same bytes: one
        # reader, not two that agree until someone edits one of them.
        a29_pdf = tmp / "a29.pdf"
        a29_pdf.write_bytes(pdf)
        _spec_lc = importlib.util.spec_from_file_location(
            "land_craft_under_test", ROOT / "scripts" / "land_craft.py")
        _lc = importlib.util.module_from_spec(_spec_lc)
        _spec_lc.loader.exec_module(_lc)
        craft_facts = _lc._pdf_facts(a29_pdf)

        # A page whose streams cannot be decoded is "not measured", not "no
        # colour operator". Craft used to fill zeros here while Illustrator
        # reported null, so the same undecodable file read as "CMYK absent" on
        # one engine and "unknown" on the other.
        undec = (b"%PDF-1.6\n"
                 b"1 0 obj\n<< /Type /Page /Contents 3 0 R >>\nendobj\n"
                 b"3 0 obj\n<< /Length 4 /Filter /FlateDecode >>\nstream\n"
                 b"\x00\x01\x02\x03\nendstream\nendobj\n")
        a29_undec = tmp / "a29-undecodable.pdf"
        a29_undec.write_bytes(undec)
        ai_undec, ai_undec_stats = _mod._content_ops(undec)
        craft_undec = _lc._pdf_facts(a29_undec)

        # One intent object, one catalog array. /OutputIntents is the array that
        # points at the intent, so counting it as an intent published 2 for a
        # file that declares 1 -- and only lib_pdf counted it, so the two
        # landers reported different numbers for the same bytes.
        intent_pdf = (b"%PDF-1.6\n"
                      b"1 0 obj\n<< /Type /Catalog /OutputIntents [ 2 0 R ] >>\nendobj\n"
                      b"2 0 obj\n<< /Type /OutputIntent /S /GTS_PDFX >>\nendobj\n")
        a29_intent = tmp / "a29-intent.pdf"
        a29_intent.write_bytes(intent_pdf)
        intent_shared = _mod._object_counts(intent_pdf)["output_intent"]
        intent_ai = _mod._observe_ai(a29_intent)["output_intent"]
        intent_craft = _lc._pdf_facts(a29_intent)

        # The schema is the single source of truth for the operator key set.
        # r36 widened the code from 4 keys to 10 while the schema still declared
        # 4, and every gate stayed green because additionalProperties is open --
        # the drift was invisible. Comparing the two makes it red instead.
        _decl_doc = json.loads(
            (ROOT / "references" / "schemas" / "deliverable.schema.json")
            .read_text(encoding="utf-8"))
        _decl_keys = set(
            _decl_doc["properties"]["observed"]["properties"]["content_ops"]["properties"])

        # One Type0 font is two /BaseFont entries (the composite and its
        # descendant CIDFont) but a single embedded program. Counting entries
        # made the manifest disagree with vectorcraft's `info` on the same file.
        fonts = _mod._font_programs(
            b"1 0 obj << /BaseFont /Stone-Bold /DescendantFonts [2 0 R] >> endobj\n"
            b"2 0 obj << /BaseFont /Stone-Bold-CID /FontDescriptor 3 0 R >> endobj\n"
            b"3 0 obj << /FontFile2 4 0 R >> endobj\n")

        # .get() throughout: a missing key must read as "0", so the message says
        # which clause is wrong instead of raising KeyError and hiding it.
        a29_ok = (ops.get("rg") == 1 and ops.get("K") == 1 and decoded == 1 and
                  ops_junk is None and decoded_junk == 0 and
                  ops_plain.get("k") == 1 and ops_plain.get("K") == 1 and dec_plain == 1 and
                  ops_inline.get("k") == 1 and ops_inline.get("G") == 0 and
                  ops_inline.get("K") == 0 and dec_inline == 1 and
                  ops_iunk is None and dec_iunk == 0 and
                  ops_bi.get("k") == 1 and ops_bi.get("K") == 1 and dec_bi == 1 and
                  ops_sub is None and dec_sub == 0 and
                  ops_fixed == expect_fixed and
                  scoped_ref.get("K") == 1 and stats_ref.get("refs") == 2 and
                  stats_ref.get("objects") == 1 and stats_ref.get("decoded") == 1 and
                  scoped_os.get("k") == 0 and scoped_os.get("K") == 1 and
                  stats_os.get("objects") == 1 and stats_os.get("decoded") == 1 and
                  scoped_n.get("K") == 1 and stats_n.get("objects") == 1 and
                  stats_n.get("decoded") == 1 and len(warn_os) == 1 and
                  craft_facts["content_ops"] == scoped and
                  craft_facts["content_streams"]["decoded"] == 1 and
                  craft_facts["content_streams"].get("refs") == 1 and
                  ai_undec is None and ai_undec_stats.get("decoded") == 0 and
                  craft_undec["content_ops"] is None and
                  craft_undec["content_streams"]["decoded"] == 0 and
                  intent_shared == 1 and intent_ai == 1 and
                  intent_craft.get("output_intent") == 1 and
                  intent_craft.get("output_intent_objects") == 1 and
                  scoped.get("k") == 0 and scoped.get("K") == 1 and scoped.get("rg") == 1 and
                  scoped_stats.get("objects") == 1 and
                  scoped_stats.get("refs") == 1 and
                  scoped_stats.get("decoded") == 1 and fonts == 1 and
                  set(scoped) == _decl_keys)
        # Short discriminating values first, then the long dictionary: add()
        # truncates at 220 chars, and the tail used to be cut off exactly when
        # a red assertion needed it (the whole dict fit, the verdict did not).
        a29_detail = ("k=" + str(scoped.get("k")) + "/0 K=" + str(scoped.get("K")) + "/1" +
                      " objects=" + str(scoped_stats.get("objects")) + "/1" +
                      " refs=" + str(scoped_stats.get("refs")) + "/1" +
                      " decoded=" + str(scoped_stats.get("decoded")) + "/1" +
                      " objstm=" + str(stats_os.get("objects")) + "/" +
                      str(stats_os.get("decoded")) +
                      " osN=" + str(stats_n.get("objects")) + "/" +
                      str(stats_n.get("decoded")) + "/w" + str(len(warn_os)) +
                      " plain=" + str(ops_plain.get("k")) + "/" + str(dec_plain) +
                      " inline=" + str(ops_inline.get("k")) + "," + str(ops_inline.get("G")) +
                      ",dec" + str(dec_inline) +
                      " iunk=" + str(ops_iunk) + "/" + str(dec_iunk) +
                      " bi=" + str(ops_bi.get("k")) + "/" + str(ops_bi.get("K")) +
                      " sub=" + str(ops_sub) + "/" + str(dec_sub) +
                      " ref=" + str(stats_ref.get("refs")) + "/" +
                      str(stats_ref.get("objects")) + "/" +
                      str(stats_ref.get("decoded")) +
                      " fixed=" + str(ops_fixed == expect_fixed) +
                      " fonts=" + str(fonts) + "/1" +
                      " craft=" + str(craft_facts["content_ops"] == scoped) +
                      " undec=" + str(ai_undec) + "/" + str(craft_undec["content_ops"]) +
                      " intent=" + str(intent_ai) + "/" +
                      str(intent_craft.get("output_intent")) + "/1" +
                      " keys=" + str(set(scoped) == _decl_keys) +
                      " ops=" + str(scoped))
    except Exception as e:                      # noqa: BLE001 - report, do not crash
        a29_detail = type(e).__name__ + ": " + str(e)[:140]
    add("A29-content-ops-measurement", a29_ok, a29_detail)


def _stage_grading(tmp, add, th, w1):
    """A12-A25: the grading tiers and the anti-fabrication guards.

    L2 has no third tier, quick mode never passes, F reaches the scores, and
    every dimension that must stay visible does: a missing score key, a
    backfilled F or a numeric L2 A/B turns the run red. Returns the generation
    budget that the tail of main() reports."""
    msg = th["messages"]
    cap = L.capability_missing_value(th)

    # A12 L2: no third grading tier, capability-missing status, delivery not blocked
    wl = tmp / "l2"
    wl.mkdir(parents=True, exist_ok=True)
    if (w1 / "spec.json").is_file():
        shutil.copyfile(w1 / "spec.json", wl / "spec.json")
    # A16 keeps the circle-bearing sample on purpose: the builtin fallback draws
    # circles as squares, so render_png judges the preview "limited" and aggregate
    # must auto-degrade to L2. The unreadable judge is the second, independent road
    # to the same status; both must land on capability_missing.
    _copy_if(GOOD / "sample.svg", wl / "final.svg")
    _call("render_png.py", ["--in", str(wl / "final.svg"),
                            "--out", str(wl / "preview.png"), "--force-builtin"], tmp)
    L.write_json_atomic(wl / "judge-src.json", {
        "schema": "vector-ai-studio/judge-v1", "readable": False,
        "items": [], "subject_conflict": False, "attributes": [], "align": []})
    _call("llm_judge.py", ["--judge", str(wl / "judge-src.json"), "--validate",
                           "--out", str(wl / "judge.json")], tmp)
    _call("score.py", ["--svg", str(wl / "final.svg"), "--judge", str(wl / "judge.json"),
                       "--spec", str(wl / "spec.json"), "--out", str(wl / "score.json")], tmp)
    _call("aggregate_log.py", ["--run-dir", str(wl),
                               "--out", str(wl / "acceptance.json")], tmp)
    la = _jread(wl / "acceptance.json")
    lblob = ((wl / "acceptance.json").read_text(encoding="utf-8")
             if (wl / "acceptance.json").is_file() else "")
    l2_words = [w for w in CLAIM_BANNED if w in lblob.replace(msg["grade_fail"], "")]
    ldn = ((wl / "delivery-note.md").read_text(encoding="utf-8")
           if (wl / "delivery-note.md").is_file() else "")
    outward_words = [w for w in CLAIM_BANNED if w in ldn]
    ok12 = (la.get("grade") == msg["l2_grade"] and
            la.get("headline") == msg["l2_headline"] and
            la.get("degraded") is True and
            la.get("needs_human_review") is True and
            la.get("reason") == msg["reason_capability_missing"] and
            la.get("capability_missing") is True and
            la.get("grading_entered") is True and
            la.get("scores", {}).get("A") == cap and
            la.get("scores", {}).get("B") == cap and
            not l2_words and not outward_words)
    add("A12-L2-not-pass-no-third-tier", ok12, "grade=" + str(la.get("grade")) +
        " banned=" + ",".join(l2_words + outward_words))

    # A14 runtime-derived generation budget + real generation_count + rejection
    budget = L.generation_budget(th)
    acc1 = _jread(w1 / "acceptance.json")
    ok14 = (budget == {"pass": th["F"]["N_pass"] * th["iterate"]["K_max"],
                       "excellent": th["F"]["N_excellent"] * th["iterate"]["K_max"]})
    ok14 = (ok14 and isinstance(acc1.get("generation_count"), int) and
            acc1["generation_count"] <= budget["excellent"] and
            acc1.get("generation_budget") == budget)
    rc14, env14, _ = _call("aggregate_log.py",
                           ["--validate-case", str(BAD / "bad_budget_exceeded.json")], tmp)
    ok14 = ok14 and rc14 == 1 and "E-BUDGET-EXCEEDED" in _codes(env14)
    add("A14-generation-budget-derived", ok14,
        "budget=" + json.dumps(budget, sort_keys=True) +
        " count=" + str(acc1.get("generation_count")))

    # A15 O4: f_source_runs / c2_revisions isolation, no cross-run F backfill
    iso = tmp / "o4-isolated"
    for i in range(1, th["F"]["N_pass"] + 1):
        run_id = "run-" + str(i)
        fs = iso / "f_source_runs" / run_id
        fs.mkdir(parents=True, exist_ok=True)
        _copy_if(w1 / "score.json", fs / "score.json")
        _copy_if(w1 / "final.svg", fs / "draft.svg")
        L.write_json_atomic(fs / "h1.json", {"pass": True})
        rev = iso / "c2_revisions" / run_id / "round-2"
        rev.mkdir(parents=True, exist_ok=True)
        L.write_json_atomic(rev / "revision.json",
                            {"run_id": run_id, "from_round": 2, "change_only": ["C"]})
    rc15, _, _ = _call("batch_check.py",
                       ["--runs-dir", str(iso), "--out", str(iso / "batch.json")], tmp)
    bd = _jread(iso / "batch.json")
    iso_ok = (rc15 == 0 and bd.get("f_data_isolated") is True and
              len(bd.get("f_source_runs", [])) == th["F"]["N_pass"] and
              len(bd.get("c2_revisions", [])) == th["F"]["N_pass"] and
              isinstance(bd.get("generation_count"), int) and
              bd["generation_count"] <= budget["excellent"])
    rc15b, env15b, _ = _call("aggregate_log.py",
                             ["--validate-case", str(BAD / "bad_f_backfill.json")], tmp)
    neg_ok = rc15b == 1 and "E-F-DATA-ISOLATION" in _codes(env15b)
    # Same SVG hash must not appear both in c2_revisions and in F source stats.
    hc = _jread(GOOD / "good_o4_isolated.json")
    if hc.get("c2_revisions"):
        hc["c2_revisions"][0]["draft_sha256"] = hc["f_source_runs"][0]["draft_sha256"]
    hash_path = tmp / "hash-overlap.json"
    L.write_json_atomic(hash_path, hc)
    rc15c, env15c, _ = _call("aggregate_log.py",
                             ["--validate-case", str(hash_path)], tmp)
    hash_ok = rc15c == 1 and "E-F-DATA-ISOLATION" in _codes(env15c)
    add("A15-F-data-isolation", iso_ok and neg_ok and hash_ok,
        "positive=" + str(iso_ok) + " backfill_rejected=" + str(neg_ok) +
        " shared_hash_rejected=" + str(hash_ok) +
        " gen_count=" + str(bd.get("generation_count")))

    # A16 L2 anti-fabrication: A/B must be status enum, never numeric scores
    ls = _jread(wl / "score.json")
    ab_ok = (ls.get("scores", {}).get("A") == cap and
             ls.get("scores", {}).get("B") == cap and
             not isinstance(ls.get("scores", {}).get("A"), dict))
    rc16, env16, _ = _call("aggregate_log.py",
                           ["--validate-case", str(BAD / "bad_l2_fabricated.json")], tmp)
    ok16 = ab_ok and rc16 == 1 and "E-FABRICATED-SCORE" in _codes(env16)
    add("A16-L2-anti-fabrication", ok16,
        "ab_status=" + str(ab_ok) + " rejected=" + str("E-FABRICATED-SCORE" in _codes(env16)))

    # A17 quick mode must never participate in a pass judgement
    rc17, env17, _ = _call("aggregate_log.py",
                           ["--validate-case", str(BAD / "bad_quick_claimed_pass.json")], tmp)
    ok17 = rc17 == 1 and "E-QUICK-UNVERIFIED" in _codes(env17)
    add("A17-quick-not-graded", ok17, "rejected=" + str(ok17))

    # A18 F merged into scores -> the pass tier becomes mechanically reachable.
    # SYNTHETIC validator run: proves the F->scores wiring, not a real .ai.
    # The SVG is rect-only with solid fills and no stroke, so render_png's own
    # _builtin_fidelity() judges the fallback "faithful" and A/B stays numeric
    # (a circle-bearing SVG would be "limited" and correctly flip to L2).
    a18 = tmp / "a18"
    a18.mkdir(parents=True, exist_ok=True)
    rect_ok = RECT.is_file()
    if rect_ok:
        shutil.copyfile(RECT, a18 / "final.svg")
    # A missing fixture deliberately leaves final.svg absent: render_png then
    # returns exit 2 and this assertion goes red, instead of copyfile raising
    # FileNotFoundError and aborting the whole selftest as E-INTERNAL.
    _call("render_png.py", ["--in", str(a18 / "final.svg"),
                            "--out", str(a18 / "preview.png"), "--force-builtin"], tmp)
    meta18 = _jread(a18 / "preview.meta.json")
    _copy_if(GOOD / "judge.json", a18 / "judge.json")
    # r70 #2: H1 now re-opens final.ai's OWN bytes. A 47-byte placeholder used
    # to be enough to reach 达标 -- that green light was the contract being
    # pinned down by the selftest itself. The pass-tier run must now carry a
    # genuinely parseable minimal PDF container (>=1 /Type /Page, 0 /Subtype
    # /Image == structure_readback.raster). It is still synthetic (it proves
    # F->scores wiring, not a real engine landing), but H1 evidence is real.
    _MIN_PDF_AI = (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>\nendobj\n"
        b"%%EOF\n")
    (a18 / "final.ai").write_bytes(_MIN_PDF_AI)
    L.write_json_atomic(a18 / "structure_readback.json", {
        "ok": True, "raster": 0, "placed": 0, "paths": 5,
        "artboard_matches_viewBox": True})
    # r70 #3: H3 now requires an assertion-count lower bound
    # (n_all >= L.EXPECTED_ASSERTIONS) and a run_id bound to --run-id. A
    # synthetic "1/1 ok" log can no longer prove H3, so this fixture must
    # carry the full, internally consistent selftest shape a real run emits.
    # Ids are placeholders for the count check, not a second assertion set.
    _n18 = L.EXPECTED_ASSERTIONS
    L.write_json_atomic(a18 / "selftest.json", {
        "schema": "vector-ai-studio/selftest-v1",
        "status": "ok", "passed": _n18, "failed": 0, "skipped": [],
        "run_id": "a18",
        "capability_skips": sorted(L.CAPABILITY_SKIPS),
        "assertions": [{"id": "A18-syn-%03d" % i, "ok": True,
                        "detail": "", "skipped": False}
                       for i in range(_n18)]})
    L.write_json_atomic(a18 / "score.json", {
        "round": 2, "l2_degraded": False, "degraded_dims": [], "failed_dims": [],
        "scores": {"A": {"score": 2, "measured": {}, "threshold": {}},
                   "B": {"score": 2, "measured": {}, "threshold": {}},
                   "C": {"score": 1, "measured": {}, "threshold": {}},
                   "D": {"score": 1, "measured": {}, "threshold": {}},
                   "E": "N/A", "F": "N/A"}})
    L.write_json_atomic(a18 / "batch.json", {
        "schema": "vector-ai-studio/batch-v1", "quick": False,
        "f_verified": True, "f_data_isolated": True, "isolation_problems": [],
        "N": 3, "generation_count": 3,
        "F": {"score": 1, "measured": {"N": 3, "h1_pass_rate": 1.0,
                                       "ad_pass_rate": 0.8333,
                                       "palette_consistent": True},
              "threshold": {}, "degraded": False},
        "f_source_runs": [{"run_id": "run-1", "draft_sha256": "a" * 64},
                          {"run_id": "run-2", "draft_sha256": "b" * 64},
                          {"run_id": "run-3", "draft_sha256": "c" * 64}],
        "c2_revisions": [],
        "per_run": [{"run": "run-" + str(i), "h1_pass": True,
                     "ad_pass_rate": 0.85, "palette": ["#111", "#eee"]}
                    for i in (1, 2, 3)]})
    rc18, env18, _ = _call("aggregate_log.py",
                           ["--run-dir", str(a18), "--run-id", "a18", "--json"], tmp)
    d18 = env18.get("data", {}) or {}
    acc18 = _jread(a18 / "acceptance.json")
    f_dict = isinstance((acc18.get("scores") or {}).get("F"), dict)
    ok18 = (rect_ok and rc18 == 0 and acc18.get("grade") == msg["grade_pass"] and f_dict
            and acc18.get("needs_human_review") is True
            and meta18.get("fidelity") == "faithful")
    add("A18-F-merged-reachable", ok18,
        ("MISSING FIXTURE rect-only.svg; " if not rect_ok else "") +
        "synthetic rect-only run: fallback judged faithful by render_png itself; "
        "proves the F->scores wiring, not preview fidelity; "
        "fidelity=" + str(meta18.get("fidelity")) +
        " grade=" + str(acc18.get("grade")) +
        " needs_human_review=" + str(acc18.get("needs_human_review")) +
        " F_is_dict=" + str(f_dict) +
        " validation=" + str(d18.get("validation_problems")))

    # A18-negative (r70 #2, pairs with A18): the SAME run with a 47-byte fake
    # .ai must now FAIL H1 and never reach 达标. This is the reverse side of
    # the fix -- without it, A18 would still be a one-sided green light.
    a18_fake = tmp / "a18-fake"
    shutil.copytree(a18, a18_fake)
    (a18_fake / "final.ai").write_bytes(
        b"synthetic-h1-file-existence-placeholder-not-a-real-ai\n")
    # Keep the H3 evidence bound to this run too, so the ONLY difference
    # between a18 and a18-fake is final.ai's bytes (a clean paired assertion).
    _st18f = _jread(a18_fake / "selftest.json")
    _st18f["run_id"] = "a18-fake"
    L.write_json_atomic(a18_fake / "selftest.json", _st18f)
    rc18f, env18f, _ = _call("aggregate_log.py",
                             ["--run-dir", str(a18_fake), "--run-id", "a18-fake", "--json"], tmp)
    acc18f = _jread(a18_fake / "acceptance.json")
    h1_18f = (acc18f.get("hard_gates", {}).get("H1", {}) or {}).get("pass")
    ok18f = (rc18f != 0 and h1_18f is False
             and acc18f.get("grade") != msg["grade_pass"])
    add("A18c-fake-ai-h1-fails", ok18f,
        "fake 47-byte .ai: exit=" + str(rc18f) +
        " h1=" + str(h1_18f) + " grade=" + str(acc18f.get("grade")) +
        " h1_reason=" + str((acc18f.get("hard_gates", {}).get("H1", {}) or {}).get("reason")))

    # A18b a compliant full-mode pass-tier acceptance log passes validation
    rc18b, env18b, _ = _call("aggregate_log.py",
                             ["--validate-case", str(GOOD / "good_acceptance_reachable.json")], tmp)
    add("A18b-validator-accepts-pass", rc18b == 0 and not _codes(env18b),
        "codes=" + ",".join(_codes(env18b)))

    # A19 full track without C/D is a broken chain, never an L2 degradation
    rc19, env19, _ = _call("aggregate_log.py",
                           ["--validate-case", str(BAD / "bad_no_score.json")], tmp)
    ok19 = rc19 == 1 and "E-NO-SCORE" in _codes(env19)
    add("A19-full-track-no-score-rejected", ok19, "codes=" + ",".join(_codes(env19)))

    # A20 landing-only track: unevaluated (null), accepted, no tier word anywhere
    rc20, env20, _ = _call("aggregate_log.py",
                           ["--validate-case", str(GOOD / "good_landing_only.json")], tmp)
    lo_blob = (GOOD / "good_landing_only.json").read_text(encoding="utf-8")
    lo_words = [w for w in (CLAIM_BANNED + ["未验证"]) if w in lo_blob]
    ok20 = rc20 == 0 and not _codes(env20) and not lo_words
    add("A20-landing-only-unevaluated", ok20,
        "codes=" + ",".join(_codes(env20)) + " words=" + ",".join(lo_words))

    # A21 the exemption must be policed from both sides
    rc21a, env21a, _ = _call("aggregate_log.py",
                             ["--validate-case", str(BAD / "bad_exemption_abuse_full.json")], tmp)
    rc21b, env21b, _ = _call("aggregate_log.py",
                             ["--validate-case", str(BAD / "bad_exemption_abuse_lo.json")], tmp)
    ok21 = (rc21a == 1 and "E-EXEMPTION-ABUSE" in _codes(env21a) and
            rc21b == 1 and "E-EXEMPTION-ABUSE" in _codes(env21b))
    add("A21-exemption-abuse-both-ways", ok21,
        "full_track_exit=" + str(rc21a) + " lo_track_exit=" + str(rc21b))

    # A22 package hygiene: runtime output/ must never live inside the skill dir,
    # and no stray __pycache__ may be shipped. scripts/ bytecode cache is created
    # by this very run, so it is ignored here and excluded by the packager.
    stray = []
    if (ROOT / "output").exists():
        stray.append("output/")
    for p in ROOT.rglob("__pycache__"):
        if p.is_dir() and p.parent != SCRIPTS:
            stray.append(str(p.relative_to(ROOT)))
    add("A22-package-hygiene", not stray,
        ",".join(stray[:6]) if stray
        else "no output/ and no stray __pycache__ (scripts/ bytecode cache ignored)")

    # A23 L2 must carry all six score keys with the right per-key status.
    # Reverse side: dropping A or F must be rejected with E-NO-SCORE.
    l2_sc = la.get("scores") or {}
    l2_keys = set(l2_sc) >= {"A", "B", "C", "D", "E", "F"}
    l2_shape = (l2_sc.get("A") == cap and l2_sc.get("B") == cap
                and isinstance(l2_sc.get("C"), dict) and isinstance(l2_sc.get("D"), dict)
                and (l2_sc.get("E") == "N/A" or isinstance(l2_sc.get("E"), dict))
                and (l2_sc.get("F") == "N/A" or isinstance(l2_sc.get("F"), dict)))
    rc_mf, env_mf, _ = _call("aggregate_log.py",
                             ["--validate-case", str(BAD / "bad_missing_f.json")], tmp)
    rc_ma, env_ma, _ = _call("aggregate_log.py",
                             ["--validate-case", str(BAD / "bad_missing_a.json")], tmp)
    mf_rej = rc_mf == 1 and "E-NO-SCORE" in _codes(env_mf)
    ma_rej = rc_ma == 1 and "E-NO-SCORE" in _codes(env_ma)
    add("A23-L2-six-keys", l2_keys and l2_shape and mf_rej and ma_rej,
        "keys=" + str(sorted(l2_sc)) + " shape=" + str(l2_shape) +
        " missing_f_rejected=" + str(mf_rej) + " missing_a_rejected=" + str(ma_rej))

    # A24 a graded (full-track) log must carry every grade-required dimension.
    full_sc = acc18.get("scores") or {}
    req_missing = sorted({"A", "B", "C", "D", "F"} - set(full_sc))
    add("A24-full-required-dims", not req_missing and mf_rej and ma_rej,
        "keys=" + str(sorted(full_sc)) + " missing=" + str(req_missing))

    # A25 a passing (grade=达标) log must keep E visible: dropping E would hide
    # "E applicable but scored 0", which is the same fake-pass surface as a
    # missing graded dimension. Reverse-verified with bad_pass_missing_e.json.
    rc_me, env_me, _ = _call("aggregate_log.py",
                             ["--validate-case", str(BAD / "bad_pass_missing_e.json")], tmp)
    me_rej = rc_me == 1 and "E-NO-SCORE" in _codes(env_me)
    add("A25-pass-missing-e-rejected", me_rej,
        "pass_missing_e_rejected=" + str(me_rej) + " codes=" + ",".join(_codes(env_me)))


def main():
    ap = argparse.ArgumentParser(description="pure-script selftest")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--log-dir", default="")
    ap.add_argument("--fixtures", action="store_true")
    ap.add_argument("--run-id", default="")
    args = ap.parse_args()

    th = L.load_thresholds()
    rid = args.run_id or L.sha_run_id("selftest", th["version"])
    log_dir = Path(args.log_dir) if args.log_dir else (L.default_output_root() / "selftest" / rid)
    log_dir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="vector-ai-selftest-"))
    a = []

    def add(aid, ok, detail="", skipped=False):
        # ok is True / False / None. None means "not evaluated", matching how
        # this project already expresses an unevaluated gate (h2=null with
        # h2_status=not_evaluated). A skip must not read as a pass to anything
        # that aggregates assertions[].ok -- that was the hole: exit 0, status
        # ok, and ok=true on an assertion that never ran.
        a.append({"id": aid, "ok": (None if skipped else bool(ok)),
                  "detail": str(detail)[:220], "skipped": bool(skipped)})
        add.seen.add(aid)

    add.seen = set()
    w1, _, chain, optional = _stage_pure_chain(tmp, add, th)
    budget = _stage_grading(tmp, add, th, w1)
    _stage_contract_registry(tmp, add, w1)
    _stage_smoke_rating(tmp, add)
    _stage_measurement(tmp, add)
    _stage_alt_engine(tmp, add)
    _stage_perceptual(tmp, add)
    _stage_p2_survivors(tmp, add)
    _check_stale_preview(tmp, add, RECT)
    _stage_silent_cli(tmp, add)
    _check_archetypes_legal(tmp, add)
    _stage_studio_chain(tmp, add)
    _check_review_pack(tmp, add)
    _check_deal_history(tmp, add)
    _stage_legibility(tmp, add)
    _check_failed_dimension(tmp, add)
    _check_empty_review_sheet(tmp, add)
    # A55 and the conformance block are separate functions, but they were
    # adjacent in the original main(); keeping the calls adjacent keeps the
    # assertion order unchanged. Until r61 this call was the last line of
    # _check_empty_review_sheet, where an early exception would have skipped
    # A56 and A59 without anything going red.
    _check_conformance(tmp, add)
    _check_ledger_trace(tmp, add)
    _check_licence_claim(tmp, add)
    _stage_print_route(tmp, add)

    # ---- r70 meta-assertions (C-3 / C-4): machine-checkable anti-regression ----
    # A77 (C-3): the pass-tier assignment must exist exactly once, inside
    # _derive_grade. A second inline `grade = msg["grade_pass"]` copy would
    # re-open the "two judgments, one trusted and one not" hole (#5 / QA-06).
    try:
        _agg_src = (SCRIPTS / "aggregate_log.py").read_text(encoding="utf-8")
        _assign = re.findall(r"grade\s*=\s*msg\[[\"']grade_pass[\"']\]", _agg_src)
        add("A77-single-grade-derivation",
            len(_assign) == 1 and "_derive_grade" in _agg_src,
            "grade_pass assignments=" + str(len(_assign)) +
            " has_derive_grade=" + str("_derive_grade" in _agg_src))
    except OSError as e:
        add("A77-single-grade-derivation", False, "read error: " + str(e)[:120])

    # A78 (C-4 / QA-09): at least 3 cases must exercise the --run-dir judging
    # path (and none of those may fall back to --validate-case's early return).
    # Otherwise the negative fixtures prove nothing about H1/H2/H3/F/grade.
    try:
        _fx = json.loads((FIX / "fixtures.json").read_text(encoding="utf-8"))
        _rundir = [c for c in _fx.get("cases", [])
                   if "--run-dir" in c.get("args", [])
                   and "--validate-case" not in c.get("args", [])]
        add("A78-rundir-fixtures-coverage", len(_rundir) >= 3,
            "run-dir cases=" + str(len(_rundir)) +
            " ids=" + ",".join(c.get("id", "?") for c in _rundir))
    except (OSError, json.JSONDecodeError) as e:
        add("A78-rundir-fixtures-coverage", False, "read error: " + str(e)[:120])

    # A79 (#5 / QA-06, paired item 1): a direct --validate-case recompute
    # check, so the guard exists even in a plain (non --fixtures) run. The
    # rebuilt V9 differs from the R-1-b revised V0 ONLY in internal_excellent;
    # rejecting it proves that field is inside the recompute range (R-1-a).
    _rc_v0, _env_v0, _ = _call(
        "aggregate_log.py", ["--validate-case", str(GOOD / "good_acceptance_reachable.json")], tmp)
    _rc_v9, _env_v9, _ = _call(
        "aggregate_log.py", ["--validate-case", str(BAD / "bad_grade_v9_excellent.json")], tmp)
    _v9_msgs = " ".join(e.get("msg", "") for e in _env_v9.get("errors", []))
    add("A79-validator-recomputes-internal-excellent",
        bool(_rc_v0 == 0 and not _codes(_env_v0)
             and _rc_v9 == 1 and "E-GRADE-MISMATCH" in _codes(_env_v9)
             and "internal_excellent" in _v9_msgs),
        "V0_codes=" + ",".join(_codes(_env_v0)) +
        " V9_codes=" + ",".join(_codes(_env_v9)) +
        " names_internal_excellent=" + str("internal_excellent" in _v9_msgs))

    # optional: fixtures regression (now includes the six v1.3 samples)
    if args.fixtures:
        frc, fenv, _ = _call("run_fixtures.py", ["--json", "--log-dir", str(log_dir)], tmp)
        add("A13-fixtures-regression", frc == 0 and fenv.get("data", {}).get("all_ok") is True,
            "exit=" + str(frc))

    # A76: a skip is only honest for a declared capability. CI accepts exit 3
    # (capability missing), so an assertion that starts skipping for any other
    # reason would otherwise hide behind a green run; this names it.
    _skips = [x["id"] for x in a if x.get("skipped")]
    _extra_skips = sorted(set(_skips) - CAPABILITY_SKIPS)
    add("A76-capability-skips-are-declared", not _extra_skips,
        "skipped=" + (",".join(_skips) or "none")
        + (" undeclared=" + ",".join(_extra_skips) if _extra_skips else ""))

    # A60: the suite must not silently lose an assertion. This counts itself,
    # so the declared number and the observed one are compared on equal terms:
    # a dropped block, a duplicated block or a stale EXPECTED_ASSERTIONS all
    # land here instead of in a human's head. Counting alone missed the
    # delete-one-duplicate-another case (r69 audit), so ids must also be unique.
    _expected = EXPECTED_ASSERTIONS + (1 if args.fixtures else 0)
    _ran = len(a) + 1
    _ids = [r["id"] for r in a]
    _dup = sorted({i for i in _ids if _ids.count(i) > 1})
    add("A60-assertion-count-declared", _ran == _expected and not _dup,
        "declared=" + str(_expected) + " ran=" + str(_ran)
        + (" duplicated=" + ",".join(_dup) if _dup else ""))

    passed = sum(1 for x in a if x["ok"] is True)
    skipped = [x["id"] for x in a if x.get("skipped")]
    # A skip is not a pass and not a failure: it is a capability that was not
    # present. It is reported separately and marks the run "partial", so it
    # can never be mistaken for coverage.
    failed = [x["id"] for x in a if x["ok"] is False]
    doc = {
        "schema": "vector-ai-studio/selftest-v1",
        # Three states, not two. "partial" means at least one guard could not
        # run; a caller that only checks exit code or status must not read that
        # as success.
        "status": "fail" if failed else ("partial" if skipped else "ok"),
        "run_id": rid,
        "passed": passed, "failed": len(failed), "skipped": skipped,
        "capability_skips": sorted(CAPABILITY_SKIPS),
        "partial": bool(skipped),
        "assertions": a,
        "chain": chain,
        "optional_deps": optional,
        "generation_budget": budget,
        "cwd_independent": True,
        "note": "pure-script layer only; real-machine checks in smoke_ai.py",
    }
    L.write_json_atomic(log_dir / "selftest.json", doc)
    L.append_run_log(log_dir, SCRIPT + " passed=" + str(passed) + "/" + str(len(a)))
    _code = L.EXIT_FAIL if failed else (L.EXIT_MISSING if skipped else L.EXIT_OK)
    return L.emit(SCRIPT, status=doc["status"],
                  code=_code,
                  run_id=rid, data=doc,
                  errors=([L.err("E-SELFTEST", "failed assertions: " + ",".join(failed))]
                          if failed else
                          ([L.err("E-SELFTEST", "not evaluated (missing capability): " +
                                  ",".join(skipped))] if skipped else [])),
                  artifacts={"selftest": "selftest.json"})


if __name__ == "__main__":
    sys.exit(L.run_cli(SCRIPT, main))

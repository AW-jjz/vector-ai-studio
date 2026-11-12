#!/usr/bin/env python3
"""Land a native .ai through VectorCraft. This is the default lander.

Why VectorCraft: it needs no Adobe licence, no COM, no Windows desktop and no
~20 s cold start. Its CLI converts to CMYK for real and writes an .ai that
Illustrator opens, with groups preserved as named layers. The old COM/JSX
path was removed in r45 because it could not do the conversion and required a
licensed desktop app.

land_craft.py is the print sibling: same engine, PDF/X master, crop marks,
bleed. Both write the same deliverable.json / structure_readback.json
contract via lib_pdf.py, so downstream code never special-cases an engine.

Verified before writing this (2026-11-08, vectorcraft 0.22.0):
  - 5 shapes / 5 named layers / 200x200 artboard survive the round trip
  - per-path sizes and colours match the source exactly
  - a PNG exported from the reopened document matches the intended design
  - file.documentColorMode {mode:"cmyk",convert:true} yields k/K operators
  - the produced PDF is not PDF/X: no /OutputIntent, no /ICCBased

Not verified, therefore not claimed: CMYK output intent, bleed separation,
ICC embedding.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L
import lib_pdf as LP          # reuses _observe_ai / _deliverable, one contract
import _contract as C         # the preview-fidelity vocabulary (generated)

SCRIPT = "land_vc.py"

PROFILE_DEFAULTS = {
    # cmyk: run the colour-mode conversion before exporting
    # pdf:  also emit a PDF next to the .ai
    # icc:  does this profile REQUEST an embedded profile / output intent?
    #       lib_pdf declares it for "print"; this engine historically wrote
    #       False unconditionally, so the same physical fact (no /OutputIntent,
    #       no /ICCBased) was "a promise not kept" on one engine and "an
    #       informational note" on the other. Whether the request can actually
    #       be satisfied is measured afterwards, never assumed here.
    "screen":  {"cmyk": False, "pdf": False, "icc": False},
    "print":   {"cmyk": True,  "pdf": True,  "icc": True},
    "handoff": {"cmyk": False, "pdf": False, "icc": False},
}


def _parse_engine_version(text):
    """Pick the dotted version out of `--version` output.

    `.split()[-1]` returns whatever the last whitespace-delimited token is:
    a build suffix, a platform tag, a parenthesised date. That made
    engine_version plausible-looking noise. Prefer x.y.z, then x.y, and say
    "unknown" instead of reporting a wrong token as if it were a version.
    """
    s = (text or "").strip()
    m = re.search(r"\b\d+\.\d+\.\d+\b", s) or re.search(r"\b\d+\.\d+\b", s)
    return m.group(0) if m else "unknown"


def _collect_export_warnings(steps):
    """Collect tool warnings verbatim, once each, in first-seen order.

    The CLI reports warnings per step, so one missing font can be emitted by
    several steps. A duplicated list is an artifact of the logging, not a
    second measurement.
    """
    out, seen = [], set()
    for st in steps or []:
        res = (st or {}).get("result") or {}
        for w in (res.get("warnings") or []):
            t = str(w)
            if t and t not in seen:
                seen.add(t)
                out.append(t)
    return out


def find_cli(explicit):
    """--vc wins; otherwise the shared locator (trace_ref.find_cli).

    Keeping a second CANDIDATES list here meant detect_deps could report FOUND
    while this lander looked in different places, or vice versa. One locator.
    """
    if explicit:
        p = Path(explicit)
        return p if p.is_file() else None
    from trace_ref import find_cli as _shared
    return _shared()


def run_cli(cli, args, timeout=300):
    r = subprocess.run([str(cli)] + args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       timeout=timeout, check=False)
    return r.returncode, r.stdout or "", r.stderr or ""


def probe_source(cli, svg):
    """Ask the CLI what it could not read faithfully, before converting.

    The tool knows things the bytes cannot tell us: which fonts are missing and
    what they were replaced with. A client receiving an .ai whose Helvetica was
    swapped for a fallback is a real defect, and it is invisible in file size,
    path counts or layer counts. So ask, and carry the answer into the manifest.
    """
    try:
        rc, out, _ = run_cli(cli, ["info", str(Path(svg).resolve())], timeout=120)
        if rc != 0:
            return {"available": False, "rc": rc}
        d = json.loads(out)
        return {"available": True,
                "fonts": d.get("fonts") or [],
                "import_warnings": d.get("importWarnings") or d.get("warnings") or [],
                "objects": (d.get("info") or {}).get("objects"),
                "color_mode": (d.get("info") or {}).get("colorMode")}
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        return {"available": False}

def parse_jsonl(out):
    """vectorcraft-cli prints one JSON object per step, one per line."""
    steps = []
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            steps.append(json.loads(line))
        except json.JSONDecodeError:
            steps.append({"unparsed": line[:200]})
    return steps


def _font_warnings(src):
    """Warnings for fonts the CLI had to substitute.

    The answer is shaped {family, missing, resolved:{family}} per font; keeping
    it in a function (rather than inline in main) is what lets a guard assert
    the customer-visible sentence without an engine installed. A substituted
    font changes the glyphs the client sees and is invisible in file size, path
    counts and layer counts alike -- so it is carried verbatim, not summarised.
    """
    out = []
    for f in ((src or {}).get("fonts") or []):
        if f.get("missing"):
            resolved = ((f.get("resolved") or {}).get("family")) or "未知替代字体"
            out.append("源文件字体缺失：" + str(f.get("family")) + "，产物中已用 " +
                       resolved + " 替代（客户看到的字形与你设计的不同）")
    return out


def _build_deliverable(ai_path, profile_name, prof, obs, kv, ver_text, steps, src):
    """Assemble deliverable.json from measured parts.

    Every field that used to be wired inline in main() is wired here. The point
    is testability, not tidiness: without the CLI installed main() cannot run
    end to end, so a guard that calls the helpers directly stays green while the
    wiring that actually produces the file is reverted -- assertion and fix
    confirming each other in an empty room. Driving this seam instead of the
    helpers means reverting either half turns the matching guard red.
    """
    opts = {"profile": profile_name,
            "compat": "pdf-compatible",
            "pdf": True,         # .ai is written as a PDF container
            "icc": prof["icc"],  # requested by the profile, not claimed as done
            "cmyk": prof["cmyk"],
            "bleed_mm": 0.0, "bleed_pt": 0.0,
            "layers": True}      # groups become layers automatically
    return LP._deliverable(ai_path, opts, obs, kv,
                           engine="vectorcraft",
                           engine_version=_parse_engine_version(ver_text),
                           export_steps=steps,
                           source_probe=src,
                           tool_warnings=_collect_export_warnings(steps))


def _record_failure(run_dir, note):
    """Leave a fail landing and no stale readback after an unexpected abort.

    Without this, an exception in the middle of a landing left the previous
    run's structure_readback.json and final.ai in place; aggregate_log would
    grade that old pass as if this run had produced it. A specific handler may
    already have written a more precise landing; it is not overwritten.
    """
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
    ap = argparse.ArgumentParser(description="land native .ai via VectorCraft")
    ap.add_argument("--svg", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--profile", default="screen",
                    choices=sorted(PROFILE_DEFAULTS))
    ap.add_argument("--vc", default="", help="path to vectorcraft-cli")
    ap.add_argument("--run-id", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    run_dir = Path(args.out)
    run_dir.mkdir(parents=True, exist_ok=True)
    try:
        return _run(args, run_dir)
    except BaseException:
        _record_failure(run_dir, "landing aborted by an exception")
        raise


def _run(args, run_dir):
    prof = PROFILE_DEFAULTS[args.profile]

    svg = Path(args.svg) if args.svg else (run_dir / "final.svg")
    if not svg.is_file():
        cleared = LP._drop_stale_readback(run_dir)
        preview_cleared = LP._drop_stale_preview(run_dir)
        L.write_json_atomic(run_dir / "landing.json",
                            LP._landing("none", "fail", "source SVG not found",
                                        False, False,
                                        {"stale_readback_removed": cleared,
                                         "stale_preview_removed": preview_cleared}))
        raise L.ToolError("E-H1-STRUCTURE", "source SVG not found: " + svg.name,
                          L.EXIT_USAGE)

    cli = find_cli(args.vc)
    if cli is None:
        # Never pretend. Without the CLI there is no native .ai from this engine.
        # A previous run's readback must not survive to be graded as this run's.
        cleared = LP._drop_stale_readback(run_dir)
        preview_cleared = LP._drop_stale_preview(run_dir)
        L.write_json_atomic(run_dir / "landing.json",
                            LP._landing("none", "degraded",
                                        "VectorCraft CLI not found", False, False,
                                        {"stale_readback_removed": cleared,
                                         "stale_preview_removed": preview_cleared}))
        return L.emit(SCRIPT, status="degraded", code=L.EXIT_MISSING,
                      run_id=args.run_id,
                      data={"engine": "vectorcraft", "found": False,
                            "stale_readback_removed": cleared,
                            "stale_preview_removed": preview_cleared},
                      degradation={"level": "L4",
                                   "reason": "vectorcraft-cli not found",
                                   "substitute": "install vectorcraft-cli (or set VECTORCRAFT_CLI) and rerun"})

    ai_path = run_dir / "final.ai"
    pdf_path = run_dir / "final.pdf"
    svg_out = run_dir / "final.svg"
    preview = run_dir / "preview.png"

    # r72: the engine's bytes decide the .ai, so an unpinned or swapped build
    # must not quietly produce different output than the regression covered.
    # A build that is not pinned at all is only a NOTE; a build that IS pinned
    # and differs is a hard stop (E-ENGINE-DRIFT).
    from trace_ref import require_locked_engine as _locked
    engine_lock = _locked(cli)
    if not engine_lock.get("locked"):
        L.append_run_log(run_dir, SCRIPT + " engine not pinned: " +
                         str(engine_lock.get("note", "")))

    # Existence is the only evidence that this run produced anything, so the
    # targets are cleared before the engine runs. A CLI that exits 0 without
    # writing (silent failure, not a crash) would otherwise inherit the previous
    # run's final.ai / preview.png and pass every downstream check. An unlink
    # that fails (file locked by another app) is a refusal, not a warning: the
    # old file would be read as this run's.
    if not LP._drop_outputs(run_dir, ("final.ai", "final.pdf", "preview.png",
                                      "preview.meta.json")):
        cleared = LP._drop_stale_readback(run_dir)
        preview_cleared = LP._drop_stale_preview(run_dir)
        L.write_json_atomic(run_dir / "landing.json",
                            LP._landing("none", "fail",
                                        "previous outputs could not be cleared",
                                        False, False,
                                        {"stale_readback_removed": cleared,
                                         "stale_preview_removed": preview_cleared}))
        return L.emit(SCRIPT, status="fail", code=L.EXIT_ERROR, run_id=args.run_id,
                      data={"outputs_cleared": False},
                      errors=[L.err("E-OUTPUT-STALE",
                                    "previous run's outputs are locked; refusing to "
                                    "run with stale files in place")])

    cmd = ["run", "--in", str(svg.resolve())]
    if prof["cmyk"]:
        cmd += ["--cmd", "file.documentColorMode",
                "--params", json.dumps({"mode": "cmyk", "convert": True})]
    cmd += ["--export", str(ai_path.resolve())]
    if prof["pdf"]:
        cmd += ["--export", str(pdf_path.resolve())]

    try:
        rc, out, err = run_cli(cli, cmd)
    except (OSError, subprocess.SubprocessError) as e:
        # A crash here is still a failed run: the previous readback and preview
        # must not survive it, or aggregate_log grades the last pass again.
        cleared = LP._drop_stale_readback(run_dir)
        preview_cleared = LP._drop_stale_preview(run_dir)
        L.write_json_atomic(run_dir / "landing.json",
                            LP._landing("none", "fail",
                                        "vectorcraft-cli could not be executed",
                                        False, False,
                                        {"stale_readback_removed": cleared,
                                         "stale_preview_removed": preview_cleared}))
        return L.emit(SCRIPT, status="error", code=L.EXIT_ERROR, run_id=args.run_id,
                      data={"stale_readback_removed": cleared,
                            "stale_preview_removed": preview_cleared},
                      errors=[L.err("E-H1-STRUCTURE",
                                    "vectorcraft-cli failed: " + type(e).__name__)])
    steps = parse_jsonl(out)
    # Every warning the tool emitted during export, kept verbatim and unique.
    export_warnings = _collect_export_warnings(steps)

    if rc != 0 or not ai_path.is_file() or ai_path.stat().st_size == 0:
        cleared = LP._drop_stale_readback(run_dir)
        preview_cleared = LP._drop_stale_preview(run_dir)
        L.write_json_atomic(run_dir / "landing.json",
                            LP._landing("vectorcraft", "fail",
                                        "no .ai produced (rc=" + str(rc) + ")",
                                        False, False,
                                        {"stale_readback_removed": cleared,
                                         "stale_preview_removed": preview_cleared}))
        return L.emit(SCRIPT, status="fail", code=L.EXIT_FAIL, run_id=args.run_id,
                      data={"engine": "vectorcraft", "steps": steps,
                            "stderr": (err or "")[:300],
                            "stale_readback_removed": cleared,
                            "stale_preview_removed": preview_cleared},
                      errors=[L.err("E-H1-STRUCTURE",
                                    "no .ai produced (rc=" + str(rc) + ")")])

    # ---- measure, do not assume ----
    obs = LP._observe_ai(ai_path)
    # Layers come from the file, not from an assumption: passing 0 here made
    # the manifest claim "only 1 layer" for a document that had five named ones.
    kv = {"layers": str(obs.get("layers") or 0), "trimmarks_ink": "", "artboard": ""}
    # Ask, do not hardcode: a literal version would be a lie the moment the
    # user has a different build on PATH. The probe result is passed into the
    # seam below so a guard can feed it a synthetic version string.
    try:
        _vrc, _vout, _ = run_cli(cli, ["--version"], timeout=30)
    except (OSError, subprocess.SubprocessError):
        _vout = ""
    # One writer per field: pass everything to the constructor instead of
    # overwriting its output afterwards. The old post-hoc assignments gave
    # tool_warnings two writers and let the same warning appear twice.
    src = probe_source(cli, svg)
    deliverable = _build_deliverable(ai_path, args.profile, prof, obs, kv,
                                     _vout, steps, src)

    deliverable["warnings"].extend(_font_warnings(src))

    for w in export_warnings:
        lw = w.lower()
        if "outline" in lw:
            # Measured, not inferred: the file keeps live Type objects
            # (vectorcraft info reports kinds.Type), but Illustrator reads the
            # PDF-compatible section where the type is outlined -- so whether
            # the client can edit the text depends on which app they open it
            # with. Saying "text becomes outlines" flatly was wrong.
            deliverable["warnings"].append(
                "文字在外观上会变：Illustrator 打开时读成轮廓（实测 textFrames=0）——"
                "这是 VectorCraft 引擎产物在 Illustrator 中的表现，非 .ai / PDF 格式属性；"
                "文件内仍保留文字对象（用 VectorCraft 打开可编辑）。"
                "若客户必须在 Illustrator 里改活字，改交付 SVG 源文件")

    # ---- the other two artifacts the chain expects ----
    # Without final.svg and preview.png the H2 gate reports "four artifacts
    # incomplete" no matter how good the .ai is. VectorCraft renders its own
    # PNG, so this engine needs no other tool to be complete end to end.
    try:
        if svg.resolve() != svg_out.resolve():
            shutil.copyfile(svg, svg_out)
    except OSError:
        pass
    preview_meta = {"schema": "vector-ai-studio/preview-meta-v1",
                    "renderer": "vectorcraft-cli", "degraded": False,
                    "fidelity": C.FIDELITY.FAITHFUL,
                    "note": "rendered by the same engine that wrote the .ai"}
    try:
        rc3, _, err3 = run_cli(cli, ["convert", str(svg_out), str(preview),
                                     "--scale", "2"], timeout=180)
        if rc3 != 0 or not preview.is_file() or preview.stat().st_size == 0:
            preview_meta = {"schema": "vector-ai-studio/preview-meta-v1",
                            "renderer": "vectorcraft-cli", "degraded": True,
                            "fidelity": C.FIDELITY.LIMITED,
                            "note": "preview export failed: " + (err3 or "")[:120]}
    except (OSError, subprocess.SubprocessError):
        preview_meta = {"schema": "vector-ai-studio/preview-meta-v1",
                        "renderer": "vectorcraft-cli", "degraded": True,
                        "fidelity": C.FIDELITY.LIMITED, "note": "preview export crashed"}
    if preview_meta["degraded"] and LP._ensure_preview(run_dir, svg_out):
        # render_png.py wrote its own preview.meta.json (renderer, fidelity,
        # note). Keep that record instead of overwriting it with a guess.
        preview_meta = None
    if preview_meta is not None:
        if "fidelity" not in preview_meta:
            # The contract decides which word goes in, not this script: the
            # judge downstream reads the same table (r72 / M1).
            preview_meta["fidelity"] = (C.FIDELITY.LIMITED if preview_meta["degraded"]
                                        else C.FIDELITY.FAITHFUL)
        L.write_json_atomic(run_dir / "preview.meta.json", preview_meta)

    # ---- structure readback, in the shape the rest of the chain reads ----
    # aggregate_log.py treats a missing structure_readback.json as "no native
    # .ai evidence" and marks H1 unverified. Producing an .ai without it would
    # make the whole chain report failure for a file that is fine.
    kinds = {}
    artboards = []
    try:
        rc2, out2, _ = run_cli(cli, ["info", str(ai_path)], timeout=120)
        if rc2 == 0:
            d2 = json.loads(out2)
            kinds = d2.get("kinds") or {}
            artboards = d2.get("artboards") or []
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        pass

    vb = None
    try:
        import re as _re
        m = _re.search(r'viewBox\s*=\s*"([^"]+)"', svg.read_text(encoding="utf-8"))
        if m:
            vb = [float(x) for x in m.group(1).replace(",", " ").split()]
    except (OSError, ValueError):
        vb = None

    ab_rect = (artboards[0] or {}).get("rect") if artboards else None
    # One source for width and height, used by the guard, the comparison and the
    # value that gets written. Previously the guard checked the raw rect while
    # the emitted value was computed separately -- so reverting only the emitted
    # expression produced artboard=[200,-200] with every gate still green. A
    # guard that checks a different expression from the one you ship is not a
    # guard.
    _w = abs(ab_rect[2] - ab_rect[0]) if ab_rect else 0.0
    _h = abs(ab_rect[3] - ab_rect[1]) if ab_rect else 0.0
    ab_ok = bool(ab_rect and _w > 0 and _h > 0)
    dims_ok = bool(ab_ok and vb and abs(_w - vb[2]) <= 1 and abs(_h - vb[3]) <= 1)
    paths_n = int(kinds.get("Path", 0))
    # Count placed/linked objects from the reopened file instead of asserting
    # zero: H1 (and aggregate_log) treat placed==0 as evidence, so a constant
    # here was not a measurement. land_craft counts the same way.
    placed_n = sum(v for k, v in kinds.items()
                   if "placed" in k.lower() or "link" in k.lower())
    layers_info = kinds.get("Layer")
    sr = {
        "schema": "vector-ai-studio/structure-readback-v1",
        "ok": bool(paths_n > 0 and obs["images"] == 0 and placed_n == 0 and dims_ok),
        "raster": int(obs["images"]),          # images embedded in the file
        "placed": int(placed_n),                # linked/placed objects found on reopen
        "paths": paths_n,
        "layers": obs["layers"],
        # rect is [left, top, right, bottom] with top > bottom, so height is
        # top - bottom. Written the other way round it produced [200, -200]
        # while artboard_matches_viewBox said true next to it -- a wrong value
        # sitting beside a reassuring flag is worse than no value.
        "artboard": ([round(_w, 2), round(_h, 2)] if ab_rect else None),
        "viewBox": vb,
        "artboard_matches_viewBox": dims_ok,
        "automation": "vectorcraft",
        # Two independent sources for the layer count; disagreement is recorded
        # rather than one silently winning.
        "layers_from_kinds": layers_info,
        "layers_from_ocg": obs["layers"],
        "layers_agree": (layers_info is None or layers_info == obs["layers"]),
        "evidence": "vectorcraft-cli info (reopen of the produced file)",
        "source": "vectorcraft-info",
        "objects_by_kind": kinds,
    }
    L.write_json_atomic(run_dir / "structure_readback.json", sr)
    if not sr["layers_agree"]:
        deliverable["warnings"].append(
            "图层数两个来源不一致：kinds.Layer=" + str(layers_info) +
            "、/OCGs=" + str(obs["layers"]) + "（清单保留两者，未择一）")

    # Fill structure{} from what was measured, not from empty placeholders.
    # Leaving seven of them null here made the manifest disagree with
    # structure_readback.json from the same run, and a reader could not tell
    # "unknown" from "empty". Written last so every warning collected above
    # lands in the file.
    _ab = sr.get("artboard")
    _vb = sr.get("viewBox")
    deliverable["structure"] = {
        "open_paths": None,                       # no open phase on this engine
        "reopen_paths": paths_n,                  # paths after re-reading the product
        "layers": sr["layers"],
        "reopen_layers": sr.get("layers_from_kinds"),
        "raster": sr["raster"],
        "placed": sr["placed"],
        "artboard": (str(int(round(_ab[0]))) + "x" + str(int(round(_ab[1])))) if _ab else None,
        "viewBox": ("_".join(str(int(round(v))) for v in _vb)) if _vb else None,
        "trimmarks_ink": None,                    # this engine draws no crop marks
    }
    L.write_json_atomic(run_dir / "deliverable.json", deliverable)

    h1 = bool(sr["ok"])
    L.write_json_atomic(run_dir / "landing.json",
                        LP._landing("vectorcraft", "ok" if h1 else "degraded",
                                    "" if h1 else "structure readback did not satisfy H1",
                                    True, h1,
                                    substitute=("inspect the source artboard/fonts; "
                                                "the engine itself was found and ran")))
    status = "ok" if h1 else "degraded"
    return L.emit(SCRIPT, status=status, code=L.EXIT_OK if h1 else L.EXIT_FAIL,
                  run_id=args.run_id,
                  data={"engine": "vectorcraft", "profile": args.profile,
                        "h1_pass": h1, "deliverable": deliverable},
                  artifacts={"final_ai": "final.ai",
                             "final_pdf": "final.pdf" if pdf_path.is_file() else "",
                             "final_svg": "final.svg" if svg_out.is_file() else "",
                             "preview_png": "preview.png" if preview.is_file() else "",
                             "deliverable": "deliverable.json"})


if __name__ == "__main__":
    sys.exit(L.run_cli(SCRIPT, main))

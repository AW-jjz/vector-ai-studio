#!/usr/bin/env python3
"""C3 / H1: deterministic SVG sanitize + static checks.

Detects bitmap, XML errors, forbidden constructs, degenerate paths and
counts semantic groups. On violation it reports explicitly and refuses to
rewrite semantic elements (semantic rewriting belongs to the LLM).
Optional --prev compares passed-item structure (E-REGRESSION warning).
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L

SCRIPT = "sanitize_svg.py"


def _clean_text(text):
    return text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")


def _structure(sig):
    return {"g_ids": sorted(sig["g_ids"]),
            "element_count": sig["element_count"],
            "colors": list(sig["colors"])}


def main():
    ap = argparse.ArgumentParser(description="sanitize and statically check an SVG")
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--prev", default="")
    ap.add_argument("--run-id", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    src = Path(args.inp)
    if not src.is_file():
        raise L.ToolError("E-XML", "input SVG not found: " + src.name, L.EXIT_USAGE)
    text = _clean_text(src.read_text(encoding="utf-8"))
    sig = L.analyze_svg(text)

    errors = []
    if not sig["parse_ok"]:
        errors.append(L.err("E-XML", sig["error"]))
    if sig["has_image"] or sig["has_base64"]:
        errors.append(L.err("E-BITMAP", "bitmap / data:image / base64 detected"))
    if sig["forbidden"]:
        errors.append(L.err("E-FORBIDDEN", "forbidden constructs: " + ", ".join(sig["forbidden"])))
    if sig["degenerate_paths"] > 0:
        errors.append(L.err("E-DEGENERATE",
                            "degenerate (zero-area / repeated-point) paths: " +
                            str(sig["degenerate_paths"])))

    warnings = []
    regression = {"checked": False, "changed": False, "detail": ""}
    if args.prev:
        pp = Path(args.prev)
        if pp.is_file():
            prev = L.analyze_svg(_clean_text(pp.read_text(encoding="utf-8")))
            now, old = _structure(sig), _structure(prev)
            changed = (now["g_ids"] != old["g_ids"] or
                       now["element_count"] != old["element_count"] or
                       now["colors"] != old["colors"])
            regression = {"checked": True, "changed": changed,
                          "detail": "passed-item structure (g ids / element count / colors)"}
            if changed:
                warnings.append("E-REGRESSION: must_keep structure changed; re-generate this round")
        else:
            warnings.append("E-REGRESSION: --prev file not found, comparison skipped")

    static_checks = {
        "xml_ok": sig["parse_ok"],
        "bitmap": sig["has_image"] or sig["has_base64"],
        "forbidden": list(sig["forbidden"]),
        "degenerate_paths": sig["degenerate_paths"],
        "g_ids": list(sig["g_ids"]),
        "g_count": len(sig["g_ids"]),
        "element_count": sig["element_count"],
        "anchor_count": sig["anchor_count"],
        "colors": list(sig["colors"]),
        "stroke_widths": list(sig["stroke_widths"]),
        "viewBox": sig["viewBox"],
        "artboard": sig["artboard"],
        "ink_bbox": sig["ink_bbox"],
        "structure": _structure(sig),
        "regression": regression,
    }

    if errors:
        # Explicit rejection: never silently delete or rewrite semantic elements.
        return L.emit(SCRIPT, status="fail", code=L.EXIT_FAIL,
                      run_id=args.run_id, data={"static_checks": static_checks},
                      errors=errors, warnings=warnings)

    artifacts = {}
    if args.out:
        L.write_text_atomic(args.out, text)
        artifacts["sanitized_svg"] = Path(args.out).name
    L.append_run_log(Path(args.out).parent if args.out else L.default_output_root(),
                     SCRIPT + " clean g_count=" + str(len(sig["g_ids"])))

    return L.emit(SCRIPT, status="ok", code=L.EXIT_OK, run_id=args.run_id,
                  data={"static_checks": static_checks},
                  warnings=warnings, artifacts=artifacts)


if __name__ == "__main__":
    sys.exit(L.run_cli(SCRIPT, main))

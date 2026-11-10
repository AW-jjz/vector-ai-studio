#!/usr/bin/env python3
"""Measure whether a mark survives being small, grey and flat.

Why: a 240px preview can look fine while the 16px version is mud and the
greyscale version has no figure at all. Both failures are invisible in the
score's geometry and colour counts, and both are what a client sees on a favicon
or a stamped bag. This script measures three things that survive the renderer
argument because it reads the PNG, not the SVG:

  contrast     WCAG 2.1 SC 1.4.11 ratio between the foreground's mean colour
               and the background's modal colour (external standard, gating)
  collapse     foreground coverage at N px; zero ink is collapsed, and full ink
               is the mirror failure (MUD) -- both leave no silhouette
  structure    connected foreground components and the coverage drift between
               full size and N px (reported; engineering values, warning only)

Hard gates are only the ones with a defensible basis. Fragmentation and
coverage drift are reported so a human can look, never silently turned into a
score.

Pillow is an optional enhancer. Without it the script degrades (exit 3) instead
of guessing from the SVG.
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L

SCRIPT = "legibility.py"


def _luma(r, g, b):
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _wcag_lum(rgb):
    def ch(c):
        c = c / 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(x) for x in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a, b):
    la, lb = _wcag_lum(a), _wcag_lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _mask(pix, w, h, bg_luma, gap):
    out = []
    for y in range(h):
        for x in range(w):
            r, g, b = pix[x, y][:3]
            out.append(1 if abs(_luma(r, g, b) - bg_luma) > gap else 0)
    return out


def _components(mask, w, h):
    seen = [False] * (w * h)
    n = 0
    for start in range(w * h):
        if mask[start] == 0 or seen[start]:
            continue
        n += 1
        stack = [start]
        seen[start] = True
        while stack:
            i = stack.pop()
            x, y = i % w, i // w
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h:
                    j = ny * w + nx
                    if mask[j] and not seen[j]:
                        seen[j] = True
                        stack.append(j)
    return n


def _analyse(img, n, gap):
    img = img.convert("RGBA")
    w, h = img.size
    flat = img.convert("RGB")
    pix = flat.load()
    lumas = Counter()
    for y in range(h):
        for x in range(w):
            r, g, b = pix[x, y]
            lumas[int(_luma(r, g, b)) // 16] += 1
    bg_bin = lumas.most_common(1)[0][0]
    bgs = [pix[x, y] for y in range(h) for x in range(w)
           if int(_luma(*pix[x, y])) // 16 == bg_bin]
    bg = tuple(sum(c[i] for c in bgs) // len(bgs) for i in range(3))
    bg_luma = _luma(*bg)
    mask = _mask(pix, w, h, bg_luma, gap)
    cov = sum(mask) / float(w * h)
    fgs = [pix[x, y] for y in range(h) for x in range(w) if mask[y * w + x]]
    fg = tuple(sum(c[i] for c in fgs) // len(fgs) for i in range(3)) if fgs else bg

    small = flat.resize((n, n))             # bicubic: averages ink like a real downscale
    sp = small.load()
    smask = _mask(sp, n, n, bg_luma, gap)
    cov_s = sum(smask) / float(n * n)
    bands = sorted({int(_luma(*pix[x, y])) // 16 for y in range(h)
                    for x in range(w) if mask[y * w + x]})
    return {
        "size": [w, h],
        "background": "#%02x%02x%02x" % bg,
        "foreground": "#%02x%02x%02x" % fg,
        "contrast": round(_contrast(fg, bg), 2),
        "coverage": round(cov, 4),
        "coverage_small": round(cov_s, 4),
        "components": _components(mask, w, h),
        "components_small": _components(smask, n, n),
        "gray_bands": len(bands),
        "small_px": n,
    }


def main():
    ap = argparse.ArgumentParser(description="16px / grey / silhouette check")
    ap.add_argument("--png", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--run-id", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    th = L.load_thresholds()
    cfg = th.get("L", {})
    n = int(cfg.get("downscale_px", 16))
    gap = float(cfg.get("gap_luminance", 32))
    ratio_min = float(th["C"]["contrast_graphic_ratio"])
    drift = cfg.get("coverage_drift_warn", [0.5, 2.0])
    frag = float(cfg.get("fragment_growth_warn", 2.0))
    mud = float(cfg.get("coverage_small_mud", 1.0))

    try:
        from PIL import Image
    except ImportError:
        return L.emit(SCRIPT, status="degraded", code=L.EXIT_MISSING,
                      data={"png": Path(args.png).name},
                      errors=[L.err("E-RENDERER-MISSING",
                                    "Pillow missing: cannot read a PNG; legibility "
                                    "stays unmeasured")])

    p = Path(args.png)
    if not p.is_file():
        raise L.ToolError("E-USAGE", "PNG not found: " + p.name, L.EXIT_USAGE)
    try:
        img = Image.open(p)
        img.load()
    except Exception as e:                      # noqa: BLE001
        raise L.ToolError("E-USAGE", "PNG unreadable: " + str(e)[:120],
                          L.EXIT_USAGE)

    m = _analyse(img, n, gap)
    verdicts, fails = [], []
    if m["contrast"] < ratio_min:
        fails.append("LOW-CONTRAST")
        verdicts.append({"id": "LOW-CONTRAST",
                         "msg": "contrast %.2f < %.1f (WCAG SC 1.4.11)"
                                % (m["contrast"], ratio_min),
                         "action": "加深前景或提亮背景，灰度下重看"})
    if m["coverage_small"] == 0:
        fails.append("COLLAPSED")
        verdicts.append({"id": "COLLAPSED",
                         "msg": "no ink left at %dpx" % n,
                         "action": "加粗主笔或放大一级形，删掉只在 240px 成立的细节"})
    # The mirror of COLLAPSED: every pixel becomes ink, so the background that
    # carried the silhouette is gone. A checkerboard or a dense line field does
    # this at 1/15 zoom. Same basis as COLLAPSED -- shape is undefined at the
    # endpoint -- so it gates rather than warns.
    if m["coverage_small"] >= mud:
        fails.append("MUD")
        verdicts.append({"id": "MUD",
                         "msg": "coverage %.2f at %dpx: silhouette filled in"
                                % (m["coverage_small"], n),
                         "action": "拉开笔距或减掉一层排线，给背景留出至少三成"})
    if m["components_small"] > max(3.0, m["components"] * frag):
        verdicts.append({"id": "FRAGMENTED", "msg": "components %d -> %d"
                         % (m["components"], m["components_small"]),
                         "action": "合并相邻图元，减少小尺寸下的碎块"})
    if m["coverage"] and not (drift[0] <= m["coverage_small"] / m["coverage"] <= drift[1]):
        verdicts.append({"id": "COVERAGE-DRIFT", "msg": "coverage %.3f -> %.3f"
                         % (m["coverage"], m["coverage_small"]),
                         "action": "检查细线/小间距是否在缩小后糊掉"})
    if not fails:
        verdicts.insert(0, {"id": "OK", "msg": "survives %dpx and greyscale" % n,
                            "action": ""})

    doc = {"schema": "vector-ai-studio/legibility-v1",
           "png": p.name, "measured": m, "verdicts": verdicts,
           "gates": {"contrast_graphic_ratio": ratio_min,
                     "collapse": "coverage at %dpx == 0" % n}}
    if args.out:
        L.write_json_atomic(Path(args.out), doc)
    code = L.EXIT_FAIL if fails else L.EXIT_OK
    return L.emit(SCRIPT, status="ok", code=code, run_id=args.run_id, data=doc,
                  artifacts={"legibility": Path(args.out).name} if args.out else {})


if __name__ == "__main__":
    sys.exit(L.run_cli(SCRIPT, main))

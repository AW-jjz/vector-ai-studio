#!/usr/bin/env python3
"""Remove the photograph's lighting before the artwork goes to print.

A studio photo bakes its lighting into the pixels: one side brighter, a cast
shadow under the base, dead-white speculars on the stones. Printed on a real
object those become a second, wrong light source.

ROOT CAUSE / CONTRACT (changed after three failed heuristic patches):
whether a near-white pixel is *paper* or a *highlight inside the object* cannot
be decided from its colour, its connectivity to the border, or any local shape
test. Every such rule has a counterexample (bridged highlights, axial slits,
diagonal slits, curved slits, thin highlights). So this script no longer tries.

  * Without --mask: only pixels that are clearly coloured/dark (luminance below
    SPEC_LUM) are de-lit. Every near-white pixel is left BYTE-IDENTICAL. Paper
    stays paper, highlights stay highlights; nothing is dyed, nothing is
    force-painted white, and specular_repaired is never inflated.
  * With --mask: the caller's silhouette is authoritative. Near-white pixels
    inside it are highlights and are repaired from the surrounding colour;
    everything outside is left untouched. Deterministic, no guessing.

To de-glare a photo's highlights, supply a subject mask. Everything else is a
known limitation and is declared in SKILL.md rather than patched with geometry.
"""
import argparse
import sys
from pathlib import Path

from PIL import Image, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L

SCRIPT = "delight.py"
SPEC_LUM = 230        # at/above this luminance a pixel is "uncertain"
SPEC_SAT = 45         # and this unsaturated -> a blown specular when masked


def conservative_subject(lum):
    """The only assertion we can make without a mask: a dark/coloured pixel is
    subject. Near-white is deliberately excluded so it can never be misjudged."""
    W, H = lum.size
    m = Image.new("L", (W, H), 0)
    mp, lp = m.load(), lum.load()
    for y in range(H):
        for x in range(W):
            if lp[x, y] < SPEC_LUM:
                mp[x, y] = 255
    return m


def load_mask(path, size):
    """A caller-supplied silhouette: non-zero = subject. Refused when sizes do
    not match, because a silently rescaled mask is a silently wrong result."""
    p = Path(path)
    if not p.is_file():
        raise L.ToolError("E-SCHEMA", "mask not found: " + p.name, L.EXIT_USAGE)
    m = Image.open(p).convert("L")
    if m.size != size:
        raise L.ToolError("E-USAGE",
                          "mask size %sx%s does not match image %sx%s" %
                          (m.width, m.height, size[0], size[1]), L.EXIT_USAGE)
    mp = m.load()
    W, H = size
    out = Image.new("L", size, 0)
    op = out.load()
    for y in range(H):
        for x in range(W):
            if mp[x, y] > 127:
                op[x, y] = 255
    return out


def delight(im, strength=0.65, blur=130, sat=1.05, lo=0.62, hi=1.65,
            mask=None, repair_spec=True):
    W, H = im.size
    px = im.load()
    lum = im.convert("L")
    lp = lum.load()

    masked = mask is not None
    subject = mask if masked else conservative_subject(lum)
    mp = subject.load()

    tot = cnt = 0
    for y in range(0, H, 2):
        for x in range(0, W, 2):
            if mp[x, y]:
                tot += lp[x, y]
                cnt += 1
    if cnt == 0:
        raise L.ToolError("E-DELIGHT-EMPTY", "no subject pixels found", L.EXIT_FAIL)
    mean_subj = tot / float(cnt)

    # illumination estimate: only subject luminance, elsewhere held at the mean
    fill = Image.new("L", (W, H), int(mean_subj))
    fp = fill.load()
    for y in range(H):
        for x in range(W):
            if mp[x, y]:
                fp[x, y] = lp[x, y]
    ip = fill.filter(ImageFilter.GaussianBlur(blur)).load()
    soft = im.filter(ImageFilter.GaussianBlur(26)).load()

    out = im.copy()
    op = out.load()
    spec = kept = 0
    for y in range(H):
        for x in range(W):
            r, g, b = px[x, y]
            if not mp[x,y]:
                # Never repaint an unclassified pixel. This is the whole fix.
                kept += 1
                continue
            mx, mn = max(r, g, b), min(r, g, b)
            is_spec = ((0.299 * r + 0.587 * g + 0.114 * b) > SPEC_LUM
                       and (mx - mn) < SPEC_SAT)
            if is_spec and masked and repair_spec:
                nr, ng, nb = soft[x, y]
                spec += 1
            else:
                k = mean_subj / max(ip[x, y], 1.0)
                k = min(hi, max(lo, k))
                k = 1.0 + strength * (k - 1.0)
                nr, ng, nb = r * k, g * k, b * k
                avg = (nr + ng + nb) / 3.0
                nr = avg + (nr - avg) * sat
                ng = avg + (ng - avg) * sat
                nb = avg + (nb - avg) * sat
            op[x, y] = (int(min(255, max(0, nr))),
                        int(min(255, max(0, ng))),
                        int(min(255, max(0, nb))))
    return out, {"mean_subject_luminance": round(mean_subj, 1),
                 "mask_mode": "explicit" if masked else "conservative",
                 "specular_repaired": spec,
                 "pixels_left_untouched": kept,
                 "subject_pixels": cnt * 4}


def _lowfreq_span(im, blur=80):
    lum = im.convert("L")
    W, H = lum.size
    lp = lum.load()
    bp = lum.filter(ImageFilter.GaussianBlur(blur)).load()
    vals = [bp[x, y] for y in range(0, H, 3) for x in range(0, W, 3) if lp[x, y] < 238]
    if not vals:
        return None
    vals.sort()
    n = len(vals)
    return vals[int(n * 0.05)], vals[int(n * 0.95)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--mask", help="grayscale subject silhouette (non-zero = object)")
    ap.add_argument("--strength", type=float, default=0.65)
    ap.add_argument("--blur", type=int, default=130)
    ap.add_argument("--sat", type=float, default=1.05)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    src = Path(args.src)
    if not src.is_file():
        raise L.ToolError("E-SCHEMA", "input not found: " + src.name, L.EXIT_USAGE)
    if not (0.0 <= args.strength <= 1.0):
        raise L.ToolError("E-USAGE", "--strength must be 0..1", L.EXIT_USAGE)

    im = Image.open(src).convert("RGB")
    m = load_mask(args.mask, im.size) if args.mask else None
    before = _lowfreq_span(im)
    out, info = delight(im, args.strength, args.blur, args.sat, mask=m)
    after = _lowfreq_span(out)

    dst = Path(args.out)
    dst.parent.mkdir(parents=True, exist_ok=True)
    out.save(dst, quality=96, subsampling=0)

    warnings = []
    if m is None:
        warnings.append(
            "conservative mode: near-white pixels (paper AND highlights) are "
            "left unchanged; supply --mask to de-glare speculars")
    if dst.suffix.lower() in (".jpg", ".jpeg"):
        warnings.append(
            "JPEG output is lossy: near-white pixels are byte-identical in "
            "memory but edges can shift by a few levels on disk; use PNG when "
            "that guarantee matters")

    data = dict(info, strength=args.strength, blur=args.blur,
                span_before=(before[1] - before[0]) if before else None,
                span_after=(after[1] - after[0]) if after else None,
                out=str(dst))
    if before and after:
        data["span_reduction_pct"] = round(
            100.0 * (1 - (after[1] - after[0]) / float(max(before[1] - before[0], 1))), 1)
    return L.emit(SCRIPT, status="ok", code=0, data=data, warnings=warnings,
                  artifacts={"image": dst.name})


if __name__ == "__main__":
    sys.exit(L.run_cli(SCRIPT, main))

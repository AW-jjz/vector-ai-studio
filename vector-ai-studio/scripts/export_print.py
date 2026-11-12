#!/usr/bin/env python3
"""Emit a print file at the size the job actually needs.

The pipeline lands vectors 1:1 with the viewBox, which is a working size, not a
delivery size. Print jobs need three numbers fixed together: finished size,
device resolution and bleed. Getting them wrong is the classic way a file comes
back the wrong physical size — and a weak RIP cannot even open a 140k-path PDF,
so the honest fallback is a flat raster at exactly the right pixels.

Vector input (.pdf/.ai/.eps/.svg) is rasterised through the VectorCraft CLI and
reported as capability_missing when that CLI is absent — never as a silent
success with a smaller file.
"""
import argparse
import subprocess
import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L
from trace_ref import find_cli

SCRIPT = "export_print.py"
VECTOR_EXT = {".pdf", ".ai", ".eps", ".svg"}


def mm_to_px(mm, dpi):
    return int(round(mm / 25.4 * dpi))


def rasterise(src, dst, dpi):
    cli = find_cli()
    if cli is None:
        raise L.ToolError(
            "E-CAPABILITY-MISSING",
            "vector input needs the VectorCraft CLI to rasterise; "
            "install it or pass a raster file", L.EXIT_MISSING)
    p = subprocess.run([str(cli), "convert", str(src), str(dst), "--ppi", str(dpi)],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=900)
    if p.returncode != 0 or not Path(dst).is_file():
        raise L.ToolError("E-RENDER", "vectorcraft convert failed: " +
                          (p.stderr or p.stdout)[-300:], L.EXIT_ERROR)


def extend_bleed(im, b):
    """Mirror the edge pixels outwards.

    A white band is not bleed: on a full-bleed or dark-background job, trimming
    to the finished size leaves a white hairline on all four sides. Mirroring the
    edge keeps the artwork continuous past the trim, and on a white-background
    logo it naturally produces white anyway.
    """
    if b <= 0:
        return im, 0
    b = min(b, im.width - 1, im.height - 1)
    W, H = im.size
    v = Image.new("RGB", (W, H + 2 * b))
    v.paste(im, (0, b))
    top = im.crop((0, 0, W, b)).transpose(Image.FLIP_TOP_BOTTOM)
    bottom = im.crop((0, H - b, W, H)).transpose(Image.FLIP_TOP_BOTTOM)
    v.paste(top, (0, 0))
    v.paste(bottom, (0, b + H))
    W2, H2 = v.size
    h = Image.new("RGB", (W2 + 2 * b, H2))
    h.paste(v, (b, 0))
    left = v.crop((0, 0, b, H2)).transpose(Image.FLIP_LEFT_RIGHT)
    right = v.crop((W2 - b, 0, W2, H2)).transpose(Image.FLIP_LEFT_RIGHT)
    h.paste(left, (0, 0))
    h.paste(right, (b + W2, 0))
    return h, b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--width-mm", type=float)
    ap.add_argument("--height-mm", type=float)
    ap.add_argument("--dpi", type=int, default=600)
    ap.add_argument("--bleed-mm", type=float, default=3.0)
    ap.add_argument("--bleed-mode", choices=("extend", "white"), default="extend")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.width_mm is None and args.height_mm is None:
        raise L.ToolError("E-USAGE", "give --width-mm or --height-mm", L.EXIT_USAGE)
    if not (72 <= args.dpi <= 1200):
        raise L.ToolError("E-USAGE", "--dpi must be 72..1200", L.EXIT_USAGE)
    if args.bleed_mm < 0:
        raise L.ToolError("E-USAGE", "--bleed-mm must be >= 0", L.EXIT_USAGE)

    src = Path(args.src)
    if not src.is_file():
        raise L.ToolError("E-SCHEMA", "input not found: " + src.name, L.EXIT_USAGE)

    work = None
    if src.suffix.lower() in VECTOR_EXT:
        work = Path(args.out).with_suffix(".raster.png")
        rasterise(src, work, args.dpi)
        src_img = work
        kind = "vector"
    else:
        src_img = src
        kind = "raster"

    im = Image.open(src_img)
    im = im.convert("RGBA") if im.mode in ("RGBA", "LA", "P") else im.convert("RGB")
    if im.mode == "RGBA":
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[3])
        im = bg

    ar = im.width / float(im.height)
    if args.width_mm and not args.height_mm:
        w_mm = args.width_mm
        h_mm = w_mm / ar
    elif args.height_mm and not args.width_mm:
        h_mm = args.height_mm
        w_mm = h_mm * ar
    else:
        w_mm, h_mm = args.width_mm, args.height_mm
        im = im.resize((mm_to_px(w_mm, args.dpi), mm_to_px(h_mm, args.dpi)), Image.LANCZOS)

    tw, th = mm_to_px(w_mm, args.dpi), mm_to_px(h_mm, args.dpi)
    if im.size != (tw, th):
        im = im.resize((tw, th), Image.LANCZOS)

    b = mm_to_px(args.bleed_mm, args.dpi)
    b_eff = b
    if args.bleed_mode == "extend":
        canvas, b_eff = extend_bleed(im, b)
    else:
        canvas = Image.new("RGB", (tw + 2 * b, th + 2 * b), (255, 255, 255))
        canvas.paste(im, (b, b))
    warnings = []
    if b_eff != b:
        warnings.append(
            "bleed clamped from %d to %d px: the artwork is smaller than the "
            "requested bleed; canvas_mm reflects the actual canvas" % (b, b_eff))

    dst = Path(args.out)
    dst.parent.mkdir(parents=True, exist_ok=True)
    ext = dst.suffix.lower()
    if ext in (".jpg", ".jpeg"):
        canvas.save(dst, quality=93, subsampling=0, dpi=(args.dpi, args.dpi))
    elif ext == ".pdf":
        canvas.save(dst, resolution=float(args.dpi))
    elif ext == ".png":
        canvas.save(dst, dpi=(args.dpi, args.dpi))
    elif ext == ".tif" or ext == ".tiff":
        canvas.save(dst, compression="tiff_lzw", dpi=(args.dpi, args.dpi))
    else:
        raise L.ToolError("E-USAGE", "unsupported output extension: " + ext, L.EXIT_USAGE)

    if work is not None:
        try:
            work.unlink()
        except OSError:
            pass

    return L.emit(SCRIPT, status="ok", code=0, data={
        "input_kind": kind,
        "trim_mm": [round(w_mm, 2), round(h_mm, 2)],
        "bleed_mm": args.bleed_mm,
        "bleed_mode": args.bleed_mode,
        "canvas_mm": [round(canvas.width / float(args.dpi) * 25.4, 2),
                      round(canvas.height / float(args.dpi) * 25.4, 2)],
        "bleed_px_effective": b_eff,
        "trim_px": [tw, th],
        "canvas_px": [canvas.width, canvas.height],
        "dpi": args.dpi,
        "bytes": dst.stat().st_size,
    }, warnings=warnings, artifacts={"print_file": dst.name})


if __name__ == "__main__":
    sys.exit(L.run_cli(SCRIPT, main))

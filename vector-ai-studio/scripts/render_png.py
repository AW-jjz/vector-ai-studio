#!/usr/bin/env python3
"""PNG preview: renderer priority probe, then a REAL builtin SVG->PNG fallback.

H2 fallback rule: the fallback only counts toward H2 if the produced file is a real,
parseable PNG (non-zero bytes, PNG magic, readable width/height, IEND). A
zero-byte or placeholder file is never written. Fidelity is explicitly
limited and flagged as degraded.
"""

import argparse
import importlib.util
import json
import shutil
import struct
import subprocess
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L

SCRIPT = "render_png.py"
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MAX_SIDE = 256  # rasterizer bound, not a quality threshold
_NAMED = {"black": (17, 17, 17), "white": (255, 255, 255),
          "red": (220, 60, 60), "blue": (47, 111, 237),
          "green": (60, 170, 90), "gray": (130, 130, 130),
          "grey": (130, 130, 130), "none": None}


def _rgb(color):
    if color is None:
        return None
    c = L.normalize_color(color)
    if c is None:
        return None
    if c.startswith("#") and len(c) == 7:
        return (int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16))
    return _NAMED.get(c, (90, 90, 90))


def _png_chunk(typ, data):
    return (struct.pack(">I", len(data)) + typ + data +
            struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF))


def _encode_png(w, h, pixels):
    raw = bytearray()
    stride = w * 4
    for y in range(h):
        raw.append(0)  # filter type 0 (None)
        raw += pixels[y * stride:(y + 1) * stride]
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return (PNG_MAGIC + _png_chunk(b"IHDR", ihdr) +
            _png_chunk(b"IDAT", zlib.compress(bytes(raw), 9)) +
            _png_chunk(b"IEND", b""))


def _builtin_fidelity(sig):
    """Can the builtin rasteriser represent *this* SVG without lying?

    _builtin_raster fills each element's bounding box with one solid colour and
    draws nothing else. That is only faithful when every element really is an
    axis-aligned, solid-filled rectangle with no visible stroke: circles become
    squares, paths become slabs, strokes disappear, text vanishes. A "limited"
    preview must never be used to judge semantic fidelity or composition
    (design 11 item 3: fallback image that cannot support reading -> auto L2).
    """
    els = sig.get("elements") or []
    if not els:
        return "limited"
    for e in els:
        if e.get("tag") != "rect":
            return "limited"
        if _rgb(e.get("fill")) is None:
            return "limited"            # stroke-only / gradient: the outline is not drawn
        sw = e.get("stroke_width")
        if isinstance(sw, (int, float)) and sw > 0 and _rgb(e.get("stroke")) is not None:
            return "limited"            # stroked edge widens the shape in any real renderer
    return "faithful"


def _builtin_raster(sig):
    aw, ah = sig["artboard"]
    if aw <= 0 or ah <= 0:
        aw, ah = 200.0, 200.0
    scale = min(1.0, MAX_SIDE / max(aw, ah))
    w, h = max(1, int(aw * scale)), max(1, int(ah * scale))
    pixels = bytearray(b"\xff\xff\xff\xff" * (w * h))  # white RGBA background
    for e in sig["elements"]:
        col = _rgb(e.get("fill")) or _rgb(e.get("stroke"))
        if col is None:
            continue
        bbox = e["bbox"]
        x0 = max(0, min(w - 1, int(bbox[0] * scale)))
        y0 = max(0, min(h - 1, int(bbox[1] * scale)))
        x1 = max(0, min(w, int(bbox[2] * scale) + 1))
        y1 = max(0, min(h, int(bbox[3] * scale) + 1))
        r, g, b = col
        for yy in range(y0, max(y0 + 1, y1)):
            row = yy * w * 4
            for xx in range(x0, max(x0 + 1, x1)):
                p = row + xx * 4
                pixels[p] = r
                pixels[p + 1] = g
                pixels[p + 2] = b
                pixels[p + 3] = 255
    return w, h, _encode_png(w, h, pixels)


def _try_resvg(src, out):
    exe = shutil.which("resvg")
    if not exe:
        return False
    try:
        r = subprocess.run([exe, str(src), str(out)], timeout=30,
                           capture_output=True, check=False)
        return r.returncode == 0 and out.is_file() and out.read_bytes()[:8] == PNG_MAGIC
    except (OSError, subprocess.SubprocessError):
        return False


def _try_cairosvg(text, out, w, h):
    if importlib.util.find_spec("cairosvg") is None:
        return False
    try:
        import cairosvg
        cairosvg.svg2png(bytestring=text.encode("utf-8"), write_to=str(out),
                         output_width=w, output_height=h)
        return out.is_file() and out.read_bytes()[:8] == PNG_MAGIC
    except Exception:  # noqa: BLE001 - optional renderer, explicit fallback
        return False


def main():
    ap = argparse.ArgumentParser(description="render a real PNG preview")
    ap.add_argument("--in", dest="inp", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--deps", default="")
    ap.add_argument("--run-id", default="")
    ap.add_argument("--force-builtin", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    src = Path(args.inp)
    if not src.is_file():
        raise L.ToolError("E-RENDERER-MISSING", "input SVG not found: " + src.name, L.EXIT_USAGE)
    text = src.read_text(encoding="utf-8")
    sig = L.analyze_svg(text)
    if not sig["parse_ok"]:
        raise L.ToolError("E-RENDERER-MISSING", "input SVG XML invalid: " + sig["error"], L.EXIT_USAGE)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    th = L.load_thresholds()

    candidates = []
    renderer = None
    degraded = False
    note = ""
    w = h = 0
    fidelity = "full"      # real renderer: pixel-faithful by construction

    if not args.force_builtin and _try_resvg(src, out):
        renderer = "resvg"
        candidates.append("resvg")
    else:
        candidates.append("resvg:MISSING" if not args.force_builtin else "resvg:SKIPPED(force-builtin)")
        aw, ah = sig["artboard"]
        tw = max(1, int(aw if aw > 0 else 200))
        thh = max(1, int(ah if ah > 0 else 200))
        if not args.force_builtin and _try_cairosvg(text, out, tw, thh):
            renderer = "cairosvg"
            candidates.append("cairosvg")
        else:
            candidates.append("cairosvg:MISSING" if not args.force_builtin
                              else "cairosvg:SKIPPED(force-builtin)")
            candidates.append("headless-browser:MISSING")
            w, h, data = _builtin_raster(sig)
            if not data or len(data) < 24 or data[:8] != PNG_MAGIC:
                return L.emit(SCRIPT, status="missing", code=L.EXIT_MISSING,
                              run_id=args.run_id,
                              data={"renderer_used": None, "candidates": candidates},
                              errors=[L.err("E-RENDERER-MISSING",
                                            "all renderers MISSING and builtin fallback failed")],
                              degradation={"level": "L4",
                                           "reason": "no PNG renderer and no valid fallback",
                                           "substitute": "install resvg or cairosvg (optional enhancement)"})
            out.write_bytes(data)
            renderer = "builtin-fallback"
            degraded = True
            fidelity = _builtin_fidelity(sig)
            note = th["messages"]["fallback_png_note"]
            candidates.append("builtin-fallback:USED")
            candidates.append("fidelity:" + fidelity)

    if renderer != "builtin-fallback":
        try:
            b = out.read_bytes()
            if len(b) >= 24 and b[:8] == PNG_MAGIC and b[12:16] == b"IHDR":
                w = struct.unpack(">I", b[16:20])[0]
                h = struct.unpack(">I", b[20:24])[0]
        except OSError:
            pass

    meta = {
        "schema": "vector-ai-studio/preview-meta-v1",
        "renderer": renderer,
        "degraded": degraded,
        "fidelity": fidelity,
        "note": note,
        "width": w, "height": h,
        "candidates": candidates,
        "source_svg": src.name,
    }
    L.write_json_atomic(out.with_name("preview.meta.json"), meta)
    L.append_run_log(out.parent, SCRIPT + " renderer=" + str(renderer) + " fidelity=" + fidelity)

    return L.emit(SCRIPT, status="degraded" if degraded else "ok",
                  code=L.EXIT_OK, run_id=args.run_id,
                  data={"renderer_used": renderer, "degraded": degraded,
                        "fidelity": fidelity,
                        "note": note, "width": w, "height": h,
                        "candidates": candidates},
                  warnings=(["fallback preview fidelity is limited: " + note]
                            if degraded and fidelity != "faithful" else []),
                  degradation=({"level": "L4", "reason": "renderer candidates MISSING",
                                "substitute": "builtin minimal SVG->PNG rasterizer"}
                               if degraded else None),
                  artifacts={"preview_png": out.name, "preview_meta": "preview.meta.json"})


if __name__ == "__main__":
    sys.exit(L.run_cli(SCRIPT, main))

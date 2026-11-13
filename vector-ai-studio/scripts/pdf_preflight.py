#!/usr/bin/env python3
"""pdf_preflight.py — an INDEPENDENT read-back of a print PDF (4.3 / r82).

Every existing print check reads the file with this project's own byte parser
(`scripts/lib_pdf.py`, and land_craft's `_pdf_facts`). One parser agreeing with
itself is not a cross-check: a misread of the PDF syntax would be repeated on
both sides.

This script opens the same file with PyMuPDF (MuPDF), a separate PDF engine,
and compares what the two implementations see:

  * page count and page size
  * image objects
  * font presence (PyMuPDF lists font entries; lib_pdf embeds programs)
  * output-intent presence (PyMuPDF reads the catalog's /OutputIntents)
  * the file is really a PDF and renders to non-blank pixels

With --require-pdfx it enforces the structural print claim: a PDF/X
identifier, at least one output intent, no encryption, and no non-embedded
font on the rendered page. With --expect-standard x1a|x3|x4 it also checks the
EXACT identifier from references/pdfx-requirements.md (X-1a:2001 is
`PDF/X-1:2001` + `GTS_PDFXConformance(PDF/X-1a:2001)`; X-4 is `PDF/X-4` in
XMP or Info). A prefix like `startswith("PDF/X")` cannot tell the levels apart
and is no longer used.

PyMuPDF is an OPTIONAL dependency. Without it the script exits 3 (declared
missing capability), never 0: an unmeasured preflight is not a pass.
"""
import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L
import lib_pdf as LP

SCRIPT = "pdf_preflight.py"
SCHEMA = "vector-ai-studio/pdf-preflight-v1"
MAX_PIXELS = 4000000

try:                                    # PyMuPDF >= 1.24 exposes the real name
    import pymupdf as fitz
except ImportError:                     # older releases only ship fitz
    try:
        import fitz
    except ImportError:
        fitz = None


def _ink(pix):
    """Count pixels that are not white, over RGB (alpha ignored)."""
    data = pix.samples
    n = pix.n
    count = 0
    for i in range(0, len(data), n):
        if data[i:i + 3] != b"\xff\xff\xff":
            count += 1
    return count


def _intents(doc):
    try:
        cat = doc.pdf_catalog()
        kind, value = doc.xref_get_key(cat, "OutputIntents")
    except Exception:                   # noqa: BLE001
        return 0, "unreadable"
    if kind == "null" or not value or value == "null":
        return 0, "none"
    return len(re.findall(r"\d+\s+\d+\s+R", value)), value


def _pdf_text(blob):
    """Undo PDF literal-string escapes before reporting."""
    return (blob.replace(b"\\\\", b"\\")
                .replace(b"\\(", b"(").replace(b"\\)", b")")).decode("latin1")


def _identification(raw, xmp):
    """Read the exact PDF/X identifiers from Info and XMP.

    X-1a:2001 is `GTS_PDFXVersion(PDF/X-1:2001)` plus
    `GTS_PDFXConformance(PDF/X-1a:2001)`; X-4 is XMP
    `pdfxid:GTS_PDFXVersion = PDF/X-4`. A prefix search cannot tell them apart.
    """
    version = conformance = None
    m = re.search(rb"/GTS_PDFXVersion\s*\(((?:\\.|[^\\()])*)\)", raw)
    if m:
        version = _pdf_text(m.group(1))
    m = re.search(rb"/GTS_PDFXConformance\s*\(((?:\\.|[^\\()])*)\)", raw)
    if m:
        conformance = _pdf_text(m.group(1))
    xmp_version = None
    m = re.search(r"<pdfxid:GTS_PDFXVersion>([^<]*)</pdfxid:GTS_PDFXVersion>", xmp)
    if m:
        xmp_version = m.group(1).strip()
    source = "info+xmp" if (version and xmp_version) else \
             ("info" if version else ("xmp" if xmp_version else None))
    return {"info_version": version, "info_conformance": conformance,
            "xmp_version": xmp_version, "present": bool(version or xmp_version),
            "source": source}


def preflight(path, require_pdfx=False, expect=""):
    pdf = Path(path)
    problems = []
    warnings = []

    def add(code, msg):
        problems.append((code, msg))

    raw = pdf.read_bytes()
    probe = LP.ai_h1_probe(str(pdf))
    obs = LP._observe_ai(str(pdf))

    doc = fitz.open(str(pdf))
    try:
        if not doc.is_pdf:
            add("E-PDF-PREFLIGHT", "PyMuPDF did not open the file as a PDF")
        pages = doc.page_count
        encrypted = bool(doc.needs_pass)
        page = doc[0] if pages else None
        rect = [round(page.rect.width, 2), round(page.rect.height, 2)] if page else None
        dpi = 72
        ink = 0
        if page is not None:
            pix = page.get_pixmap(dpi=dpi)
            if pix.width * pix.height > MAX_PIXELS:
                dpi = 36
                pix = page.get_pixmap(dpi=dpi)
            ink = _ink(pix)
        fonts = page.get_fonts(full=True) if page is not None else []
        not_embedded = [f for f in fonts
                        if isinstance(f, (list, tuple)) and len(f) > 1
                        and str(f[1]) in ("n/a", "")]
        images = len(page.get_images(full=True)) if page is not None else 0
        intents, intents_raw = _intents(doc)
        try:
            xmp = doc.get_xml_metadata() or ""
        except Exception:               # noqa: BLE001
            xmp = ""
    finally:
        doc.close()

    independent = {
        "tool": "pymupdf",
        "version": getattr(fitz, "VersionBind", None) or
                   (getattr(fitz, "__version__", None) if fitz else None),
        "pages": pages,
        "encrypted": encrypted,
        "page_size_pt": rect,
        "render_dpi": dpi,
        "render_ink": ink,
        "fonts": len(fonts),
        "fonts_not_embedded": len(not_embedded),
        "images": images,
        "output_intents": intents,
        "output_intents_raw": intents_raw,
        "gts_pdfx_in_xmp": "GTS_PDFXVersion" in xmp,
    }
    ident = _identification(raw, xmp)
    ident["expected"] = expect or None
    ident["matches"] = (LP.pdfx_id_ok(expect, ident["info_version"],
                                      ident["info_conformance"],
                                      ident["xmp_version"]) if expect else None)
    internal = {
        "pdf_ok": bool(probe.get("pdf_ok")),
        "pages": probe.get("page_count"),
        "images": probe.get("image_count"),
        "fonts": obs.get("fonts"),
        "output_intent": obs.get("output_intent"),
        "gts_pdfx": raw.count(b"/GTS_PDFXVersion"),
        "artboard_pt": obs.get("artboard_pt"),
    }
    checks = {
        "pdf_ok": internal["pdf_ok"],
        "pages": internal["pages"] == pages,
        "images": internal["images"] == images,
        "fonts_presence": (internal["fonts"] == 0) == (len(fonts) == 0),
        "output_intent_presence": (internal["output_intent"] > 0) == (intents > 0),
    }
    agree = all(checks.values())
    if not agree:
        for name, ok in checks.items():
            if not ok:
                add("E-PDF-CROSS",
                    "PyMuPDF and lib_pdf disagree on %s (independent=%r, internal=%r)"
                    % (name, independent.get(name), internal.get(name)))

    require = require_pdfx or bool(expect)
    if require:
        if encrypted:
            add("E-PDFX-MISSING", "encrypted PDF cannot be a PDF/X master")
        if not ident["present"]:
            add("E-PDFX-MISSING", "no PDF/X identifier in Info or XMP")
        if expect and not ident["matches"]:
            add("E-PDFX-ID",
                "expected %s; got version=%r conformance=%r xmp=%r"
                % (expect, ident["info_version"], ident["info_conformance"],
                   ident["xmp_version"]))
        if intents < 1:
            add("E-PDFX-MISSING", "PyMuPDF sees no /OutputIntents in the catalog")
        if not_embedded:
            add("E-PDFX-MISSING",
                "font(s) not embedded: " + ", ".join(str(f[3]) for f in not_embedded[:5]))
    if ink == 0:
        warnings.append("independent render is blank (0 non-white pixels); "
                        "a legitimate all-white design would do this, so it is "
                        "reported, not failed")

    return {
        "schema": SCHEMA,
        "pdf": pdf.name,
        "bytes": len(raw),
        "require_pdfx": bool(require),
        "ok": not problems,
        "identification": ident,
        "independent": independent,
        "internal": internal,
        "cross_check": {"agree": agree, "checks": checks},
        "problems": [{"code": c, "message": m} for c, m in problems],
        "warnings": warnings,
    }


def main():
    ap = argparse.ArgumentParser(description="independent PDF/X preflight")
    ap.add_argument("--pdf", required=True, help="print.pdf or a PDF-compatible .ai")
    ap.add_argument("--require-pdfx", action="store_true",
                    help="enforce the PDF/X claim (identifier + output intent + embedded fonts)")
    ap.add_argument("--expect-standard", default="",
                    choices=("", "x1a", "x3", "x4"),
                    help="require the exact identifier for this level; implies --require-pdfx")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    pdf = Path(args.pdf)
    if not pdf.is_file():
        raise L.ToolError("E-USAGE", "PDF not found: " + str(pdf), L.EXIT_USAGE)
    if fitz is None:
        return L.emit(SCRIPT, status="degraded", code=L.EXIT_MISSING,
                      data={"ok": False, "capability": "pymupdf",
                            "note": "install pymupdf to run the independent preflight"},
                      errors=[L.err("E-CAPABILITY-MISSING",
                                    "PyMuPDF is not installed; independent "
                                    "preflight is a declared skip, never a pass")])
    try:
        doc = preflight(str(pdf), require_pdfx=args.require_pdfx,
                        expect=args.expect_standard)
    except L.ToolError:
        raise
    except Exception as e:              # noqa: BLE001
        return L.emit(SCRIPT, status="fail", code=L.EXIT_FAIL,
                      data={"ok": False, "pdf": pdf.name},
                      errors=[L.err("E-PDF-PREFLIGHT",
                                    type(e).__name__ + ": " + str(e)[:160])])
    return L.emit(SCRIPT, status="ok" if doc["ok"] else "fail",
                  code=L.EXIT_OK if doc["ok"] else L.EXIT_FAIL,
                  data=doc,
                  errors=[L.err(p["code"], p["message"]) for p in doc["problems"]],
                  warnings=doc["warnings"])


if __name__ == "__main__":
    sys.exit(main())

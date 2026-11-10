#!/usr/bin/env python3
"""Shared library: read a PDF-compatible .ai from its own bytes, and build the
manifest/landing documents every engine writes.

There is no Adobe dependency here. A .ai written by VectorCraft is a PDF
container; everything below is measurement of that container: object streams,
content-stream colour operators, embedded fonts, images, optional-content
groups. Nothing is inferred from what an engine was asked to do.

Why this lives outside the landers: land_vc.py, land_craft.py and selftest.py
must report the same physical file the same way. One reader and one landing
shape. land_vc builds its manifest here; land_craft writes its own manifest
inline (different save_options and PDF/X fields), so the shared part is the
measurement and the landing document, not the manifest constructor.
"""

import hashlib
import re
import subprocess
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L

SCRIPTS = Path(__file__).resolve().parent


def _pdf_objects(raw, warnings=None):
    """object number -> body. A segment ends at the next object header, never
    at an `endobj` string that may occur inside compressed data.

    Objects packed into an /ObjStm are unpacked too. Without that, a PDF whose
    page dictionary was object-streamed shows no /Type /Page at all, so the file
    reports "0 objects" and the colour measurement silently degrades to the
    declared dictionary -- a miss that looks like a clean run. A direct object
    wins over the packed copy, since an incremental update may redefine one.
    """
    starts = [(int(m.group(1)), m.start(), m.end())
              for m in re.finditer(rb"(?m)^(\d+)\s+0\s+obj\b", raw)]
    out = {}
    for i, (num, _start, body_start) in enumerate(starts):
        end = starts[i + 1][1] if i + 1 < len(starts) else len(raw)
        seg = raw[body_start:end]
        cut = seg.rfind(b"endobj")
        out[num] = seg[:cut] if cut >= 0 else seg
    for num, body in _objstm_objects(out, warnings):
        out.setdefault(num, body)
    return out


def _objstm_objects(objs, warnings=None):
    """(number, body) pairs unpacked from every /Type /ObjStm stream.

    Layout: /N pairs of `num offset` in the header, then the bodies start at
    /First; each body ends where the next one begins.

    A stream whose /N disagrees with the packed pairs is read at its packed
    count, not at /N. Trusting a too-small /N silently dropped the page
    dictionary, so the file reported one selected stream and zero decoded --
    a clean-looking "unmeasured" instead of the real content.
    """
    pairs = []
    for body in list(objs.values()):
        head = body.split(b"stream", 1)[0]
        if not re.search(rb"/Type\s*/ObjStm\b", head):
            continue
        n = re.search(rb"/N\s+(\d+)", head)
        first = re.search(rb"/First\s+(\d+)", head)
        data = _inflate(_stream_payload(body))
        if not n or not first or data is None:
            continue
        first_off = int(first.group(1))
        nums = [int(x) for x in re.findall(rb"\d+", data[:first_off])]
        table = list(zip(nums[0::2], nums[1::2]))
        declared = int(n.group(1))
        if len(table) != declared and warnings is not None:
            warnings.append("object stream declares /N %d but packs %d pairs; "
                            "the packed count was used" % (declared, len(table)))
        for idx, (onum, off) in enumerate(table):
            start = first_off + off
            end = (first_off + table[idx + 1][1]) if idx + 1 < len(table) else len(data)
            pairs.append((onum, data[start:end]))
    return pairs


def _stream_payload(body):
    m = re.search(rb"stream\r?\n", body)
    if not m:
        return None
    end = body.rfind(b"endstream")
    return body[m.end():end] if end >= m.end() else None


def _inflate(blob):
    """zlib.decompress() raises "incomplete" when the segment carries a
    trailing CR/LF before endstream, which real PDF writers emit.
    decompressobj tolerates that. Using the strict call made every real
    deliverable report "undecodable", so the measurement never happened."""
    if blob is None:
        return None
    for attempt in (blob, blob.rstrip(b"\r\n")):
        try:
            return zlib.decompressobj().decompress(attempt)
        except Exception:
            continue
    return None


def _decode_stream(body):
    """Stream body -> decoded bytes, or None when this reader cannot decode it.

    A PDF may store a content stream uncompressed. Assuming every stream is
    Flate made a perfectly legal page report "0 of 1 decoded" and the colour
    measurement never ran. Filters this reader does not implement (DCT, LZW,
    ASCII85, ...) stay None: reported as undecoded, never guessed.
    """
    blob = _stream_payload(body)
    if blob is None:
        return None
    head = body.split(b"stream", 1)[0]
    m = re.search(rb"/Filter\s*(\[[^\]]*\]|/\w+)", head)
    if not m:
        return blob
    names = re.findall(rb"/(\w+)", m.group(1))
    if any(n in (b"FlateDecode", b"Fl") for n in names):
        return _inflate(blob)
    return None


_INLINE_COMPONENTS = {b"G": 1, b"DeviceGray": 1, b"RGB": 3, b"DeviceRGB": 3,
                      b"CMYK": 4, b"DeviceCMYK": 4}


def _inline_length(head):
    """Candidate sample byte counts for an inline-image dictionary, or None.

    A whitespace-delimited `EI` is not a delimiter: the sample itself may
    contain those two bytes, so scanning for the first one ends the image too
    early and returns the rest of the sample to the operator count. With a
    declared size the end is computed instead of guessed.

    Sub-byte depths (1/2/4 bpc) have two readings in the wild: one bit stream
    for the whole image, or each row padded to a byte boundary. Both are
    returned so the caller can take the one that lands on a real `EI`; picking
    only the first truncates every row-aligned 1-bit mask.

    Filters and indexed colour spaces resize the payload in ways this reader
    cannot derive, so they stay None and the stream is reported as unmeasured
    rather than guessed.
    """
    m = re.search(rb"/L\s+(\d+)", head)
    if m:
        return [int(m.group(1))]
    if re.search(rb"/F\b", head) or re.search(rb"/DP\b", head):
        return None
    w = re.search(rb"/W\s+(\d+)", head)
    h = re.search(rb"/H\s+(\d+)", head)
    cs = re.search(rb"/CS\s*/(\w+)", head)
    if not (w and h and cs):
        return None
    comp = _INLINE_COMPONENTS.get(cs.group(1))
    if comp is None:
        return None
    bpc = re.search(rb"/BPC\s+(\d+)", head)
    depth = int(bpc.group(1)) if bpc else 8
    width = int(w.group(1))
    height = int(h.group(1))
    stream = (width * height * comp * depth + 7) // 8
    row = ((width * comp * depth + 7) // 8) * height
    return [stream] if stream == row else [stream, row]


def _strip_plain(data):
    """Blank out literal and hex strings, which never hold an operator."""
    data = re.sub(rb"\((?:\\.|[^\\()])*\)", b"()", data)
    return re.sub(rb"<[0-9A-Fa-f\s]*>", b"<>", data)


def _strip_strings(data):
    """Blank out data that is not an operator. None means "not measurable".

    Inline images are cut on the byte offsets declared in the BI dictionary,
    before any string blanking, because replacing a `(...)` inside a sample
    shifts every offset after it. A dict this reader cannot size makes the
    whole stream unmeasured: counting part of its sample is how a bitmap got
    reported as colour operators, and counting none of it hides operators that
    follow.
    """
    # `/BI` is a name, not an inline image start; a dict key must follow.
    begin = re.compile(rb"(?<![\w./])BI(?=\s*/[A-Za-z])")
    data_id = re.compile(rb"(?<![\w.])ID\s")
    data_ei = re.compile(rb"(?<=[\s\x00])EI(?=[\s\x00]|$)")
    spans, pos = [], 0
    for m in begin.finditer(data):
        if m.start() < pos:
            continue
        idm = data_id.search(data, m.end())
        if not idm:
            break                          # truncated: keep the rest as text
        sizes = _inline_length(data[m.end():idm.start()])
        if sizes is None:
            return None
        hits = [data_ei.search(data, max(idm.end() + n - 1, idm.end())) for n in sizes]
        if not any(hits):
            return None                    # declared size overruns the stream
        if len(sizes) > 1 and all(hits):
            # Both the packed-bit-stream end and the row-padded end land on a
            # whitespace " EI ". Nothing in the file says which produced it, so
            # neither is taken.
            return None
        eim = hits[0] or hits[1]
        spans.append((m.start(), eim.end()))
        pos = eim.end()
    out, cur = [], 0
    for start, end in spans:
        out.append(_strip_plain(data[cur:start]))
        out.append(b" BI ID EI ")
        cur = end
    out.append(_strip_plain(data[cur:]))
    return b"".join(out)


def _page_streams(raw, warnings=None):
    """Only the streams that describe the page.

    Counting every decodable stream inflates the count: ICC profiles, font
    programs and CMaps are binary blobs that contain k/K bytes by accident,
    and an embedded thumbnail is a picture of the page, not the page. The
    scope is the /Contents of /Type /Page objects plus /Subtype /Form
    XObjects -- the same scope land_craft.py uses, so the two engines report
    comparable numbers.

    Returns (bodies, refs, found). `refs` counts the page's /Contents
    references, `found` how many of those objects exist in this file. They
    differ when a reference points at an object the reader never unpacked, and
    reporting only the reference count made that look like a stream that failed
    to decode.
    """
    objs = _pdf_objects(raw, warnings)
    wanted = set()
    for num, body in objs.items():
        head = body.split(b"stream", 1)[0]
        if re.search(rb"/Type\s*/Page\b", head):
            for m in re.finditer(rb"/Contents\s+(\d+)\s+0\s+R", head):
                wanted.add(int(m.group(1)))
            arr = re.search(rb"/Contents\s*\[([^\]]*)\]", head)
            if arr:
                for m in re.finditer(rb"(\d+)\s+0\s+R", arr.group(1)):
                    wanted.add(int(m.group(1)))
        if re.search(rb"/Subtype\s*/Form\b", head):
            wanted.add(num)
    bodies = [objs[n] for n in sorted(wanted) if n in objs]
    return bodies, len(wanted), len(bodies)


def _count_ops(streams):
    """Colour operators in already-selected stream bodies.

    A stream whose inline-image samples cannot be delimited is left out of
    `decoded` rather than counted on a guess, so the caller can tell
    "measured zero" from "could not measure".
    """
    ops = {k: 0 for k in ("k", "K", "rg", "RG", "g", "G", "sc", "SC", "scn", "SCN")}
    decoded = 0
    for body in streams:
        data = _decode_stream(body)
        if data is None:
            continue
        data = _strip_strings(data)
        if data is None:
            continue
        decoded += 1
        for op in ops:
            ops[op] += len(re.findall(rb"(?<![\w.])" + op.encode() + rb"(?![\w])", data))
    return (ops if decoded else None), decoded


def _font_programs(raw):
    """Embedded font *programs*, not font entries.

    Counting /BaseFont counted one Type0 font twice (the composite plus its
    descendant CIDFont) and disagreed with both vectorcraft's `info` and
    land_craft's fonts_embedded on the same file.
    """
    return (len(re.findall(rb"/FontFile(?![0-9])", raw))
            + raw.count(b"/FontFile2") + raw.count(b"/FontFile3"))


def _content_ops(raw, warnings=None):
    """Colour operators in the page's own content streams.

    A resource dictionary saying /DeviceRGB tells you what the PDF *declares*;
    the content stream tells you what it *draws*. Both are worth measuring and
    they can disagree. Streams that cannot be decoded are reported, not guessed.

    Returns (ops, stats) where stats records how many page streams were
    referenced, how many were found, and how many were decoded.
    """
    streams, refs, objects = _page_streams(raw, warnings)
    ops, decoded = _count_ops(streams)
    stats = {"objects": objects, "refs": refs, "decoded": decoded,
             "scope": "page /Contents refs + /Subtype /Form streams only"}
    return ops, stats


def _layer_count(text):
    """Number of optional-content groups, i.e. layers, read from the file."""
    oc = re.search(r"/OCGs\s*\[([^\]]*)\]", text)
    return len(re.findall(r"\d+\s+\d+\s+R", oc.group(1))) if oc else 0


_OBJECT_COUNTERS = (
    ("device_rgb", rb"/DeviceRGB"),
    ("device_cmyk", rb"/DeviceCMYK"),
    # /OutputIntents is the catalog array that points at the intent objects, so
    # counting it as an intent reports two for a file that declares one. It also
    # made the same PDF read differently here and in land_craft, which already
    # excluded the array.
    ("output_intent", rb"/OutputIntent(?![s])"),
    ("iccbased", rb"/ICCBased"),
)


def _object_counts(raw):
    """Declared colour resources, counted once and the same way for both landers.

    These numbers describe one file; two readers must not publish two values for
    the same byte sequence.
    """
    return {key: len(re.findall(pat, raw)) for key, pat in _OBJECT_COUNTERS}


def _color_mode(ops, device_cmyk, device_rgb, iccbased):
    """What the file actually paints, not what its resource dictionary declares.

    These two disagree in practice. A file converted to CMYK by vectorcraft
    carries k/K operators and no /DeviceCMYK entry at all -- an undeclared k
    operand means DeviceCMYK by definition -- so counting /DeviceCMYK marked a
    correct CMYK file as RGB. The content stream is the authority; the resource
    dictionary is a hint.

    When no stream can be decoded the answer is qualified, never silent.
    """
    if not ops:
        if device_cmyk:
            return "cmyk(declared-only)"
        if device_rgb:
            return "rgb(declared-only)"
        if iccbased:
            return "icc(declared-only)"
        return "unknown"
    k = ops["k"] + ops["K"]
    rgb = ops["rg"] + ops["RG"]
    if k and not rgb:
        return "cmyk(operators)"
    if rgb and not k:
        return "rgb(operators)"
    if k and rgb:
        return "mixed(operators)"
    return "unknown"


def _observe_ai(path, warnings=None):
    """Read back what the .ai actually is, from its own bytes.

    We do not trust what we asked the engine to do; we look at the file. This
    is the same principle as the H1 reopen assertion, applied to the container:
    colour space, ICC, fonts, images and the private segment are all measurable.
    """
    raw = Path(path).read_bytes()
    t = raw.decode("latin-1")
    counts = _object_counts(raw)
    mb = re.search(r"/MediaBox\s*\[\s*([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)", t)
    w = h = None
    if mb:
        x0, y0, x1, y1 = (float(v) for v in mb.groups())
        w, h = round(x1 - x0, 2), round(y1 - y0, 2)
    ops, op_stats = _content_ops(raw, warnings)
    return {
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "pdf_compatible": t.startswith("%PDF"),
        "pdf_header": t[:8].strip() if t.startswith("%PDF") else "",
        "has_ai_private": "AIPrivateData" in t,
        "device_rgb": counts["device_rgb"],
        "device_cmyk": counts["device_cmyk"],
        "output_intent": counts["output_intent"],
        "iccbased": counts["iccbased"],
        # FontFile counts embedded font *programs*. Counting /BaseFont counted
        # one Type0 font twice (the type plus its descendant CIDFont) and
        # disagreed with both vectorcraft's `info` and land_craft.
        "fonts": _font_programs(raw),
        "images": len(re.findall(r"/Subtype\s*/Image", t)),
        "artboard_pt": [w, h],
        "artboard_mm": [round(w * 25.4 / 72.0, 1), round(h * 25.4 / 72.0, 1)] if w else None,
        # Layer count from the optional-content groups, i.e. from the file.
        # Passing 0 here once made the manifest claim "only 1 layer" for a
        # document that had five named ones.
        "layers": _layer_count(t),
        "content_ops": ops,
        "content_streams": op_stats,
        # Derived verdict: operators first, declarations only as fallback.
        "color_mode": _color_mode(ops, counts["device_cmyk"],
                                  counts["device_rgb"], counts["iccbased"]),
    }


def _deliverable(path, opts, obs, kv, engine="", engine_version="",
                 export_steps=None, source_probe=None, tool_warnings=None):
    """The document a client can check instead of arguing.

    Everything here is a measured value or an explicit warning; nothing is a
    promise. If a claim cannot be backed by a measurement it is listed under
    "unverified" rather than quietly omitted.
    """
    warns, unverified = [], []
    if not obs["has_ai_private"]:
        warns.append("缺少 AIPrivateData：这是纯 PDF，不可在 Illustrator 中编辑")
    if not opts["pdf"]:
        warns.append("已关闭 PDF 兼容：非 Illustrator 软件无法置入本文件")

    ops = obs.get("content_ops") or {}
    st = obs.get("content_streams") or {}
    mode = str(obs.get("color_mode") or "unknown")
    measured = ("色彩模式判定 " + mode + "；/DeviceCMYK " + str(obs["device_cmyk"]) +
                " 处、/DeviceRGB " + str(obs["device_rgb"]) + " 处")
    if ops:
        measured += ("；页面内容流 " + str(st.get("decoded", 0)) + "/" +
                     str(st.get("objects", 0)) + " 段中，填充 rg=" + str(ops["rg"]) +
                     "、描边 RG=" + str(ops["RG"]) +
                     "、填充 k=" + str(ops["k"]) + "、描边 K=" + str(ops["K"]))

    if opts["cmyk"] and mode.startswith("cmyk"):
        # Real conversion, evidenced by the operators that painted the page.
        warns.append("CMYK 已生效，但文件内未声明输出意图（无 /OutputIntent、无 /ICCBased）："
                     "用的是哪个色彩配置无法从文件得知，印刷前需与印厂确认")
    elif opts["cmyk"]:
        unverified.append(
            "已请求 CMYK，但产物未以 CMYK 着色（" + measured +
            "）→ 印刷交付请改用 PDF/X 或由印厂转换")
    if opts["icc"] and obs["iccbased"] == 0 and obs["output_intent"] == 0:
        unverified.append("要求嵌入 ICC，但文件中未见 ICCBased / OutputIntent")
    if not opts["cmyk"]:
        warns.append("RGB 交付：印刷前必须由印厂或设计师转 CMYK，荧光色会有可见变化")
    if not opts["icc"]:
        warns.append("未嵌入 ICC：色彩意图未声明，跨设备一致性弱")
    if opts["bleed_mm"] and obs["artboard_pt"]:
        warns.append("画板已含 " + str(opts["bleed_mm"]) + "mm 出血；裁切线为矢量标记层，非印刷标准色标")
        ink = str(kv.get("trimmarks_ink") or "")
        flat = ink.replace(" ", "")
        # The readback string ends with ":n=8", so endswith() never matched and
        # this guard was dead code. Substring test is what is actually meant.
        invisible = ("gray=0" in flat or "rgb=255,255,255" in flat)
        if invisible:
            warns.append("裁切线描边为白色，在白底上不可见：" + ink)
        elif not ink:
            warns.append("未能读回裁切线描边色")
    if opts["bleed_mm"] == 0:
        warns.append("无出血：裁切偏移会露白边，印刷前需加出血")
    try:
        layer_count = int(kv.get("layers") or 0)
    except (TypeError, ValueError):
        layer_count = 0
    if layer_count <= 1:
        warns.append("仅 1 个图层：客户改稿需自行拆组")

    warns.append("第三方工具（Acrobat 等）重新保存会剥离 AIPrivateData，导致不可编辑")

    return {
        "schema": "vector-ai-studio/deliverable-v1",
        "file": Path(path).name,
        # Which engine produced this decides everything downstream: whether
        # text stayed live, whether CMYK was possible, whether layers exist.
        "engine": engine,
        "engine_version": engine_version,
        "profile": opts.get("profile", ""),
        "save_options": {
            "compatibility": opts.get("compat"),
            "pdf_compatible": bool(opts.get("pdf")),
            "embed_icc": bool(opts.get("icc")),
            "cmyk": bool(opts.get("cmyk")),
            "bleed_mm": opts.get("bleed_mm"),
            "bleed_pt": opts.get("bleed_pt"),
            "layers_from_groups": bool(opts.get("layers")),
        },
        "observed": obs,
        "structure": {k: kv.get(k) for k in
                      ("open_paths", "reopen_paths", "layers", "reopen_layers",
                       "raster", "placed", "artboard", "viewBox",
                       "trimmarks_ink")},
        "engine_notes": (kv.get("notes") or "").split(",") if kv.get("notes") else [],
        # Engine-specific payloads, present on both engines so the manifest has
        # one shape. Empty means "this engine has none", not "forgot to write".
        "export_steps": export_steps if export_steps is not None else [],
        "source_probe": source_probe,
        "tool_warnings": tool_warnings if tool_warnings is not None else [],
        # Only an engine that draws crop marks has a value here. An empty
        # string would claim "marks exist and are blank", which is a different
        # and worse statement than "this engine draws none".
        "trimmarks_ink": (kv.get("trimmarks_ink") or None),
        "warnings": warns,
        "unverified": unverified,
        "verify_yourself": [
            "head -c 8 " + Path(path).name + "   # 期望 %PDF-",
            "grep -c AIPrivateData " + Path(path).name + "   # 期望 >=1，0 表示不可编辑",
            "sha256: " + obs["sha256"],
        ],
    }


def _landing(automation, status, note, ai_produced, h1_pass, extra=None,
             substitute=None):
    doc = {
        "schema": "vector-ai-studio/landing-v1",
        "automation": automation,
        "status": status,
        "degradation": ({"level": "L4", "reason": note,
                         "substitute": (substitute or
                                        "install vectorcraft-cli (or set VECTORCRAFT_CLI) and rerun")}
                        if status == "degraded" else None),
        "note": note,
        "ai_produced": bool(ai_produced),
        "h1_pass": bool(h1_pass),
    }
    if extra:
        doc.update(extra)
    return doc


def _drop_stale_readback(run_dir):
    """Drop the previous run's structure readback; return True when gone.

    aggregate_log reads structure_readback.json and would grade a stale pass
    for a run that just failed. A locked file cannot be unlinked, so in that
    case it is overwritten with an explicit failure: leaving the old pass in
    place is the one outcome that must not happen. The caller records the
    boolean in landing.json, because a silently failed cleanup used to look
    exactly like a successful one.
    """
    stale = run_dir / "structure_readback.json"
    try:
        if stale.is_file():
            stale.unlink()
        return True
    except OSError:
        try:
            L.write_json_atomic(stale, {
                "schema": "vector-ai-studio/structure-readback-v1",
                "ok": False, "stale": True,
                "source": "stale-readback-cleanup-failed",
            })
        except OSError:
            pass
        return False


def _drop_stale_preview(run_dir):
    """Delete the run's preview picture and its meta; True when both are gone.

    H2 only asks whether preview.png parses, so a picture left by an earlier
    design passes H2 for a run that rendered nothing. Both landers call this on
    every failure exit, and _ensure_preview calls it before rendering.
    """
    for name in ("preview.png", "preview.meta.json"):
        p = run_dir / name
        try:
            if p.is_file():
                p.unlink()
        except OSError:
            return False
    return True


def _drop_outputs(run_dir, names):
    """Delete this run's target artifacts before the engine is invoked.

    Existence checks are the only evidence that the engine wrote something this
    run. Leaving an older final.ai / preview.png in place makes "the file is
    there" true for a run that produced nothing -- and a CLI that exits 0
    without writing (a silent failure, not a crash) then passes every check.
    Clearing first makes "it exists" mean "this run wrote it". Returns True when
    every named file is gone.
    """
    ok = True
    for name in names:
        p = run_dir / name
        try:
            if p.is_file():
                p.unlink()
        except OSError:
            ok = False
    return ok


def _ensure_preview(run_dir, svg):
    """Render preview.png with render_png.py and never reuse an older one.

    Reusing an existing valid PNG meant a re-run whose own preview export failed
    shipped the previous design's picture (H2 only checks that the PNG parses),
    and the stale preview.meta.json from that older run survived beside it.
    Deleting first is what makes that impossible: a failed render now leaves no
    preview at all, which H2 reports as incomplete instead of grading the last
    design. If the old file cannot be removed, the run fails rather than letting
    an unlinkable stale picture stand in for this run's.
    """
    png = run_dir / "preview.png"
    if not _drop_stale_preview(run_dir):
        return False
    try:
        r = subprocess.run([sys.executable, str(SCRIPTS / "render_png.py"),
                            "--in", str(svg), "--out", str(png)],
                           timeout=60, capture_output=True, text=True, check=False)
        return r.returncode == 0 and png.is_file()
    except (OSError, subprocess.SubprocessError):
        return False

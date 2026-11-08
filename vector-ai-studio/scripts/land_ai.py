#!/usr/bin/env python3
"""C4 / H1 / H2: orchestrate JSX + COM, read back structure, land four files.

Semi-manual COM fallback: when detect_deps confirms COM is not drivable, the user may EXPLICITLY
choose --semi-manual. That produces a ready-to-run manual-bridge.jsx plus a
clear "one manual script execution in Illustrator is required" note. Logs are
forced to automation=semi-manual, degradation.level=L4, status=degraded. It is
never triggered silently and never claims full automation.
"""

import argparse
import hashlib
import re
import shutil
import subprocess
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L

SCRIPT = "land_ai.py"
SCRIPTS = Path(__file__).resolve().parent

MANUAL_STEPS = """# 半自动落地步骤

仅当依赖探测真机确认 Illustrator COM 不可驱动时，由用户显式选择本路径。

## 前置

- run 目录内已有净化 final.svg 与 preview.png。
- 本机已安装 Adobe Illustrator，且可手动执行 ExtendScript。

## 步骤

1. 在 Illustrator 中依次打开文件、脚本、其他脚本，选择本目录下的 manual-bridge.jsx。
2. 脚本自动打开净化 SVG、做结构断言、用 saveAs() 保存原生 final.ai、重新打开并回读对象计数，结果写入 manual-result.txt。
3. 回到技能执行收集命令：python scripts/land_ai.py --collect --out 该 run 目录。

## 说明

- 本路径需人工在 Illustrator 内执行一次脚本，不是全自动，也不是装好即用无人工。
- 日志强制标记 automation 为 semi-manual、degradation.level 为 L4、status 为 degraded。
- 原生 .ai 只能由 Illustrator 自身 saveAs() 写入，本路径不改名、不伪造。
- 若 Illustrator 报告 SVG_NOT_FOUND，请把本目录复制到不含中文与空格的纯 ASCII 路径后重试（ExtendScript 对非 ASCII 路径支持有限）。
"""


def _com_status(deps_path):
    if not deps_path:
        return "UNVERIFIED"
    d = None
    try:
        import json
        d = json.loads(Path(deps_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "UNVERIFIED"
    # detect_deps.py emits its payload under "data"; an older flattened form
    # puts it at the top level. Accept both, otherwise a perfectly good probe
    # result reads as UNVERIFIED and the whole chain degrades for no reason.
    deps = d.get("dependencies")
    if deps is None:
        deps = (d.get("data") or {}).get("dependencies")
    for item in (deps or []):
        if item.get("name") == "illustrator_com":
            return str(item.get("status", "UNVERIFIED"))
    return "UNVERIFIED"


def _jsx_path(p):
    """ExtendScript File() needs forward slashes; backslashes become JS escapes."""
    return str(Path(p)).replace("\\", "/")


# Delivery profiles. These are defaults only: every value can be overridden on
# the command line, because "what the client needs" is not something a program
# should guess silently.
PROFILE_DEFAULTS = {
    "screen":  {"compat": "latest", "pdf": 1, "icc": 0, "cmyk": 0,
                "bleed_mm": 0.0, "layers": 0},
    "print":   {"compat": "latest", "pdf": 1, "icc": 1, "cmyk": 1,
                "bleed_mm": 3.0, "layers": 0},
    "handoff":  {"compat": "latest", "pdf": 1, "icc": 0, "cmyk": 0,
                 "bleed_mm": 0.0, "layers": 1},
}


def _resolve_options(args):
    """Merge profile defaults with explicit overrides. Explicit wins."""
    base = dict(PROFILE_DEFAULTS[args.profile])
    if args.compat:
        base["compat"] = args.compat
    for key, flag in (("pdf", args.pdf_compatible), ("icc", args.embed_icc),
                      ("cmyk", args.cmyk), ("layers", args.layers_from_groups)):
        if flag != "":
            base[key] = int(flag)
    if args.bleed_mm != "":
        base["bleed_mm"] = float(args.bleed_mm)
    base["bleed_pt"] = round(base["bleed_mm"] * 72.0 / 25.4, 3)
    return base


def _make_jsx(svg, out_ai, outline, result_path, opts=None):
    opts = opts or PROFILE_DEFAULTS["screen"]
    tpl = (SCRIPTS / "ai_bridge.jsx").read_text(encoding="utf-8")
    return (tpl.replace("__SVG_PATH__", _jsx_path(svg))
               .replace("__OUT_PATH__", _jsx_path(out_ai))
               .replace("__OUTLINE_TEXT__", "1" if outline else "0")
               .replace("__COMPAT__", str(opts.get("compat", "latest")))
               .replace("__PDF_COMPATIBLE__", str(int(opts.get("pdf", 1))))
               .replace("__EMBED_ICC__", str(int(opts.get("icc", 0))))
               .replace("__CMYK__", str(int(opts.get("cmyk", 0))))
               .replace("__BLEED_PT__", str(opts.get("bleed_pt", 0)))
               .replace("__LAYERS_FROM_GROUPS__", str(int(opts.get("layers", 0))))
               .replace("__RESULT_PATH__", _jsx_path(result_path)))


def _parse_ok(line):
    if not line.startswith("OK|"):
        return None
    kv = {}
    for seg in line.split("|")[1:]:
        if "=" in seg:
            k, v = seg.split("=", 1)
            kv[k] = v
    return kv


def _structure_doc(kv, sig, automation):
    art = kv.get("artboard", "")
    m = re.fullmatch(r"(\d+)x(\d+)", art)
    w = int(m.group(1)) if m else None
    h = int(m.group(2)) if m else None
    vb = sig.get("viewBox")
    dims_ok = (w is not None and h is not None and vb is not None and
               abs(w - vb[2]) <= 1 and abs(h - vb[3]) <= 1)
    raster = int(kv.get("raster", -1))
    placed = int(kv.get("placed", -1))
    paths = int(kv.get("paths", kv.get("open_paths", -1)))
    reopen = int(kv.get("reopen_paths", -1))
    ok = raster == 0 and placed == 0 and paths > 0 and reopen > 0 and dims_ok
    return {
        "schema": "vector-ai-studio/structure-readback-v1",
        "ok": ok,
        "raster": raster, "placed": placed,
        "paths": paths, "reopen_paths": reopen,
        "layers": int(kv.get("layers", 0)),
        "artboard": [w, h], "viewBox": vb,
        "artboard_matches_viewBox": bool(dims_ok),
        "automation": automation,
        # The JSX reports what each optional step actually did. Persisting this
        # matters: a request like "convert to CMYK" can fail silently, and
        # without the note there is no way to tell "did not ask" from "asked and
        # it did not take".
        "jsx_notes": (kv.get("notes") or "").split(",") if kv.get("notes") else [],
        "trimmarks_ink": kv.get("trimmarks_ink") or "",
        "source": "illustrator-saveAs-reopen",
    }


def _content_ops(raw):
    """Count colour operators in the decodable PDF content streams.

    A resource dictionary saying /DeviceRGB tells you what the PDF *declares*;
    the content stream tells you what it *draws*. Both are worth measuring and
    they can disagree. Some streams are Illustrator's own compressed blocks and
    cannot be decoded here -- that is reported, not guessed at.

    Returns (ops, stats) where stats records how many streams were seen and how
    many decoded.
    """
    ops = {"rg": 0, "RG": 0, "k": 0, "K": 0}
    seen = decoded = 0
    for m in re.finditer(rb"stream\r?\n", raw):
        start = m.end()
        end = raw.find(b"endstream", start)
        if end < 0:
            continue
        seen += 1
        # zlib.decompress() raises "incomplete" when the segment carries a
        # trailing CR/LF before endstream, which Illustrator's streams do.
        # decompressobj tolerates that. Using the strict call made this
        # function report "undecodable" on every real deliverable, so the
        # measurement it was supposed to provide never happened.
        txt = None
        for attempt in (raw[start:end], raw[start:end].rstrip(b"\r\n")):
            try:
                txt = zlib.decompressobj().decompress(attempt).decode("latin-1", "replace")
                break
            except Exception:
                continue
        if txt is None:
            continue
        decoded += 1
        for op in ops:
            ops[op] += len(re.findall(r"(?:^|[\s\]])" + op + r"(?=[\s]|$)", txt))
    stats = {"streams": seen, "decoded": decoded}
    return (ops if decoded else None), stats


def _layer_count(text):
    """Number of optional-content groups, i.e. layers, read from the file."""
    oc = re.search(r"/OCGs\s*\[([^\]]*)\]", text)
    return len(re.findall(r"\d+\s+\d+\s+R", oc.group(1))) if oc else 0


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


def _observe_ai(path):
    """Read back what the .ai actually is, from its own bytes.

    We do not trust what we asked Illustrator to do; we look at the file. This
    is the same principle as the H1 reopen assertion, applied to the container:
    colour space, ICC, fonts, images and the private segment are all measurable.
    """
    raw = Path(path).read_bytes()
    t = raw.decode("latin-1")
    mb = re.search(r"/MediaBox\s*\[\s*([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)", t)
    w = h = None
    if mb:
        x0, y0, x1, y1 = (float(v) for v in mb.groups())
        w, h = round(x1 - x0, 2), round(y1 - y0, 2)
    ops, op_stats = _content_ops(raw)
    return {
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "pdf_compatible": t.startswith("%PDF"),
        "pdf_header": t[:8].strip() if t.startswith("%PDF") else "",
        "has_ai_private": "AIPrivateData" in t,
        "device_rgb": len(re.findall(r"/DeviceRGB", t)),
        "device_cmyk": len(re.findall(r"/DeviceCMYK", t)),
        "output_intent": len(re.findall(r"/OutputIntent", t)),
        "iccbased": len(re.findall(r"/ICCBased", t)),
        "fonts": len(re.findall(r"/BaseFont", t)),
        "images": len(re.findall(r"/Subtype\s*/Image", t)),
        "artboard_pt": [w, h],
        "artboard_mm": [round(w * 25.4 / 72.0, 1), round(h * 25.4 / 72.0, 1)] if w else None,
        # Layer count from the optional-content groups, i.e. from the file.
        # land_vc.py has no JSX to report it, and passing 0 made the manifest
        # claim "only 1 layer" for a document that had five named ones.
        "layers": _layer_count(t),
        "content_ops": ops,
        "content_streams": op_stats,
        # Derived verdict: operators first, declarations only as fallback.
        "color_mode": _color_mode(ops,
                                  len(re.findall(r"/DeviceCMYK", t)),
                                  len(re.findall(r"/DeviceRGB", t)),
                                  len(re.findall(r"/ICCBased", t))),
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
        measured += ("；可解码内容流 " + str(st.get("decoded", 0)) + "/" +
                     str(st.get("streams", 0)) + " 段中，填充 rg=" + str(ops["rg"]) +
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
    # kv values arrive from the JSX as strings; coerce before comparing.
    try:
        layer_count = int(kv.get("layers") or 0)
    except (TypeError, ValueError):
        layer_count = 0
    if layer_count <= 1:
        warns.append("仅 1 个图层：客户改稿需自行拆组（可用 --layers-from-groups 1）")

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
        "jsx_notes": (kv.get("notes") or "").split(",") if kv.get("notes") else [],
        # Promoted to top level as well: a reader checking "are the crop marks
        # actually black" should not have to dig through structure{}.
        # Engine-specific payloads, present on both engines so the manifest has
        # one shape. Empty means "this engine has none", not "forgot to write".
        "export_steps": export_steps if export_steps is not None else [],
        "source_probe": source_probe,
        "tool_warnings": tool_warnings if tool_warnings is not None else [],
        # Only the engine that draws crop marks has a value here. An empty
        # string would claim "marks exist and are blank", which is a different
        # and worse statement than "this engine draws none".
        "trimmarks_ink": ((kv.get("trimmarks_ink") or "") if engine == "illustrator" else None),
        "warnings": warns,
        "unverified": unverified,
        "verify_yourself": [
            "head -c 8 " + Path(path).name + "   # 期望 %PDF-",
            "grep -c AIPrivateData " + Path(path).name + "   # 期望 >=1，0 表示不可编辑",
            "sha256: " + obs["sha256"],
        ],
    }


def _landing(automation, status, note, ai_produced, h1_pass, extra=None):
    doc = {
        "schema": "vector-ai-studio/landing-v1",
        "automation": automation,
        "status": status,
        "degradation": ({"level": "L4", "reason": note,
                         "substitute": "semi-manual JSX or install/enable COM"}
                        if status == "degraded" else None),
        "note": note,
        "ai_produced": bool(ai_produced),
        "h1_pass": bool(h1_pass),
    }
    if extra:
        doc.update(extra)
    return doc


def _ensure_preview(run_dir, svg):
    png = run_dir / "preview.png"
    if png.is_file() and png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n":
        return True
    try:
        r = subprocess.run([sys.executable, str(SCRIPTS / "render_png.py"),
                            "--in", str(svg), "--out", str(png)],
                           timeout=60, capture_output=True, text=True, check=False)
        return r.returncode == 0 and png.is_file()
    except (OSError, subprocess.SubprocessError):
        return False


def main():
    ap = argparse.ArgumentParser(description="land native .ai + four artifacts")
    ap.add_argument("--svg", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--deps", default="")
    ap.add_argument("--outline-text", default="0")
    ap.add_argument("--profile", default="screen",
                    choices=["screen", "print", "handoff"],
                    help="delivery profile; sets save-option defaults")
    ap.add_argument("--compat", default="", help="Compatibility constant name, or 'latest'")
    ap.add_argument("--pdf-compatible", default="", choices=["", "0", "1"],
                    help="PDF-compatible container (default: 1)")
    ap.add_argument("--embed-icc", default="", choices=["", "0", "1"])
    ap.add_argument("--cmyk", default="", choices=["", "0", "1"])
    ap.add_argument("--bleed-mm", default="", help="bleed in mm (print profile default 3)")
    ap.add_argument("--layers-from-groups", default="", choices=["", "0", "1"])
    ap.add_argument("--collect", action="store_true")
    ap.add_argument("--semi-manual", action="store_true")
    ap.add_argument("--run-id", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    save_opts = _resolve_options(args)
    save_opts["profile"] = args.profile

    run_dir = Path(args.out)
    run_dir.mkdir(parents=True, exist_ok=True)
    th = L.load_thresholds()
    msg = th["messages"]
    com = _com_status(args.deps)

    svg = Path(args.svg) if args.svg else (run_dir / "final.svg")
    if not svg.is_file():
        raise L.ToolError("E-H1-STRUCTURE", "source SVG not found: " + svg.name, L.EXIT_USAGE)
    text = svg.read_text(encoding="utf-8")
    sig = L.analyze_svg(text)
    final_svg = run_dir / "final.svg"
    if svg.resolve() != final_svg.resolve():
        shutil.copyfile(svg, final_svg)

    # ---- explicit semi-manual collect (after the human ran the JSX) ----
    if args.collect:
        result_file = run_dir / "manual-result.txt"
        if not result_file.is_file():
            L.write_json_atomic(run_dir / "landing.json",
                                _landing("semi-manual", "degraded",
                                         msg["semi_manual_note"], False, False,
                                         {"manual_jsx": "manual-bridge.jsx"}))
            return L.emit(SCRIPT, status="degraded", code=L.EXIT_MISSING,
                          run_id=args.run_id,
                          data={"automation": "semi-manual", "ai_produced": False,
                                "note": msg["semi_manual_note"]},
                          degradation={"level": "L4",
                                       "reason": "manual JSX not executed yet",
                                       "substitute": msg["semi_manual_note"]})
        line = result_file.read_text(encoding="utf-8").strip().splitlines()[0]
        kv = _parse_ok(line)
        if kv is None:
            return L.emit(SCRIPT, status="fail", code=L.EXIT_FAIL,
                          run_id=args.run_id,
                          errors=[L.err("E-H1-STRUCTURE", "manual JSX returned: " + line[:160])])
        sd = _structure_doc(kv, sig, "semi-manual")
        L.write_json_atomic(run_dir / "structure_readback.json", sd)
        L.write_json_atomic(run_dir / "landing.json",
                            _landing("semi-manual", "degraded",
                                     msg["semi_manual_note"],
                                     (run_dir / "final.ai").is_file(), sd["ok"],
                                     {"manual_jsx": "manual-bridge.jsx",
                                      "manual_result": "manual-result.txt"}))
        _ensure_preview(run_dir, final_svg)
        return L.emit(SCRIPT, status="degraded", code=L.EXIT_OK if sd["ok"] else L.EXIT_FAIL,
                      run_id=args.run_id,
                      data={"automation": "semi-manual", "status": "degraded",
                            "h1_pass": sd["ok"], "structure_readback": sd,
                            "note": msg["semi_manual_note"]},
                      degradation={"level": "L4", "reason": "semi-manual path",
                                   "substitute": msg["semi_manual_note"]},
                      artifacts={"final_ai": "final.ai" if (run_dir / "final.ai").is_file() else "",
                                 "final_svg": "final.svg",
                                 "preview_png": "preview.png" if (run_dir / "preview.png").is_file() else ""})

    # ---- COM state ----
    # MISSING     : COM creation raised. Degrade, do not pretend.
    # UNVERIFIED  : the probe could not tell (timeout). Still attempt the real
    #               bridge once -- a slow cold start is not an absent product,
    #               and degrading on a timeout is how a working machine gets
    #               reported as unsupported.
    # --semi-manual with anything other than FOUND keeps the manual path.
    if com == "MISSING" or (com == "UNVERIFIED" and args.semi_manual):
        if args.semi_manual:
            jsx = _make_jsx(final_svg.resolve(), (run_dir / "final.ai").resolve(),
                            args.outline_text == "1", (run_dir / "manual-result.txt").resolve())
            L.write_text_atomic(run_dir / "manual-bridge.jsx", jsx)
            L.write_text_atomic(run_dir / "MANUAL-STEPS.md", MANUAL_STEPS)
            _ensure_preview(run_dir, final_svg)
            L.write_json_atomic(run_dir / "landing.json",
                                _landing("semi-manual", "degraded",
                                         msg["semi_manual_note"], False, False,
                                         {"manual_jsx": "manual-bridge.jsx",
                                          "manual_steps": "MANUAL-STEPS.md"}))
            return L.emit(SCRIPT, status="degraded", code=L.EXIT_MISSING,
                          run_id=args.run_id,
                          data={"automation": "semi-manual", "status": "degraded",
                                "ai_produced": False, "com_status": com,
                                "note": msg["semi_manual_note"],
                                "manual_jsx": "manual-bridge.jsx"},
                          degradation={"level": "L4", "reason": "COM not drivable",
                                       "substitute": msg["semi_manual_note"]},
                          artifacts={"manual_jsx": "manual-bridge.jsx",
                                     "manual_steps": "MANUAL-STEPS.md",
                                     "final_svg": "final.svg"})
        # automatic L4 degradation: SVG + PNG only, no fake .ai
        _ensure_preview(run_dir, final_svg)
        L.write_json_atomic(run_dir / "landing.json",
                            _landing("none", "degraded", msg["l4_note"], False, False))
        return L.emit(SCRIPT, status="degraded", code=L.EXIT_MISSING,
                      run_id=args.run_id,
                      data={"automation": "none", "status": "degraded",
                            "ai_produced": False, "com_status": com,
                            "note": msg["l4_note"]},
                      degradation={"level": "L4", "reason": "Illustrator COM unavailable",
                                   "substitute": "deliver sanitized SVG + PNG only; H1 fails"},
                      artifacts={"final_svg": "final.svg",
                                 "preview_png": "preview.png" if (run_dir / "preview.png").is_file() else ""})

    if args.semi_manual:
        raise L.ToolError("E-SEMI-MANUAL-NOT-ALLOWED",
                          "semi-manual is only allowed after detect_deps confirms COM is not drivable",
                          L.EXIT_USAGE)

    # ---- automatic COM path ----
    pw = shutil.which("powershell") or shutil.which("pwsh")
    if not pw:
        raise L.ToolError("E-H1-STRUCTURE", "PowerShell not found", L.EXIT_MISSING)
    # ExtendScript File() is unreliable with Windows backslashes and non-ASCII
    # path segments, so stage the bridge files in a pure-ASCII temp workspace.
    import tempfile
    workspace = Path(tempfile.mkdtemp(prefix="vector-ai-bridge-"))
    ws_svg = workspace / "final.svg"
    ws_ai = workspace / "final.ai"
    result_file = workspace / "bridge-result.txt"
    tmp_jsx = workspace / "_bridge_runtime.jsx"
    shutil.copyfile(final_svg, ws_svg)
    jsx = _make_jsx(ws_svg, ws_ai, args.outline_text == "1", result_file, save_opts)
    L.write_text_atomic(tmp_jsx, jsx)
    try:
        r = subprocess.run([pw, "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-File", str(SCRIPTS / "ai_bridge.ps1"),
                            "-JsxPath", str(tmp_jsx)],
                           timeout=180, capture_output=True, text=True, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        return L.emit(SCRIPT, status="error", code=L.EXIT_ERROR,
                      run_id=args.run_id,
                      errors=[L.err("E-H1-STRUCTURE", "COM bridge exception: " + type(e).__name__)])
    line = (r.stdout or "").strip().splitlines()
    line = line[-1] if line else ""
    if result_file.is_file():
        rl = result_file.read_text(encoding="utf-8").strip().splitlines()
        if rl:
            line = rl[0]
    if r.returncode != 0 and not line.startswith("OK|"):
        return L.emit(SCRIPT, status="error", code=L.EXIT_ERROR,
                      run_id=args.run_id,
                      errors=[L.err("E-H1-STRUCTURE",
                                    "COM bridge failed rc=" + str(r.returncode) +
                                    " stderr=" + (r.stderr or "")[:160])])
    kv = _parse_ok(line)
    if kv is None:
        return L.emit(SCRIPT, status="error", code=L.EXIT_ERROR,
                      run_id=args.run_id,
                      errors=[L.err("E-H1-STRUCTURE", "JSX did not return OK: " + line[:160])])
    # Copy the real native .ai produced by saveAs() back into the run dir.
    if ws_ai.is_file() and ws_ai.stat().st_size > 0:
        shutil.copyfile(ws_ai, run_dir / "final.ai")
    sd = _structure_doc(kv, sig, "auto")
    L.write_json_atomic(run_dir / "structure_readback.json", sd)

    # Delivery manifest: measured facts + explicit warnings, so the client can
    # verify the artefact instead of taking our word for it.
    deliverable = None
    ai_path = run_dir / "final.ai"
    if ai_path.is_file() and ai_path.stat().st_size > 0:
        obs = _observe_ai(ai_path)
        deliverable = _deliverable(ai_path, save_opts, obs, kv,
                                   engine="illustrator",
                                   engine_version=str(kv.get("app_version") or "unknown"))
        L.write_json_atomic(run_dir / "deliverable.json", deliverable)

    _ensure_preview(run_dir, final_svg)
    L.write_json_atomic(run_dir / "landing.json",
                        _landing("auto", "ok" if sd["ok"] else "degraded",
                                 "" if sd["ok"] else "H1 structure assertion failed",
                                 (run_dir / "final.ai").is_file(), sd["ok"]))
    try:
        shutil.rmtree(workspace, ignore_errors=True)
    except OSError:
        pass
    if not sd["ok"]:
        return L.emit(SCRIPT, status="fail", code=L.EXIT_FAIL,
                      run_id=args.run_id,
                      data={"structure_readback": sd},
                      errors=[L.err("E-H1-STRUCTURE", "artboard/viewBox or object-count assertion failed")])
    return L.emit(SCRIPT, status="ok", code=L.EXIT_OK, run_id=args.run_id,
                  data={"automation": "auto", "structure_readback": sd,
                        "h1_pass": True, "com_status": com,
                        "deliverable": deliverable},
                  artifacts={"final_ai": "final.ai", "final_svg": "final.svg",
                             "preview_png": "preview.png" if (run_dir / "preview.png").is_file() else "",
                             "deliverable": "deliverable.json" if deliverable else ""})


if __name__ == "__main__":
    sys.exit(L.run_cli(SCRIPT, main))

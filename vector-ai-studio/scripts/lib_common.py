"""vector-ai-studio shared library (pure Python standard library).

All *judgment* paths resolve from skill_root(), never from the caller's cwd.
The one exception is default_output_root(): runtime artifacts are deliberately
written outside the package so a self-test can never contaminate a zip.
All JSON uses ASCII straight quotes and a fixed key order so that identical
inputs produce byte-identical output (idempotency). Wall-clock time never
enters a judgment JSON; it is only appended to run.log.
"""

import hashlib
import json
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "vector-ai-studio/v1"

EXIT_OK = 0        # success
EXIT_FAIL = 1      # criterion / validation failed (business failure, not crash)
EXIT_USAGE = 2     # bad usage or malformed input (explicit error, never guess)
EXIT_MISSING = 3   # dependency MISSING, already degraded (never silent success)
EXIT_ERROR = 4     # internal exception or real-machine COM failure (L4)

CREDENTIAL_ENV_NAMES = ("RECRAFT_API_KEY", "RECRAFT_TOKEN")

_NEUTRAL_NAMES = {"black", "white", "gray", "grey", "silver", "gainsboro", "whitesmoke"}
_SKIP_COLORS = {"none", "transparent", "currentcolor", "inherit", "initial", "unset", ""}
_FORBIDDEN_TAGS = {"clippath", "filter", "mask", "pattern", "use", "image", "foreignobject"}


class ToolError(Exception):
    """Explicit, non-guessed error carrying an E-* code and an exit code."""

    def __init__(self, code, msg, exit_code=EXIT_USAGE):
        super().__init__(msg)
        self.code = code
        self.msg = msg
        self.exit_code = exit_code


def skill_root():
    """Absolute skill root = parent of scripts/. Independent of cwd."""
    return Path(__file__).resolve().parent.parent


def config_path():
    return skill_root() / "config" / "thresholds.json"


def templates_dir():
    return skill_root() / "templates"


def fixtures_dir():
    return skill_root() / "tests" / "fixtures"


def default_output_root():
    """Runtime artifacts live OUTSIDE the skill package, always.

    Writing them under skill_root() meant every self-test run deposited
    output/ into the package directory, and a subsequent zip shipped the
    tester's scratch logs as if they were part of the skill. Override with
    VECTOR_AI_STUDIO_OUT when a caller wants artifacts somewhere specific.
    """
    env = os.environ.get("VECTOR_AI_STUDIO_OUT")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".vector-ai-studio" / "output"


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha_run_id(*parts):
    h = hashlib.sha256()
    for p in parts:
        h.update(str(p).encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()[:12]


def load_thresholds(path=None):
    p = Path(path) if path else config_path()
    if not p.is_file():
        raise ToolError("E-CONFIG-MISSING", "thresholds file not found: " + p.name, EXIT_USAGE)
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ToolError("E-CONFIG-PARSE", "cannot parse thresholds: " + str(e), EXIT_USAGE)
    required = ["schema", "version", "score_levels", "A", "B", "C", "D", "E", "F",
                "grade", "iterate", "orchestration", "messages",
                "calibration_policy", "calibration_log"]
    missing = [k for k in required if k not in obj]
    if missing:
        raise ToolError("E-CONFIG-KEYS", "missing keys: " + ",".join(missing), EXIT_USAGE)
    return obj


def generation_budget(th):
    """Runtime-derived generation budget = F.N x iterate.K_max.

    The upper bounds are NEVER hardcoded in config or scripts; they are derived
    from the frozen criteria so a threshold change propagates automatically.
    """
    k = th["iterate"]["K_max"]
    return {"pass": th["F"]["N_pass"] * k,
            "excellent": th["F"]["N_excellent"] * k}


def capability_missing_value(th):
    """L2 status enum for A/B (not a score, never a number)."""
    return th["messages"]["l2_capability_missing"]


def file_sha256(path):
    p = Path(path)
    if not p.is_file():
        return ""
    h = hashlib.sha256()
    h.update(p.read_bytes())
    return h.hexdigest()


def read_json(path, expect_schema=None):
    p = Path(path)
    if not p.is_file():
        raise ToolError("E-SCHEMA", "input file not found: " + p.name, EXIT_USAGE)
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ToolError("E-SCHEMA", "malformed JSON: " + str(e), EXIT_USAGE)
    if not isinstance(obj, dict):
        raise ToolError("E-SCHEMA", "top-level JSON must be an object", EXIT_USAGE)
    if expect_schema is not None and obj.get("schema") != expect_schema:
        raise ToolError("E-SCHEMA",
                        "schema mismatch: expected " + str(expect_schema) +
                        ", got " + str(obj.get("schema")), EXIT_USAGE)
    return obj


def write_json_atomic(path, obj):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(obj, ensure_ascii=False, indent=2) + "\n"
    _atomic_write(p, text)


def write_text_atomic(path, text):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(p, text)


def _atomic_write(p, text):
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def append_run_log(run_dir, line):
    """Append-only wall-clock log. The only file allowed to contain time."""
    p = Path(run_dir) / "run.log"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8", newline="\n") as f:
        f.write(now_iso() + " " + line + "\n")


def assert_no_credentials(env=None):
    """H4: check only whether credential env NAMES exist; never read values."""
    env = env if env is not None else os.environ
    return {name: (name in env) for name in CREDENTIAL_ENV_NAMES}


def envelope(script, status="ok", code=0, data=None, errors=None, warnings=None,
             degradation=None, artifacts=None, run_id=""):
    env = {
        "schema": SCHEMA,
        "script": script,
        "run_id": run_id,
        "status": status,
        "code": code,
        "data": data or {},
        "errors": errors or [],
        "warnings": warnings or [],
        "artifacts": artifacts or {},
    }
    if degradation is not None:
        env["degradation"] = degradation
    return env


def emit(script, status="ok", code=0, data=None, errors=None, warnings=None,
         degradation=None, artifacts=None, run_id=""):
    env = envelope(script, status, code, data, errors, warnings,
                   degradation, artifacts, run_id)
    sys.stdout.write(json.dumps(env, ensure_ascii=False, indent=2) + "\n")
    return code


def err(code, msg):
    return {"code": code, "msg": msg}


def fail(script, exit_code, err_code, msg, status=None, **kw):
    if status is None:
        status = "error" if exit_code >= 2 else "fail"
    errors = kw.pop("errors", None) or [err(err_code, msg)]
    return emit(script, status=status, code=exit_code, errors=errors, **kw)


def run_cli(script, main_fn):
    """Uniform top-level wrapper: maps ToolError / Exception to the envelope."""
    try:
        return main_fn()
    except ToolError as e:
        return fail(script, e.exit_code, e.code, e.msg)
    except Exception as e:  # noqa: BLE001 - explicit internal error, never silent
        return fail(script, EXIT_ERROR, "E-INTERNAL",
                    "internal error: " + type(e).__name__ + ": " + str(e))


# ---------------------------------------------------------------------------
# Deterministic SVG structural analysis (no scoring, no thresholds here).
# score.py / sanitize_svg.py / render_png.py consume this shared extraction.
# ---------------------------------------------------------------------------

def _local(tag):
    return tag.split("}")[-1].lower() if isinstance(tag, str) else ""


def _num(s, default=0.0):
    if s is None:
        return default
    m = re.search(r"-?\d*\.?\d+(?:[eE][-+]?\d+)?", str(s))
    return float(m.group(0)) if m else default


def normalize_color(raw):
    """Return a normalized color string, or None when it must be ignored."""
    if raw is None:
        return None
    c = str(raw).strip().lower()
    if c in _SKIP_COLORS or c.startswith("url("):
        return None
    if re.fullmatch(r"#[0-9a-f]{3}", c):
        return "#" + "".join(ch + ch for ch in c[1:])
    if re.fullmatch(r"#[0-9a-f]{6}", c):
        return c
    m = re.fullmatch(r"rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", c)
    if m:
        return "#%02x%02x%02x" % (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return c or None


def is_neutral(color):
    """Neutral = black / white / gray family (a parsing rule, not a threshold)."""
    if color is None:
        return True
    c = normalize_color(color)
    if c is None:
        return True
    if c in _NEUTRAL_NAMES:
        return True
    if re.fullmatch(r"#[0-9a-f]{6}", c):
        r, g, b = int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)
        return r == g == b
    return False


def _style_attrs(elem):
    attrs = {}
    style = elem.get("style")
    if style:
        for part in style.split(";"):
            if ":" in part:
                k, v = part.split(":", 1)
                attrs[k.strip().lower()] = v.strip()
    return attrs


def parse_path_points(d):
    """Approximate anchor points of a path d attribute (M/L/H/V/C/S/Q/T/A/Z)."""
    if not d:
        return []
    token_re = re.compile(r"([MmLlHhVvCcSsQqTtAaZz])|(-?\d*\.?\d+(?:[eE][-+]?\d+)?)")
    toks = token_re.findall(d)
    seq = []
    for letter, number in toks:
        if letter:
            seq.append(("cmd", letter))
        else:
            seq.append(("num", float(number)))
    params = {"M": 2, "L": 2, "T": 2, "H": 1, "V": 1,
              "C": 6, "S": 4, "Q": 4, "A": 7, "Z": 0}
    points = []
    cx = cy = sx = sy = 0.0
    i = 0
    cmd = None
    while i < len(seq):
        kind, val = seq[i]
        if kind == "cmd":
            cmd = val
            i += 1
            if cmd in "Zz":
                cx, cy = sx, sy
                points.append((cx, cy))
            continue
        if cmd is None:
            i += 1
            continue
        up = cmd.upper()
        rel = cmd.islower()
        n = params.get(up, 2)
        nums = []
        j = i
        while j < len(seq) and seq[j][0] == "num" and len(nums) < n:
            nums.append(seq[j][1])
            j += 1
        if not nums:
            i += 1
            continue
        i = j
        if up in ("M", "L", "T"):
            if len(nums) >= 2:
                x, y = nums[0], nums[1]
                if rel:
                    x, y = cx + x, cy + y
                cx, cy = x, y
                points.append((cx, cy))
                if up == "M":
                    sx, sy = cx, cy
        elif up == "H":
            cx = cx + nums[0] if rel else nums[0]
            points.append((cx, cy))
        elif up == "V":
            cy = cy + nums[0] if rel else nums[0]
            points.append((cx, cy))
        elif up in ("C", "S", "Q"):
            pts = [(nums[k], nums[k + 1]) for k in range(0, len(nums) - 1, 2)]
            for (x, y) in pts:
                ax, ay = (cx + x, cy + y) if rel else (x, y)
                points.append((ax, ay))
            if pts:
                cx, cy = points[-1]
        elif up == "A":
            if len(nums) >= 7:
                x, y = nums[5], nums[6]
                cx, cy = (cx + x, cy + y) if rel else (x, y)
                points.append((cx, cy))
    return points


def _bbox_of_points(points):
    if not points:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return [min(xs), min(ys), max(xs), max(ys)]


def _elem_bbox(tag, a):
    g = lambda k, d=0.0: _num(a.get(k), d)
    if tag == "rect":
        x, y, w, h = g("x"), g("y"), g("width"), g("height")
        return [x, y, x + w, y + h] if w >= 0 and h >= 0 else None
    if tag == "circle":
        cx, cy, r = g("cx"), g("cy"), g("r")
        return [cx - r, cy - r, cx + r, cy + r] if r > 0 else None
    if tag == "ellipse":
        cx, cy, rx, ry = g("cx"), g("cy"), g("rx"), g("ry")
        return [cx - rx, cy - ry, cx + rx, cy + ry] if rx > 0 and ry > 0 else None
    if tag == "line":
        return _bbox_of_points([(g("x1"), g("y1")), (g("x2"), g("y2"))])
    if tag in ("polyline", "polygon"):
        pts = []
        for pair in re.findall(r"-?\d*\.?\d+\s*[, ]\s*-?\d*\.?\d+", a.get("points", "")):
            xy = re.findall(r"-?\d*\.?\d+", pair)
            if len(xy) >= 2:
                pts.append((float(xy[0]), float(xy[1])))
        return _bbox_of_points(pts)
    if tag == "path":
        return _bbox_of_points(parse_path_points(a.get("d", "")))
    return None


def analyze_svg(text):
    """Extract deterministic structure. Never scores, never guesses semantics.

    Returns a dict with parse_ok, geometry (measurements only), colors,
    stroke_widths, g_ids, forbidden constructs, bitmap flags, degenerate
    path count and anchor count.
    """
    out = {
        "parse_ok": False, "error": "", "viewBox": None,
        "artboard": [0.0, 0.0], "ink_bbox": None,
        "whitespace": None, "centroid_offset_ratio": None, "out_of_frame": False,
        "elements": [], "g_ids": [], "groups": [],
        "colors": [], "stroke_widths": [], "anchor_count": 0,
        "forbidden": [], "has_image": False, "has_base64": False,
        "degenerate_paths": 0, "element_count": 0,
    }
    low = text.lower()
    out["has_image"] = ("<image" in low) or ("data:image" in low)
    out["has_base64"] = ("base64" in low)
    for tok in ("<clippath", "<filter", "<use", "<mask", "<pattern",
                "filter=", "mix-blend-mode", "blend-mode", "style=\"filter"):
        if tok in low:
            out["forbidden"].append(tok)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        out["error"] = "xml: " + str(e)
        return out
    out["parse_ok"] = True
    vb = root.get("viewBox") or root.get("viewbox")
    if vb:
        nums = re.findall(r"-?\d*\.?\d+", vb)
        if len(nums) >= 4:
            out["viewBox"] = [float(x) for x in nums[:4]]
    w = _num(root.get("width"), 0.0)
    h = _num(root.get("height"), 0.0)
    if out["viewBox"]:
        out["artboard"] = [out["viewBox"][2], out["viewBox"][3]]
    else:
        out["artboard"] = [w, h]

    g_index = {}
    color_occurrences = []
    sw_occurrences = []

    def walk(elem, cur_g):
        for child in list(elem):
            tag = _local(child.tag)
            if tag in _FORBIDDEN_TAGS and tag not in ("image",):
                if tag not in out["forbidden"]:
                    out["forbidden"].append("<" + tag + ">")
            if tag == "g":
                gid = child.get("id")
                ng = gid if gid else cur_g
                if gid:
                    entry = {"id": gid, "colors": [], "color_total": 0, "element_count": 0}
                    out["groups"].append(entry)
                    out["g_ids"].append(gid)
                    g_index[gid] = entry
                walk(child, ng)
                continue
            if tag not in ("rect", "circle", "ellipse", "line", "polyline",
                           "polygon", "path"):
                walk(child, cur_g)
                continue
            a = dict(child.attrib)
            a.update(_style_attrs(child))
            bbox = _elem_bbox(tag, a)
            fill = normalize_color(a.get("fill", "black"))
            stroke = normalize_color(a.get("stroke"))
            sw = a.get("stroke-width")
            swv = _num(sw, 0.0) if sw is not None else None
            pts = parse_path_points(a.get("d", "")) if tag == "path" else []
            anchors = len(pts) if tag == "path" else (4 if bbox else 0)
            out["anchor_count"] += anchors
            if bbox is None or (bbox[2] - bbox[0]) <= 0 or (bbox[3] - bbox[1]) <= 0:
                out["degenerate_paths"] += 1
            else:
                out["elements"].append({
                    "tag": tag, "bbox": bbox, "fill": fill, "stroke": stroke,
                    "stroke_width": swv, "id": child.get("id") or "",
                })
            for c in (fill, stroke):
                if c is not None:
                    color_occurrences.append(c)
                    if cur_g in g_index:
                        g_index[cur_g]["colors"].append(c)
                        g_index[cur_g]["color_total"] += 1
            if swv is not None:
                sw_occurrences.append(swv)
            if cur_g in g_index:
                g_index[cur_g]["element_count"] += 1
            out["element_count"] += 1

    walk(root, None)
    out["colors"] = sorted(set(color_occurrences))
    out["stroke_widths"] = sorted(set(sw_occurrences))
    for gi in out["groups"]:
        gi["colors"] = sorted(set(gi["colors"]))

    if out["elements"]:
        x0 = min(e["bbox"][0] for e in out["elements"])
        y0 = min(e["bbox"][1] for e in out["elements"])
        x1 = max(e["bbox"][2] for e in out["elements"])
        y1 = max(e["bbox"][3] for e in out["elements"])
        out["ink_bbox"] = [x0, y0, x1, y1]
    aw, ah = out["artboard"]
    if aw > 0 and ah > 0 and out["ink_bbox"]:
        ib = out["ink_bbox"]
        ink_area = max(0.0, ib[2] - ib[0]) * max(0.0, ib[3] - ib[1])
        art_area = aw * ah
        out["whitespace"] = max(0.0, min(1.0, 1.0 - ink_area / art_area))
        cx = (ib[0] + ib[2]) / 2.0
        cy = (ib[1] + ib[3]) / 2.0
        acx, acy = aw / 2.0, ah / 2.0
        short = min(aw, ah)
        out["centroid_offset_ratio"] = (((cx - acx) ** 2 + (cy - acy) ** 2) ** 0.5) / short
        vb = out["viewBox"]
        if vb:
            vx0, vy0 = vb[0], vb[1]
            vx1, vy1 = vb[0] + vb[2], vb[1] + vb[3]
            eps = 1e-6
            out["out_of_frame"] = (ib[0] < vx0 - eps or ib[1] < vy0 - eps or
                                    ib[2] > vx1 + eps or ib[3] > vy1 + eps)
    return out

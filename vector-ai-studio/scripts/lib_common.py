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
import math
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
EXIT_ERROR = 4     # internal exception or landing-engine failure

CREDENTIAL_ENV_NAMES = ("RECRAFT_API_KEY", "RECRAFT_TOKEN")

# ---- H3 policy: single source of truth (r70) --------------------------------
# selftest.py writes the selftest.json that proves H3; aggregate_log.py judges
# H3 from that log. Both used to keep their own copy of these two policies:
# selftest declared EXPECTED_ASSERTIONS / CAPABILITY_SKIPS, while aggregate_log
# trusted whatever `capability_skips` the *log itself* carried. A log could
# therefore declare its own skip whitelist -- self-attesting. These constants
# now live here, in the shared library, and BOTH sides read them: selftest to
# write/verify, aggregate_log to judge. Never reintroduce a second copy.
#
# Registration condition: an assertion belongs in CAPABILITY_SKIPS only when
# the capability itself is optional (engine / optional external library that
# may legitimately be absent). Adding a guard here to make a run go green
# turns the guard into decoration.
#
# EXPECTED_ASSERTIONS is the number a complete selftest run must leave behind,
# including A60 (the guard that checks this number) but excluding A13, which
# only runs with --fixtures. Add or remove an assertion -> bump this number
# too; A60 turns a stale count red instead of leaving it to a human.
# r71: +A80 (registry scan catches unregistered codes), +A81 (smoke rating
# follows the acceptance verdict) -> 79 + 2 = 81.
# r72: +A82 (shipped attack set), +A83 (contract is the single source),
# +A84 (claim registry), +A85 (narrow track cannot launder), +A86 (three-valued
# verdict), +A87 (degradation loss list), +A88 (engine lock detects a
# swapped binary) -> 81 + 7 = 88.
# r72 (docs round): +A89 (validate_claims accepts only capability-skips in a
# supplied selftest log) -> 88 + 1 = 89.
# r72 (install-verifier round): +A90 (the shipped install verifier may not
# report a green it did not earn) -> 89 + 1 = 90.
# r73 (M2): +A91 (the evidence journal chains a real run and replays clean),
# +A92 (replay catches every cheap forgery) -> 90 + 2 = 92.
# r74 (M2 slice 2): +A93 (the witness anchors certification: create/verify,
# tamper, and no-record all behave) -> 92 + 1 = 93.
# r75 (M3): +A94 (the fidelity claims are measured: matrix drift + the
# faithful-implies-consistent direction) -> 93 + 1 = 94.
# r76 (M4): +A95 (Krippendorff's alpha is pinned to the published worked
# examples) + A96 (the review protocol is preregistered and verified) ->
# 94 + 2 = 96.
# r77 (M4 slice 2): +A97 (Bradley-Terry iteration properties) + A98 (the
# pairwise round trip) -> 96 + 2 = 98.
# r78 (M4 close): +A99 (content dedup + reviewer-token identity) -> 98 + 1 = 99.
# r79 (M5): +A100 (threshold promotion guard) -> 99 + 1 = 100.
# r80 (M5 slice 2): +A101 (calibration bench) -> 100 + 1 = 101.
# r81 (M5 close): +A102 (verifiable ruling records) -> 101 + 1 = 102.
# r82 (4.3): +A103 (independent PDF cross-check on a fixture) + A104 (a real
# print.pdf passes the PDF/X preflight) -> 102 + 2 = 104.
# r85 (M3 slice 3): +A105 (optional cairosvg cross-check) -> 104 + 1 = 105.
# r86 (verdict-kit phase 0): +A106 (vendored kit runs; manifest matches) ->
# 105 + 1 = 106.
# r87 (verdict-kit phase 1a): +A107 (envelope facade byte parity) ->
# 106 + 1 = 107.
# r89 (verdict-kit phase 1b): +A108 (claims adapter parity) -> 107 + 1 = 108.
EXPECTED_ASSERTIONS = 108
CAPABILITY_SKIPS = frozenset({
    "A30-vectorcraft-chain-h1-h2",
    "A31-deliverable-schema-no-drift",
    "A42-delight-flattens-lighting",
    "A43-export-print-dimensions",
    "A44-trace-ref-contract",
    "A45-uniform-error-envelope",
    "A46-craft-only-landing",
    "A53-legibility-fails-what-the-eye-misses",
    "A103-pdf-independent-preflight",
    "A104-pdfx-print-preflight",
    "A105-cross-renderer-cairosvg",
})

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


# ---- engine lock (r72, plan 4.4) --------------------------------------------
# The engine is where the facts come from: its bytes decide the .ai. Pinning
# a VERSION is not enough -- the same version number can ship a different
# build -- so the lock pins the binary's sha256 per platform. A build that
# does not match the lock is not trusted: the regression that was run against
# the old binary no longer applies.

def engine_platform():
    """A coarse platform key: windows-x64, darwin-arm64, linux-x64, ..."""
    import platform as _pl
    sysname = _pl.system().lower()
    machine = (_pl.machine() or "").lower()
    if sysname.startswith("win"):
        base = "windows"
    elif sysname == "darwin":
        base = "darwin"
    else:
        base = sysname or "unknown"
    if machine in ("arm64", "aarch64"):
        arch = "arm64"
    elif machine in ("x86_64", "amd64", "x64"):
        arch = "x64"
    else:
        arch = machine or "unknown"
    return base + "-" + arch


def engine_lock_path():
    return skill_root() / "engine.lock.json"


def load_engine_lock():
    p = engine_lock_path()
    if not p.is_file():
        return {"schema": "vector-ai-studio/engine-lock-v1", "entries": []}
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema": "vector-ai-studio/engine-lock-v1", "entries": []}
    if not isinstance(obj, dict):
        return {"schema": "vector-ai-studio/engine-lock-v1", "entries": []}
    return obj


def check_engine_lock(cli_path, version, lock=None):
    """Compare an engine binary with the committed lock.

    Returns {"sha256", "platform", "locked", "drift", "expected", "note"}.

    * locked=False : this platform+version is not pinned at all -> a NOTE,
      not a failure. The tool still runs; the log says the build is unknown.
    * drift=True   : the platform+version IS pinned and the binary differs ->
      a hard stop. The build was swapped and the regression is void.

    `lock` is injectable so the check itself can be tested without touching
    the committed lock file.
    """
    if lock is None:
        lock = load_engine_lock()
    plat = engine_platform()
    sha = file_sha256(cli_path)
    entries = [e for e in (lock.get("entries") or [])
               if isinstance(e, dict) and e.get("version") == version
               and e.get("platform") == plat]
    if not entries:
        return {"sha256": sha, "platform": plat, "locked": False, "drift": False,
                "expected": "",
                "note": "this engine build (" + plat + " " + str(version) +
                        ") is not pinned in engine.lock.json: the regression "
                        "that covers it is unknown, so pin it after a run"}
    expected = str(entries[0].get("sha256", ""))
    if expected and expected != sha:
        return {"sha256": sha, "platform": plat, "locked": True, "drift": True,
                "expected": expected,
                "note": "the engine binary changed for version " + str(version) +
                        " on " + plat + ": pinned sha256 " + expected +
                        ", found " + sha + ". Re-run the engine regression and "
                        "update engine.lock.json before trusting the output"}
    return {"sha256": sha, "platform": plat, "locked": True, "drift": False,
            "expected": expected,
            "note": "engine build matches engine.lock.json"}


def engine_version(cli_path, timeout=30):
    """The engine's own version string, or "" if it will not say."""
    import subprocess
    try:
        r = subprocess.run([str(cli_path), "--version"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    m = re.search(r"\b\d+\.\d+\.\d+\b", (r.stdout or "") + (r.stderr or ""))
    return m.group(0) if m else ""


_VK_ENV = None


def _vk_envelope():
    """Load the vendored verdict_kit.envelope by path (Phase 1a).

    The vendor directory carries a hyphen in its project name, so it is not an
    importable package. Loading the single module by path keeps this facade
    from inserting a global sys.path entry for all 35 importers of lib_common.
    """
    global _VK_ENV
    if _VK_ENV is None:
        import importlib.util
        p = (Path(__file__).resolve().parent / "_vendor" / "verdict-kit" /
             "verdict_kit" / "envelope.py")
        spec = importlib.util.spec_from_file_location("_vector_vk_envelope", p)
        mod = importlib.util.module_from_spec(spec)
        # Loading by path must not create a nested __pycache__: A22 only
        # ignores a cache whose parent is exactly scripts/.
        _old = sys.dont_write_bytecode
        sys.dont_write_bytecode = True
        try:
            spec.loader.exec_module(mod)
        finally:
            sys.dont_write_bytecode = _old
        _VK_ENV = mod
    return _VK_ENV


def envelope(script, status="ok", code=0, data=None, errors=None, warnings=None,
             degradation=None, artifacts=None, run_id=""):
    # Phase 1a: the vendored kit owns the envelope shape and key order. The
    # schema stays this skill's; A107 compares the bytes with frozen goldens.
    return _vk_envelope().envelope(
        script, status, code, data, errors, warnings, degradation,
        artifacts, run_id, schema=SCHEMA)


def emit(script, status="ok", code=0, data=None, errors=None, warnings=None,
         degradation=None, artifacts=None, run_id=""):
    env = envelope(script, status, code, data, errors, warnings,
                   degradation, artifacts, run_id)
    sys.stdout.write(_vk_envelope().dumps(env))
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


def parse_style(style):
    """Inline style attribute as {property: (value, important)}.

    One parser for the whole toolkit: the structure reader and conformance's
    cascade used to split on ";" separately and only one of them understood
    !important. Later declarations win, as in CSS.
    """
    out = {}
    for part in (style or "").split(";"):
        if ":" not in part:
            continue
        key, _, raw = part.partition(":")
        key = key.strip().lower()
        if not key:
            continue
        val = raw.strip()
        imp = False
        if val.lower().endswith("!important"):
            imp = True
            val = val[:-len("!important")].strip()
        out[key] = (val, imp)
    return out


def _style_attrs(elem):
    return {k: v for k, (v, _) in parse_style(elem.get("style")).items()}


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


_SHORT_SEG_FRACTION = 0.006     # fontspector outline_short_segments
_SHORT_SEG_ESCAPE = 100         # fontspector stops reporting past this count
_JAG_ANGLE_RAD = 0.25           # fontspector outline_jaggy_segments (14.32 deg)
_CORNER_ANGLE_RAD = 1.22173     # Blender curve.draw corner_angle default (~70 deg)


def _cubic_points(p0, p1, p2, p3, steps=12):
    pts = []
    for k in range(steps + 1):
        t = k / float(steps)
        mt = 1.0 - t
        a, b, c, d = mt * mt * mt, 3 * mt * mt * t, 3 * mt * t * t, t * t * t
        pts.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
                    a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))
    return pts


def _unit(v):
    n = math.hypot(v[0], v[1])
    return (0.0, 0.0) if n < 1e-12 else (v[0] / n, v[1] / n)


def path_metrics(d):
    """Segment-level measurements for one path `d`.

    Two numbers that professional vector tooling uses instead of counting
    anchors, because an absolute anchor count drifts with canvas size:

    short_ratio  share of segments shorter than 0.6% of the path's own length
                 (fonttools/fontspector outline_short_segments). Excluded for
                 paths carrying more than `_SHORT_SEG_ESCAPE` short segments:
                 past that point the drawing is genuinely dense and the check
                 stops discriminating -- the escape valve is borrowed from the
                 same check, which admits it over-reports on detailed artwork.

    reversals    joins where the path doubles back on itself, i.e. the previous
                 segment's end tangent pointing backwards and the next one's
                 start tangent pointing the same way, within 0.25 rad
                 (fontspector outline_jaggy_segments). This is NOT a sharp
                 corner detector: at a smooth or merely angled join the
                 reversed end tangent is roughly opposite the next tangent, so
                 the angle is near pi and nothing fires. Only a spike does.

    Arcs are approximated by their chord; the error is bounded by the sagitta
    and irrelevant at this sampling density.
    """
    out = {"segments": 0, "total_len": 0.0, "short_count": 0,
           "short_ratio": 0.0, "reversals": 0, "dense": False,
           "dense_short_count": 0, "corner_frac": 0.0, "mean_turn_deg": 0.0}
    if not d:
        return out

    seq = []
    for letter, number in re.findall(
            r"([MmLlHhVvCcSsQqTtAaZz])|(-?\d*\.?\d+(?:[eE][-+]?\d+)?)", d):
        seq.append(("cmd", letter) if letter else ("num", float(number)))

    cx = cy = sx = sy = 0.0
    prev_c2 = None          # last cubic control point, for S
    prev_q = None           # last quadratic control point, for T
    segs = []
    i, cmd = 0, None
    params = {"M": 2, "L": 2, "T": 2, "H": 1, "V": 1,
              "C": 6, "S": 4, "Q": 4, "A": 7, "Z": 0}
    while i < len(seq):
        kind, val = seq[i]
        if kind == "cmd":
            cmd = val
            i += 1
            if cmd in "Zz":
                if (cx, cy) != (sx, sy):
                    segs.append(((cx, cy), (cx, cy), (sx, sy), (sx, sy)))
                cx, cy = sx, sy
                prev_c2 = prev_q = None
            continue
        if cmd is None:
            i += 1
            continue
        up, rel = cmd.upper(), cmd.islower()
        n = params.get(up, 2)
        nums, j = [], i
        while j < len(seq) and seq[j][0] == "num" and len(nums) < n:
            nums.append(seq[j][1])
            j += 1
        if not nums:
            i += 1
            continue
        i = j
        p0 = (cx, cy)
        if up in ("M", "L"):
            if len(nums) < 2:
                continue
            x, y = nums[0], nums[1]
            x, y = (cx + x, cy + y) if rel else (x, y)
            if up == "L":
                segs.append((p0, p0, (x, y), (x, y)))
            else:
                sx, sy = x, y
                prev_c2 = prev_q = None
            cx, cy = x, y
        elif up == "H":
            x = cx + nums[0] if rel else nums[0]
            segs.append((p0, p0, (x, cy), (x, cy)))
            cx = x
        elif up == "V":
            y = cy + nums[0] if rel else nums[0]
            segs.append((p0, p0, (cx, y), (cx, y)))
            cy = y
        elif up == "C":
            if len(nums) < 6:
                continue
            c1 = (nums[0], nums[1])
            c2 = (nums[2], nums[3])
            p3 = (nums[4], nums[5])
            if rel:
                c1 = (cx + c1[0], cy + c1[1])
                c2 = (cx + c2[0], cy + c2[1])
                p3 = (cx + p3[0], cy + p3[1])
            segs.append((p0, c1, c2, p3))
            cx, cy = p3
            prev_c2, prev_q = c2, None
        elif up == "S":
            if len(nums) < 4:
                continue
            c2 = (nums[0], nums[1])
            p3 = (nums[2], nums[3])
            if rel:
                c2 = (cx + c2[0], cy + c2[1])
                p3 = (cx + p3[0], cy + p3[1])
            c1 = ((2 * cx - prev_c2[0], 2 * cy - prev_c2[1])
                  if prev_c2 else p0)
            segs.append((p0, c1, c2, p3))
            cx, cy = p3
            prev_c2, prev_q = c2, None
        elif up in ("Q", "T"):
            if up == "Q":
                if len(nums) < 4:
                    continue
                q = (nums[0], nums[1])
                p3 = (nums[2], nums[3])
                if rel:
                    q = (cx + q[0], cy + q[1])
                    p3 = (cx + p3[0], cy + p3[1])
            else:
                if len(nums) < 2:
                    continue
                p3 = (nums[0], nums[1])
                if rel:
                    p3 = (cx + p3[0], cy + p3[1])
                q = ((2 * cx - prev_q[0], 2 * cy - prev_q[1])
                     if prev_q else p0)
            c1 = (p0[0] + 2.0 / 3.0 * (q[0] - p0[0]),
                  p0[1] + 2.0 / 3.0 * (q[1] - p0[1]))
            c2 = (p3[0] + 2.0 / 3.0 * (q[0] - p3[0]),
                  p3[1] + 2.0 / 3.0 * (q[1] - p3[1]))
            segs.append((p0, c1, c2, p3))
            cx, cy = p3
            prev_q, prev_c2 = q, None
        elif up == "A":
            if len(nums) < 7:
                continue
            x, y = nums[5], nums[6]
            x, y = (cx + x, cy + y) if rel else (x, y)
            segs.append((p0, p0, (x, y), (x, y)))
            cx, cy = x, y
            prev_c2 = prev_q = None

    if not segs:
        return out

    lengths, t_starts, t_ends = [], [], []
    for p0, p1, p2, p3 in segs:
        pts = _cubic_points(p0, p1, p2, p3)
        length = sum(math.hypot(pts[k + 1][0] - pts[k][0],
                                pts[k + 1][1] - pts[k][1])
                     for k in range(len(pts) - 1))
        lengths.append(length)
        ts = _unit((p1[0] - p0[0], p1[1] - p0[1]))
        if ts == (0.0, 0.0):
            ts = _unit((p3[0] - p0[0], p3[1] - p0[1]))
        te = _unit((p3[0] - p2[0], p3[1] - p2[1]))
        if te == (0.0, 0.0):
            te = _unit((p3[0] - p0[0], p3[1] - p0[1]))
        t_starts.append(ts)
        t_ends.append(te)

    total = sum(lengths)
    out["segments"] = len(segs)
    out["total_len"] = round(total, 6)
    if total <= 0:
        return out

    limit = total * _SHORT_SEG_FRACTION
    short = sum(1 for L_ in lengths if L_ < limit)
    out["short_count"] = short
    if short > _SHORT_SEG_ESCAPE:
        out["dense"] = True
        out["short_ratio"] = 0.0
        # The escape valve still keeps this path out of the ratio, so nothing
        # starts failing that used to pass. But the count itself is preserved:
        # "this path was skipped" must not also mean "nothing is known about
        # this path". Zeroing the evidence is how a flattened circle stayed
        # invisible while the report said the check had run.
        out["dense_short_count"] = short
    else:
        out["short_ratio"] = round(short / float(len(segs)), 6)

    rev = 0
    for k in range(len(segs) - 1):
        back = (-t_ends[k][0], -t_ends[k][1])     # reversed end tangent
        nxt = t_starts[k + 1]
        if back == (0.0, 0.0) or nxt == (0.0, 0.0):
            continue
        dot = max(-1.0, min(1.0, back[0] * nxt[0] + back[1] * nxt[1]))
        if math.acos(dot) <= _JAG_ANGLE_RAD:
            rev += 1
    out["reversals"] = rev

    # How sharply does the path actually bend at each join? Measured on the
    # forward tangents (not reversed), so this is the ordinary turning angle.
    #
    # Why it is recorded even for dense paths: a circle cut into 200 chords
    # turns 1.8 degrees at every join and is a flattening artefact; a 40-tooth
    # gear turns 144 degrees and is genuine detail. Both trip the short-segment
    # escape valve, so the valve alone cannot tell them apart -- but the turn
    # angle separates them by two orders of magnitude, and costs nothing
    # because the tangents are already computed above.
    #
    # The corner threshold is Blender's curve.draw corner_angle default
    # (1.22173 rad, ~70 degrees), the one published number found for
    # "this vertex is a corner, not a smooth transition".
    turns = []
    for k in range(len(segs) - 1):
        a, b = t_ends[k], t_starts[k + 1]
        if a == (0.0, 0.0) or b == (0.0, 0.0):
            continue
        d = max(-1.0, min(1.0, a[0] * b[0] + a[1] * b[1]))
        turns.append(math.acos(d))
    if turns:
        out["corner_frac"] = round(
            sum(1 for t in turns if t > _CORNER_ANGLE_RAD) / float(len(turns)), 6)
        out["mean_turn_deg"] = round(math.degrees(sum(turns) / len(turns)), 3)
    return out


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
        "short_segment_ratio": 0.0, "tangent_reversal_count": 0,
        "dense_paths": 0, "short_segment_note": "",
        "dense_short_segments": 0, "dense_turns_deg": [],
        "dense_corner_fracs": [],
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
    _segs = 0
    _short = 0
    sw_occurrences = []

    def walk(elem, cur_g):
        nonlocal _segs, _short
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
            # r75 (M3): the builtin rasteriser IGNORES transforms and fill
            # opacity -- a rotated rect is drawn unrotated, a 40% fill is drawn
            # fully opaque. The fidelity classifier must see both to fail
            # closed, so the element record carries them: transform as a
            # boolean (attribute or style form), fill-opacity as a parsed
            # float, or the raw string when unparseable (the classifier fails
            # closed on what it cannot read).
            _tr = (a.get("transform") or "").strip()
            _fo_raw = a.get("fill-opacity")
            _fo = None
            if _fo_raw is not None:
                _s = str(_fo_raw).strip()
                try:
                    _fo = float(_s[:-1]) / 100.0 if _s.endswith("%") else float(_s)
                except ValueError:
                    _fo = _fo_raw
            pts = parse_path_points(a.get("d", "")) if tag == "path" else []
            anchors = len(pts) if tag == "path" else (4 if bbox else 0)
            out["anchor_count"] += anchors
            if tag == "path":
                pm = path_metrics(a.get("d", ""))
                if pm["segments"]:
                    _segs += pm["segments"]
                    _short += pm["short_count"] if not pm["dense"] else 0
                    out["tangent_reversal_count"] += pm["reversals"]
                    if pm["dense"]:
                        out["dense_paths"] += 1
                        # Keep what the escape valve would otherwise discard.
                        # A dense path contributes 0 to the ratio above by
                        # design; it must not also contribute 0 to the record.
                        out["dense_short_segments"] += pm["dense_short_count"]
                        out["dense_turns_deg"].append(pm["mean_turn_deg"])
                        out["dense_corner_fracs"].append(pm["corner_frac"])
            # A zero-extent bounding box is not by itself junk. A stroke-only
            # horizontal rule has height 0 and is a legitimate design element
            # (dividers, underlines, table rules). What is actually junk is a
            # FILL with no area, or an element that paints nothing at all.
            # Note that geometry validity checks do not catch these either:
            # both JTS and Shapely consider a near-zero-area sliver valid, so
            # this has to stay its own rule rather than ride on validity.
            zero_extent = (bbox is not None and
                           ((bbox[2] - bbox[0]) <= 0 or (bbox[3] - bbox[1]) <= 0))
            # SVG's initial fill is black, so a <line> reports fill="black"
            # whether or not the author wrote one. A line has no interior to
            # fill, so that fill can never paint. Everything else (path,
            # polygon, polyline) is implicitly closed for filling and would
            # paint something.
            fill_paints = fill is not None and tag != "line"
            if bbox is None:
                out["degenerate_paths"] += 1
            elif zero_extent:
                if fill_paints:
                    out["degenerate_paths"] += 1        # zero-area fill
                elif stroke is None:
                    out["degenerate_paths"] += 1        # paints nothing
                # else: stroke-only with no thickness in one axis -> a line
            else:
                out["elements"].append({
                    "tag": tag, "bbox": bbox, "fill": fill, "stroke": stroke,
                    "stroke_width": swv, "id": child.get("id") or "",
                    "transform": bool(_tr), "fill_opacity": _fo,
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
    if _segs:
        out["short_segment_ratio"] = round(_short / float(_segs), 6)
    if out["dense_paths"]:
        turns = out["dense_turns_deg"]
        out["short_segment_note"] = (
            "short-segment share not scored on " + str(out["dense_paths"]) +
            " path(s): past 100 short segments the drawing is simply dense, "
            "and the check stops discriminating. Kept as evidence: "
            + str(out["dense_short_segments"]) + " short segment(s) total, "
            "mean turn " + "/".join("%.1f" % t for t in turns) + " deg")
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

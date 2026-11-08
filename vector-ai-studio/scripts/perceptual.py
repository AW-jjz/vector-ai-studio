# -*- coding: utf-8 -*-
"""Perceptual colour math for vector-ai-studio.

Pure standard library, no IO, no third-party imports.

Every constant in this file is either transcribed from a published formula or
from a published lookup table, and carries its source. Nothing here is tuned so
that a test passes -- if a reference vector disagrees with this module, the
module is wrong.

Rounding policy: functions return full-precision floats. Callers round only when
writing output (see references/pipeline-contract.md: 6 decimal places).
"""

import math

# --------------------------------------------------------------- sRGB primaries
# IEC 61966-2-1 matrix, D65 white point.
_WHITE = (0.95047, 1.0, 1.08883)
_EPS = (6.0 / 29.0) ** 3


def srgb_to_linear(c):
    """0-255 sRGB channel -> linear light, 0-1."""
    v = c / 255.0
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def srgb_to_xyz(rgb):
    """sRGB(0-255) -> XYZ(D65)."""
    r, g, b = (srgb_to_linear(v) for v in rgb)
    return (0.4124564 * r + 0.3575761 * g + 0.1804375 * b,
            0.2126729 * r + 0.7151522 * g + 0.0721750 * b,
            0.0193339 * r + 0.1191920 * g + 0.9503041 * b)


def xyz_to_lab(xyz):
    def f(t):
        return (t ** (1.0 / 3.0) if t > _EPS
                else t / (3 * (6.0 / 29.0) ** 2) + 4.0 / 29.0)
    fx, fy, fz = (f(v / w) for v, w in zip(xyz, _WHITE))
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def srgb_to_lab(rgb):
    return xyz_to_lab(srgb_to_xyz(rgb))


def parse_hex(text):
    """'#rgb' / 'rgb(...)' / '#rrggbb' -> (r, g, b) or None.

    Only the shapes this project actually emits are accepted; anything else
    returns None rather than a guess.
    """
    if not isinstance(text, str):
        return None
    s = text.strip().lower()
    if s.startswith("#"):
        h = s[1:]
        if len(h) == 3:
            return tuple(int(ch * 2, 16) for ch in h)
        if len(h) == 6:
            return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
        return None
    if s.startswith("rgb(") and s.endswith(")"):
        parts = [p.strip() for p in s[4:-1].split(",")]
        if len(parts) != 3:
            return None
        try:
            vals = [int(round(float(p))) for p in parts]
        except ValueError:
            return None
        return tuple(max(0, min(255, v)) for v in vals)
    return None


# ------------------------------------------------- WCAG 2.x relative luminance

def relative_luminance(rgb):
    """WCAG 2.x definition of relative luminance (not the simple 2.4 exponent
    that APCA uses -- they differ, and mixing them makes every number wrong)."""
    r, g, b = (srgb_to_linear(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def wcag_ratio(c1, c2):
    """WCAG contrast ratio, 1.0 .. 21.0. Order-independent."""
    l1, l2 = relative_luminance(c1), relative_luminance(c2)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


# ---------------------------------------------- colour vision deficiency (CVD)
# Machado, Oliveira & Fernandes (2009), "A Physiologically-based Model for
# Simulation of Color Vision Deficiency", IEEE TVCG 15(6):1291-1298.
#
# Only the full-severity (1.0) matrices are reproduced. The paper publishes an
# 11-step severity ramp; interpolating between its entries is NOT the same as
# blending this matrix with the identity, so intermediate severities are
# rejected rather than approximated. A wrong curve would look plausible and be
# undetectable.
#
# The matrices act on GAMMA-ENCODED sRGB in 0..1, which is the convention used
# by culori (MIT) and by R's colorspace package.
MACHADO = {
    "prot": ((0.152286, 1.052583, -0.204868),
             (0.114503, 0.786281, 0.099216),
             (-0.003882, -0.048116, 1.051998)),
    "deut": ((0.367322, 0.860646, -0.227968),
             (0.280085, 0.672501, 0.047413),
             (-0.011820, 0.042940, 0.968881)),
    "trit": ((1.255528, -0.076749, -0.178779),
             (-0.078411, 0.930809, 0.147602),
             (0.004733, 0.691367, 0.303900)),
}

CVD_KINDS = ("prot", "deut", "trit")


def simulate_cvd(rgb, kind, severity=1.0):
    """Simulate dichromatic vision. Returns (r, g, b) ints clipped to 0..255.

    severity must be 1.0: see the note above on why intermediate values are
    refused instead of approximated.
    """
    if kind not in MACHADO:
        raise ValueError("unknown CVD kind: " + repr(kind))
    if severity != 1.0:
        raise ValueError(
            "only severity=1.0 is supported; the published severity ramp is "
            "not a linear blend of the identity and must not be faked")
    m = MACHADO[kind]
    r, g, b = (v / 255.0 for v in rgb)
    out = [m[i][0] * r + m[i][1] * g + m[i][2] * b for i in range(3)]
    return tuple(int(max(0, min(255, round(v * 255.0)))) for v in out)


def contrast_under_cvd(fg, bg):
    """Contrast ratios of one ink/backdrop pair as seen by four observer types.

    Returns a dict with 'normal', one key per CVD kind, and 'min' across all
    four. The 'min' is what a gate would compare against a floor: a pair that
    is legible to most observers but invisible to one is not legible.
    """
    out = {"normal": wcag_ratio(fg, bg)}
    for kind in CVD_KINDS:
        out[kind] = wcag_ratio(simulate_cvd(fg, kind), simulate_cvd(bg, kind))
    out["min"] = min(out["normal"], out["prot"], out["deut"], out["trit"])
    return out


# ------------------------------------------------------------- colour difference
_POW25_7 = 25.0 ** 7


def delta_e_2000(lab1, lab2, kL=1.0, kC=1.0, kH=1.0):
    """CIEDE2000, CIE 142-2001 with kL=kC=kH=1.

    Fitted for SMALL colour differences (< ~5 DE*ab). Do not use it to decide
    whether two colours are "far enough apart" -- see hyab() for that.
    """
    L1, a1, b1 = lab1
    L2, a2, b2 = lab2
    C1 = math.hypot(a1, b1)
    C2 = math.hypot(a2, b2)
    Cbar = (C1 + C2) / 2.0

    G = 0.5 * (1 - math.sqrt(Cbar ** 7 / (Cbar ** 7 + _POW25_7 + 1e-30)))
    a1p, a2p = (1 + G) * a1, (1 + G) * a2
    C1p, C2p = math.hypot(a1p, b1), math.hypot(a2p, b2)

    h1p = 0.0 if (a1p == 0 and b1 == 0) else math.degrees(math.atan2(b1, a1p)) % 360
    h2p = 0.0 if (a2p == 0 and b2 == 0) else math.degrees(math.atan2(b2, a2p)) % 360

    dLp = L2 - L1
    dCp = C2p - C1p

    if C1p * C2p == 0:
        dhp = 0.0
    elif abs(h2p - h1p) <= 180:
        dhp = h2p - h1p
    elif h2p - h1p > 180:
        dhp = h2p - h1p - 360
    else:
        dhp = h2p - h1p + 360
    dHp = 2 * math.sqrt(C1p * C2p) * math.sin(math.radians(dhp / 2.0))

    Lbp = (L1 + L2) / 2.0
    Cbp = (C1p + C2p) / 2.0

    if C1p * C2p == 0:
        hbp = h1p + h2p
    elif abs(h1p - h2p) <= 180:
        hbp = (h1p + h2p) / 2.0
    elif h1p + h2p < 360:
        hbp = (h1p + h2p + 360) / 2.0
    else:
        hbp = (h1p + h2p - 360) / 2.0

    T = (1 - 0.17 * math.cos(math.radians(hbp - 30))
         + 0.24 * math.cos(math.radians(2 * hbp))
         + 0.32 * math.cos(math.radians(3 * hbp + 6))
         - 0.20 * math.cos(math.radians(4 * hbp - 63)))

    dtheta = 30 * math.exp(-(((hbp - 275) / 25.0) ** 2))
    RC = 2 * math.sqrt(Cbp ** 7 / (Cbp ** 7 + _POW25_7 + 1e-30))
    SL = 1 + (0.015 * (Lbp - 50) ** 2) / math.sqrt(20 + (Lbp - 50) ** 2)
    SC = 1 + 0.045 * Cbp
    SH = 1 + 0.015 * Cbp * T
    RT = -math.sin(math.radians(2 * dtheta)) * RC

    tL = dLp / (kL * SL)
    tC = dCp / (kC * SC)
    tH = dHp / (kH * SH)
    return math.sqrt(max(0.0, tL * tL + tC * tC + tH * tH + RT * tC * tH))


def hyab(lab1, lab2):
    """HyAB distance, for LARGE colour differences.

    Abasi, Amani Tehran & Fairchild (2020), "Distance metrics for very large
    color differences", Color Research & Application 45(2):208-223.
    City-block on lightness, Euclidean in the (a, b) plane.
    """
    dL = abs(lab1[0] - lab2[0])
    da = lab1[1] - lab2[1]
    db = lab1[2] - lab2[2]
    return dL + math.hypot(da, db)


# ---------------------------------------------------------------- self-check
# Sharma, Wu & Dalal (2005), "The CIEDE2000 Color-Difference Formula:
# Implementation Notes, Supplementary Test Data, and Mathematical Observations",
# Table 1 + supplementary data. 34 pairs.
SHARMA_VECTORS = [
    ((50.0000, 2.6772, -79.7751), (50.0000, 0.0000, -82.7485), 2.0425),
    ((50.0000, 3.1571, -77.2803), (50.0000, 0.0000, -82.7485), 2.8615),
    ((50.0000, 2.8361, -74.0200), (50.0000, 0.0000, -82.7485), 3.4412),
    ((50.0000, -1.3802, -84.2814), (50.0000, 0.0000, -82.7485), 1.0000),
    ((50.0000, -1.1848, -84.8006), (50.0000, 0.0000, -82.7485), 1.0000),
    ((50.0000, -0.9009, -85.5211), (50.0000, 0.0000, -82.7485), 1.0000),
    ((50.0000, 0.0000, 0.0000), (50.0000, -1.0000, 2.0000), 2.3669),
    ((50.0000, -1.0000, 2.0000), (50.0000, 0.0000, 0.0000), 2.3669),
    ((50.0000, 2.4900, -0.0010), (50.0000, -2.4900, 0.0009), 7.1792),
    ((50.0000, 2.4900, -0.0010), (50.0000, -2.4900, 0.0010), 7.1792),
    ((50.0000, 2.4900, -0.0010), (50.0000, -2.4900, 0.0011), 7.2195),
    ((50.0000, 2.4900, -0.0010), (50.0000, -2.4900, 0.0012), 7.2195),
    ((50.0000, -0.0010, 2.4900), (50.0000, 0.0009, -2.4900), 4.8045),
    ((50.0000, -0.0010, 2.4900), (50.0000, 0.0010, -2.4900), 4.8045),
    ((50.0000, -0.0010, 2.4900), (50.0000, 0.0011, -2.4900), 4.7461),
    ((50.0000, 2.5000, 0.0000), (50.0000, 0.0000, -2.5000), 4.3065),
    ((50.0000, 2.5000, 0.0000), (73.0000, 25.0000, -18.0000), 27.1492),
    ((50.0000, 2.5000, 0.0000), (61.0000, -5.0000, 29.0000), 22.8977),
    ((50.0000, 2.5000, 0.0000), (56.0000, -27.0000, -3.0000), 31.9030),
    ((50.0000, 2.5000, 0.0000), (58.0000, 24.0000, 15.0000), 19.4535),
    ((50.0000, 2.5000, 0.0000), (50.0000, 3.1736, 0.5854), 1.0000),
    ((50.0000, 2.5000, 0.0000), (50.0000, 3.2972, 0.0000), 1.0000),
    ((50.0000, 2.5000, 0.0000), (50.0000, 1.8634, 0.5757), 1.0000),
    ((50.0000, 2.5000, 0.0000), (50.0000, 3.2592, 0.3350), 1.0000),
    ((60.2574, -34.0099, 36.2677), (60.4626, -34.1751, 39.4387), 1.2644),
    ((63.0109, -31.0961, -5.8663), (62.8187, -29.7946, -4.0864), 1.2630),
    ((61.2901, 3.7196, -5.3901), (61.4292, 2.2480, -4.9620), 1.8731),
    ((35.0831, -44.1164, 3.7933), (35.0232, -40.0716, 1.5901), 1.8645),
    ((22.7233, 20.0904, -46.6940), (23.0331, 14.9730, -42.5619), 2.0373),
    ((36.4612, 47.8580, 18.3852), (36.2715, 50.5065, 21.2231), 1.4146),
    ((90.8027, -2.0831, 1.4410), (91.1528, -1.6435, 0.0447), 1.4441),
    ((90.9257, -0.5406, -0.9208), (88.6381, -0.8985, -0.7239), 1.5381),
    ((6.7747, -0.2908, -2.4247), (5.8714, -0.0985, -2.2286), 0.6377),
    ((2.0776, 0.0795, -1.1350), (0.9033, -0.0636, -0.5514), 0.9082),
]


def sharma_max_error():
    """Largest |computed - published| over the 34 reference pairs."""
    worst = 0.0
    for lab1, lab2, expected in SHARMA_VECTORS:
        worst = max(worst, abs(delta_e_2000(lab1, lab2) - expected))
    return worst


# Contrast anchors. Values are the published WCAG results for these pairs.
CONTRAST_ANCHORS = [
    ((0, 0, 0), (255, 255, 255), 21.0),
    ((255, 255, 255), (255, 255, 255), 1.0),
    ((119, 119, 119), (255, 255, 255), 4.48),
    ((255, 0, 0), (0, 0, 0), 5.25),
]

# CVD anchors: red through a dichromatic eye, PINNED as unrounded floats.
#
# These are not rounded to 0-255 on purpose. The first version of this check
# compared rounded integers with a tolerance of +/-1 per channel, and a mutation
# test proved it toothless: nudging a matrix coefficient by 0.0001 moves the
# output by 0.0255 units, which rounds to the same integer and passes. A guard
# that cannot see a one-digit change is decoration.
#
# The values below are the published matrix applied to pure red, kept to six
# decimals. Editing any matrix coefficient MUST break this pin -- that is the
# point. If the pin has to move, move it deliberately and say why in the commit,
# because "adjust the expectation until it is green" is how the real bug gets
# buried.
CVD_ANCHORS = [
    ((255, 0, 0), "prot", (38.832930, 29.198265, -0.989910)),
    ((255, 0, 0), "deut", (93.667110, 71.421675, -3.014100)),
]

# Each row of a Machado matrix sums to 1: white must map to white. A typo in one
# coefficient breaks the row sum even when a compensating typo is not present,
# so this catches slips that the single-red anchor alone would miss.
ROWSUM_TOL = 5e-6


def simulate_cvd_raw(rgb, kind):
    """Unrounded simulated colour, 0-255 scale. Used by the pin above."""
    if kind not in MACHADO:
        raise ValueError("unknown CVD kind: " + repr(kind))
    m = MACHADO[kind]
    r, g, b = (v / 255.0 for v in rgb)
    return tuple((m[i][0] * r + m[i][1] * g + m[i][2] * b) * 255.0
                 for i in range(3))


def selfcheck():
    """Return a list of human-readable problems. Empty list means all good."""
    problems = []
    worst = sharma_max_error()
    if worst > 1e-4:
        problems.append("CIEDE2000 max error %.3e > 1e-4" % worst)
    for c1, c2, expected in CONTRAST_ANCHORS:
        got = wcag_ratio(c1, c2)
        if abs(got - expected) > 0.005:
            problems.append("contrast %s/%s = %.4f, expected %.2f"
                            % (c1, c2, got, expected))
    for rgb, kind, expected in CVD_ANCHORS:
        got = simulate_cvd_raw(rgb, kind)
        worst = max(abs(a - b) for a, b in zip(got, expected))
        if worst > 1e-4:
            problems.append("cvd %s %s = %s, pinned %s (worst %.6f)"
                            % (rgb, kind, got, expected, worst))
    for kind in sorted(MACHADO):
        for idx, row in enumerate(MACHADO[kind]):
            total = sum(row)
            if abs(total - 1.0) > ROWSUM_TOL:
                problems.append("cvd matrix %s row %d sums to %.8f, must be 1"
                                % (kind, idx + 1, total))
    return problems


if __name__ == "__main__":
    bad = selfcheck()
    print("CIEDE2000 max error vs Sharma 2005 (34 pairs): %.6e"
          % sharma_max_error())
    for rgb, kind, expected in CVD_ANCHORS:
        got = simulate_cvd_raw(rgb, kind)
        print("simulate_cvd_raw(%s, %r) = (%.6f, %.6f, %.6f)  pinned (%.6f, %.6f, %.6f)"
              % ((rgb, kind) + tuple(got) + tuple(expected)))
    for c1, c2, expected in CONTRAST_ANCHORS:
        print("wcag_ratio(%s, %s) = %.4f  (expected %.2f)"
              % (c1, c2, wcag_ratio(c1, c2), expected))
    print()
    if bad:
        for p in bad:
            print("FAIL:", p)
        raise SystemExit(1)
    print("PERCEPTUAL SELF-CHECK: all reference vectors pass")

"""GENERATED FILE -- do not edit by hand.

Source of truth: contract/contract.json
Regenerate:    python scripts/gen_contract.py
Check (CI):    python scripts/gen_contract.py --check

Producers and judges import THIS, so a second vocabulary cannot exist:
a typo becomes an AttributeError instead of a silent wrong verdict.
"""

CONTRACT_SCHEMA = "vector-ai-studio/contract-v1"
CONTRACT_VERSION = "1.0.0"
CRITERIA_VERSION = "2.0.0"


class DEGRADATION_LEVEL:
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"
    L5 = "L5"
    VALUES = ["L1", "L2", "L3", "L4", "L5"]


class FIDELITY:
    """预览保真度。r71 的 P0-1 就在这里：生产侧写 full、判据侧只认 faithful、六处三种说法、零处校验。现在只有一个词表、一个接受集合，full 这个词不再存在于代码里。"""
    FAITHFUL = "faithful"
    LIMITED = "limited"
    UNKNOWN = "unknown"
    VALUES = ["faithful", "limited", "unknown"]
    FAIL_CLOSED_DEFAULT = "limited"
    ACCEPTED_BY = {"aggregate_log": {"A": ["faithful"], "B": ["faithful"]}}
    PRODUCERS = {"land_vc": {"preview_failed": "limited", "vectorcraft_preview_ok": "faithful"}, "render_png": {"builtin_other": "limited", "builtin_rect_only": "faithful", "real_renderer": "faithful"}}


class H2_STATUS:
    PASS = "pass"
    FAIL = "fail"
    NOT_EVALUATED = "not_evaluated"
    VALUES = ["pass", "fail", "not_evaluated"]


class MODE:
    FULL = "full"
    LANDING_ONLY = "landing-only"
    VALUES = ["full", "landing-only"]


class REASON:
    NONE = "none"
    QUALITY_FAIL = "quality_fail"
    CAPABILITY_MISSING = "capability_missing"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    VALUES = ["none", "quality_fail", "capability_missing", "insufficient_evidence"]


class STATUS:
    OK = "ok"
    DEGRADED = "degraded"
    FAIL = "fail"
    ERROR = "error"
    VALUES = ["ok", "degraded", "fail", "error"]


class TIER:
    """机器可读档位。indeterminate = 未判定：我们没能力测，不等于不达标。"""
    PASS = "pass"
    NOT_PASS = "not-pass"
    INDETERMINATE = "indeterminate"
    VALUES = ["pass", "not-pass", "indeterminate"]

class VERDICT:
    """Fields the judge re-derives from evidence; a log may not attest them."""
    TIERS = ["pass", "fail", "indeterminate"]
    TIER_TO_GRADE_KEY = {"fail": "grade_fail", "indeterminate": "grade_indeterminate", "pass": "grade_pass"}
    LEGACY_GRADE_KEY = "grade_fail"
    LEGACY_TIER = "not-pass"
    REQUIRED_DIMS = ["A", "B", "C", "D", "F"]
    OPTIONAL_DIMS = ["E"]
    SELF_ATTESTATION_FORBIDDEN = ["grade", "tier", "headline", "status", "degraded", "needs_human_review", "reason", "internal_excellent", "evidence_coverage", "deliverable_status"]

BANNED_CLAIM_WORDS = ["达标", "优秀", "完美", "Excellent"]
COVERAGE_FIX_HINTS = {"f_unverified": "跑 batch_check.py 汇总 N>=3 个独立 run，batch.json 里 f_verified 才为真", "fidelity": "安装 resvg 或 cairosvg 提供可信预览；或改 contract/contract.json 的 accepted_by 让判据接受当前取值", "judge": "让宿主 LLM 真读一次预览图并写出 readable=true 的 judge.json", "quick": "关掉 --quick 重跑完整链，或改用 batch 汇总后的 run"}

LITERAL_SCAN_FILES = ["scripts/render_png.py", "scripts/land_vc.py", "scripts/aggregate_log.py", "scripts/iterate.py", "scripts/score.py"]
LITERAL_SCAN_EXCLUDE = ["scripts/_contract.py"]
LITERAL_SCAN_FORBIDDEN = ["faithful", "limited", "unknown", "full"]


class CLAIMS:
    """The outward-promise registry: which files are scanned, which words
    make a sentence a promise, and the coverage floor CI enforces."""
    REGISTRY = "contract/claims.json"
    SCAN_FILES = ["SKILL.md", "README.md"]
    MANDATORY_WORDS = ["应", "必须", "绝不", "不会", "只", "不许", "禁止", "不能", "无法", "一律", "恒"]
    COVERAGE_FLOOR = 1.0


def is_fidelity(value):
    """Is this one of the contract's fidelity values?"""
    return isinstance(value, str) and value in FIDELITY.VALUES


def fidelity_accepted_for(consumer, dim):
    """The fidelity values this consumer accepts for this dimension."""
    return FIDELITY.ACCEPTED_BY.get(consumer, {}).get(dim, ())


def fidelity_for(producer, case):
    """The fidelity value this producer must write for this case."""
    return FIDELITY.PRODUCERS.get(producer, {}).get(case)


def fidelity_is_judged(consumer, dim, value):
    """May this consumer judge this dimension on a preview of this fidelity?"""
    return value in fidelity_accepted_for(consumer, dim)

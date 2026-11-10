#!/usr/bin/env python3
"""Deal three design directions instead of letting the model pick one.

Why: asked to invent a look, a model returns the category default -- the same
centred mark, the same three colours, the same badge. The fix is not a stronger
"be creative" instruction; it is a deck, a history and a rule that the next
round cannot reuse the last two rounds' archetypes.

This deals from references/archetypes (ten marks), a palette deck and five
compositions. It does not draw anything and does not score anything: the host
LLM still has to write the SVG and fill each direction's `why` slot with a
sentence about this brief.

Determinism: same seed + same history => byte-identical deal.json, so a re-run
is reproducible. A different history gives a different deal, which is the point.
A corrupt history is refused (E-SCHEMA) rather than silently reset -- silently
resetting would recreate the repetition this script exists to prevent.
"""
import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import lib_common as L

SCRIPT = "deal.py"
SCHEMA = "vector-ai-studio/deal-v1"
HISTORY_SCHEMA = "vector-ai-studio/deal-history-v1"

# traits are the vocabulary a brief can modulate; the deck is small on purpose
ARCHETYPES = [
    {"id": "swiss-mark", "file": "01-swiss-mark.svg",
     "traits": ["克制", "机构", "系统", "编辑", "理性"],
     "structure": "单一大色块 + 细网格线延伸"},
    {"id": "bauhaus-primary", "file": "02-bauhaus-primary.svg",
     "traits": ["活泼", "文化", "教育", "几何", "基础形"],
     "structure": "圆 / 半圆 / 三角，三原色各一"},
    {"id": "japanese-ma", "file": "03-japanese-ma.svg",
     "traits": ["东方", "安静", "手作", "饮食", "留白"],
     "structure": "一条粗横线 + 一枚朱红，大片留白"},
    {"id": "constructivist-diagonal", "file": "04-constructivist-diagonal.svg",
     "traits": ["力量", "运动", "声明", "粗犷", "对角"],
     "structure": "两条对角粗带对撞"},
    {"id": "isometric-cube", "file": "05-isometric-cube.svg",
     "traits": ["技术", "制造", "空间", "工程", "等轴"],
     "structure": "三个菱形，明度三阶"},
    {"id": "negative-space", "file": "06-negative-space.svg",
     "traits": ["巧思", "克制", "双关", "极简", "负空间"],
     "structure": "实心块内挖出第二形"},
    {"id": "memphis-blocks", "file": "07-memphis-blocks.svg",
     "traits": ["潮流", "消费", "青年", "活泼", "拼贴"],
     "structure": "三色块 + 同组点阵 + 底线"},
    {"id": "editorial-rule", "file": "08-editorial-rule.svg",
     "traits": ["编辑", "出版", "内容", "秩序", "理性"],
     "structure": "粗细对比的横线组 + 实心方块"},
    {"id": "orbit-mark", "file": "09-orbit-mark.svg",
     "traits": ["科技", "社群", "天文", "系统", "圆"],
     "structure": "同心圆 + 一条斜切线"},
    {"id": "pixel-grid", "file": "10-pixel-grid.svg",
     "traits": ["数字", "游戏", "开发", "模块", "技术"],
     "structure": "5x5 模块网格内的抽象字形"},
]

PALETTES = [
    {"id": "ink-red", "colors": ["#111111", "#c8362c"], "traits": ["克制", "东方", "编辑"]},
    {"id": "ink-blue", "colors": ["#111111", "#1f4fd8"], "traits": ["技术", "系统", "工程"]},
    {"id": "paper-ink", "colors": ["#111111", "#f2f2f2"], "traits": ["极简", "出版", "理性"]},
    {"id": "primary-three", "colors": ["#d7263d", "#f2b705", "#1f4fd8"], "traits": ["活泼", "文化", "几何"]},
    {"id": "warm-neutral", "colors": ["#2b2b2b", "#c8362c", "#efe7d6"], "traits": ["手作", "饮食", "温暖"]},
    {"id": "slate-tech", "colors": ["#2f3e5c", "#6b7a99", "#cfd6e4"], "traits": ["技术", "空间", "冷静"]},
    {"id": "mono-vermilion", "colors": ["#111111", "#d7263d", "#ffffff"], "traits": ["力量", "声明", "海报"]},
    {"id": "sage", "colors": ["#2f4f3f", "#8fae8b", "#f4f1e8"], "traits": ["安静", "自然", "生活"]},
    {"id": "navy-sand", "colors": ["#12213f", "#e8dcc3", "#c9a227"], "traits": ["稳健", "机构", "金融"]},
    {"id": "charcoal-lime", "colors": ["#1c1c1c", "#c7f04a"], "traits": ["青年", "运动", "潮流"]},
    {"id": "clay", "colors": ["#5b3a29", "#c97c5d", "#f3e6dd"], "traits": ["手作", "温暖", "食品"]},
    {"id": "double-ink", "colors": ["#111111", "#5a5a5a", "#e6e6e6"], "traits": ["编辑", "克制", "中性"]},
]

COMPOSITIONS = [
    {"id": "centred-mass", "note": "主体居中，靠留白撑住"},
    {"id": "asymmetric", "note": "主体偏一侧，另一侧留给次级形"},
    {"id": "diagonal", "note": "主体沿对角线走，制造动势"},
    {"id": "grid", "note": "所有边界落在模块线上"},
    {"id": "negative-space", "note": "用挖空承担第二层含义"},
]

TONE_WORDS = ["克制", "安静", "活泼", "东方", "技术", "力量", "编辑", "手作",
              "饮食", "潮流", "青年", "机构", "系统", "空间", "巧思", "数字"]


def _tones(prompt):
    return [w for w in TONE_WORDS if w in prompt]


def _fit(item_traits, tones):
    return len(set(item_traits) & set(tones))


def _read_history(path):
    p = Path(path) if path else None
    if not p or not p.is_file():
        return []
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise L.ToolError("E-SCHEMA", "deal history is unreadable: " + str(e),
                          L.EXIT_USAGE)
    if d.get("schema") != HISTORY_SCHEMA or not isinstance(d.get("rounds"), list):
        raise L.ToolError("E-SCHEMA",
                          "deal history has the wrong schema; refusing to reset it",
                          L.EXIT_USAGE)
    rounds = [r for r in d["rounds"] if isinstance(r, list)]
    return rounds[-2:]          # only the last two rounds constrain this one


def _write_history(path, rounds, picked):
    d = {"schema": HISTORY_SCHEMA, "rounds": (rounds + [picked])[-2:]}
    L.write_json_atomic(Path(path), d)


def _pick(rng, pool, n):
    """Deterministic seeded sample that keeps the given order stable."""
    idx = list(range(len(pool)))
    rng.shuffle(idx)
    return [pool[i] for i in idx[:n]]


def main():
    ap = argparse.ArgumentParser(description="deal three design directions")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--out", default="")
    ap.add_argument("--history", default="")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--count", type=int, default=3)
    ap.add_argument("--run-id", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.count < 1 or args.count > len(ARCHETYPES):
        raise L.ToolError("E-USAGE", "--count outside 1..%d" % len(ARCHETYPES),
                          L.EXIT_USAGE)
    spec = L.read_json(args.spec)
    tones = _tones(str(spec.get("prompt") or ""))
    history = _read_history(args.history)
    seen = {a for r in history for a in r}

    def rank(items):
        return sorted(items, key=lambda x: (-_fit(x["traits"], tones), x["id"]))

    pool = [a for a in rank(ARCHETYPES) if a["id"] not in seen]
    if len(pool) < args.count:
        # the deck is smaller than the exclusion window; fall back to the full
        # deck rather than silently repeating a shorter list
        pool = rank(ARCHETYPES)
    rng = random.Random("%d:%d" % (args.seed, len(history)))
    archetypes = _pick(rng, pool, args.count)
    palettes = _pick(rng, rank(PALETTES), args.count)
    comps = _pick(rng, COMPOSITIONS, args.count)

    directions = []
    for i, a in enumerate(archetypes):
        directions.append({
            "archetype": a["id"],
            "reference": "references/archetypes/" + a["file"],
            "structure": a["structure"],
            "palette": {"id": palettes[i]["id"], "colors": palettes[i]["colors"]},
            "composition": comps[i]["id"],
            "composition_note": comps[i]["note"],
            "why": "",
            "risk": "",
        })
    doc = {"schema": SCHEMA, "seed": args.seed, "tones": tones,
           "excluded_from_previous_rounds": sorted(seen),
           "directions": directions,
           "note": "fill why/risk per direction before drawing; empty why means "
                   "the direction was chosen by dice, not by the brief"}
    if args.out:
        L.write_json_atomic(Path(args.out), doc)
    if args.history:
        _write_history(args.history, history, [a["id"] for a in archetypes])
    return L.emit(SCRIPT, status="ok", code=0, run_id=args.run_id,
                  data={"tones": tones, "directions": directions},
                  artifacts={"deal": Path(args.out).name} if args.out else {})


if __name__ == "__main__":
    sys.exit(L.run_cli(SCRIPT, main))

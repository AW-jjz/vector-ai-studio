# 示例（examples）

三个现实口径的需求，各自带三件文件：需求原文与约束（`examples/01-bookshop/brief.md` 形状）、冻结后的必备元素与色板（`examples/01-bookshop/spec.json`）、原创成品（`examples/01-bookshop/final.svg`）。用途：

1. 演示「需求 → 冻结 → 设计」时每一层长什么样；
2. 给 `scripts/studio.py` 一条命令跑通完整链路提供可复现输入；
3. 给盲评提供真实候选（`--review-pack`）。

**边界必须说清**：这三份是示例，不是客户项目，也没有外部评审结论。任何「本技能能做出好设计」的主张，需要把 `templates/design-review.md` 的盲评表交给真人回填后才成立。

每个目录内的文件：

| 文件 | 作用 |
|---|---|
| `examples/01-bookshop/brief.md` | 需求原文、硬约束、明确不要的东西 |
| `examples/01-bookshop/spec.json` | 冻结清单（`scripts/freeze_spec.py` 可校验） |
| `examples/01-bookshop/final.svg` | 成品源文件，240×240，纯矢量，色板内取值 |

跑法（技能根目录）：

```
python scripts/studio.py --spec examples/01-bookshop/spec.json \
  --svg examples/01-bookshop/final.svg --out output/bookshop --profile screen
```

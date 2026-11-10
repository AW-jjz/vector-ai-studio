# vector-ai-studio

一句话把需求做成按可断言判据达到质量阈值、可继续编辑的原生 `.ai` 矢量成品。

## 装好就能用

默认链路只依赖 Python 3.13 标准库，零付费凭据、零联网、零额外组件。安装后先跑：

```
python scripts/detect_deps.py --json
python scripts/selftest.py
python scripts/run_fixtures.py
```

真机冒烟是独立命令（需要本机 VectorCraft CLI）：

```
python scripts/smoke_ai.py
```

引擎不可用时该命令显式降级并留下真实日志，不会假装成功、不会编造 `.ai`。

## 完整出图

主流程见 `SKILL.md`：冻结必备元素清单与风格色板，按 `references/design-language.md` 选型 → 生成结构化纯矢量 SVG → 净化与六维打分 → N 个独立 run 各自内部完成至多 K 轮定向收敛 → 汇总 F → VectorCraft 落地为原生 `.ai` → 聚合验收日志。按需求 §7.5.1 原编排实现，无编排变更。

## 四件产物

- 原生可编辑 `.ai`（由 VectorCraft 产出，附 `info` 重开回读计数）
- 净化后的 `.svg`
- PNG 预览
- 机器可读验收日志（A–F 分数、轮次记录、H1–H4、降级标记）

产物落在运行期目录，打包时排除，不随包提交。

## 需要什么

- 必需：Python 3.13 标准库；VectorCraft CLI（`vectorcraft-cli`，格式底线；可用 `VECTORCRAFT_CLI` 指定路径）。
- 可选增强：PNG 渲染器、Node / svgo、风格参考、商业生成端凭据。
- 引擎、渲染器、宿主读图三项一律先探测，缺失显式列出 `MISSING` 与替代方案并降级，不假设已装。不依赖 Adobe / COM。

## 边界

- 不承诺一次直出即达到阈值；达标由验收日志按 `config/thresholds.json` 机械比较裁定。
- 默认零凭据链路下若宿主读不到渲染图，按 L2 交付：A/B 记 capability_missing 状态枚举（非分数）、grade 不达标、四件产物照常落盘、不阻断交付。
- 取证分工：H1 由 landing-only 轨取证（smoke_ai.py 加 --landing-only），H2/H3/H4 由完整链（默认）取证。
- 不伪造 `.ai`；引擎缺失时明确标注「非原生 `.ai`，格式底线未满足」。
- 路径含中文或空格时，需确认 VectorCraft CLI 能读取该路径；运行期请勿移动源文件。
- 对外说明只承诺「达标」；未达标或降级时首行标注需人工终审。
- 用户可见说明仅此一页；`references/design-language.md` 与 `references/archetypes/` 是设计与生成约束，其余 references 是施工契约，都不是使用手册。
- 单稿链路可压成一条命令：`python scripts/studio.py --spec ... --svg ... --out output/<run-id>`；阶段状态见 `output/<run-id>/studio.json`。

## 自检说明

`scripts/selftest.py` 是纯脚本层，切换 cwd 后仍可用；`scripts/run_fixtures.py` 跑随包回归样例。两者都不需要 VectorCraft CLI 或渲染器，零凭据即可执行。

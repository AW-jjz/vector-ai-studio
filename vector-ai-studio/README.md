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

## 三例示例的证据状态

随包三例（书本店、咖啡、开发者工具）各有一份 review-status.md（位于 examples 各目录），写清它现在是**什么等级的证据**：

- 当前等级一律是 `machine_only`：三个独立模型实例按同一张盲评表打分，表不空、可复核，但**不是外部评审**，不可对外声称「质量已验证」。
- 需求符合度里机械可判的部分（尺寸、色数、色面积上限、线宽档数、16px 与单色可辨、禁用构造）由 `scripts/conformance.py` 按各例的 conformance.json 判定，逐条写进状态文件；规则文件里没写的项进 `unchecked`，不显示成通过。剩下的审美判断（主张是否成立、主次、气质）只能人判。
- 两名真人回收后用 `scripts/review.py` 汇总，`usable_for_claim` 才会为真；在此之前恒为假。请求件在 `templates/review-request.md`。

## 素材政策：两条通道，不建库

- **参考通道**：风格、构图、质感参照可以来自任何素材站，但只进 `source-ledger`（URL、许可、获取日期、用途），**不进入** final.svg / .ai。
- **产出通道**：纹理、笔刷、边框等非标志性构件若要用，必须许可明确允许商用、账本登记、SVG 元素带 `data-source`，并由 `scripts/conformance.py` 校验「有标记必有登记、有登记必有元素、许可在白名单」。
- **标识主体不用现成素材**：撞标与「非原创」风险高，且与「可复制、可断言」的证据链冲突。
- **不下载、不建库**：链接与许可会变，库存的是记录而不是资产；账本模板在 `templates/source-ledger.json`。

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
- 盲评回收用 `python scripts/review.py --sheet ... --key ... --reviewer-kind external`：空表判 `E-REVIEW-EMPTY`、缺格判 `E-REVIEW-INVALID`；只有 ≥2 名外部评审且零空格时 `evidence.usable_for_claim` 才为真。评审请求件在 `templates/review-request.md`。

## 自检说明

`scripts/selftest.py` 是纯脚本层，切换 cwd 后仍可用；`scripts/run_fixtures.py` 跑随包回归样例。两者都不需要 VectorCraft CLI 或渲染器，零凭据即可执行。

---
name: vector-ai-studio
description: 把一句自然语言需求变成按可断言判据达到质量阈值、可继续编辑的原生 .ai 矢量成品；用户说生成矢量图 / logo / 图标 / 插画 / 要可继续编辑 / 要原生 .ai / 装好就能用时触发；只要位图 / 照片级真实感 / 3D、已持有 SVG 仅需转格式、或纯格式考据与可行性调研时不触发。
description_zh: 当用户要求生成可编辑矢量图、原生 .ai 文件或 Illustrator 插画时使用；也覆盖需要批量同风格矢量素材、或用户自带风格参考要求品牌一致性的场景。不适用于只需要位图或照片级真实感、3D、像素级修图、把已有 SVG 仅做格式转换，以及纯格式考据与可行性调研。
description_en: Use when the user wants an editable vector graphic, a native .ai file, or an Illustrator illustration, including batch production of same-style vector assets or brand consistency against a supplied style reference. Do not use it for bitmap or photorealistic output, 3D, pixel retouching, pure format conversion of an existing SVG, or format research and feasibility write-ups.
version: 1.0.0
author: skill-lifecycle-studio
# The four fields above (description_zh / description_en / version / author) are
# required at top level by this project's internal contract (contract.yaml
# v2.0.0), but the public Agent Skills specification only defines name,
# description, license, compatibility, metadata and allowed-tools. author and
# version are mirrored into metadata below so that spec-aware clients find them
# where the specification's own example puts them. selftest asserts the two
# copies stay identical, so the duplication cannot drift.
metadata:
  author: skill-lifecycle-studio
  version: "1.0.0"
  description_zh: 当用户要求生成可编辑矢量图、原生 .ai 文件或 Illustrator 插画时使用；也覆盖需要批量同风格矢量素材、或用户自带风格参考要求品牌一致性的场景。不适用于只需要位图或照片级真实感、3D、像素级修图、把已有 SVG 仅做格式转换，以及纯格式考据与可行性调研。
  description_en: Use when the user wants an editable vector graphic, a native .ai file, or an Illustrator illustration, including batch production of same-style vector assets or brand consistency against a supplied style reference. Do not use it for bitmap or photorealistic output, 3D, pixel retouching, pure format conversion of an existing SVG, or format research and feasibility write-ups.
---

# vector-ai-studio

把一句需求做成按可断言判据达到质量阈值、可继续编辑的原生 `.ai` 矢量成品。第一优先是出图质量闭环，格式落地是底线约束。

## 触发条件

应当触发：

- 用户要生成矢量图 / logo / 图标 / 插画 / 信息图，并明确要「可继续编辑」「原生 `.ai`」成品。
- 用户给出风格参考，要求批量产出风格一致的一组矢量素材。
- 用户说「装好就能用」，要求验证本机能否真机跑通一次完整出图。

不应触发：

- 只要位图 / 照片级真实感 / 3D / 像素级修图。
- 已持有成品 SVG、仅需转换为 `.ai`（属纯格式转换，不启用生成与质量闭环）。
- 纯格式考据、可行性论证、来源对比或尽调报告。
- 本机没有 Illustrator 且不接受「格式底线未满足」的降级说明。

触发词（矢量图 / logo / 图标 / 插画）仅为演示，规则本身适用于任何纯矢量图形领域，不把能力窄化为某一类图形。

## 环境与零门槛声明

- 本技能按 `marketplace` 上架口径立标（假设）；上架字段仅为静态元数据，任何脚本都不读取它们。以 `local` 方式安装时零门槛、无需联网 / 凭据 / 额外组件，默认链路完全相同。
- 默认链路零付费凭据、零第三方付费服务；商业生成端只在用户自行提供凭据时启用，不隐式调用。
- 一切外部依赖（Illustrator COM、PNG 渲染器、宿主 LLM 读图）一律先由 `scripts/detect_deps.py` 探测，显式列出 FOUND / MISSING / UNVERIFIED 与替代方案，不假设已装。

## 工作流程

### 步 0 依赖探测（C5 / H4）

先探测本机，再决定后续分支。缺失项显式列出并走降级，不静默跳过。

```
python scripts/detect_deps.py --json --out path/to/run/deps.json
```

产出 deps.json。凭据环境变量只判断名称是否存在，从不读取值。

### 步 1 冻结必备视觉元素与风格色板（C1）

由宿主 LLM 从用户当次 prompt 逐条写出必备视觉元素清单（≥1 条）、附加属性与风格色板，写成 `templates/spec.template.json` 形状的 JSON，再校验。

```
python scripts/freeze_spec.py --spec path/to/spec.json --validate --out path/to/run/spec.json
```

空 prompt / 空清单 / 畸形 schema 一律显式拒绝并要求补充，不臆造需求。冻结清单与色板全轮次只读。

### 步 2 生成结构化纯矢量 SVG（C1）

宿主 LLM 按 `references/svg-dsl.md` 的冻结清单逐条覆盖、色板内取值、语义分组命名、纯矢量子集约束，直接写出一轮草稿。默认链路零凭据、零联网。

产出 round-k/draft.svg。示例不窄化领域，规则本身与具体题材无关。

### 步 3 净化与静态校验（C3 / H1）

```
python scripts/sanitize_svg.py --in path/to/round/draft.svg --out path/to/round/sanitized.svg
```

确定性检出位图、XML 错误、禁用构造、退化路径并计数语义分组。违规即显式报错退回重生成，不擅自删改语义元素（删改会破坏 A 项命中，语义改稿归 LLM）。带 `--prev` 时比对通过项结构，改动则输出 `E-REGRESSION` 并要求重生成。

### 步 4 渲染预览（C2 / C3）

```
python scripts/render_png.py --in path/to/round/sanitized.svg --out path/to/round/preview.png
```

渲染器按优先级探测；全部 MISSING 时用内置最小 SVG→PNG 兜底，产物必须是真实可解析 PNG（非零字节、PNG 魔数、可读宽高、IEND 完整），并标 `renderer: builtin-fallback` 与「预览保真度受限」。零字节或占位文件一律判 PNG 缺失。兜底图不足以支撑 A/B 读图时自动转 L2，不伪造分数。

### 步 5 二值判读的校验与统计（A / B）

宿主 LLM 读预览图，按冻结清单逐条给命中 / 未命中、主体是否相悖、主体完整 / 无明显失衡 / 对齐一致，写成逐条二值的判读文件；脚本只校验 schema、统计命中率，绝不自行判读语义。

```
python scripts/llm_judge.py --judge path/to/round/judge.json --validate --spec path/to/run/spec.json --out path/to/round/judge.json
```

宿主读图不可用时输出 `degraded`（L2），不伪造分数。

### 步 6 六维确定性打分（C3 铁律）

```
python scripts/score.py --svg path/to/round/sanitized.svg --judge path/to/round/judge.json --spec path/to/run/spec.json --out path/to/round/score.json
```

B 几何、C 色值线宽集合、D 路径结构、E 四类 token 的 Jaccard、F 统计全部由脚本计算；A 消费步 5 的命中率统计。所有阈值只从 `config/thresholds.json` 读取，脚本零硬编码。LLM 不得自行写分数。E 无参考时记 N/A，不计入总分。

#### C 维的感知测量（`scripts/perceptual.py`）

C 维除了数颜色，还测三件互不相同的事，分开报告，绝不揉成一个「色彩分」——揉在一起就看不出是哪一件坏了：

| 测什么 | 用什么 | 为什么是它 |
|---|---|---|
| 两个色是不是同一个色 | CIEDE2000 < 2.3 | 这是**小色差**问题，ΔE00 正是为小色差拟合的 |
| 声明要区分的色是否真的拉开 | HyAB < 10 | 这是**大色差**问题，此时 ΔE00 会失真 |
| 前景/背景能不能读 | WCAG 2.1 对比度 + 三种色觉缺陷模拟取最小值 | 见下 |

**对比度的作用域是刻意收窄的。** WCAG 2.1 SC 1.4.11 只约束 "parts of graphics required to understand the content"，并明确豁免纯装饰。所以只有在 spec 里**显式声明** `contrast_pairs` 时才检查，未声明记 `not_applicable`——「没检查」不能显示成「通过」。`role` 取 `text`（走 SC 1.4.3）、`meaning`（走 SC 1.4.11）或 `decorative`（豁免，但仍记录实测比值，便于复核豁免是否正当）。

**色觉缺陷这一层是本项目扩展，不是标准。** WCAG 不要求模拟色觉缺陷。加它是因为正常视觉达标而色盲几乎看不见的配色大量存在：#FF0000 在黑底上 WCAG 判 5.25 通过 AA，红色盲视角只有 1.26。`cvd_contrast_floor` 在配置里标注「无标准依据、未标定」。

**这一轮只测量、不判失败。** 前两个阈值没有权威出处（配置里 `_src_*` 已写明），拿未标定的数字当闸门，等于把噪声点成红灯。等 24 个 fixture 的真实分布出来再定切点。

`perceptual.py` 自带参考向量自检：Sharma 等 (2005) 的 34 组 CIEDE2000、四个 WCAG 对比度锚点、两个色觉缺陷模拟值。任一不符即报错——向量对不上说明公式或矩阵抄错了，此时**调参数去凑是最坏的处理方式**。


### 步 7 每个独立 run 内部完成 C2 收敛（按需求 §7.5.1 原编排）

N 个 run 各自独立生成（严禁共享生成结果），每个 run 内部独立执行步 2–6 的 A–D/E 与 H1，并按 L1 做至多 K 轮定向收敛（C2 早退：该 run 一旦达标即停止后续收敛轮）；K 是每 run 上限，不是全局上限。达标档 N=3、内部更高档 N=5，生成次数上界由 `config/thresholds.json` 的 F.N 与迭代轮上限运行时派生（达标档 9、内部更高档 15），阈值零改动。

```
python scripts/iterate.py --run run-1 --round 1 --score path/to/f_source_runs/run-1/score.json --spec path/to/run/spec.json --out path/to/c2_revisions/run-1/round-1/revision.json
```

revision.json 的 `change_only` 只含失败项，`must_keep` 锁定通过项结构；宿主 LLM 读它回到步 2 只改失败项，脚本不写自然语言改稿内容。收敛轮明细写入 c2_revisions/run-i/，与 F 取证源 f_source_runs/run-i/ 物理隔离。

### 步 8 F 项汇总（N 个收敛后的 run）

```
python scripts/batch_check.py --runs-dir path/to/run --out path/to/run/batch.json
```

F 统计只从 f_source_runs 按 run 取数，C2 收敛产物严禁跨 run 回填 F 统计；脚本校验一一对应与 SVG 哈希隔离，违规报 `E-F-DATA-ISOLATION`。汇总 generation_count 与 generation_budget，超界报 `E-BUDGET-EXCEEDED`。取「A–E 综合最高且通过 H1」的 run 套 §7.3 分档并作为最终交付文件，其余保留为批量样本。缺任一 run 显式列出 `E-BATCH-INCOMPLETE`，不按默认通过补齐。`--quick` 为单 run 显式预览、不参与达标判定，该模式 `f_verified: false`、首行标注「F 未验证，不宣称达标」，违例报 `E-QUICK-UNVERIFIED`。

按需求 §7.5.1 原编排实现，无编排变更；阈值一个都不改。详见 `references/pipeline-contract.md`。

### 步 9 本机 Illustrator 落地（C4 / H1 / H2）

```
python scripts/land_ai.py --svg path/to/round/sanitized.svg --out path/to/run --deps path/to/run/deps.json
```

`scripts/land_ai.py` 运行时替换 `scripts/ai_bridge.jsx` 的占位符，经 `scripts/ai_bridge.ps1` 的 COM 通道执行：打开 SVG → 结构断言 → `saveAs()` 产出原生 `.ai` → 重新打开并重读对象计数。断言 `rasterItems=0`、`placedItems=0`、`pathItems>0` 且画板与 viewBox 一致。绝不把 SVG 改后缀、绝不生成 PDF 容器冒称原生可编辑 `.ai`。

**交付档位**：落地前先明确客户要什么，不要用默认值赌。

```
--profile screen     屏幕交付（默认）：PDF 兼容、RGB、无出血
--profile print      印刷交付：PDF 兼容 + 请求嵌入 ICC + 逐对象转 CMYK + 3mm 出血与裁切线
--profile handoff    客户改稿：把顶层分组建成命名图层，便于分件开关
```

单项可覆盖：`--compat` / `--pdf-compatible` / `--embed-icc` / `--cmyk` / `--bleed-mm` / `--layers-from-groups`。所有保存选项都显式写入 `IllustratorSaveOptions`，不依赖 Illustrator 默认值——`pdfCompatible` 默认恰好为真、`embedICCProfile` 默认恰好为假，靠默认值等于把交付质量交给安装环境。

落地成功会额外产出交付清单 deliverable.json（见输出规范）。其中的 `unverified` 字段记录「要求了但没兑现」的项：例如请求了 CMYK 而产物未以 CMYK 着色时，这条差异会连着实测到的算子计数写进清单，代码不会假装成功。

**色彩模式的判定依据是内容流算子，不是资源字典。** 未声明的 `k` 操作数按 PDF 规范即 DeviceCMYK；只数 `/DeviceCMYK` 会把一份合格的 CMYK 文件误判成 RGB——这在两条引擎上都发生过。

### 备选引擎：VectorCraft（不依赖 Illustrator）

```
python scripts/land_vc.py --svg path/to/final.svg --out path/to/run --profile print
```

`scripts/land_vc.py` 走 VectorCraft 的 CLI（`vectorcraft-cli`），不需要 Illustrator、不需要 COM、跨平台。档位行为：

| 档位 | 行为 |
|---|---|
| `screen` | 导出 PDF 兼容 `.ai`（RGB） |
| `print` | 先 `file.documentColorMode {mode:"cmyk",convert:true}`，再导出 `.ai` **与** `.pdf` |
| `handoff` | 同 screen；SVG 的 `<g>` 自动成为命名图层 |

**两条引擎是并行关系，不是替代**：产出同一套产物与同一份交付清单契约，`engine` 字段记录实际用的是哪一个，一条路出问题不会污染另一条。

实测差异（vectorcraft 0.22.0，2026-11-08）：

- **CMYK 在这条路上是真的**（内容流出现 k/K 算子、rg/RG 归零）；Illustrator 脚本路径做不到，PDF 层始终写 RGB
- **文字在 Illustrator 里会变成轮廓**：实测同一份产物在 VectorCraft 中 `kinds.Type=2`（文字对象仍在、字体仍挂着），在 Illustrator 中 `textFrames=0`、`pathItems` 增加。**能否改字取决于客户用哪个软件打开**；以 Illustrator 为交付环境时应改走 Illustrator 引擎保住活字。清单会写明这一条
- **字体缺失会被静默替换**：源文件的字体若本机没有，产物用回退字体渲染。工具会报出来，清单照抄进 `warnings`
- **图层不完全可靠**：5 个「只含单个元素」的 `<g>` 实测生成 5 个命名图层；另两个「`<g>` 内含多个子元素」的复杂样本都只有 1 层——**其中一个并没有 defs 也没有剪切蒙版**，所以塌层与它们无关。规律尚未确认，清单里的层数取自文件的 /OCGs，是实测值、不是承诺值
- 产物**不含输出意图与 ICC**：是 CMYK、不是 PDF/X，印刷条件未声明
- 不支持出血，`--bleed-mm` 仅对 Illustrator 引擎有效

**这两条（文字转轮廓、字体替换）都是复杂样本才暴露的**，只拿干净的小图测会漏掉——所以换引擎前必须用带文字、渐变、剪切蒙版的样本压一遍。

`scripts/render_png.py` 的渲染器按 resvg → cairosvg → 无头浏览器 → **Illustrator 导出** → 内置兜底依次尝试；其中 Illustrator 导出由 `scripts/render_export.jsx` 经同一个 `scripts/ai_bridge.ps1` 通道执行 `exportFile(..., ExportType.PNG24, ...)`。Illustrator 本来就是产出原生 `.ai` 的硬依赖，因此只要能交付 `.ai`，就必然能导出与 `.ai` 一致的忠实预览，无需额外安装第三方渲染器。

### 步 10 验收日志聚合（H2 / H3 / H4）

```
python scripts/aggregate_log.py --run-dir path/to/run --out path/to/run/acceptance.json
```

读全部轮次与 run 产物，汇总 A–F 分数、轮次记录、H1–H4 结论与降级标记，按 `config/thresholds.json` 机械比较得出分档（数值判定，LLM 不得宣布）。四件不齐即 `E-H2-INCOMPLETE` 并列出缺哪件。校准记录 `calibration_log` 做前后值与裁决引用一致性断言。对外说明用 `templates/delivery-note.template.md` 生成，只承诺「达标」，内部评分字段不外露。

两条证据轨，不得混用：默认是**完整链**（`mode=full`、`grading_entered=true`，H1–H4 全判并进入评级）；`--landing-only` 是**只取 H1 证据的窄轨**（`mode=landing-only`，h2、tier、grade、h3、h4 全为 `null`，`h2_status=not_evaluated`，不进入评级），仅用于「机器上只跑得动落地、跑不动完整链」时留存原生 `.ai` 结构证据。伪造 `mode=full` 却带 `grading_entered=false`，或 `landing-only` 却带档位词，一律 `E-EXEMPTION-ABUSE`。

### 步 11 装好即用自检（C5）

```
python scripts/selftest.py --fixtures --log-dir <RUN_DIR>
python scripts/run_fixtures.py
python scripts/smoke_ai.py
```

H3 的取证文件是 `<RUN_DIR>/selftest.json`，由上面第一条命令产生（`--log-dir` 必须指向运行目录）；不传 `--log-dir` 时日志落在默认输出根下，`scripts/aggregate_log.py` 找不到它，H3 恒不通过。也可用 `--selftest-log <path>` 显式指定。

`scripts/selftest.py` 是纯脚本层，零外部依赖必能跑；`scripts/run_fixtures.py` 跑随包 good / bad 回归；`scripts/smoke_ai.py` 是独立的真机冒烟命令，真实尝试 COM，不可用按 L4 显式降级并留真实日志，绝不编造 `.ai`。

## 命令速查

全部命令在技能根目录执行；脚本以自身位置解析路径，切换 cwd 不影响结果。

### 探测与自检

```
python scripts/detect_deps.py --json
python scripts/selftest.py --fixtures --log-dir <RUN_DIR>
python scripts/run_fixtures.py
python scripts/smoke_ai.py
```

### 结果契约校验

```
python scripts/schema_check.py references/schemas/acceptance.schema.json <RUN_DIR>/acceptance.json
python scripts/schema_check.py references/schemas/deliverable.schema.json <RUN_DIR>/deliverable.json
```

`references/schemas/` 下是两份 JSON Schema（draft 2020-12）：验收日志与交付清单。字段契约从散文变成了机器可校验的东西，任何消费方都能独立判定一份日志合不合法，不必相信产出方。`scripts/schema_check.py` 是零依赖的子集校验器（type / enum / required / patternProperties / anyOf 等），遇到不认识的 schema 关键字会**报错而不是静默忽略**——静默忽略会让校验器变成装饰。

### 冻结与净化

```
python scripts/freeze_spec.py --spec path/to/spec.json --validate --out path/to/run/spec.json
python scripts/sanitize_svg.py --in path/to/draft.svg --out path/to/round/sanitized.svg
python scripts/render_png.py --in path/to/round/sanitized.svg --out path/to/round/preview.png
```

### 判读、打分与收敛

```
python scripts/llm_judge.py --judge path/to/round/judge.json --validate --spec path/to/run/spec.json
python scripts/score.py --svg path/to/round/sanitized.svg --judge path/to/round/judge.json --spec path/to/run/spec.json --out path/to/round/score.json
python scripts/iterate.py --round 1 --score path/to/round/score.json --spec path/to/run/spec.json --out path/to/round/revision.json
python scripts/batch_check.py --runs-dir path/to/run --out path/to/run/batch.json
```

### 落地与聚合

```
python scripts/land_ai.py --svg path/to/round/sanitized.svg --out path/to/run --deps path/to/run/deps.json
python scripts/aggregate_log.py --run-dir path/to/run --out path/to/run/acceptance.json
```

快速预览用 `scripts/aggregate_log.py --quick`；只取 H1 证据、不评级用 `scripts/aggregate_log.py --landing-only`；半自动落地用 `scripts/land_ai.py --semi-manual`（仅 COM 不可驱动时由用户显式选择）与 `scripts/land_ai.py --collect`。

## 输出规范

四件产物落在运行期目录，**默认在技能包之外**：`~/.vector-ai-studio/output/<run-id>/`，可用环境变量 `VECTOR_AI_STUDIO_OUT` 覆盖。包内不留任何运行期文件，打 zip 不会带出测试残留。

```
~/.vector-ai-studio/output/<run-id>/final.ai
~/.vector-ai-studio/output/<run-id>/final.svg
~/.vector-ai-studio/output/<run-id>/preview.png
~/.vector-ai-studio/output/<run-id>/acceptance.json
~/.vector-ai-studio/output/<run-id>/deliverable.json
```

交付清单 deliverable.json 是**给客户的**，与验收日志分开：它不评价好坏，只记录物件本身的可核事实（色彩空间、ICC、出血、图层、字体、位图、画板 pt/mm、sha256）与必须说明的风险（第三方重存会剥离 AIPrivateData、CMYK 未做色彩管理等）。每一项都是实测值，unverified 字段专门记录「要求了但没兑现」的条目——例如要求转 CMYK 而 PDF 兼容层仍写 RGB 时会出现在这里，而不是被略过。

- final.ai 由 Illustrator `saveAs()` 产出，附重开回读计数；final.svg 与它同源；preview.png 为真实可解析 PNG；acceptance.json 含 A–F 各项分数、轮次记录、H1–H4 与降级标记。
- 阈值、分档、降级文案的唯一事实源是 `config/thresholds.json`；`calibration_policy` 登记可校准白名单与审批流，判据定义类一律锁定，任何放宽必须标 `loosening: true` 且带裁决引用。
- 判定类 JSON 不含 wall-clock，同一输入两次执行逐字节一致；时间只进 append-only 运行日志。
- 验收日志另含 generation_count / generation_budget（运行时派生）、f_source_runs / c2_revisions / f_data_isolated、f_verified、needs_human_review、reason、h1 至 h4 等强制字段，供 QA 机械核对。
- 对外说明由 `templates/delivery-note.template.md` 生成，只消费分档为「达标」的结论；未达标或降级时首行显式标注需人工终审，禁止宣称达标 / 完美。
- 轮次与批量样本保留在各轮目录，供 F 项与人工复核追溯。

## 降级与边界

- L0 默认零凭据本地链路；L1 质量未达标进入定向收敛；L2 宿主读图不可用时 A/B 记 N/A (capability_missing)（状态枚举、非分数，禁用几何量折算，违例 `E-FABRICATED-SCORE`），仍按 §7.3 机械比较得出「不达标」、不引入额外档位；强制 degraded / needs_human_review / reason 为 capability_missing、首行「语义 / 构图判读未取证，需人工终审」；四件产物含原生 `.ai` 照常落盘、交付不阻断；L3 商业增强需用户自行提供凭据；L4 依赖缺失；L5 无风格参考时 E 记 N/A。
- COM 不可驱动：默认 L4 全自动降级，H1 判不达标，仅交付净化 SVG 与 PNG 并标注「非原生 `.ai`」。用户可显式选择半自动路径，在本机 Illustrator 内手动执行一次 `scripts/ai_bridge.jsx`，仍由 `saveAs()` 产出真 `.ai`；日志强制 `automation: semi-manual`、degradation.level 为 L4、`status: degraded`，并写明「需人工在 Illustrator 内执行一次脚本」，禁止宣称全自动或装好即用无人工。半自动绝不静默触发。

## 注意事项

- 所有脚本以 `scripts/lib_common.py` 的 `skill_root()` 解析路径，配置 / references / fixtures / 运行期目录都相对技能根，禁止依赖调用时 cwd。
- 不伪造 `.ai`、不承诺一次直出即达到阈值；达标由 `scripts/aggregate_log.py` 按配置机械比较裁定。
- 宿主 LLM 只负责语义生成与二值判读，数值 / 集合 / 结构判定全部由确定性脚本产出。

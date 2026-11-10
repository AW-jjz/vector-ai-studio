---
name: vector-ai-studio
description: 把一句自然语言需求变成按可断言判据达到质量阈值、可继续编辑的原生 .ai 矢量成品；用户说生成矢量图 / logo / 图标 / 插画 / 要可继续编辑 / 要原生 .ai / 装好就能用时触发；只要位图 / 照片级真实感 / 3D、已持有 SVG 仅需转格式、或纯格式考据与可行性调研时不触发。
description_zh: 当用户要求生成可编辑矢量图、原生 .ai 文件或 Illustrator 插画时使用；也覆盖需要批量同风格矢量素材、或用户自带风格参考要求品牌一致性的场景。不适用于只需要位图或照片级真实感、3D、像素级修图、把已有 SVG 仅做格式转换，以及纯格式考据与可行性调研。
description_en: Use when the user wants an editable vector graphic, a native .ai file, or an Illustrator illustration, including batch production of same-style vector assets or brand consistency against a supplied style reference. Do not use it for bitmap or photorealistic output, 3D, pixel retouching, pure format conversion of an existing SVG, or format research and feasibility write-ups.
version: 1.1.0
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
  version: "1.1.0"
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
- 本机没有 VectorCraft CLI 且不接受「格式底线未满足」的降级说明。

触发词（矢量图 / logo / 图标 / 插画）仅为演示，规则本身适用于任何纯矢量图形领域，不把能力窄化为某一类图形。

## 环境与零门槛声明

- 本技能按 `marketplace` 上架口径立标（假设）；上架字段仅为静态元数据，任何脚本都不读取它们。以 `local` 方式安装时零门槛、无需联网 / 凭据 / 额外组件，默认链路完全相同。
- 默认链路零付费凭据、零第三方付费服务；商业生成端只在用户自行提供凭据时启用，不隐式调用。
- 一切外部依赖（VectorCraft CLI、PNG 渲染器、宿主 LLM 读图）一律先由 `scripts/detect_deps.py` 探测，显式列出 FOUND / MISSING / UNVERIFIED 与替代方案，不假设已装。

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

### 步 1.5 选型与设计语言（C1 质量前置）

冻结清单之后、动笔之前，先读 `references/design-language.md`，从第 8 节的十个原型里选一个，并写清「为什么是它」。原型范例在 `references/archetypes/`，只作语法参照，直接改色改形复用只会得到「像范例但不如范例」的结果。

这一步决定成品上限。跳过它，后面的净化与打分只能保证「不出错」，不能保证「好看」。人评口径见该文件第 10 节，盲评表模板是 `templates/design-review.md`。

### 步 1.6 发牌：让三轮不撞原型（C1 质量前置）

直接问模型「给三个方向」，得到的是同一张居中徽章换三种颜色。改法是发牌：

```
python scripts/deal.py --spec path/to/run/spec.json --out path/to/run/deal.json \
  --history path/to/run/deal-history.json --seed 7
```

从 `references/archetypes/` 的十个原型、十二套色板、五种构图里各发三张，并按历史排除**最近两轮**用过的原型——窗口是轮次，不是「原则上尽量不重复」。同一 seed 加同一历史必得同一份 `output/<run-id>/deal.json`，重跑可复现；历史文件损坏或 schema 不符时以 `E-SCHEMA` 退出 2，**拒绝静默重置**（重置会重新造出它本来要防的重复）。

每个方向留了 `why` / `risk` 两个空槽，必须由人按当次 brief 填满再动笔。空 `why` 意味着方向是骰子选的，不是 brief 选的。

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

### 步 4.5 小尺寸、灰度、剪影三检（C1 质量前置）

```
python scripts/legibility.py --png path/to/round/preview.png --out path/to/round/legibility.json
```

240px 预览看不出的三种失效：16px 时墨没了、16px 时整块糊成实心、前景与背景灰度太近。三种都在 favicon、烫印、单色印刷上现形。读 PNG 而不是读 SVG，所以与渲染器无关。

硬门只有三条，且每条都有外部依据或定义即失效：对比度低于 `C.contrast_graphic_ratio`（WCAG 2.1 SC 1.4.11）判 `LOW-CONTRAST`；16px 覆盖率为 0 判 `COLLAPSED`；16px 覆盖率满格判 `MUD`（与前者互为镜像，两种都没有剪影）。`FRAGMENTED` 与 `COVERAGE-DRIFT` 只报告、不判失败——那两个门限是工程值，未标定。

Pillow 未装时显式降级（exit 3 + `E-RENDERER-MISSING`），不从 SVG 猜。

### 步 4.6 需求符合度（机械可判的那一半）

```
python scripts/conformance.py --svg path/to/round/sanitized.svg \
  --rules path/to/brief/conformance.json --out path/to/round/conformance.json
```

盲评不给评审人 brief，所以「是否符合需求」不能在评分表里判。但它大部分不是审美问题：画板尺寸、允许的颜色集合、颜色面积上限、允许的描边宽度、允许的图元种类、图元数量区间、最小留白、禁用构造，都能从 SVG 直接读出来。规则文件是 brief 硬约束的结构化写法——人写一次数字，脚本每轮核对，后续改稿无法悄悄偏离需求。

判定只看规则文件里写了的项，没写的进 `unchecked`，不把「没检查」显示成「通过」。有任一检查失败即 `E-CONFORMANCE` 退出 1，逐条列出 id 与实测值。语义层（「不要画咖啡豆」到底画没画）它判不了，仍归人。

**素材走两条通道，都不建库。** 参考素材（风格、构图、质感参照）只进账本、不进产物；要进产物的构件必须有 URL、许可、许可声明页与核实日期，外加对应元素 id，账本模板是 `templates/source-ledger.json`。规则文件里加 `source_ledger` 指向账本、`licenses_allowed` 列可接受许可后，脚本会查五件事：带 `data-source` 的元素必须在账本里有条目、产出条目的许可必须在白名单内、账本写的元素 id 必须真的存在于 SVG、非 `own` 条目必须给出 `license_source`（许可声明页，不是素材下载页）、`license_verified_at` 必须是合法 `YYYY-MM-DD`。

`licenses_allowed` 用显式许可名（cc0、mit、cc-by-4.0、ofl-1.1 …），不要写 `commercial`——那是用途不是许可，会把需署名与免署名混成一类。`own` 条目豁免后两项：自产内容没有对外许可声明页，强制填 URL 只会逼人编造。过期复核不进脚本：判「是否过期」需要当天日期，同一输入一年后结果不同，那是人的复核动作，不是闸门。站点选择与核实方法见 `references/sources-whitelist.md`。

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

**每个失败维必须带一句具名判决和一个动作。** `output/<run-id>/round-1/score.json` 与信封的 `verdicts` 字段由 `VERDICT_ACTIONS` 映射产出：`OVER-COLOURED` 会附上「9 colours > 8」和「合并最接近的两色」。判决表是既有阈值的一层映射，不新增任何数字，也不改分数；没有失败维时只输出一条 `PASS`，不输出空数组——「没有判决」和「没有失败」必须能区分。

**C 维的感知测量（`scripts/perceptual.py`）**

C 维除了数颜色，还测三件互不相同的事，分开报告，绝不揉成一个「色彩分」——揉在一起就看不出是哪一件坏了：

| 测什么 | 用什么 | 为什么是它 |
|---|---|---|
| 两个色是不是同一个色 | CIEDE2000 < 2.3 | 这是**小色差**问题，ΔE00 正是为小色差拟合的 |
| 声明要区分的色是否真的拉开 | HyAB < 10 | 这是**大色差**问题，此时 ΔE00 会失真 |
| 前景/背景能不能读 | WCAG 2.1 对比度 + 三种色觉缺陷模拟取最小值 | 见下 |

**对比度的作用域是刻意收窄的。** WCAG 2.1 SC 1.4.11 只约束 "parts of graphics required to understand the content"，并明确豁免纯装饰。所以只有在 spec 里**显式声明** `contrast_pairs` 时才检查，未声明记 `not_applicable`——「没检查」不能显示成「通过」。`role` 取 `text`（走 SC 1.4.3）、`meaning`（走 SC 1.4.11）或 `decorative`（豁免，但仍记录实测比值，便于复核豁免是否正当）。

**色觉缺陷这一层是本项目扩展，不是标准。** WCAG 不要求模拟色觉缺陷。加它是因为正常视觉达标而色盲几乎看不见的配色大量存在：#FF0000 在黑底上 WCAG 判 5.25 通过 AA，红色盲视角只有 1.26。`cvd_contrast_floor` 在配置里标注「无标准依据、未标定」。

**这一轮只测量、不判失败。** 前两个阈值没有权威出处（配置里 `_src_*` 已写明），拿未标定的数字当闸门，等于把噪声点成红灯。等 24 个 fixture 的真实分布出来再定切点。

`scripts/perceptual.py` 自带参考向量自检：Sharma 等 (2005) 的 34 组 CIEDE2000、四个 WCAG 对比度锚点、两个色觉缺陷模拟值。任一不符即报错——向量对不上说明公式或矩阵抄错了，此时**调参数去凑是最坏的处理方式**。

但要分清这两个锚点的**强度不同**：CIEDE2000 与 WCAG 的期望值是**外部**权威数据，它们能证明实现正确；而色觉缺陷那两个值是同一个矩阵乘纯红的产物，它只能证明"矩阵没有被改过"，**不能证明矩阵是从论文抄对的**。两者都叫"自检"会让人误以为后者也有外部背书，所以代码里把它们命名为 `CVD_DRIFT_PINS`（漂移守卫）而不是 anchors。


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

### 步 9 落地为原生 `.ai`（C4 / H1 / H2）

```
python scripts/land_vc.py --svg path/to/round/sanitized.svg --out path/to/run --profile print
```

`scripts/land_vc.py` 走 VectorCraft 的 CLI（`vectorcraft-cli`）：不需要 Adobe、不需要 COM、不需要 Windows 桌面。流程是 `run --in SVG --export final.ai [--export final.pdf]`，再用 `info` 重新打开产物读回结构，最后由 `scripts/lib_pdf.py` 直接测量文件字节。断言 `images=0`、`paths>0` 且画板与 viewBox 一致（容差 1pt）。绝不把 SVG 改后缀、绝不把没有结构回读的文件冒称合格 `.ai`。

**交付档位**：落地前先明确客户要什么，不要用默认值赌。

| 档位 | 行为 |
|---|---|
| `screen` | 导出 PDF 兼容 `.ai`（RGB） |
| `print` | 先 `file.documentColorMode {mode:"cmyk",convert:true}`，再导出 `.ai` **与** `output/<run-id>/final.pdf` |
| `handoff` | 同 screen；SVG 的 `<g>` 自动成为命名图层 |

落地成功会额外产出交付清单 `output/<run-id>/deliverable.json`。其中 `unverified` 记录「要求了但没兑现」的项：例如请求了 CMYK 而产物未以 CMYK 着色时，差异会连着实测算子计数写进清单，代码不会假装成功。

**色彩模式的判定依据是内容流算子，不是资源字典。** 未声明的 `k` 操作数按 PDF 规范即 DeviceCMYK；只数 `/DeviceCMYK` 会把一份合格的 CMYK 文件误判成 RGB。

**印刷母版**由 `scripts/land_craft.py` 产出：同一引擎，额外给 PDF/X-1a / X-3 / X-4、裁切线、出血与 TrimBox/BleedBox 实测。

```
python scripts/land_craft.py --svg path/to/sanitized.svg --out path/to/run \
  --profile print --pdf-standard pdfX1a --bleed-mm 3
```

两个落地脚本共用 `scripts/lib_pdf.py` 的 PDF 解析器与 landing 契约；清单字段各自构造（都以 `references/schemas/deliverable.schema.json` 为准），同一份 PDF 上的算子计数不允许出现两个答案。

实测限制（vectorcraft 0.22.0，2026-11-08）：

- **CMYK 是真的**（内容流出现 k/K 算子、rg/RG 归零）
- **文字在 Illustrator 里会变成轮廓**：同一份产物在 VectorCraft 中 `kinds.Type>0`，在 Illustrator 中 `textFrames=0`、路径增加。能否改字取决于客户用哪个软件打开；清单会写明这一条
- **字体缺失会被静默替换**：源文件字体本机没有时，产物用回退字体渲染。工具会报出来，清单照抄进 `warnings`
- **图层不完全可靠**：5 个「只含单个元素」的 `<g>` 实测生成 5 个命名图层；两个「`<g>` 内含多个子元素」的样本只有 1 层（均无 defs、无剪切蒙版）。规律尚未确认，清单层数取自文件 /OCGs，是实测值
- `.ai` 本身不含输出意图与 ICC：是 CMYK、不是 PDF/X；需要印刷条件时走 `scripts/land_craft.py`
- 这些差异只在带文字、渐变、剪切蒙版的复杂样本上暴露，换版本前必须压一遍

`scripts/render_png.py` 的渲染器按 resvg → cairosvg → 内置兜底依次尝试（无头浏览器只记 MISSING，不实际调用）。`scripts/land_vc.py` 自己会先用 VectorCraft 导出预览；失败时才回落到 `scripts/render_png.py`。两个落地脚本在**调用引擎之前**先清掉本轮目标产物（`output/<run-id>/final.ai`、`output/<run-id>/print.pdf`、`output/<run-id>/preview.png` 与它的 meta），所以「文件存在」只代表**本轮**写的——CLI 退出 0 却没写文件这种静默失败不会被上一轮产物顶替。任何失败出口再清一次旧 `output/<run-id>/structure_readback.json` 与预览：`land_vc` 在回落渲染前清，`land_craft` 在失败收尾清（它不做 render_png 回落）。两路都失败就**没有预览**（H2 记四件不齐），不会拿上一版设计的图顶替本轮。`land_vc` 产出的 `output/<run-id>/preview.meta.json` 如实标注 renderer 与 fidelity。

### 步 10 验收日志聚合（H2 / H3 / H4）

```
python scripts/aggregate_log.py --run-dir path/to/run --out path/to/run/acceptance.json
```

读全部轮次与 run 产物，汇总 A–F 分数、轮次记录、H1–H4 结论与降级标记，按 `config/thresholds.json` 机械比较得出分档（数值判定，LLM 不得宣布）。四件不齐即 `E-H2-INCOMPLETE` 并列出缺哪件。校准记录 `calibration_log` 做前后值与裁决引用一致性断言。对外说明用 `templates/delivery-note.template.md` 生成，只承诺「达标」，内部评分字段不外露。

两条证据轨，不得混用：默认是**完整链**（`mode=full`、`grading_entered=true`，H1–H4 全判并进入评级）；`--landing-only` 是**只取 H1 证据的窄轨**（`mode=landing-only`，h2、tier、grade、h3、h4 全为 `null`，`h2_status=not_evaluated`，不进入评级），仅用于「机器上只跑得动落地、跑不动完整链」时留存原生 `.ai` 结构证据。伪造 `mode=full` 却带 `grading_entered=false`，或 `landing-only` 却带档位词，一律 `E-EXEMPTION-ABUSE`。

### 步 10.5 盲评回收与证据分级（C1 质量后验）

```
python scripts/review.py --sheet path/to/reviewer-1.csv --sheet path/to/reviewer-2.csv \
  --key path/to/review/key.json --reviewer-kind external --out path/to/review/review-summary.json
```

盲评包发出去、表收回来之后，先过这一关。它只做两件事：算人不该手算的，以及拒绝不该被算的。

- 空白格不是 0 分，也不是通过：整表全空 → `E-REVIEW-EMPTY`；缺一格或越界 → `E-REVIEW-INVALID`，都退出 1。
- 每个 case 按 `templates/design-review.md` 的结论写法判 `pass / conditional / fail`，阈值在 `config/thresholds.json` 的 `R` 块，与那张表同源。
- 评审人之间的完全一致率会被算出来并列出分歧格；一致率低只报警告，不参与判定——两把尺子不同时均值没有解释力，但也不能因此判图不合格。
- `evidence.usable_for_claim` 只在「评审人 ≥2 且 `--reviewer-kind external` 且零空格」时为真。`internal`（自己人）与 `machine`（模型评审）都能归档成记录，但永远不会被提升为对外证据。

AI 不写分数，也不代替评审。这个脚本读的是人写下的格子，空着就判空。

### 步 11 装好即用自检（C5）

```
python scripts/selftest.py --fixtures --log-dir <RUN_DIR>
python scripts/run_fixtures.py
python scripts/smoke_ai.py
```

H3 的取证文件是 `<RUN_DIR>/selftest.json`，由上面第一条命令产生（`--log-dir` 必须指向运行目录）；不传 `--log-dir` 时日志落在默认输出根下，`scripts/aggregate_log.py` 找不到它，H3 恒不通过。也可用 `--selftest-log <path>` 显式指定。

`scripts/selftest.py` 是纯脚本层，零外部依赖必能跑；`scripts/run_fixtures.py` 跑随包 good / bad 回归；`scripts/smoke_ai.py` 是独立的真机冒烟命令，真实探测并调用 VectorCraft 落地，引擎不可用按 L4 显式降级并留真实日志，绝不编造 `.ai`。

## 命令速查

全部命令在技能根目录执行；脚本以自身位置解析路径，切换 cwd 不影响结果。

### 探测与自检

```
python scripts/detect_deps.py --json
python scripts/selftest.py --fixtures --log-dir <RUN_DIR>
python scripts/run_fixtures.py
python scripts/smoke_ai.py
```

### 一条命令跑完单稿（推荐入口）

```
python scripts/studio.py --spec path/to/spec.json --svg path/to/draft.svg \
  --out output/<run-id> --profile screen [--judge path/to/judge.json] [--reference path/to/reference.svg]
```

依次执行冻结校验、净化、预览、方寸三检、判读与打分（只有给了 `--judge` 才做）、落地。任一阶段失败即停，每阶段状态写进 `output/<run-id>/studio.json`。方寸三检只在预览来自真渲染器时执行：内置兜底图保真度不够，量它等于量兜底图而不是量稿子，此时记 `skipped` 而不是假装测过。它不替代步 7–10 的 F 汇总与验收聚合，只是把「单稿能不能干净落地」压成一条命令，少一次漏跑 `--validate` 的机会。

盲评包：

```
python scripts/studio.py --review-pack output/review --seed 7 \
  --candidate output/run-1 --candidate output/run-2
```

生成匿名 case 与空评分表，映射单独放在 `output/<run-id>/review/key.json`。分数由真人回填，脚本不写审美分。回填后用 `scripts/review.py` 校验并汇总，空表与缺格会被判红。

可发给评审人的请求件在 `templates/review-request.md`：两人、各 15 分钟、独立不看对方，五维锚点已附。

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

### 发牌与三检

```
python scripts/deal.py --spec path/to/run/spec.json --out path/to/run/deal.json \
  --history path/to/run/deal-history.json --seed 7
python scripts/legibility.py --png path/to/round/preview.png --out path/to/round/legibility.json
python scripts/conformance.py --svg path/to/round/sanitized.svg \
  --rules path/to/brief/conformance.json --out path/to/round/conformance.json
```

`scripts/deal.py` 与 `scripts/legibility.py` 都不画画也不打分：前者从原型 / 色板 / 构图上发牌并排除最近两轮，后者读 PNG 报 `LOW-CONTRAST` / `COLLAPSED` / `MUD` 三条硬门。两者都带 `--json` 标准信封，退出码沿用 0/1/3 约定。`scripts/conformance.py` 判 brief 里机械可判的那一半（canvas / 颜色 / 色面积 / 描边宽度 / 图元种类与数量 / 留白 / 禁用构造），失败报 `E-CONFORMANCE` 退出 1；没写进规则文件的项进 `unchecked`，不显示成通过；反过来，写了但脚本不认识的键也不忽略——拼错的键名、编造的 `forbid` 构造名、`elements` 里的多余子键一律报 `E-SCHEMA` 退出 2，画板缺 `viewBox`、或图上没有可测量几何时对应规则判失败而不是跳过。颜色与描边按 CSS 继承取值：写在根 `<svg>` 上的 presentation attribute 与内联 `style=""` 都会向下继承，同元素上 `style` 压过属性，显式 `none` 终止继承。`<style>` 样式表按一套写明的最小 CSS 子集真正生效：选择器限类型 / 类 / ID 的组合，加后代与子代组合；声明限 `fill` / `stroke` / `stroke-width`（另放行四个不参与测量的描边属性）；级联顺序为 `!important` > 内联 `style` > 样式表 > presentation attribute，三个属性都向下继承。子集之外的任何写法——伪类、属性选择器、`@` 规则、`media`、未放行的属性——整份表拒绝并判 `STYLE-SHEETS` 失败，不半套应用：半套应用量出来的是没渲染过的数。命中规则数记在 `measured.style_rules`。

### 判读、打分与收敛

```
python scripts/llm_judge.py --judge path/to/round/judge.json --validate --spec path/to/run/spec.json
python scripts/score.py --svg path/to/round/sanitized.svg --judge path/to/round/judge.json --spec path/to/run/spec.json --out path/to/round/score.json
python scripts/iterate.py --round 1 --score path/to/round/score.json --spec path/to/run/spec.json --out path/to/round/revision.json
python scripts/batch_check.py --runs-dir path/to/run --out path/to/run/batch.json
```

### 落地与聚合

```
python scripts/land_vc.py --svg path/to/round/sanitized.svg --out path/to/run --profile print
python scripts/aggregate_log.py --run-dir path/to/run --out path/to/run/acceptance.json
```

快速预览用 `scripts/aggregate_log.py --quick`；只取 H1 证据、不评级用 `scripts/aggregate_log.py --landing-only`。

### 路线 B：客户给了一张照片，要印刷件

上面整条流程是「从需求生成」。另一种常见委托是「我有一张产品照，做成能印的矢量文件」——
这不是生成，也不是纯格式转换，走三个脚本：

```
python scripts/delight.py --in photo.jpg --out delit.png --strength 0.65 --json
python scripts/trace_ref.py --in delit.png --out art.ai --colors 96 --paths 90 --noise 2 --simplify 60 --json
python scripts/export_print.py --in art.ai --out print.jpg --height-mm 300 --dpi 600 --bleed-mm 3 --json
```

三件事各自有坑，脚本把它们固化下来：

- **先去光照，再描摹。** 摄影棚的光被拍进像素后，印到实物上会变成第二层光：高光成死白、投影成脏痕。
  `scripts/delight.py` 用低频除法拉平照明，`--strength` 默认 0.65（全强度会把深红提亮成粉红、丢掉珐琅的深度）。
  **确定性契约**：「这个近白像素是纸背景还是物体内部高光」从颜色、连通性、局部形状都判不出来——
  桥接高光、轴向缝、斜缝、曲线缝、细长高光互为反例（第 23 轮 QA 连查三轮）。脚本因此不再猜：
  · 不给 `--mask`：只对明显有色/暗的像素（亮度 < 230）去光照，**所有近白像素逐字节保持不变**；
    纸白不被染色、高光不被挖空、`specular_repaired` 不虚增，并在 envelope 里发 warning 说明。
  · 给 `--mask`（灰度轮廓，非黑即主体）：掩膜是唯一权威，其内部的死白高光才用周围颜色修复，外部一律不动。
  **已知限制（随包声明）**：无掩膜模式不去高光；要高光去眩光必须提供掩膜。任何「再从像素推一次掩膜」的启发式都被禁止——它必然有第三种几何形态反例。`A42` 用五种几何形态（含斜缝/曲线缝/细长高光）验证近白零改动。中间产物用 PNG：JPEG 有损，边缘纹理处会改动少量近白像素（第 25 轮 QA 实测 3,949/13,996），脚本在 JPEG 输出时会发 warning。
- **描摹参数嵌一层。** `scripts/trace_ref.py` 把设置放进 `{"params": {...}}`，平铺传会被引擎拒收。
  `ignoreWhite` 必须保持 false——开了它会把珍珠、水晶内部的纯白高光当背景挖掉，留下黑洞。
  描摹后接 `object.path.simplify` 可再砍三成锚点，颜色不动。
- **矢量没有物理尺寸。** `scripts/export_print.py` 一次定死成品尺寸、设备 dpi 与出血，
  并产出弱 RIP 能吃的位图；矢量输入靠 VectorCraft CLI 光栅化，CLI 不在时报 `E-CAPABILITY-MISSING`
  而不是悄悄产出一个更小的文件。
- **出血是内容延展，不是四边填白。** 默认 `--bleed-mode extend` 把边缘像素镜像出去，
  满版稿裁切后不会露白线；白底 logo 用 `--bleed-mode white` 才是纯白边。
  误用 white 会在非白底稿四周留下白线，这是第 23 轮 QA 查出来的真问题。
- **三个新脚本与既有 14 个脚本同契约**：统一 `L.run_cli`，损坏输入返回
  `code=4` 的 JSON envelope，不吐 traceback（`delight`/`export_print` 报 `E-INTERNAL`，`trace_ref` 报 `E-TRACE`）。

### 路线 C：只用 VectorCraft（默认推荐，不依赖 Adobe）

`scripts/land_craft.py` 与 `scripts/land_vc.py` **同输出契约**，但整条链只用 VectorCraft CLI，
无 COM、无 Windows 依赖、无 Adobe 授权。Adobe 官方说明 Creative Cloud 服务未在中国大陆的
adobe.com 上开放，自动化产线不应把落地绑在桌面程序上。

```
python scripts/land_craft.py --svg path/to/sanitized.svg --out path/to/run \
    --profile print --bleed-mm 3 --pdf-standard pdfX1a --json
```

产出六件：源 SVG、可编辑 .ai、印刷 PDF、预览 PNG、交付清单、结构回读 JSON。print 档走
真 CMYK 转换 → 文档出血设置 → PDF/X 导出（PDF/X-1a / X-3 / X-4、TrimBox / BleedBox /
MediaBox、裁切与套准标记、色标、页面信息、内嵌输出意图、字体内嵌）。
结构回读用 CLI 的 info 命令重开 .ai，H1 判据为 raster=0 / placed=0 / paths>0 /
画板与 viewBox 一致，再由脚本解析 PDF **页面内容流（/Contents 引用与 /Subtype /Form）**
确认颜色算子确实存在——ICC、字体程序、CMap 等二进制流不计入，它们只是碰巧含有 k/K 字节。
X-3 / X-4 的颜色走 ICCBased（内容流里是 `scn`/`SCN`），因此只有 X-1a 用 DeviceCMYK 的 `k`/`K` 判档；
把 `k+K==0` 套到 X-3/X-4 上会误报，脚本已按档位分开判定。

实测（套娃稿 630×1100pt）：528 路径、H1 通过、GTS_PDFXVersion 1 处、
`/Type /OutputIntent` 对象 1 个、OutputCondition 为 `VectorCraft Generic CMYK (SWOP-like)`、
TrimBox 630×1100pt、BleedBox 647×1117pt；CMYK 算子数由脚本从页面内容流现算并写进
observed.print_pdf.content_ops 段，本文件不再固定一个会随稿件变化的数字。

**已知限制（写进交付清单的 warnings，不隐藏）**：
  · VectorCraft 写的是 **PDF 兼容 .ai** 与 legacy version 7 .ai（保图层/组/文字），
    **不写 Adobe 的私有编辑数据**；文件里的 `/AIPrivateData1` 是它自己的回写流引用（对象 19–22），
    不是 Adobe 原生编辑数据。客户若硬性要 Adobe 原生编辑数据，交 SVG + PDF/X 让印厂转。
    VectorCraft 是 MIT / Apache-2.0 开源项目（GitHub storytold 组织的 vectorcraft 仓库，
    Rust，自带 headless CLI + MCP）；该项目的官方差距文档亦把「真实 .ai 的图层结构与
    私有编辑数据」列为未完成项，不应对外承诺等效。
  · handoff 档：PDF 带图层，但 .ai 保留 SVG 分组——引擎目前没有「分组转图层」命令。
  · PDF/X-1a 按规范是「未标签 DeviceCMYK + 内嵌输出意图」，所以 ICCBased 为 0 属正常；
    要 ICC 标签颜色用 pdfX3 或 pdfX4。
  · 引擎仍是 alpha（2026-09 建仓），锁定版本、把关键行为做成断言（A46），不依赖未发布修复。

## 输出规范

四件产物落在运行期目录，**默认在技能包之外**：`~/.vector-ai-studio/output/<run-id>/`，可用环境变量 `VECTOR_AI_STUDIO_OUT` 覆盖。包内不留任何运行期文件，打 zip 不会带出测试残留。

```
~/.vector-ai-studio/output/<run-id>/final.ai
~/.vector-ai-studio/output/<run-id>/final.svg
~/.vector-ai-studio/output/<run-id>/preview.png
~/.vector-ai-studio/output/<run-id>/acceptance.json
~/.vector-ai-studio/output/<run-id>/deliverable.json
```

交付清单 deliverable.json 是**给客户的**，与验收日志分开：它不评价好坏，只记录物件本身的可核事实（色彩空间、ICC、出血、图层、字体、位图、画板 pt/mm、sha256）与必须说明的风险（第三方重存会剥离 AIPrivateData、CMYK 未做色彩管理等）。`observed` 段每一项都是从产物字节实测（含 print.pdf 的 /OutputConditionIdentifier）；`save_options` 段记录本次**请求**的保存参数，两者不混写——唯一例外是 `save_options.output_intent`：没有 PDF 可读回时它记请求值，读得到时它记读回值（请求值另存 `output_intent_requested`），因为「按请求写的」与「文件里真的这么写」是两件事，混在一起就无法区分。`unverified` 字段专门记录「要求了但没兑现」的条目——例如要求转 CMYK 而 PDF 兼容层仍写 RGB 时会出现在这里，而不是被略过。

- final.ai 由 VectorCraft `run --export` 产出、再由 `info` 重开回读计数；final.svg 与它同源；preview.png 为真实可解析 PNG；acceptance.json 含 A–F 各项分数、轮次记录、H1–H4 与降级标记。
- 阈值、分档、降级文案的唯一事实源是 `config/thresholds.json`；`calibration_policy` 登记可校准白名单与审批流，判据定义类一律锁定，任何放宽必须标 `loosening: true` 且带裁决引用。
- 判定类 JSON 不含 wall-clock，同一输入两次执行逐字节一致；时间只进 append-only 运行日志。
- 验收日志另含 generation_count / generation_budget（运行时派生）、f_source_runs / c2_revisions / f_data_isolated、f_verified、needs_human_review、reason、h1 至 h4 等强制字段，供 QA 机械核对。
- 对外说明由 `templates/delivery-note.template.md` 生成，只消费分档为「达标」的结论；未达标或降级时首行显式标注需人工终审，禁止宣称达标 / 完美。
- 轮次与批量样本保留在各轮目录，供 F 项与人工复核追溯。

## 降级与边界

- L0 默认零凭据本地链路；L1 质量未达标进入定向收敛；L2 宿主读图不可用时 A/B 记 N/A (capability_missing)（状态枚举、非分数，禁用几何量折算，违例 `E-FABRICATED-SCORE`），仍按 §7.3 机械比较得出「不达标」、不引入额外档位；强制 degraded / needs_human_review / reason 为 capability_missing、首行「语义 / 构图判读未取证，需人工终审」；四件产物含原生 `.ai` 照常落盘、交付不阻断；L3 商业增强需用户自行提供凭据；L4 依赖缺失；L5 无风格参考时 E 记 N/A。
- VectorCraft CLI 缺失：L4 全自动降级，H1 判不达标，仅交付净化 SVG 与 PNG 并标注「非原生 `.ai`」，`substitute` 写明安装方式或 `VECTORCRAFT_CLI`。不存在半自动兜底路径（r45 起移除 Adobe/COM），也不允许把 `.ai` 后缀贴到 SVG 上冒充。

## 注意事项

- 所有脚本以 `scripts/lib_common.py` 的 `skill_root()` 解析路径，配置 / references / fixtures / 运行期目录都相对技能根，禁止依赖调用时 cwd。
- 不伪造 `.ai`、不承诺一次直出即达到阈值；达标由 `scripts/aggregate_log.py` 按配置机械比较裁定。
- 宿主 LLM 只负责语义生成与二值判读，数值 / 集合 / 结构判定全部由确定性脚本产出。

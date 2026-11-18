# 评分、落地与评审

本页承载步 5–11，供判读、迭代、验收与自检时加载。

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
| 前景/背景能不能读 | WCAG 2.1 对比度（2.2 沿用同编号同阈值）+ 三种色觉缺陷模拟取最小值 | 见下 |

**对比度的作用域是刻意收窄的。** WCAG 2.1 SC 1.4.11（2.2 沿用同编号同阈值，检索日 2026-11-11）只约束 "parts of graphics required to understand the content"，并明确豁免纯装饰。所以只有在 spec 里**显式声明** `contrast_pairs` 时才检查，未声明记 `not_applicable`——「没检查」不能显示成「通过」。`role` 取 `text`（走 SC 1.4.3）、`meaning`（走 SC 1.4.11）或 `decorative`（豁免，但仍记录实测比值，便于复核豁免是否正当）。

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

**印刷母版**由 `scripts/land_craft.py` 产出：同一引擎，额外给 PDF/X-1a / X-3 / X-4、裁切线、出血与 TrimBox/BleedBox 实测。**PDF/X 合规限定（C7，状态 A）**：关键条款已取证（ISO 15930 预览 / PRONOM / LoC / PDF Association / Adobe TN #5413 / ECI，见 `references/pdfx-requirements.md`），完整 ISO 文本未购；代码只证结构存在（标识串、输出意图、盒子、部分色彩算子），不证 ICC 有效性、字体全嵌入、透明度/图层或元数据完整。本链路不作为印厂合规依据，交付印厂前请用 Acrobat Preflight / callas pdfToolbox / veraPDF 独立核验。

**独立预检（r82，4.3）**：`scripts/pdf_preflight.py` 用 PyMuPDF（MuPDF）作为第二个 PDF 引擎重读同一份印刷 PDF，与 `scripts/lib_pdf.py` 交叉核对页数、页面尺寸、图像、字体存在性与输出意图；`--require-pdfx` 另查 GTS 标记、输出意图、加密与未嵌入字体。PyMuPDF 是可选依赖：缺席时脚本退 3（声明式缺失），绝不报通过。一个解析器与自己一致不算交叉校验，所以 A103/A104 让第二个解析器说出它看到的东西。

**PDF/X 标识（r83）**：`--expect-standard x1a|x3|x4` 按 `references/pdfx-requirements.md` 的精确值核对——X-1a:2001 是 PDF/X-1:2001 + GTS_PDFXConformance(PDF/X-1a:2001)，X-1a:2003 是 PDF/X-1a:2003，X-3 是 PDF/X-3:2002 或 :2003，X-4 是 PDF/X-4（XMP 或 Info）。`scripts/smoke_ai.py --print` 不再用 `startswith("PDF/X")` 这种级别不敏感的检查。

```
python scripts/land_craft.py --svg path/to/sanitized.svg --out path/to/run \
  --profile print --pdf-standard pdfX1a --bleed-mm 3
```

两个落地脚本共用 `scripts/lib_pdf.py` 的 PDF 解析器与 landing 契约；清单字段各自构造（都以 `references/schemas/deliverable.schema.json` 为准），同一份 PDF 上的算子计数不允许出现两个答案。

实测限制（vectorcraft 0.22.0，2026-11-08）：

- **CMYK 是真的**（内容流出现 k/K 算子、rg/RG 归零）
- **文字在 Illustrator 中表现为轮廓（引擎行为，非格式属性）**：VectorCraft 输出的 `.ai` 在 Illustrator 中文字读作轮廓（实测 `textFrames=0`），同一产物在 VectorCraft 中 `kinds.Type>0`；这是该引擎产物在 Illustrator 中的表现，不是 `.ai` / PDF 格式固有属性（Adobe 官方仅称 PDF 兼容层 "saves a PDF representation" 且 embeds fonts，未声称打开即轮廓化）。能否改字取决于客户用哪个软件打开；清单会写明这一条
- **字体缺失会被静默替换**：源文件字体本机没有时，产物用回退字体渲染。工具会报出来，清单照抄进 `warnings`
- **图层不完全可靠**：5 个「只含单个元素」的 `<g>` 实测生成 5 个命名图层；两个「`<g>` 内含多个子元素」的样本只有 1 层（均无 defs、无剪切蒙版）。规律尚未确认，清单层数取自文件 /OCGs，是实测值
- `.ai` 本身不含输出意图与 ICC：是 CMYK、不是 PDF/X；需要印刷条件时走 `scripts/land_craft.py`
- 这些差异只在带文字、渐变、剪切蒙版的复杂样本上暴露，换版本前必须压一遍

`scripts/render_png.py` 的渲染器按 resvg → cairosvg → 内置兜底依次尝试（无头浏览器只记 MISSING，不实际调用）。`scripts/land_vc.py` 自己会先用 VectorCraft 导出预览；失败时才回落到 `scripts/render_png.py`。两个落地脚本在**调用引擎之前**先清掉本轮目标产物（`output/<run-id>/final.ai`、`output/<run-id>/print.pdf`、`output/<run-id>/preview.png` 与它的 meta），所以「文件存在」只代表**本轮**写的——CLI 退出 0 却没写文件这种静默失败不会被上一轮产物顶替。任何失败出口再清一次旧 `output/<run-id>/structure_readback.json` 与预览：`land_vc` 在回落渲染前清，`land_craft` 在失败收尾清（它不做 render_png 回落）。两路都失败就**没有预览**（H2 记四件不齐），不会拿上一版设计的图顶替本轮。`land_vc` 产出的 `output/<run-id>/preview.meta.json` 如实标注 renderer 与 fidelity。

### 步 10 验收日志聚合（H2 / H3 / H4）

```
# RUN_ID 与步 11 是同一个内容派生值（sha256(spec + svg + thresholds.version) 前 12 位，
# 单一实现在 scripts/studio.py 的 content_run_id()；不含时间 / 随机数 / 签名）。
# 不传 --run-id 时 H3 的 run_id 绑定必判 fail（selftest.json.run_id 无法绑定到本次运行）。
python scripts/aggregate_log.py --run-dir path/to/run --out path/to/run/acceptance.json --run-id "$RUN_ID"
```

读全部轮次与 run 产物，汇总 A–F 分数、轮次记录、H1–H4 结论与降级标记，按 `config/thresholds.json` 机械比较得出分档（数值判定，LLM 不得宣布）。四件不齐即 `E-H2-INCOMPLETE` 并列出缺哪件。校准记录 `calibration_log` 做前后值与裁决引用一致性断言。对外说明用 `templates/delivery-note.template.md` 生成，只承诺分档结论，内部评分字段不外露。

**判决是三值的**（判据版本 2.0.0 起，见 `MIGRATION.md`）：

| `grade` | `tier` | 含义 | 什么时候 |
|---|---|---|---|
| 达标 | `pass` | 必需维全部**测过**且没有一维失败，硬门全过，F 已验证 | 证据齐全且测量结果支持 |
| 不达标 | `not-pass` | 某维**测过**且失败，或硬门明确为假（产物字节与回读对不上、自检日志坏了） | 有确定的不合格事实 |
| 未判定 | `indeterminate` | **没测**，不是「测了没过」 | A/B 读不了图、F 未验证、quick 模式、四件产物不齐、自检证据缺失 |

「没测出来」不等于「质量差」——这是 r72 最重要的一条改动。原实现把宿主读不了图（`capability_missing`）一律记成「不达标」，用户会去改一幅根本没被看过的稿子；现在这类结论记「未判定」，并由 `evidence_coverage` 说清**哪几维测过、哪几维没测、为什么、怎么修**。

「能不能交付」与「质量达没达标」是两个问题，分开两个字段：`grade` 说质量结论，`deliverable_status` 说文件能不能交（`deliverable` / `deliverable_with_human_review` / `not_deliverable` / `evidence_only`）。验收日志另记 `criteria_version` 与 `thresholds_sha256`：结论只在该判据版本下有效。

需要旧的两值写法时加 `--two-valued`：结论被折回旧措辞，日志标 `legacy_grading: true`，**达标 / 不达标的判定边界一个都没变**（只有「未判定 → 不达标」这一处措辞不同）。

两条证据轨，不得混用：默认是**完整链**（`mode=full`、`grading_entered=true`，H1–H4 全判并进入评级）；`--landing-only` 是**只取 H1 证据的窄轨**（`mode=landing-only`，h2、tier、grade、h3、h4 全为 `null`，`h2_status=not_evaluated`，不进入评级），仅用于「机器上只跑得动落地、跑不动完整链」时留存原生 `.ai` 结构证据。伪造 `mode=full` 却带 `grading_entered=false`，或 `landing-only` 却带档位词，一律 `E-EXEMPTION-ABUSE`；**已经跑过完整链的目录也不许再走窄轨**（目录里已有 `output/<run-id>/score.json` / `output/<run-id>/judge.json` / `output/<run-id>/selftest.json` / `output/<run-id>/batch.json` 时 `--landing-only` 直接 `E-EXEMPTION-ABUSE`）——窄轨是给跑不动完整链的机器留的取证通道，不是洗白失败 run 的后门。

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
- **评分者间信度是量出来的（r76，M4）**：`scripts/review.py` 用 **Krippendorff's α（ordinal 度量）**回答「评审人的一致性是否高于纯属巧合」——精确一致率只数全同格，α 则按机会修正并区分 0-vs-1 与 0-vs-2 的分歧大小。公式取自已发表文献（Krippendorff 2011），实现钉在论文自己的算例上（nominal 0.743 / ordinal 0.815 / interval 0.849，见 `scripts/krippendorff.py`）。**α 只报告不闸**：还没有经校准的 α 阈值，未校准的数当闸门等于把噪声点成红灯。盲评包生成时评审协议（维度 / 量级 / 案例 / seed）被**预注册**进随包的 key 文件（评审人看不到的那份）并带哈希，聚合时核验——协议在评审之后被改动，`E-REVIEW-INVALID` 当场红；无协议块的旧包会被警告（无法核对），不会被当成已预注册。
- **两两偏好与标度（r77，M4 第二片）**：绝对分要求评审人同时做两件事——判断质量并校准自己的尺子。盲评包现含 review-pairs.csv（全配对轮换：每行一对，winner 填两者之一或 tie）——只回答「哪个更好」。`scripts/review.py --pairs` 汇总后用 **Bradley-Terry 模型**（MM/Zermelo 迭代，平局计半，`scripts/bradleyterry.py`，性质自锚：全胜序、平局等强、确定性、反转让序、全平局等值、全败者正下界）拟合出每个案例的**标度强度**。坏表与评分表同一条拒绝分支（未知 pair_id / 换对 / 越界 winner / 缺格全部 `E-REVIEW-INVALID`）；**只报告不闸**，原因同 α。评审人身份在 r78 收紧为内容去重 + `reviewer_token`（见下一条）。
- **评审人 token 身份（r78，M4 收尾）**：盲评包的 review-sheet.csv 增一列 `reviewer_token`，由每位评审人填一个**自报的稳定假名**（如 `rev-a1b2`），同一张表每行一致。它不是账号、不是密钥（H4 零凭据不变），换文件名不会丢失；两张表用同一 token 会被判为同一人重复提交（`E-REVIEW-INVALID`），而**内容 sha256 去重**让「同一份表换文件名再交一次」也被拒。对外证据字段 `evidence.usable_for_claim` 现在要求所有计入表都带 token 且互不相同；无 token 的旧表仍能聚合，但只能当记录，不能当对外证据。**诚实边界**：同一个人编两个 token、写两份不同内容的表，脚本无法分辨——token 认的是「提交」不是「人」。

AI 不写分数，也不代替评审。这个脚本读的是人写下的格子，空着就判空。

### 步 11 装好即用自检（C5）

```
# 同一个「本次内容派生」run-id 必须同时传给 selftest.py 与 aggregate_log.py，
# 否则 H3 判定 selftest.json 不属于本次运行，恒判红。值由 spec + svg + 版本派生，
# 不含时间 / 随机数 / 签名，重跑同一输入得到同一 id（判定 JSON 逐字节幂等）。
RUN_ID=<sha256(spec + svg + thresholds.version)[:12]>
python scripts/selftest.py --fixtures --log-dir <RUN_DIR> --run-id "$RUN_ID"
python scripts/run_fixtures.py --run-id "$RUN_ID"
python scripts/aggregate_log.py --run-dir <RUN_DIR> --run-id "$RUN_ID"
python scripts/smoke_ai.py --run-id "$RUN_ID"
```

H3 的取证文件是 `<RUN_DIR>/selftest.json`，由上面第一条命令产生（`--log-dir` 必须指向运行目录，`--run-id` 写进日志的 `run_id`）；`scripts/aggregate_log.py` 校验 `selftest.json.run_id == 本次 --run-id`，两者取值必须一致。只给 `scripts/aggregate_log.py` 传 `--run-id`、却让 `scripts/selftest.py` 用默认固定值，会让所有正常日志 H3 全红。也可用 `--selftest-log <path>` 显式指定日志路径。

这条绑定拦住的是**跨 run 复用旧日志**：把上一次 / 别处的 `output/<run-id>/selftest.json` 塞进本次 run-dir，其 `run_id` 与本次内容派生值不符 → H3 fail。它**拦不住**「同一次 run 内生成方自写一份 run_id 匹配的假 `output/<run-id>/selftest.json`」——`output/<run-id>/` 是生成方可写的、`run_id` 本身公开可推导，属已声明的威胁模型边界；同 run 内自证这一层由「断言总数下界 ≥ 90」+「#2 产物字节复算」+「#5 grade 单一函数复算」共同收紧，不靠绑定宣称「不可伪造」。`scripts/studio.py` 的 `content_run_id()` 是生成该值的单一实现，编排各阶段与步 11 都复用它。

`scripts/selftest.py` 是纯脚本层，零外部依赖必能跑；`scripts/run_fixtures.py` 跑随包 good / bad 回归；`scripts/smoke_ai.py` 是独立的真机冒烟命令，真实探测并调用 VectorCraft 落地，引擎不可用按 L4 显式降级并留真实日志，绝不编造 `.ai`。

**证据可重放（r73）**：`scripts/aggregate_log.py` 聚合时把 run 目录里每个文件（除 journal 自身与 wall-clock 的 run.log）哈希进一条链，写成 `output/<run-id>/journal.jsonl`——**随包的验收日志 acceptance.json 本身也在链里**。消费方用 `scripts/journal.py --verify` 验链、用 `scripts/replay.py` 在 run 目录的**副本**上重跑同一个聚合器并逐字节比对验收日志与 journal：篡改证据、直改判决、偷换预览，都会被重放打回真实结论（重算的 journal 与随包 journal 不一致即 `E-ARTIFACT-DRIFT`，重算的验收日志不一致即 `E-REPLAY-MISMATCH`）。journal 不含 wall-clock，故同输入两次聚合的 journal 逐字节一致（A3 口径不受影响）。**已声明边界**：run 目录由生成方可写，能把证据、journal、验收日志三者一起重造的伪造仍拦不住——与 H3 的同 run 自证边界同一威胁模型。

**见证层（r74，角色分离的落点）**：这个边界的出口在 run 目录**外面**。签收一个 run 时，`scripts/witness.py create` 把它的验收日志与 journal 哈希写进一条**独立于 run 目录**的 append-only 见证链（随仓库提交、推送、打 tag）；消费侧 `scripts/witness.py verify`（或 `scripts/replay.py --witness <见证文件>` 的 R5 项）把当前字节对锚核验——**签收之后再动验收日志，`E-WITNESS-MISMATCH` 当场红**；没有见证记录的 run_id 同样不给过。本项目不用密钥（H4 零凭据原则）：**仓库历史就是身份**——append-only、已推送、有 tag，谁都能核；能 force-push 那条历史的人能伪造锚，这是已声明的边界，tag 把它压到"必须在 tag 里可见"。

**保真度是量出来的（r75，M3 第一片）**：内置兜底渲染器的「faithful」不再是静态嗅探的自证——`scripts/fidelity_matrix.py` 为每类特征（实心矩形 / 描边 / 圆 / 椭圆 / 线 / 路径填充 / 路径描边 / 渐变 / 文本 / transform / 透明度）造一个探针，用**生产渲染器**与 **Pillow 参照渲染器**（真实的第二实现）各画一遍、逐像素比对，落成**已测能力矩阵**（`references/fidelity/capability-matrix.json`，随包提交）。测量抓到并修掉了两个分类器盲区：**transform**（旋转元素被画成未旋转的 bbox）与 **fill-opacity**（半透明被画成全不透明）——现在都 fail-closed 判 limited。A94 钉住承重方向：**分类器说 faithful 的特征，必须实测 consistent**；反过来（实测一致 → 可考虑升级声明）只记在矩阵里、不强制——保持 limited 是 fail-closed，不是不诚实。内置渲染器预览的 meta 另带 `fidelity_vector`（逐元素的词与原因），判决仍只读契约词表的那一个词。

**跨渲染器（r84/r85，M3 第二/三片）**：r84 修了 Pillow 参考的两处 bug（stroke 居中、transform 旋转符号）并重测矩阵；r85 加可选第三渲染器：`scripts/fidelity_matrix.py --cross-check` 用 cairosvg，只要求分类器宣称 faithful 的特征与它像素一致，limited 只要求渲染非空。cairosvg/原生 cairo 缺席退 3（声明式缺失），绝不报通过。

`scripts/verify_install.py` 回答的是另一个问题：**这台机器上的引擎路径通不通**。自检里 A30 / A31 / A46 三条依赖真实引擎，没有引擎时它们是 *skipped*（跳过是「没测」，不是「通过」）——所以「自检全绿」并不证明引擎能用。`scripts/verify_install.py` 复用这三条断言（不另立判据），再真跑一次 `land_vc` 与 `land_craft` 留下产物证据，并核对引擎二进制哈希与 `engine.lock.json` 里钉的那一个是否一致。**引擎缺席时它只报「无法复测」并退 3，绝不报 ok**；哈希对不上则报 `E-ENGINE-DRIFT`（同版本、不同构建，之前的回归结论不再成立）。

```
python scripts/verify_install.py --json     # 退出码：0 已验证 / 1 有项目失败 / 3 引擎缺席（不是通过）
```

# vector-ai-studio

把一句自然语言需求，做成**可继续编辑的 VectorCraft PDF 兼容 `.ai`**，并且**只在判决层自己复算过、这份文件真的达到判据时才给出可复核的质量结论**。

这不是又一个"AI 画图"封装。它的重点在反面：**防止"看起来达标、其实很烂"**。v1.4.0（r70）起，判决层不再只读日志里的结论：H1 会第二次打开 `.ai` 做字节断言，`--validate-case` 会用与主路径同一函数重推分档；v1.4.1（r71）起，安装自检同样不说谎——`smoke_ai` 的结论由验收链定档， degraded 就写 degraded。

## 它解决的具体问题

常见的 AI 出图链路有三个坑，这个技能是绕着它们设计的：

| 坑 | 表现 | 这里的做法 |
|---|---|---|
| **用失真图冒充读图判读** | 兜底渲染把圆画成方块，链路仍给"语义优秀" | 渲染器必须机械声明 `fidelity`；非 `faithful` 自动转 L2，**字段缺失按 `limited`**（fail-closed） |
| **"达标"在数学上不可达** | 判据要求 F 维在场，但代码从不把它并进分数 | F 并入 `scores`，仅 `f_verified` 为真才采纳，并标注来源 |
| **交付物不是原生格式** | SVG 改后缀、或 PDF 容器冒充 `.ai` | VectorCraft 导出后**重新打开读回对象计数**验证；断言 `raster=0`、`placed=0`、`paths>0`、画板 = viewBox |

## 需要什么

- **`vectorcraft-cli`**（跨平台，无需 Adobe、无需 COM、无需 Windows 桌面）。缺失时落地走 L4 降级，如实记 `capability_missing`，不假装成功。
- Python 3.10+。**技能本身零第三方依赖**，纯标准库，装好即跑。
- 可选增强（缺了会自动降级并如实标注）：PyYAML、cairosvg、resvg。

## 安装

```
git clone <this repo>
cp -r vector-ai-studio ~/.workbuddy/skills/     # 或你的技能目录
```

## 先自检，再使用

```
python vector-ai-studio/scripts/selftest.py --fixtures
python vector-ai-studio/scripts/run_fixtures.py
```

期望结果取决于是否装了 VectorCraft 引擎，**两种都不算失败，但退出码不同**：

| 环境 | `selftest --fixtures` | `run_fixtures` |
|---|---|---|
| 装了 VectorCraft CLI | **111/111**（`--fixtures` 为 **112/112**，含 A13），`status=ok`，**exit 0** | **37/37**，exit 0 |
| 没装 | 依赖引擎或 Pillow 的断言转 skipped，`status=partial`，**exit 3** | 该组合在 34 条后**未单独实测**，不作承诺 |

**`exit 3` 是"可选项未执行"，不是"检查失败"**——只卡 `exit 0` 的流水线会把它误判成红灯。
两条都不依赖 Adobe，纯脚本层就能跑通；如果这里就失败，先别往下走。

探测本机依赖：

```
python vector-ai-studio/scripts/detect_deps.py --json
```

> 依赖探测是三态的：`FOUND` / `MISSING` / `UNVERIFIED`。**不知道不等于没装**。

## 快速上手

```
python vector-ai-studio/scripts/freeze_spec.py --spec spec.json --validate --out <RUN>/spec.json
python vector-ai-studio/scripts/deal.py --spec <RUN>/spec.json --out <RUN>/deal.json --history <RUN>/deal-history.json
python vector-ai-studio/scripts/sanitize_svg.py --in draft.svg --out <RUN>/final.svg
python vector-ai-studio/scripts/render_png.py --in <RUN>/final.svg --out <RUN>/preview.png
python vector-ai-studio/scripts/legibility.py --png <RUN>/preview.png --out <RUN>/legibility.json
python vector-ai-studio/scripts/land_vc.py --svg <RUN>/final.svg --out <RUN> --profile print
python vector-ai-studio/scripts/score.py --svg <RUN>/final.svg --judge judge.json --spec spec.json --out <RUN>/score.json
python vector-ai-studio/scripts/aggregate_log.py --run-dir <RUN> --run-id <ID>
```

`deal.py` 在动笔前发三个方向并排除最近两轮用过的原型，避免每轮都回到同一张居中徽章；`legibility.py` 在画完后读预览 PNG，卡三件 240px 看不出的失效：16px 时墨没了（`COLLAPSED`）、糊成实心（`MUD`）、灰度对比不足（`LOW-CONTRAST`）。

盲评回收用 `review.py`：空表判 `E-REVIEW-EMPTY`、缺格判 `E-REVIEW-INVALID`，只有「≥2 名外部评审 + 零空格」时 `evidence.usable_for_claim` 才为真。可直接转发给评审人的请求件在 `templates/review-request.md`，生成的包不含映射文件 `key.json`。

印刷母版还有 `land_craft.py`（PDF/X-1a/X-3/X-4、出血、裁切标记），与 `land_vc.py` 写同一套 `deliverable.json` / `structure_readback.json` / `landing.json`。

### 交付档位

落地前先声明客户要什么，不要用默认值赌：

```
--profile screen     屏幕交付（默认）：PDF 兼容、RGB、无出血
--profile print      印刷：请求 CMYK 转换 + 3mm 出血与裁切线（PDF/X 走 land_craft）
--profile handoff    客户改稿：顶层分组建成命名图层
```

所有保存选项都**显式写入**清单，读的是产出文件的实测结果，不是命令行意图。

### 交付清单

落地成功会产出 `deliverable.json`：不评价好坏，只记录**从文件字节实测**的事实（色彩空间、出血、图层数、字体、位图、画板 pt/mm、sha256）与必须告知客户的风险。

其中 `unverified` 字段专门记录「**要求了但没兑现**」的项。例如请求了 ICC 输出意图而产出中没有 `/OutputIntent` 时，这一条会出现在那里——工具不会因为命令返回成功就宣称完成。

## 一条引擎，两个出口

| | `land_vc.py`（默认） | `land_craft.py`（印刷母版） |
|---|---|---|
| 用途 | 屏幕 / 通用交付 | 印刷：PDF/X、出血、裁切标记 |
| 依赖 | 只要 `vectorcraft-cli` | 同左 |
| 落盘 | CLI 导出 → 重新打开读回 `structure_readback.json` | 同左，另测 PDF 盒子与 CMYK 算子 |
| CMYK | 请求转换后实测内容流中的 `k`/`K` 算子 | 同左，并核对 PDF/X 声明 |
| 活字 | 文件内保留文字对象；Illustrator 打开会**变轮廓** | 同左 |

两者写同一份 `deliverable.json` 契约，断言 A31 用**同一份 schema** 卡真实产物——
结构漂移不会因为换了出口就漏掉。

## 色彩不是「数颜色个数」

C 维以前只有 `len(colors) <= N` 一条：一张全用同一支灰的画，和一张九个不相干饱和色硬凑的画，可能同分。

现在拆成三件互不相同的事，**分开报告，不揉成一个「色彩分」**——揉在一起就看不出是哪一件坏了：

| 问题 | 用什么 |
|---|---|
| 两个色是不是同一个色 | CIEDE2000 < 2.3（小色差，ΔE00 的主场） |
| 声明要区分的色真拉开了吗 | HyAB < 10（大色差，此时 ΔE00 会失真） |
| 前景/背景读得出来吗 | WCAG 2.1 对比度 + 三种色觉缺陷模拟取最小值 |

为什么值得加：`#FF0000` 在黑底上 WCAG 判 **5.25 通过 AA**，红色盲视角只有 **1.26** ——
几乎看不见。而「黑底红字」是最常见的默认强调色搭配。这不是为了更严格，是让**一类看不见的失败**变得可见。

**作用域刻意收窄**：WCAG 2.1 SC 1.4.11 只约束 "parts of graphics **required to understand the content**"，
明确豁免装饰。所以只在 spec 显式声明 `contrast_pairs` 时才查；未声明记 `not_applicable`，
全部标 `decorative` 记 **`exempt`** 而**不是 `pass`** —— 「没比较过任何阈值」不能显示成「通过」。

**这三个阈值都还没校准**（出处、或「无出处」都写在 `config/thresholds.json` 的 `_src_*` 里），
所以本版**只测量、不判失败**：发现进 `perceptual_warnings` 和 `acceptance.json`，看得见，但不改变结论。

## 已知限制（如实列出，不假装已解决）

| 项 | 实际情况 |
|---|---|
| **「好看」没有统计依据** | B 维（审美）的阈值是手工经验值，**从未用人类评分校准过**。调研过的开源项目里没有一个公开说明自己的审美阈值怎么标定——最常被引用的 LAION 4.5 是「留下 12 亿张」的分位数，不是及格线。要解决只能自建评分集（30 张 × ≥15 人，报 ICC 信度），改代码解决不了。 |
| **「符合需求」靠模型读图** | A 维消费大模型的命中率统计，没有确定性等价物。读图能力缺失时它会输出 `capability_missing` 而不编数字——这是取舍，不是遗漏：宁可承认测不了，也不伪造一个几何分数去顶替语义判断。 |
| **色彩阈值未校准** | C 维三个阈值（重复色 2.3 / 显著差异 10.0 / 色觉缺陷 3.0）都没经分布校准，因此只测量不判失败。升级为硬门禁必须先附「≥N 样本分布 + 校准记录」。 |
| **CMYK 未验证输出意图** | `land_vc.py --profile print` 请求转换后，内容流实测有 `k`/`K` 算子；但产出无 `/OutputIntent`、无 `/ICCBased`，印刷厂要求 PDF/X 时用 `land_craft.py`。RGB→CMYK 的色域损失本身无法避免。 |
| **ICC 未生效** | 请求了 CMYK 输出意图，但产出中未观察到 `/OutputIntent` 或 `/ICCBased`，清单把它写进 `unverified`。 |
| **`.ai` 是 PDF 容器** | VectorCraft 写出的 `.ai` 即 PDF 兼容文件，观察全部来自容器字节；没有 Illustrator 私有压缩段可解。 |
| **A8 双运行时** | 覆盖「默认 vs `-I` 隔离」两种运行模式，**未覆盖跨 Python 版本**。可选的 PyYAML / cairosvg 分支在本机未安装，未实测。 |
| **`--validate-case` 的自证边界** | r70 起 validator 用与主路径同一 `_derive_grade` 从 `scores` + `hard_gates` 重推 `grade` / `tier` / `internal_excellent` 等字段，不一致即 `E-GRADE-MISMATCH`（V1/V2/V3/V7/V8/V9 全拒）。**仍未覆盖**：生成方在同一 run 内自写全部日志——这属已声明的威胁模型边界，不宣称抗伪造。 |

## 仓库结构

```
vector-ai-studio/     技能本体（137 文件，与发布基线逐字节一致）
docs/                 发布说明、发布纪律、校验留痕
```

`docs/sha256.txt` 里写了怎么自己验证这份包——不只有校验值，还有校验步骤。

## 许可证

MIT，见 `LICENSE`。

技能内**未使用** APCA（Accessible Perceptual Contrast Algorithm）。APCA 的授权仅允许用于 web 内容的对比度预测，且商用需书面协议，与本项目（印刷/矢量）的用途不符，因此未纳入。技能内的对比度相关实现均基于公开标准或自身定义。

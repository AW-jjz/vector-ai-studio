# vector-ai-studio

把一句自然语言需求，做成**原生可编辑的 `.ai` 矢量成品**，并且**只在这份文件真的达到判据时才说达标**。

这不是又一个"AI 画图"封装。它的重点在反面：**防止"看起来达标、其实很烂"**。

## 它解决的具体问题

常见的 AI 出图链路有三个坑，这个技能是绕着它们设计的：

| 坑 | 表现 | 这里的做法 |
|---|---|---|
| **用失真图冒充读图判读** | 兜底渲染把圆画成方块，链路仍给"语义优秀" | 渲染器必须机械声明 `fidelity`；非 `faithful` 自动转 L2，**字段缺失按 `limited`**（fail-closed） |
| **"达标"在数学上不可达** | 判据要求 F 维在场，但代码从不把它并进分数 | F 并入 `scores`，仅 `f_verified` 为真才采纳，并标注来源 |
| **交付物不是原生格式** | SVG 改后缀、或 PDF 容器冒充 `.ai` | `saveAs()` 产出，**重新打开读回对象计数**验证；断言 `raster=0`、`placed=0`、`paths>0`、画板 = viewBox |

## 需要什么

- **Windows + Adobe Illustrator**（COM 自动化）。这是硬依赖，不是可选项。
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
| 装了 VectorCraft CLI | **34/34**，`status=ok`，**exit 0** | **24/24**，exit 0 |
| 没装 | 32 passed + **2 skipped**（A30/A31 的 VectorCraft 半边），`status=partial`，**exit 3** | 24/24，exit 0 |

**`exit 3` 是"可选项未执行"，不是"检查失败"**——只卡 `exit 0` 的流水线会把它误判成红灯。
这两条都不依赖 Illustrator，纯脚本层就能跑通；如果这里就失败，先别往下走。

探测本机依赖（含真实 COM 创建尝试）：

```
python vector-ai-studio/scripts/detect_deps.py --probe-com --json
```

> COM 探测是三态的：`FOUND` / `MISSING`（创建抛异常）/ `UNVERIFIED`（超时，不知道）。
> **超时不等于没装**——Illustrator 冷启动要 15–20 秒，把超时记成"缺失"会让一台能用的机器被判定为不支持。

## 快速上手

```
python vector-ai-studio/scripts/freeze_spec.py --spec spec.json --validate --out <RUN>/spec.json
python vector-ai-studio/scripts/sanitize_svg.py --in draft.svg --out <RUN>/final.svg
python vector-ai-studio/scripts/render_png.py --in <RUN>/final.svg --out <RUN>/preview.png
python vector-ai-studio/scripts/land_ai.py --svg <RUN>/final.svg --out <RUN> --deps <RUN>/deps.json --profile print
python vector-ai-studio/scripts/score.py --svg <RUN>/final.svg --judge judge.json --spec spec.json --out <RUN>/score.json
python vector-ai-studio/scripts/aggregate_log.py --run-dir <RUN> --run-id <ID>
```

### 交付档位

落地前先声明客户要什么，不要用默认值赌：

```
--profile screen     屏幕交付（默认）：PDF 兼容、RGB、无出血
--profile print      印刷：请求嵌入 ICC + 逐对象转 CMYK + 3mm 出血与裁切线
--profile handoff    客户改稿：顶层分组建成命名图层
```

所有保存选项都**显式写入** `IllustratorSaveOptions`。`pdfCompatible` 官方默认恰好为真、`embedICCProfile` 恰好为假——靠默认值等于把交付质量交给安装环境。

### 交付清单

落地成功会产出 `deliverable.json`：不评价好坏，只记录**从文件字节实测**的事实（色彩空间、ICC、出血、图层数、字体、位图、画板 pt/mm、sha256）与必须告知客户的风险。

其中 `unverified` 字段专门记录「**要求了但没兑现**」的项。例如要求转 CMYK 而 PDF 兼容层仍写入 RGB 时，这一条会出现在那里——工具不会因为命令返回成功就宣称完成。

## 两条引擎，不是二选一

| | Illustrator 引擎 | VectorCraft 引擎 |
|---|---|---|
| 依赖 | Illustrator + COM（Windows） | 只要 `vectorcraft-cli`，跨平台 |
| 落盘 | `saveAs()` → `structure_readback.json` 回读校验 | CLI 导出 → 回读 |
| CMYK | 逐对象转换，**PDF 兼容层仍写 RGB**（实测 `rg`/`RG` 存在） | **内容流里全是 CMYK 算子**（实测 `rg=0, RG=0, k=4, K=1`） |
| 活字 | 保留（`textFrames` 仍在） | 在 Illustrator 里打开会**变轮廓**（能否改字看客户用哪个软件） |

两条链写同一份 `deliverable.json` 契约，断言 A31 用**同一份 schema** 卡两者——
结构漂移不会因为换了引擎就漏掉。选哪条取决于客户拿什么打开，不取决于哪个更好写。

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
| **CMYK 不完整** | `--profile print` 只做到「原生对象颜色逐个转换」，**PDF 兼容层仍写 RGB**（实测 `/DeviceCMYK` 0 处、`/DeviceRGB` 1 处）。要真正的印刷文件，用 Illustrator 另存 PDF/X 或交印厂转。RGB→CMYK 的色域损失本身无法避免。 |
| **ICC 未生效** | `embedICCProfile=true` 请求了，但产出中未观察到 `/ICCBased`。 |
| **`.ai` 私有压缩段不可解** | 6 个 stream 里只有 1 个可解压统计着色算子；绘图主体在 Illustrator 专有压缩块内。 |
| **A8 双运行时** | 覆盖「默认 vs `-I` 隔离」两种运行模式，**未覆盖跨 Python 版本**。可选的 PyYAML / cairosvg 分支在本机未安装，未实测。 |
| **`--validate-case` 边界** | 只校验达标日志「必须含 E 键」，**不校验 E 分值与达标结论一致**。手改日志写 `E=0` 配 `grade=达标` 仍能过。 |

## 仓库结构

```
vector-ai-studio/     技能本体（54 文件，与发布基线逐字节一致）
docs/                 发布说明、发布纪律、校验留痕
```

`docs/sha256.txt` 里写了怎么自己验证这份包——不只有校验值，还有校验步骤。

## 许可证

MIT，见 `LICENSE`。

技能内**未使用** APCA（Accessible Perceptual Contrast Algorithm）。APCA 的授权仅允许用于 web 内容的对比度预测，且商用需书面协议，与本项目（印刷/矢量）的用途不符，因此未纳入。技能内的对比度相关实现均基于公开标准或自身定义。

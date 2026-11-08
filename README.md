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
| 装了 VectorCraft CLI | **32/32**，`status=ok`，**exit 0** | **24/24**，exit 0 |
| 没装 | 30 passed + **2 skipped**（A30/A31 的 VectorCraft 半边），`status=partial`，**exit 3** | 24/24，exit 0 |

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

## 已知限制（如实列出，不假装已解决）

| 项 | 实际情况 |
|---|---|
| **CMYK 不完整** | `--profile print` 只做到「原生对象颜色逐个转换」，**PDF 兼容层仍写 RGB**（实测 `/DeviceCMYK` 0 处、`/DeviceRGB` 1 处）。要真正的印刷文件，用 Illustrator 另存 PDF/X 或交印厂转。RGB→CMYK 的色域损失本身无法避免。 |
| **ICC 未生效** | `embedICCProfile=true` 请求了，但产出中未观察到 `/ICCBased`。 |
| **`.ai` 私有压缩段不可解** | 6 个 stream 里只有 1 个可解压统计着色算子；绘图主体在 Illustrator 专有压缩块内。 |
| **A8 双运行时** | 覆盖「默认 vs `-I` 隔离」两种运行模式，**未覆盖跨 Python 版本**。可选的 PyYAML / cairosvg 分支在本机未安装，未实测。 |
| **`--validate-case` 边界** | 只校验达标日志「必须含 E 键」，**不校验 E 分值与达标结论一致**。手改日志写 `E=0` 配 `grade=达标` 仍能过。 |

## 仓库结构

```
vector-ai-studio/     技能本体（53 文件，与发布基线逐字节一致）
docs/                 发布说明、发布纪律、校验留痕
```

`docs/sha256.txt` 里写了怎么自己验证这份包——不只有校验值，还有校验步骤。

## 许可证

MIT，见 `LICENSE`。

技能内**未使用** APCA（Accessible Perceptual Contrast Algorithm）。APCA 的授权仅允许用于 web 内容的对比度预测，且商用需书面协议，与本项目（印刷/矢量）的用途不符，因此未纳入。技能内的对比度相关实现均基于公开标准或自身定义。

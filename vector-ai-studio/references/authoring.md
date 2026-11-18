# 设计方向与施工约束

本页承载步 1.5–4.6，供生成或修改 SVG 时加载。

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

确定性检出位图、XML 错误、禁用构造、退化路径并计数语义分组。违规即显式报错退回重生成，不擅自删改语义元素（删改会破坏 A 项命中，语义改稿归 LLM）。带 `--prev` 时比对通过项结构；改动只在 `warnings` 里写 `E-REGRESSION` 并要求重生成，不改变退出码（净化本身已通过）。

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

硬门只有三条，且每条都有外部依据或定义即失效：对比度低于 `C.contrast_graphic_ratio`（WCAG 2.1 SC 1.4.11（2.2 沿用同编号同阈值），检索日 2026-11-11）判 `LOW-CONTRAST`；16px 覆盖率为 0 判 `COLLAPSED`；16px 覆盖率满格判 `MUD`（与前者互为镜像，两种都没有剪影）。`FRAGMENTED` 与 `COVERAGE-DRIFT` 只报告、不判失败——那两个门限是工程值，未标定。

Pillow 未装时显式降级（exit 3 + `E-RENDERER-MISSING`），不从 SVG 猜。

### 步 4.6 需求符合度（机械可判的那一半）

```
python scripts/conformance.py --svg path/to/round/sanitized.svg \
  --rules path/to/brief/conformance.json --out path/to/round/conformance.json
```

盲评不给评审人 brief，所以「是否符合需求」不能在评分表里判。但它大部分不是审美问题：画板尺寸、允许的颜色集合、颜色面积上限、允许的描边宽度、允许的图元种类、图元数量区间、最小留白、禁用构造，都能从 SVG 直接读出来。规则文件是 brief 硬约束的结构化写法——人写一次数字，脚本每轮核对，后续改稿无法悄悄偏离需求。

判定只看规则文件里写了的项，没写的进 `unchecked`，不把「没检查」显示成「通过」。有任一检查失败即 `E-CONFORMANCE` 退出 1，逐条列出 id 与实测值。语义层（「不要画咖啡豆」到底画没画）它判不了，仍归人。

**素材走两条通道，都不建库。** 参考素材（风格、构图、质感参照）只进账本、不进产物；要进产物的构件必须有 URL、许可、许可声明页与核实日期，外加对应元素 id，账本模板是 `templates/source-ledger.json`。规则文件里加 `source_ledger` 指向账本、`licenses_allowed` 列可接受许可后，脚本会查六件事：每个条目必须具备 `channel`/`license`/`fetched`/`use` 四个字段、带 `data-source` 的元素必须在账本里有条目、产出条目的许可必须在白名单内、账本写的元素 id 必须真的存在于 SVG、非 `own` 条目必须给出 `license_source`（许可声明页，不是素材下载页）、`license_verified_at` 必须是合法 `YYYY-MM-DD`。

`licenses_allowed` 用显式许可名（cc0、mit、cc-by-4.0、ofl-1.1 …），不要写 `commercial`——那是用途不是许可，会把需署名与免署名混成一类。`own` 条目豁免后两项：自产内容没有对外许可声明页，强制填 URL 只会逼人编造。过期复核不进脚本：判「是否过期」需要当天日期，同一输入一年后结果不同，那是人的复核动作，不是闸门。站点选择与核实方法见 `references/sources-whitelist.md`。

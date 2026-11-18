# 产物与执行命令

本页承载命令速查、结果契约与照片/印刷路线。

## 命令速查

全部命令在技能根目录执行；脚本以自身位置解析路径，切换 cwd 不影响结果。

### 探测与自检

```
python scripts/detect_deps.py --json
python scripts/selftest.py --fixtures --log-dir <RUN_DIR>
python scripts/run_fixtures.py
python scripts/smoke_ai.py
python scripts/verify_install.py --json   # 引擎路径自验：0 已验证 / 3 引擎缺席（不是通过）
```

### 证据重放与见证（r73 / r74）

```
python scripts/journal.py --verify output/<run-id>/journal.jsonl --root output/<run-id>
python scripts/replay.py --run-dir output/<run-id> --json   # 0 = 重放逐字节一致 / 1 = 重放失败
python scripts/witness.py create --run-dir output/<run-id> --witness witnesses.jsonl   # 签收即见证
python scripts/witness.py verify --run-dir output/<run-id> --witness witnesses.jsonl   # 对锚核验
python scripts/fidelity_matrix.py --check --json           # 保真度声明是否被测量支撑（r75）
```

### 契约与自证（r72）

```
python scripts/gen_contract.py            # 从 contract/contract.json 生成契约产物
python scripts/gen_contract.py --check    # CI 用：生成物与契约不一致即红
python scripts/redteam.py --json          # 随包攻击集：每个攻击都必须被拦下
python scripts/validate_claims.py         # 对外声明 → 测试 的强制映射
```

`contract/contract.json` 是**唯一事实源**：跨文件的枚举（保真度、档位、判读字段、必需维）与「谁能写、谁接受」的接受集合都只在这里定义一次，由 `scripts/gen_contract.py` 生成到 `scripts/_contract.py`（生产侧与判据侧都 import 它）、`references/schemas/contract-enums.schema.json`、`docs/generated/contract-table.md` 与 `tests/fixtures/contract/contract-matrix.json`。生成物与契约不一致时 `--check` 报 `E-CONTRACT-DRIFT`。r71 的 P0-1（生产侧写 `full`、判据侧只认 `faithful`、六处三种说法零处校验）就是靠这条消灭的：**词表不再可能漂移，因为代码里已经没有第二份词表。**

`scripts/redteam.py` 是**随包攻击集**：每个攻击是一份数据（`tests/attacks/*.json`），都写明自己攻的是什么、期望被拦还是被放（后者是**已声明的边界**，必须有反向证明）。跑一遍就知道这个技能在哪些地方会认输：

```
python scripts/redteam.py --json     # 期望：所有 expect=block 的攻击全被拦下，exit 0
```

`scripts/validate_claims.py` 把**对外声明**与**测试**绑在一起：`contract/claims.json` 登记每一条可证伪的对外承诺（原文哈希、机制、对应测试），校验器在 CI 里跑——改了文档没改契约就红；`assertion` 类声明必须挂真实存在且通过的测试；`boundary-declaration` 类必须挂一条**真的漏过去**的攻击（否则就是在低估自己）。

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

`references/schemas/` 下是两份 JSON Schema（draft 2020-12）：验收日志与交付清单。字段契约从散文变成了机器可校验的东西，任何消费方都能独立判定一份日志合不合法，不必相信产出方。**状态 A 限定（r70）**：此处「独立判定」仅指 JSON Schema 的字段契约校验（type / enum / required 等），**不是**对 `grade` 判定结论的复算——`--validate-case` 在 r70 已用与主路径同源的 `_derive_grade` 逐字段重推并比对（`E-GRADE-MISMATCH`），但两份 Schema 本身不校验分档语义。引用 `grade=达标` 仍须按上文状态 A 三项自复核，不得据此句对外宣称质量已被独立验证。`scripts/schema_check.py` 是零依赖的子集校验器，遇到不认识的 schema 关键字会**报错而不是静默忽略**——静默忽略会让校验器变成装饰。

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

`scripts/deal.py` 与 `scripts/legibility.py` 都不画画也不打分：前者从原型 / 色板 / 构图上发牌并排除最近两轮，后者读 PNG 报 `LOW-CONTRAST` / `COLLAPSED` / `MUD` 三条硬门。两者都带 `--json` 标准信封，退出码沿用 0/1/3 约定。`scripts/conformance.py` 判 brief 里机械可判的那一半（canvas / 颜色 / 色面积 / 描边宽度 / 图元种类与数量 / 留白 / 禁用构造），失败报 `E-CONFORMANCE` 退出 1；没写进规则文件的项进 `unchecked`，不显示成通过；反过来，写了但脚本不认识的键也不忽略——拼错的键名、编造的 `forbid` 构造名、`elements` 里的多余子键一律报 `E-SCHEMA` 退出 2，画板缺 `viewBox`、或图上没有可测量几何时对应规则判失败而不是跳过。只量真正会画出来的东西：`<defs>` / `<symbol>` 等定义、`display="none"` 子树、`visibility:hidden`、`opacity="0"`、`fill-opacity="0"` 一律不计入颜色、面积与图元数。`currentColor` 按 `color` 解析，未声明 `color` 时保留字面量。面积与留白遇到算不出的几何——`path` 的面积、带 `transform` 的位置——判「unmeasurable」并写明原因，不拿 0 或未变换坐标冒充结果；`area_unmeasured` / `bbox_unmeasured` 记录原因。颜色与描边按 CSS 继承取值：写在根 `<svg>` 上的 presentation attribute 与内联 `style=""` 都会向下继承，同元素上 `style` 压过属性，显式 `none` 终止继承。`<style>` 样式表按一套写明的最小 CSS 子集真正生效：选择器限类型 / 类 / ID 的组合，加后代与子代组合；声明限 `fill` / `stroke` / `stroke-width`（另放行四个不参与测量的描边属性）；级联顺序为 `!important` > 内联 `style` > 样式表 > presentation attribute，三个属性都向下继承。子集之外的任何写法——伪类、属性选择器、`@` 规则、`media`、未放行的属性——整份表拒绝并判 `STYLE-SHEETS` 失败，不半套应用：半套应用量出来的是没渲染过的数。命中规则数记在 `measured.style_rules`。描边按墨量计：`周长 × stroke-width`；同元素填充与描边并存时，填充色扣掉描边带压在填充上的内侧半幅（`周长 × width / 2`），接缝与端帽不计，椭圆周长用 Ramanujan 近似，`path` 仍判 unmeasurable。长度按绝对单位读：无单位、`px`、`pt`、`mm`、`cm`、`in` 换算成用户单位；`%` 与 `em` 判 unmeasurable 而不是 0，声明了却读不出的 `stroke-width` 判 `STROKE-WIDTHS` 失败，不按默认 1.0 计。颜色先归一再比较（`#F00`、`rgb(255,0,0)`、`#ff0000` 等价）；未声明 `fill` 按 `#000000` 计（`<line>` 没有内部，不计填充）；`fill-opacity="0%"` 按 0 计。`viewBox` 接受逗号分隔，根 `width`/`height` 接受带单位写法。规则文件里的非数值（`colors_max: "two"`、面积上限写成字符串）报 `E-SCHEMA` 退出 2，不再以 `E-INTERNAL` 崩溃。整张图没有任何可见墨量（`measured.painted == 0`，例如全透明）判 `PAINTED` 失败。

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
# RUN_ID：内容派生（见步 11），由 scripts/studio.py 的 content_run_id() 生成；缺省必 H3=fail
python scripts/aggregate_log.py --run-dir path/to/run --out path/to/run/acceptance.json --run-id "$RUN_ID"
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
- **全部 26 个脚本同契约**：统一 `L.run_cli`，损坏输入返回
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
**PDF/X 合规限定（C7，状态 A）**：关键条款已取证（ISO 15930 预览 / PRONOM / LoC / PDF Association / Adobe TN #5413 / ECI，见 `references/pdfx-requirements.md`），完整 ISO 文本未购；代码只证结构存在（标识串、输出意图、盒子、部分色彩算子），不证 ICC 有效性、字体全嵌入、透明度/图层或元数据完整。本链路不作为印厂合规依据，交付印厂前请用 Acrobat Preflight / callas pdfToolbox / veraPDF 独立核验。以下 PDF/X 相关断言均为「代码侧自洽」的实测记录，不是规范合规结论。
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

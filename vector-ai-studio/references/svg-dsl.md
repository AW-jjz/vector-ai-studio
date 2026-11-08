# SVG 生成侧契约（svg-dsl）

本文件是 C1 出图质量的唯一生成约束。先读「通用原则」，再看演示示例；示例不窄化规则。

## 通用原则（先于任何示例）

1. 冻结清单逐条覆盖：每个必备视觉元素都要在 SVG 里有可识别的对应图元，不遗漏、不臆造。
2. 色板内取值：所有 `fill` / `stroke` 取自 spec.json 冻结色板，或黑白灰；不引入色板外色值。
3. 语义分组命名：用可读语义的 `<g id>` 分组，不用 g1 / g2 自动编号；分组数不少于配置下限。
4. 纯矢量子集：只用矩形 / 圆 / 椭圆 / 线 / 折线 / 多边形 / 路径；不嵌位图、不引外部资源。
5. 构图可控：全部图元包围盒落在 viewBox 内，留白与视觉重心留在配置允许区间。
6. 线宽收敛：去重线宽数不超过配置上限，同一语义分组内复用同一色值。

以上六条是领域无关的通用规则，适用于任何题材的纯矢量图形。

## 冻结清单与色板 schema

由宿主 LLM 生成、`../scripts/freeze_spec.py` 校验，形状参照 `../templates/spec.template.json`：

```json
{
  "schema": "vector-ai-studio/spec-v1",
  "prompt": "用户当次需求原文",
  "elements": [{"id": "e1", "desc": "必备元素的可识别描述"}],
  "attributes": [{"id": "a1", "desc": "数量 / 方位 / 相对关系", "kind": "count"}],
  "palette": ["#2f6fed", "#111111", "#ffffff"],
  "rounds": {"current": 1, "K_max_ref": "config/thresholds.json"}
}
```

空 prompt、空清单、空色板或畸形 schema 一律显式拒绝并要求补充，不补默认值。

## 允许的 SVG 子集

- 根元素写 `viewBox` 与 `width` / `height`，画板尺寸与 viewBox 一致。
- 图元：`rect` / `circle` / `ellipse` / `line` / `polyline` / `polygon` / `path`。
- 属性：`fill` / `stroke` / `stroke-width` / `rx` / `ry` / `font-family`（可编辑文字时）。
- 分组：`<g id="语义名">`，可嵌套；分组结构在多轮改稿中保持不变。
- 静态校验由 `../scripts/sanitize_svg.py` 执行，指标计算由 `../scripts/score.py` 执行。

## 禁用构造（命中即退回重生成）

- `<image>`、`data:image`、base64、任何外链资源。
- `clipPath` / `filter` / `blend` / `mask` / `pattern` / `<use>` 等易被扁平化的构造。
- 零面积路径、重复点退化路径、出框裁切、超量色值或线宽。

## few-shot 演示（仅为演示，规则本身领域无关）

下面片段只演示「语义分组 + 色板内取值 + 无禁用构造」的写法，题材（徽章）不构成能力边界；换成图标、信息图、插画或任意主题，六条通用原则完全不变。

```
<svg viewBox="0 0 200 200" width="200" height="200">
  <g id="frame">
    <rect x="25" y="25" width="150" height="150" fill="none" stroke="#111111" stroke-width="3"/>
  </g>
  <g id="subject">
    <circle cx="100" cy="100" r="45" fill="#2f6fed"/>
  </g>
  <g id="accent-top">
    <rect x="70" y="40" width="60" height="12" fill="#ffb020"/>
  </g>
</svg>
```

遮住示例，通用原则仍应覆盖全部题材（rubric A3）。

## 多轮改稿约束

- `../scripts/iterate.py` 产出 revision.json，`change_only` 只含失败项，`must_keep` 锁定通过项。
- 下一轮只针对 `change_only` 改语义，`must_keep` 的分组集合、路径数、色值集合保持不变；`../scripts/sanitize_svg.py --prev` 做回归比对。
- 自然语言改稿内容由宿主 LLM 写，脚本只出结构化指令骨架。

## 校验入口

- 冻结校验：`../scripts/freeze_spec.py`
- 静态净化：`../scripts/sanitize_svg.py`
- 确定性打分：`../scripts/score.py`
- 阈值唯一事实源：`../config/thresholds.json`

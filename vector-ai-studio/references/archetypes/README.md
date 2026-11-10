# 原型范例（archetypes）

十个原创范例，对应 `design-language.md` 第 8 节的原型表。用途只有两个：给宿主 LLM 一个「合格长什么样」的参照，以及让 `../scripts/selftest.py` 的 A49 断言有可校验对象。

约定：

- 全部 240×240、纯矢量、无 `image` / `filter` / `clipPath` / `use` / 渐变。
- 每图 ≥3 个语义分组、≤5 个色值、≤2 种线宽。
- 范例是**语法示范**，不是可复用模板：直接改色改形会得到「像范例但不如范例」的结果；正确用法是照原型配方重新构图。

| 文件 | 原型 | 演示的规则 |
|---|---|---|
| `archetypes/01-swiss-mark.svg` | swiss-mark | 单一大色块 + 细网格线；一色点缀 |
| `archetypes/02-bauhaus-primary.svg` | bauhaus-primary | 基本形三种、面积主次分明 |
| `archetypes/03-japanese-ma.svg` | japanese-ma | 留白承担构图；一枚朱红控制视线 |
| `archetypes/04-constructivist-diagonal.svg` | constructivist-diagonal | 双对角带，角度一致 |
| `archetypes/05-isometric-cube.svg` | isometric-cube | 明度三阶区分三个面 |
| `archetypes/06-negative-space.svg` | negative-space | 实心块内挖形，边缘干净 |
| `archetypes/07-memphis-blocks.svg` | memphis-blocks | 三色块 + 同组点阵 + 底线 |
| `archetypes/08-editorial-rule.svg` | editorial-rule | 粗细对比建立层级 |
| `archetypes/09-orbit-mark.svg` | orbit-mark | 同心圆 + 一条斜切线 |
| `archetypes/10-pixel-grid.svg` | pixel-grid | 模块网格内的抽象字形 |

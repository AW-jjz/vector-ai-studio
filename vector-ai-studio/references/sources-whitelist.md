# 素材网站白名单与许可说明

版本：v5（2026-11-10 检索）
用途：为 `../scripts/conformance.py` 的 `licenses_allowed` 与 `../templates/source-ledger.json` 的 `license` 字段提供可核对取值。

## 0 三条硬规则

1. **标识主体不用现成素材。** 所有素材许可都不授予商标权，多数明令禁止用作 logo。产品标识、品牌主体必须自产（`license: own`）。
2. **聚合站标签不作数。** 聚合站上的图标，许可由原始出处决定，必须指向原始许可声明页。
3. **许可绑定版本与核实日期。** 账本必须记 `license_source` 与 `license_verified_at`，超过一年视为待复核。

## 1 核实等级

| 等级 | 定义 |
|---|---|
| E1 | 已读该站官方许可页或仓库 LICENSE 全文，2026-11-10 |
| E2 | 两个以上独立来源说法一致，未读官方页 |
| E3 | 仅见于聚合推荐列表，未交叉验证 |

**E3 不可直接登记。** 本轮实测反例：三篇 2026 推荐文都称 unDraw 为 MIT，实际读 https://undraw.co/license 是一份自造合同，含禁止 AI 训练条款。二手列表错了不是偶然，是结构性的。

## 2 图标库

| 来源 | 许可 | 可入产物 | 核实 | 关键限制 |
|---|---|---|---|---|
| Simple Icons | CC0 1.0 | 可以 | E1 | 3400+ 全是品牌 logo；CC0 不授予商标权 |
| Lucide | ISC，部分（源自 Feather）MIT | 可以 | E1 | 必须随产物保留声明，删注释即违约 |
| Heroicons | MIT | 可以 | E2 | 与 Hero Patterns 同作者，风格同源 |
| Tabler Icons | MIT | 可以 | E2 | 风格统一，适合系统化 |
| Phosphor | MIT | 可以 | E2 | 六种字重，适合需要粗细层次的场景 |
| Bootstrap Icons | MIT | 可以 | E2 | 与 BS 生态绑定 |
| Ionicons | MIT | 可以 | E2 | 三种风格 |
| Feather | MIT | 可以 | E2 | 280+，极简线条，已停止大更新 |
| IconPark | Apache 2.0 | 可以 | E3 | 字节出品，可一图四主题；Apache 含专利授权与 NOTICE 义务 |
| Material Symbols | Apache 2.0 | 可以 | E3 | 保留 NOTICE |
| SVG Repo | 站方自造 + 聚合 | **不可直接用** | E1 | 自称 proxy distributor，默认许可非 CC0 |
| Iconify | 聚合 | **不可直接用** | E3 | 同 SVG Repo 问题：许可按原始出处 |

## 3 插画

| 来源 | 许可 | 可入产物 | 核实 | 关键限制 |
|---|---|---|---|---|
| unDraw | 自定义合同（非 MIT / CC / OFL） | 有条件 | E1 | 二次核实文本一致（Copyright 2026）；免署名可商用，**禁止**打包再分发、做竞品、AI/ML 训练 |
| Open Doodles | CC0 | 可以 | E2 | 手绘涂鸦风 |
| Open Peeps | CC0 | 可以 | E2 | 人物拼装组件 |
| Humaaans | CC BY 4.0 | 有条件 | E2 | 需署名，风格偏 UI 场景 |
| DrawKit | 自定义，分免费/付费档 | 有条件 | E2 | 各档许可不同，须逐档读 |
| Blush | 混合（部分需付费） | 不可 | E3 | 免费额度限制，许可按素材集区分 |
| Pixels Market | 自定义 | 不可 | E3 | 未核 |

## 4 图案 / 纹理 / 装饰

| 来源 | 许可 | 可入产物 | 核实 | 关键限制 |
|---|---|---|---|---|
| FreeSVG.org | CC0（站方声明） | 有条件 | E1 | 社区上传站，CC0 由上传者声明，**不能保证上传者有权 CC0**；须人工看单页来源 |
| Hero Patterns | CC BY 4.0 | 有条件 | E2 | 需署名；一手站首页未列许可文本，登记前须再确认 |
| ambientCG | CC0 | 可以 | E2 | 纹理为主，可含原始文件再分发 |
| Poly Haven | CC0 | 可以 | E2 | 同上，质量高、更新活跃 |
| SVG Backgrounds | 自定义 | 不可 | E3 | 未核 |
| Doodad Pattern Generator | 生成器产物 | 可以 | E3 | 生成器，不是素材库；须确认生成条款 |

## 5 字体

| 来源 | 许可 | 可入产物 | 核实 | 关键限制 |
|---|---|---|---|---|
| Google Fonts | 逐款 OFL 1.1 或 Apache 2.0 | 可以 | E2 | 官方 FAQ 抓取失败，无法升 E1；必须逐款看字体页，不能按「都是 OFL」登记 |
| Adobe Source 系列（Source Sans 3 / Source Serif 4 / Source Code Pro） | OFL 1.1 | 可以 | E2 | 整族单一机构出品，许可一致，可整套取用 |
| Fontshare（Indian Type Foundry） | 自定义，免费商用 | 有条件 | E3 | 首页只有字表无许可文本；Satoshi / Clash Display / General Sans 等可变字体质量高，但须读其 FAQ |
| Font Squirrel | 逐款，多为可商用 | 有条件 | E3 | 人工筛选，但逐款许可不同，不能整站记 |
| openfontlicense.org | OFL 目录 | 可以 | E2 | 目录站，非分发方 |
| Geist（Vercel） | 未核 | 不可 | E3 | 仅见推荐文，未读许可 |

字体三条要点：

1. **OFL 允许嵌入与商用，但 Reserved Font Name 限制「改名后才可修改分发」**。轮廓化后使用不触发该限制；保留字体文件再分发会触发。
2. 「Google Fonts 上都是 OFL」是错的，其中含 Apache 2.0 字体。整站登记许可必然失真。
3. **桌面许可不自动包含自托管 webfont 权利**。这是字体许可里最常见的越界。

## 6 配色

### 6.1 工具（产出色值，无许可风险）

| 工具 | 能力 | 备注 |
|---|---|---|
| Coolors | 生成器 + 社区色板 + 对比检查 | 免费档含广告；导出色值不受限 |
| Adobe Color | 生成器 + WCAG 2.1 AA/AAA 实时对比 | 无障碍检查最完整 |
| Khroma | 基于偏好训练的个性化调色板 | 需先选色训练 |
| Accessible Palette | OKLCH 取色 + WCAG 与 APCA 双显示 | 适合同时看两套标准 |
| Polychrom | APCA 对比计算 | 专注 APCA |
| Stark | 对比度 / 色觉缺陷模拟插件 | 有免费档 |
| ColorHexa | 色值转换与同色系推导 | 工具型 |
| ColorBox（Lyft） | 按感知均匀插值生成色阶 | 适合做 50–950 |

### 6.2 色板（可直接当 design tokens）

| 来源 | 许可 | 备注 |
|---|---|---|
| Tailwind Colors | MIT | 11 阶，工程化程度最高 |
| Radix Colors | 开源 | 含深色模式与语义角色映射 |
| Open Color | MIT | 轻量十阶，体量小 |
| Material Theme Builder | Apache 2.0 | 从品牌色推导完整色调 |

### 6.3 算法层（2026 现状）

- **OKLCH**：感知均匀色彩空间，做色阶时明度步进才均匀。新工具普遍已支持。
- **APCA**：WCAG 3 候选对比算法，输出 Lc 值（-108 ~ +106）。**但 WCAG 2.1 仍是现行合规标准，APCA 尚未取代它**；把 APCA 结果当合规依据是错的。
- CSS `color-contrast()` 截至 2026 年 5 月仍在 Safari TP / Chrome Canary 实验阶段，不能作为交付依赖。

事实：单纯的颜色组合不构成受版权保护的作品（法律常识判断，非本站核实）。配色站不存在许可风险，风险只在字体、图标、插画、纹理。

## 7 不属本表的类别

视觉参考站（MeiGen、Pinterest、Cosmos）与 AI 生成工具**不提供可入产物的素材，因此不属素材库，本表不评其许可**。它们的使用方式与边界见 `design-language.md` §8.1。

本表只收录「其内容可能进入产物」的来源。

## 8 与 conformance / 账本的对接

当前默认 `licenses_allowed` 为 `["own","cc0","public-domain","commercial"]`。建议改为显式许可名，废弃 `commercial`——它描述用途不是许可，会把 CC BY 4.0 与 CC0 混为一类。

```
own, cc0, public-domain, mit, isc, apache-2.0, cc-by-4.0, ofl-1.1, custom
```

`custom` 仅用于 unDraw / iconmonstr / DrawKit 这类自造条款，必须同时给 `license_source`，由人判断是否落在允许用途内。

账本建议增加两字段：

| 字段 | 含义 |
|---|---|
| `license_source` | 许可声明页 URL，不是素材下载 URL |
| `license_verified_at` | 人核实日期，YYYY-MM-DD |

判定建议：`license` 在白名单内 **且** `license_source` 非空 **且** `license_verified_at` 一年内，三项齐才通过；缺任一项记为「未验证」，不是「通过」。

## 9 站点筛选标准（为什么是这些）

1. **整库单一许可** 优于逐条不同：可一次核实，账本记账成本低。
2. **原生 SVG** 优于 PNG 转换：符合本项目纯矢量约束。
3. **2025–2026 仍在更新**：本次检索只收录活跃维护者。
4. **条款不禁止本项目主场景**：标识、商用交付、可继续编辑。
5. **可定位到原始许可页**：能填 `license_source`。

## 10 已知失效模式

| 失效 | 后果 |
|---|---|
| 用聚合站标签当授权 | 实际许可可能更严（如 CC BY-SA），商用即侵权 |
| 相信推荐文里的许可名 | 实测 unDraw 被三家误标为 MIT |
| 删掉内嵌许可注释 | MIT / ISC / CC BY 均因此违约 |
| 把品牌 logo 当自有标识 | 触及商标法，与版权许可无关 |
| 账本写 `commercial` | 无法区分需署名与免署名，审计不可判 |
| 社区上传站的 CC0 声明 | 上传者未必有权 CC0，声明不等于权利来源 |
| 许可页改版后沿用旧记录 | 记录失真，须按 `license_verified_at` 过期复核 |

## 11 边界

「最全」不可达成，世界上素材站无穷且持续增减。本文档给的是覆盖七个主类别的最优集，扩充入口是 §9 的五条标准，不是继续堆列表。E2 / E3 条目在正式登记前必须升到 E1。

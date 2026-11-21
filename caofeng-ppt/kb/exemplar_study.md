# 语料示范质量分层（2224 页全量，非抽样）

好示范 835 页（37.5%）｜坏示范 1389 页（62.5%）｜整册过半是坏示范的模板 117 个

## 1 判据（每条都是可复核的正则或阈值）

- 广告推广页：share_channel｜more_templates｜shop_ad｜tutorial_promo｜legal_boilerplate｜closing_cliche
- 商品编号当主视觉：`^(NO\.?\s?\d+|第?\s*\d+\s*套)\b`
- 空页（无字、无媒体、无动画）
- 密集文字墙：单页 >=300 字
- 没有视觉层级：整页最大字号 <14pt
- 字压亮底：文字区背景亮度中位 >0.55

## 2 坏示范构成（命中次数）

- empty_page: 560
- ad:share_channel: 496
- text_on_bright_bg: 346
- dense_text_wall: 70
- no_type_hierarchy: 20
- product_number_hero: 16
- ad:more_templates: 7
- ad:closing_cliche: 1
- ad:shop_ad: 1

## 3 版式原型占比：原始 vs 只算好示范

| 原型 | 原始页 | 原始占比 | 好示范页 | 好示范占比 |
|---|---|---|---|---|
| pure-visual | 744 | 33.5% | 184 | 22.0% |
| full-bleed | 608 | 27.3% | 487 | 58.3% |
| split-centre-image | 504 | 22.7% | 49 | 5.9% |
| text-block | 114 | 5.1% | 19 | 2.3% |
| icon-accent | 58 | 2.6% | 13 | 1.6% |
| photo-wall | 51 | 2.3% | 24 | 2.9% |
| split-left-image | 50 | 2.2% | 5 | 0.6% |
| bg-blip-text-only | 43 | 1.9% | 26 | 3.1% |
| pic-inset | 21 | 0.9% | 11 | 1.3% |
| card-grid | 8 | 0.4% | 4 | 0.5% |
| timeline-on-line | 7 | 0.3% | 5 | 0.6% |
| centred-statement | 6 | 0.3% | 6 | 0.7% |
| split-right-image | 5 | 0.2% | 1 | 0.1% |
| stack-top-image | 3 | 0.1% | 0 | 0.0% |
| text-from-layout-placeholder | 1 | 0.0% | 0 | 0.0% |
| stack-bottom-image | 1 | 0.0% | 1 | 0.1% |

## 4 色板家族：全部册 vs 好示范册

- blue: 155 册 -> 好示范册 45
- yellow/gold: 5 册 -> 好示范册 3
- red: 4 册 -> 好示范册 2
- cyan: 3 册 -> 好示范册 3
- orange/amber: 2 册 -> 好示范册 0
- magenta: 1 册 -> 好示范册 1

## 5 好示范页自己的量纲

- chars_median: 19
- max_pt_median: 66
- anim_pages: 668
- media_pages: 660

坏示范页对照：字数中位 41，最大字号中位 28

## 6 过半是坏示范的模板（做片子时不要参考这几册）

- 产品需求文档.pptx：41/42 页坏示范
- PPT动画音频效果.pptx：38/39 页坏示范
- 转正升职竞聘述职PPT模版.pptx：27/32 页坏示范
- 【PPT模版】年终工作汇报总结模版.pptx：26/29 页坏示范
- 【PPT模版】文字排版.pptx：26/29 页坏示范
- PPT图标素材.pptx：18/18 页坏示范
- 【PPT模版】党建红色主题中国风模版.pptx：17/22 页坏示范
- PPT多图排版.pptx：16/16 页坏示范
- PPT随机抽奖模版.pptx：16/20 页坏示范
- PPT动态表格.pptx：15/16 页坏示范
- NO.100 PPT千锤百炼动态封面（含教程）.pptx：14/17 页坏示范
- NO.74 PPT创赛级国风模版.pptx：14/20 页坏示范
- NO.77 PPT创意图标条形图.pptx：14/14 页坏示范
- NO.79 PPT创意图标柱状图.pptx：14/14 页坏示范
- NO.85 PPT玻璃质感立体柱状图.pptx：14/14 页坏示范
- PPT可视化动态饼图.pptx：14/15 页坏示范
- 【PPT模版】随机抽签.pptx：14/18 页坏示范
- PPT动态圆环年中工作汇报模版.pptx：13/22 页坏示范
- PPT动态轮盘（工作汇报模版）.pptx：13/22 页坏示范
- NO.102 PPT展翅高飞（含教程）.pptx：12/15 页坏示范
- PPT动态折线面积图（含模版+教程）.pptx：12/13 页坏示范
- 【PPT模版】动态折线图.pptx：12/13 页坏示范
- NO.101 PPT动态转盘（含教程）.pptx：11/13 页坏示范
- NO.89 PPT蓝色透明玻璃质感图标.pptx：11/11 页坏示范
- PPT动态条形图.pptx：11/12 页坏示范

## 7 广告页文本样本（判据取证）

- 360°全方位产品介绍PPT模版.pptx p013：制作动态排名图 P P T P P T 第三方作者 [外部下载链接已移除] 下载地址： 
- 360°全方位产品介绍PPT模版.pptx p015：新旧对比 模版 P P T P P T 第三方作者 [外部下载链接已移除] 下载地址： 
- 360°全方位产品介绍PPT模版.pptx p016：多段文字 排版 P P T P P T 第三方作者 [外部下载链接已移除] 下载地址： 
- 360°全方位产品介绍PPT模版.pptx p017：用 AI 制作 动态封面 P P T P P T 第三方作者 [外部下载链接已移除]
- 360°全方位产品介绍PPT模版.pptx p018：图片动态轮播模版 P P T P P T 第三方作者 [外部下载链接已移除]
- NO.100 PPT千锤百炼动态封面（含教程）.pptx p005：视频教程 / 介绍 模版下载 [外部下载链接已移除] [提取码已移除] https://v
- NO.100 PPT千锤百炼动态封面（含教程）.pptx p006：视频教程 / 介绍 模版下载 [外部下载链接已移除] [提取码已移除] https://v
- NO.100 PPT千锤百炼动态封面（含教程）.pptx p007：创意动态 封面模版 P P T P P T 第三方作者 [外部下载链接已移除] 下载地址
- NO.100 PPT千锤百炼动态封面（含教程）.pptx p008：视频教程 / 介绍 模版下载 [外部下载链接已移除] [提取码已移除] https://v
- NO.100 PPT千锤百炼动态封面（含教程）.pptx p009：视频教程 / 介绍 模版下载 [外部下载链接已移除] [提取码已移除] https://v
- NO.100 PPT千锤百炼动态封面（含教程）.pptx p010：视频教程 / 介绍 模版下载 [外部下载链接已移除] [提取码已移除] https://v
- NO.100 PPT千锤百炼动态封面（含教程）.pptx p012：数据分析 可视化模版 P P T P P T 第三方作者 [外部下载链接已移除] 下载地

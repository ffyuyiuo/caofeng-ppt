# 171 个模板的技法台账（全部来自 OOXML 实测，非推测）

模板数=171  幻灯片数=2224  含内嵌媒体的页=1181

## 1 转场

- 类型分布: [('2006/fade', 213), ('2006/morph', 192), ('2006/sndAc', 104), ('2006/blinds', 35), ('2006/prstTrans', 10), ('2006/checker', 10), ('2006/prism', 8), ('2006/reveal', 1)]
- morph option: [('byObject', 192)]
- 显式时长(ms): [('2000', 218), ('1250', 9), ('2500', 5), ('4000', 5), ('0', 2), ('6000', 1), ('3400', 1), ('700', 1), ('10', 1)]

## 2 动画原语与节奏

- presetClass: [('mediacall', 2128), ('entr', 851), ('exit', 108), ('path', 70), ('emph', 39)]
- 时间线元素: [('cmd', 2128), ('video', 1034), ('set', 959), ('anim', 785), ('animEffect', 705), ('animScale', 85), ('animMotion', 71), ('audio', 58)]
- nodeType: [('clickEffect', 1479), ('tmRoot', 1222), ('mainSeq', 1221), ('interactiveSeq', 1053), ('afterEffect', 898), ('withEffect', 819)]
- 常见 delay(ms): [('0', 3760), ('null', 2443), ('500', 84), ('1000', 63), ('2000', 37), ('1500', 33), ('3000', 24), ('2500', 18), ('4000', 16), ('400', 14), ('1300', 13), ('3500', 10)]
- preset 组合 Top40 (class:id:subtype -> 次数):

  - mediacall:1:0 -> 1092
  - mediacall:2:0 -> 1036
  - entr:10:0 -> 209
  - entr:22:8 -> 109
  - entr:53:16 -> 74
  - entr:22:4 -> 66
  - exit:10:0 -> 61
  - entr:23:528 -> 60
  - entr:1:0 -> 41
  - entr:2:8 -> 40
  - entr:22:1 -> 35
  - entr:2:2 -> 32
  - entr:42:0 -> 32
  - entr:22:2 -> 32
  - exit:1:0 -> 27
  - entr:2:1 -> 25
  - emph:6:0 -> 21
  - path:63:0 -> 20
  - entr:2:4 -> 19
  - entr:16:37 -> 19
  - path:35:0 -> 14
  - path:64:0 -> 14
  - entr:14:10 -> 14
  - emph:8:0 -> 12
  - exit:22:8 -> 12
  - path:0:0 -> 11
  - path:42:0 -> 10
  - entr:47:0 -> 9
  - entr:21:1 -> 7
  - entr:26:0 -> 7
  - entr:23:32 -> 4
  - entr:12:2 -> 4
  - emph:3:2 -> 3
  - entr:16:21 -> 2
  - entr:2:6 -> 2
  - entr:2:9 -> 2
  - emph:35:0 -> 2
  - entr:49:0 -> 2
  - exit:2:2 -> 1
  - emph:34:0 -> 1

- 运动路径样本(前6条,截断):
  - M -2.5E-6 0 L -0.38932 0 
  - M -2.5E-6 0 L -0.38932 0 
  - M -2.5E-6 0 L -0.38932 0 
  - M 2.70833E-6 4.44444E-6 L 2.70833E-6 -1.58172 
  - M 0 -0.00069 L 0.03086 -0.01921 L 0.03451 -0.01088 L 0.05547 -0.0324 L 0.05547 -0.0324 L 0.05977 -0.02569 L 0.07487 -0.02569 L 0.07813 -0.0324 L 0.09063 -0.0275
  - M -3.125E-6 -2.22222E-6 L 0.00052 -1.66597 

## 3 形状与效果

- 形状统计: [('sp', 20332), ('prst:rect', 14286), ('custGeom', 7785), ('pic', 5717), ('grpSp', 3312), ('prst:roundRect', 1948), ('prst:ellipse', 1287), ('cxnSp', 1131), ('prst:line', 1127), ('prst:arc', 139), ('prst:trapezoid', 120), ('graphicFrame', 83), ('prst:halfFrame', 70), ('prst:triangle', 69), ('prst:can', 42), ('prst:parallelogram', 34), ('prst:star5', 30), ('prst:donut', 29), ('prst:pie', 25), ('tbl', 22), ('prst:flowChartOffpageConnector', 16), ('prst:round2SameRect', 12), ('prst:wedgeRoundRectCallout', 5), ('prst:chevron', 5), ('prst:straightConnector1', 4), ('prst:snip2DiagRect', 3), ('prst:flowChartTerminator', 2), ('prst:downArrow', 2), ('prst:snip1Rect', 1), ('prst:round2DiagRect', 1)]
- 效果统计: [('lumMod', 7864), ('softEdge', 1051), ('reflection', 348), ('satMod', 196), ('sp3d', 170), ('scene3d', 154), ('glow', 88), ('duotone', 74), ('tint', 30), ('bevelT', 11), ('biLevel', 3)]
- 背景类型: [('blipFill', 689), ('gradFill', 40), ('solidFill', 20)]

## 4 字体与字号

- 字体 Top25: [('等线', 11233), ('微软雅黑', 5287), ('Hanson', 3848), ('+mn-lt', 3460), ('思源黑体 CN Bold', 2158), ('等线 Light', 1762), ('思源宋体 CN Medium', 1105), ('思源黑体', 838), ('Arial', 825), ('思源宋体 CN Heavy', 646), ('+mn-ea', 394), ('江西拙楷', 293), ('MiSans Semibold', 234), ('arial', 213), ('+mj-ea', 196), ('黑体', 176), ('MiSans Light', 117), ('思源黑体 CN Normal', 114), ('鸿雷板书简体', 112), ('Calibri', 88), ('演示春风楷', 86), ('仓耳周珂正大榜书', 77), ('Helvetica Neue', 75), ('PingFangSC-Medium', 73), ('Barlow Bold', 67)]
- 字号(pt)分布: [('80', 2908), ('18', 2365), ('16', 2208), ('14', 1512), ('12', 1220), ('60', 1136), ('22', 722), ('66', 683), ('28', 547), ('20', 534), ('10', 407), ('24', 345), ('11', 311), ('40', 268), ('32', 228), ('54', 162), ('250', 160), ('36', 133), ('44', 122), ('9', 110), ('8', 77), ('96', 66), ('48', 62), ('4', 55), ('13', 52), ('100', 49), ('200', 49), ('180', 48), ('72', 47), ('42', 44)]
- 高频色值 Top30: [('FFFFFF', 1794), ('000000', 1053), ('4472C4', 965), ('191A1B', 956), ('2F2F2F', 800), ('444444', 478), ('030303', 478), ('202020', 478), ('4E6172', 416), ('016DFF', 253), ('009EFA', 237), ('00FFFF', 209), ('6B95F2', 160), ('FFC000', 143), ('9F070E', 134), ('448FE2', 111), ('136EBF', 99), ('66D9F7', 96), ('003069', 84), ('03A2FF', 76), ('535454', 67), ('F5DC46', 54), ('404F5F', 51), ('333333', 50), ('FCDC4A', 50), ('2972F5', 45), ('11508C', 44), ('A50E13', 42), ('FF0000', 38), ('5C3511', 37)]

## 5 文本密度

- 每页字符数: 中位 24, 均值 72.4, 最大 3120, 空页比例 33.5%

## 6 每模板指纹（动画量/媒体量）

- PPT动画音频效果.pptx: ctn=362 media=69 custGeom=52 grpSp=12 pic=45 sp=119
- PPT竞赛答题模版.pptx: ctn=351 media=29 custGeom=65 grpSp=15 pic=35 sp=162
- NO.75 PPT国风文字飞出效果.pptx: ctn=332 media=11 custGeom=52 grpSp=13 pic=34 sp=149
- 转正升职竞聘述职PPT模版.pptx: ctn=316 media=10 custGeom=251 grpSp=117 pic=203 sp=665
- 【PPT模版】年终工作汇报总结模版.pptx: ctn=268 media=10 custGeom=203 grpSp=125 pic=24 sp=748
- 创意动态时间轴.pptx: ctn=237 media=20 custGeom=83 grpSp=48 pic=47 sp=216
- 创意目录页.pptx: ctn=211 media=23 custGeom=133 grpSp=78 pic=69 sp=500
- 地图缩放定位.pptx: ctn=228 media=17 custGeom=95 grpSp=25 pic=24 sp=215
- PPT动态笔刷开场模版.pptx: ctn=163 media=37 custGeom=68 grpSp=27 pic=73 sp=142
- 产品需求文档.pptx: ctn=237 media=12 custGeom=115 grpSp=42 pic=48 sp=276
- NO.176  PPT海浪动态封面（含教程）.pptx: ctn=212 media=17 custGeom=3 grpSp=4 pic=28 sp=54
- NO.85 PPT玻璃质感立体柱状图.pptx: ctn=185 media=22 custGeom=144 grpSp=34 pic=40 sp=292
- PPT可视化动态饼图.pptx: ctn=175 media=25 custGeom=144 grpSp=69 pic=30 sp=299
- NO.68 PPT动态时间轴.pptx: ctn=183 media=21 custGeom=65 grpSp=34 pic=28 sp=176
- PPT照片墙扩散轮播效果.pptx: ctn=192 media=18 custGeom=65 grpSp=15 pic=36 sp=111
- PPT动态圆环年中工作汇报模版.pptx: ctn=131 media=38 custGeom=120 grpSp=42 pic=19 sp=381
- PPT动态轮盘（工作汇报模版）.pptx: ctn=131 media=38 custGeom=120 grpSp=42 pic=19 sp=381
- PPT动态表格.pptx: ctn=162 media=27 custGeom=157 grpSp=36 pic=31 sp=266
- NO.74 PPT创赛级国风模版.pptx: ctn=169 media=22 custGeom=15 grpSp=66 pic=142 sp=158
- PPT科技风动态轮盘目录页.pptx: ctn=164 media=22 custGeom=120 grpSp=32 pic=25 sp=232
- NO.77 PPT创意图标条形图.pptx: ctn=166 media=20 custGeom=130 grpSp=30 pic=24 sp=222
- NO.155 PPT动态破浪时间轴（含教程）.pptx: ctn=162 media=19 custGeom=0 grpSp=12 pic=49 sp=51
- 【PPT模版】动态折线图.pptx: ctn=159 media=20 custGeom=132 grpSp=34 pic=22 sp=231
- PPT动态条形图.pptx: ctn=161 media=19 custGeom=104 grpSp=34 pic=24 sp=195
- NO.95 PPT帆船动态开场页面（含教程）.pptx: ctn=148 media=21 custGeom=67 grpSp=21 pic=27 sp=127
- PPT动态折线面积图（含模版+教程）.pptx: ctn=147 media=21 custGeom=117 grpSp=30 pic=26 sp=205
- NO.79 PPT创意图标柱状图.pptx: ctn=147 media=20 custGeom=130 grpSp=30 pic=26 sp=226
- NO.119 PPT图片墙.pptx: ctn=142 media=19 custGeom=48 grpSp=21 pic=86 sp=72
- PPT商品一键换色模版.pptx: ctn=153 media=15 custGeom=78 grpSp=18 pic=22 sp=137
- NO.97 PPT万物生长效果（含教程）.pptx: ctn=134 media=21 custGeom=66 grpSp=19 pic=29 sp=121
- PPT形状创意页面.pptx: ctn=123 media=24 custGeom=25 grpSp=21 pic=90 sp=180
- NO.171 科技风动态扫描展示页（含教程）.pptx: ctn=131 media=21 custGeom=0 grpSp=5 pic=39 sp=30
- NO.93 PPT动态封面（含教程）.pptx: ctn=130 media=21 custGeom=53 grpSp=22 pic=29 sp=115
- PPT创意动态封面.pptx: ctn=129 media=21 custGeom=67 grpSp=20 pic=25 sp=132
- PPT3D动态人物介绍.pptx: ctn=144 media=15 custGeom=65 grpSp=19 pic=20 sp=141
- NO.170 PPT科技风动态点亮展示页（含教程）.pptx: ctn=129 media=19 custGeom=0 grpSp=6 pic=40 sp=32
- PPT动态柱状图.pptx: ctn=141 media=15 custGeom=78 grpSp=28 pic=20 sp=151
- PPT产品参数对比模版.pptx: ctn=134 media=16 custGeom=104 grpSp=28 pic=23 sp=214
- NO.87 PPT动态封面.pptx: ctn=131 media=16 custGeom=54 grpSp=17 pic=19 sp=103
- 【PPT模版】笔刷擦除模版.pptx: ctn=126 media=17 custGeom=98 grpSp=23 pic=20 sp=168

## 好示范配方（2026-11-07 第八轮：用户指"把错误示范学到了"后，重渲染本项目技法册逐页提取）

坏示范长什么样（已被否的两版成品）：直角方框贴_stock 照片_、平涂单色竖条、20–30pt 小标题、
表格缩在左上角、下半页空渐变。下面四条才是本项目模板与教程视频的精髓，均已落进装配引擎：

1. **图进形状不进框**（NO.103 菱形介绍 p2、NO.119 图片墙 p2、NO.127 拼贴风 p3）：
   照片用 prstGeom 遮罩切成 diamond / ellipse / roundRect / snip2DiagRect，或多块倾斜圆角卡片拼贴；
   图文左右分置（左大标题+段落，右形状图组）。实现：`caofeng.add_picture_in_box(shape=…)`，
   分栏页 hero 图默认 diamond，栅格 tile 默认 roundRect。
2. **数字是页面主角**（NO.116 数字滚动时间轴 p2/p3：满幅历史画 + 约 200pt 年份 + 书法体标题 + 底部小字段落）：
   陈述/隔页的大数字上限提到语料 centred-statement 档（180pt），标题 40pt+，正文 14–16pt 压底；
   数据页两个并列数字走页底一排大数字+半字号单位+小注释（`data_layout.number_row`）。
3. **图表是设计出来的**（教程 1ea8b415/06811c99 原话：绘制矩形设置阴影承载数据；放大数据颜色强调；
   增长/下降不同颜色并绘箭头；圆形里放图标；立方体渐变复制填充进柱状图；缩小间隙宽度）：
   实现：`data_layout.bar_group` = 半透明圆角承载面板 + 柱身竖向渐变（上亮下深）+ 最大柱换强调色
   + 柱顶白字加粗 26pt 档 + 柱底类目小字；不再平涂、不再青字压亮柱。
4. **表格铺满页**（语料台账行高中位 1.10in、表头 20pt/正文 16pt）：`caofeng.add_table` 行高按留白区摊满
   （0.45–1.10in），数字列右对齐、每列最大值亮青加粗（语料 48% 单元格加粗的习惯）。

配套构图骨架见 SPEC §12"构图骨架"：正文 ≤2 行不派分栏（图铺满压字）、满幅蒙版两头暗中间亮、
字号阶梯按 archetype_max_pt_median 给。验收一律真渲染逐页目检 + 与本项目同版式页并排比。

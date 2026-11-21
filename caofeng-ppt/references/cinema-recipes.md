# 电影感配方（20 张技法卡 + 教程原话）

机器可读版在 `kb/deck_tech.json`；教程逐字稿随 skill 发在 `kb/transcripts/`（252 份，源自
`CAOFENG_WORK/transcripts/`；253 条去重视频的索引在 `kb/video_index.json`）。下面的数值全部来自 171 模板 OOXML 实测或讲解原话，**不是建议值**。

## 这 20 张卡谁来产出（先看清这条界线）

分镜里 `tech_recipe` 声明的卡，只要落在下面"引擎产出"那一档（表里共 16 张），`build` 就必须真写进成品 XML。
其中 **12 张由 G6 按 `caofeng.py` 的 `RECIPE_TOKENS` 逐页数 token**（声明了没写直接判不合格；
键名与 `kb/deck_tech.json` 的卡 id 一致）；另 4 张（`click-gated-motion`/`wipe-family`/
`video-click-pause`/`exemplar-policy`）不在 token 表里，由 G18/G19 按成品 timing 与坏示范判据验。
标"手工"的是在 PowerPoint 里点的操作，引擎不产也不谎称产，闸不拦。

| 技法卡 | 产出方 | 成品里怎么验 |
|---|---|---|
| `video-fullbleed` | 引擎（`attach_video` + `inject_autoplay_timing`） | G11 内嵌 mp4 字节与时长、G15 首帧链、G3b 内嵌分辨率 |
| `morph-camera` | 引擎（`add_morph` + `share_anchor`） | G14 逐页锚点配对、G6 `p14:dur` 1500–2500ms |
| `mask-dim` | 引擎（`add_scrim`） | G4 渲染像素实测对比度、G6 数 `a:gradFill` |
| `giant-numeral` | 引擎（`place_text` 用 `HERO_BIG` 排 150pt） | G6 该页须有 ≥100pt 的 `a:rPr sz` |
| `gif-motion-element` | 引擎（内嵌 GIF，`make_gif.py` 出透明底） | G11 帧数/短边/透明底、G3 内嵌字节绑定 |
| `text-animation-presets` | 引擎（`inject_entrance(preset=...)` 按 `corpus_tables.ANIM` 出三元组） | G6 数 entr/emph/path 节点、G18 三元组与 filter 必须在成品 timing 里数得到 |
| `click-gated-motion` | 引擎（`node_type` 可写 afterEffect/withEffect/clickEffect，delay 取实测阶梯） | G6/G18 |
| `palette-lock` | 引擎（`apply_theme` 写 `theme1.xml` accent1..6 + 全片取色同一块板） | **G16 逐页彩色必须在色板内 + theme accent1 等于主色**、G2 色彩闭集 |
| `table-motion` | 引擎（`add_page_transition` 写 `p:checker`/`p:blinds`） | G6 数转场 token、G13 逐格核对、G7 真 `a:tbl` |
| `3d-model-depth` | 引擎（`add_3d_panel` 写 `a:sp3d`+`a:scene3d`+反射） | G6 数 `a:sp3d`、G8 `CT_SpPr` 顺序与展台压行 |
| `blip-duotone` | 引擎（`add_duotone` 挂 `a:duotone`，非破坏） | G6 数 `a:duotone`、G4 对比度（锁色改明度） |
| `emphasis-scale` | 引擎（`inject_emphasis_scale` 写 `p:animScale`） | G6 数 `p:animScale`、G8 版式 |
| `split-reflow` | 引擎（`layout_for` + `add_picture_in_box` + `split_zone_fits` 退回满幅） | **G17 用与语料同一份 `deck_measure` 量成品 bbox**、G5 留白 |
| `wipe-family` | 引擎（`_inject_preset` 生成 animEffect/anim/set 三件套，含出场） | **G18 三元组 + filter 字符串**、G6 节奏 |
| `video-click-pause` | 引擎（`inject_video_click` 补 interactiveSeq + togglePause） | **G18 视频页三样缺一即拦** |
| `exemplar-policy` | 引擎（不产广告页/空页；`pick_pt` 保层级；满幅必压蒙版） | **G19 逐页判据（与 `grade_exemplars.py` 同源 BAD_RULES）**、G4 |
| `bool-shape` | 手工（PowerPoint 布尔/裁切） | 无闸——别声明了指望它自动出现 |
| `motion-path` | 手工（PowerPoint 直线/路径动画） | 无闸——滚动 marquee 要复制内容手设 |
| `softedge-glow` | 手工（PowerPoint 形状效果） | 无闸——`field_bg` 的柔边是画在 JPG 里的，不是 OOXML 效果 |
| `sound-cue` | 手工（PowerPoint 插入音频） | 无闸——版权与体积都要人判断 |

## 语料的真实构成（先理解再模仿）

| 事实 | 数值 | 含义 |
|---|---|---|
| 幻灯片总数 | 2224 | |
| **好示范页 / 坏示范页** | **835 / 1389** | 六成页面不能当范本，学习对象只取前者（`kb/exemplar_study.md`） |
| 满幅压字页占好示范 | **58.3%**（487 页） | 主力版式；原始占比 27.3% 是被广告页稀释的 |
| 图文分栏页占好示范 | 5.9%（49 页） | 图上不压字，读数据页用；原始 22.7% |
| 主用彩色是蓝的好示范册 | **45/54** | 4472C4 / 086DB5 是语料真正的底色；红金青是点缀 |
| 含内嵌媒体的页 | 1187（53%） | 一半以上的页靠素材不靠排版 |
| 视频页带点击暂停链 | **1042 页 / 165 册** | `playFrom` 自动播 + `togglePause` 点击停，两条都要写 |
| `mediacall` 触发次数 | **2128** | 电影感主力=整页视频 |
| `entr` 进入动画 | 851 | |
| `path` 运动路径 | 70 | 用得比想象少 |
| `morph` 平滑 | **192**（option 全 byObject） | 第二主力，当运镜用 |
| `fade` 转场 | 213 | morph 的 Fallback |
| `sndAc` 音效 | 104 | 点睛，一册 ≤3 次 |
| `lumMod` 亮度调整 | **7864** | 压暗蒙版是最高频手法 |
| `custGeom` 自定义几何 | 7785 | 布尔运算产物 |
| `blipFill` 整页图背景 | 689 页 | |
| 每页中位字数 | **23** | 一页一意 |
| 几乎无字的页 | **33.5%** | |
| 字号 80pt 出现 | 2908 次 | 大数字/大标题是主视觉 |
| 字号 250/200/180/100pt | 160/49/48/49 次 | 里程碑数字敢给到 250pt |

## 卡 1 `video-fullbleed` 整页视频背景

**何时用**：封面、章节隔页、结尾页。
**参数**：视频 5–10 秒；16:9；分辨率 ≥1920×1080；音量 `vol="80000"`。
**讲解原话**：
- 「设置素材在 PPT 演示时自动播放」（NO.161）
- 「在动画窗格里，设置音频自动播放，延迟 0.3 秒」（NO.71 水墨开场）
- 「我们把刚刚制作好的视频导入到 PPT 里，然后在播放菜单里设置自动播放」（制作动态排名图）
- 「设置与上一动画同时开始，**持续时间和延迟时间根据视频设置**」（NO.151/152/171 反复出现）
  → 这条是同步纪律：文字动画的时长/延迟必须对齐视频节奏，否则字比画面先出。

**OOXML**（照抄语料结构，已在本技能 `caofeng.py attach_video` 实现并 WPS 实测通过）：

```xml
<!-- rels：同一个 mp4 要两条关系 -->
<Relationship Id="rId1" Type="http://schemas.microsoft.com/office/2007/relationships/media" Target="../media/media1.mp4"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/video" Target="../media/media1.mp4"/>

<p:pic>
 <p:nvPicPr>
  <p:cNvPr id="4" name="…"><a:hlinkClick r:id="" action="ppaction://media"/></p:cNvPr>
  <p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr>
  <p:nvPr><a:videoFile r:link="rId2"/>
   <p:extLst><p:ext uri="{DAA4B4D4-6D71-4841-9C94-3DE7FCFB9230}">
    <p14:media r:embed="rId1"/></p:ext></p:extLst></p:nvPr>
 </p:nvPicPr>
 <p:blipFill><a:blip r:embed="rId海报"/><a:stretch><a:fillRect/></a:stretch></p:blipFill>
 <p:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="12192000" cy="6858000"/></a:xfrm>
  <a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>
</p:pic>
```

timing 树：`tmRoot → mainSeq → par(cond delay=indefinite + cond evt=onBegin delay=0 tn=2) → par(delay 0)
→ cTn presetClass="mediacall" presetID="1" nodeType="afterEffect" → cmd playFrom(0.0)`，
同级再挂 `<p:video><p:cMediaNode vol="80000">`（注意它是 `tmRoot/childTnLst` 的子节点）。

## 卡 2 `morph-camera` 平滑当运镜

**参数**：`p14:dur="2000"`（语料 218 次显式时长里 2000ms 占绝对多数；另有 1250/2500 各 9/5 次），`spd="slow"`，
`option="byObject"`。
**讲解原话**：
- 「最后全选所有页面，添加平滑切换效果」（NO.120 笔刷时间轴）
- 「选中所有幻灯片，添加平滑切换的效果」（NO.101 动态转盘）
- 「同样也是用万能的平滑切换」（NO.103 菱形介绍）
- 「**平滑开始和平滑结束拖动到 0**」（PPT 动态表格）→ 否则物体会先停半拍再动，观感散。

**做法**：相邻两页放**同名同 id 的形状**，只改位置/大小，PPT 自动补出运镜。
**OOXML**：`mc:Choice Requires="p159"` 内写 `p159:morph`，`mc:Fallback` 内写 `p:fade` 兜底（老版本不崩）。

## 卡 3 `mask-dim` 压暗蒙版

**参数**：小字区背景压到亮度 ≤35%；渐变蒙版 alpha 从 80%→0%。
**讲解原话**：「将背景颜色设置为黑色……设置三维格式为右透视，添加映像」（PPT 创意动态封面）。
**闸**：G4 用渲染像素实测 WCAG（小字 4.5:1，≥24pt 3.0:1）。

## 卡 4 `bool-shape` 布尔运算造形

**证据**：`custGeom` 7785 个；单页最高 13 个（NO.100）；最多的模板达 251 个（转正述职模版）。
**用途**：撕纸边、笔刷擦除、扇形/花瓣阵列、图片异形裁切、封面标题「变脸」。
**封面变脸的点击级步骤**（语料根目录独立教程，逐字稿 `64fa5cec…txt`，44.7 秒）：
① 做好标题文字并全选 → 形状格式「组合」；② 复制到准备好的视频上；
③ 依次选中视频与文字 → 形状格式「相交」；④ 得到的效果字放回原视频，边框改 **1.5 磅白色**；
⑤ 标题视频与背景视频的播放都改成自动；⑥ 打开动画窗格，把「开始」全部改为**与上一动画同时**，
再加点修饰内容就完成。
**代价**：布尔后是 `custGeom`，不能当普通矩形直接改比例——要改得回源形状重做。
**产出方：手工（PowerPoint）**。引擎不生成 `custGeom`（`field_bg` 画的是 JPG，不是形状），所以别在 `shots.json` 里声明这张卡指望它自动出现，G6 也不管它。


## 卡 5 `click-gated-motion` 点击才动

**证据**：`clickEffect` 1479 次、`interactiveSeq` 1053 次（`kb/animation_mechanics.json` 的
`nodeType_total`，机器可读口径；早期一轮挖掘的散文写过 1486/1059，以这份 json 为准）、`delay=0` 3778 次。
**做法**：首屏元素 `withEffect`（delay 0），讲到哪点到哪；同组用 `afterEffect` 串。
**产出方：引擎部分**。`inject_entrance()` 写的是 `nodeType="afterEffect"` + delay 阶梯，真·单击触发（`clickEffect`）要在 PowerPoint 动画窗格里手改。G6 数动效节点。

## 卡 6 `motion-path` 自定义路径

**证据**：`path` 70 次、`animMotion` 71 个。路径串是**相对画布的比例坐标**：
`M -2.5E-6 0 L -0.38932 0`（0～1 区间，可为负）。
**用途**：飞机划过、列车行驶、花瓣旋转、文字跟随滑动。
**产出方：手工（PowerPoint）**。引擎不写 `p:animMotion`：滚动 marquee 要复制内容并手设重复次数。声明了这张卡不会有产出，G6 也不拦（不在 `RECIPE_TOKENS` 里）。

## 卡 7 `giant-numeral` 超大数字当主视觉

**参数阶梯**（语料实测）：里程碑数字 100–250pt；隔页大数字 60–80pt；页标题 30–44pt；
正文 14–18pt；脚注 10–12pt。
**纪律**：一页只放一个数；该页正文 ≤60 字。

## 卡 8 `sound-cue` 音效

**证据**：`sndAc` 104 次。原话「添加平滑切换和**齿轮音效**」（NO.101 动态转盘）。
**纪律**：一场 deck ≤3 处，用在揭幕/破土/升空这类瞬间。
**产出方：手工（PowerPoint）**。引擎不嵌音频。**没有闸**：G11 只核 mp4 与 GIF 的字节，从来没管过音频——别以为声明了就有声音。

## 卡 9 `softedge-glow` 柔边与霓虹

**证据**：`softEdge` 1051、`glow` 88、`reflection` 348、`sp3d` 170、`scene3d` 154。
**参数（171 模板实测半径分布，`kb/deck_tech.json` 的 `stats.effect_radii`）**：`a:glow rad` 最常见 **228600**（37 次）、38100（30 次）、127000（18 次）；`a:softEdge rad` 最常见 **12700**（482 次）与 76200（478 次）。12700 EMU≈1pt。旧版这里写 6350–12700、别处写 63500–127000，差 10 倍且都复现不出来，已按实测更正。
**产出方：手工（PowerPoint）**。引擎不写 `a:softEdge`/`a:glow`（`field_bg` 的柔边是画在 JPG 里的）；G4 只看对比度，不看发光。

## 卡 10 `palette-lock` 一册一色域

**语料实测高频色**：
- 科技蓝 `016DFF` `009EFA` `00FFFF` `03A2FF`
- 近黑底 `191A1B` `030303` `202020` `2F2F2F`
- 党建红 `9F070E` `A50E13`
- 描金 `FFC000` `F5DC46` `FCDC4A`

**字体实测**：等线 11233、微软雅黑 5287、Hanson 3848、思源黑体 CN Bold 2183、思源宋体 CN Medium 1105、
江西拙楷 293、MiSans Semibold 234、鸿雷板书简体 112、演示春风楷 86、仓耳周珂正大榜书 77。
→ 国风册靠**书法体标题 + 黑体正文**；科技册靠**思源黑体/MiSans**。用前先确认本机装了这些字体，
没装就退回微软雅黑 + 等线，不许让 PPT 偷偷替换成宋体。

## 卡 11 `text-animation-presets` 文字入场/退场（按 presetID 实测）

**语料实测**（`slides[].timing.combos` 的键就是 `presetClass:presetID:子类型`，1222 页带动画）：

| preset | 次数 | 页数 | 讲解语料里对应的说法 |
|---|---|---|---|
| `entr:22` | 242 | 100 | 逐行/逐段浮现类入场 |
| `entr:10` | 209 | 66 | 淡入类入场 |
| `entr:2` | 120 | 72 | 飞入类入场 |
| `entr:53` | 74 | 43 | 缩放/弹入类入场 |
| `entr:23` | 64 | 4 | 高频集中在少数模板（打字机类） |
| `exit:10` | 61 | 17 | 淡出退场 |

**怎么写**：`p:cTn presetClass="entr" presetID="22"`，配 `p:set` + `p:animEffect transition="in"
filter="fade|wipe|blend"`；同一段落里多个元素用 `delay` 递进 100–300ms，形成"讲到哪出现到哪"。

**为什么不挂中文名**：微软官方 presetID→动画名对照表在本机取不到（链接实测跳到别的页面），
转写语料里只有"平滑切换/持续时间/延迟"这类**说法**而没有 ID 对应关系。所以这里只给原始 ID 与次数——
**照抄 ID 就能复现效果**，编一个中文名反而会把 agent 带偏（SPEC §9 交底）。

**闸**：G6 节奏 / G7 可编辑；G9 的技法库不变量会检查这张卡是否带着实测最高频的 presetID。

## 卡 12 `gif-motion-element` 动图当元素动效（161 个 GIF 实测）

**语料实测**：171 个模板里 **133 个含 GIF，共 161 个**；帧数**最少 24、中位 51**；短边≥640 的 37 个。
常见尺寸 `426x240`、`1138x640`、`800x800`。典型用法是**开场与元素级动效**，不是整页背景：
动态扇子（NO.109/134/142）、图片墙（NO.119）、水墨开场（NO.71，1138x640/116 帧）、
飞机划过（NO.73）、破碎开场（NO.76）、闪光划过（NO.78/80，601 帧）、旋转开场（NO.82）。

**什么时候选它**：这页想要"动起来"，但整页视频太重（文件大、投放慢）或会抢文字。
动图只占画面一块，放映时由播放器自动循环，**不需要 `p:timing`**（OOXML 里就是 `p:pic` +
`a:blipFill` 指向 `ppt/media/imageN.gif`）。

**怎么做**：`shots.json` 该页 `asset_kind="gif"` → `scaffold` 写 `pNNN_gif.txt`（六段式，
必须写无缝循环与透明/纯黑底）→ 即梦「图片动画/循环动图」，或本机
`python scripts/make_gif.py --out gen/pNNN/anim.gif --kind arc|sweep|bloom|spin`
（带**透明索引**，四种动效、三种色相）→ 存成 `gen/pNNN/gif.gif`（同目录任意 `*.gif` 也认）→
`build` 把它叠在**蒙版之上**（`gif_box` 默认 `[0.60, 0.20, 0.34, 0.44]`，避开留白区）。

**为什么必须透明**：语料的 GIF 都压在暗底上，黑底看不出来；本项目的隔页/数据页常用矢量色场，
实测纯黑底 GIF 叠上去就是一块方框（渲染图确认），而像素色差只有 5/255——所以闸不猜色差，
直接按确定性的两条拦：**不透明底 + 矢量色场背景 = 不合格**。


**为什么必须验帧数**：单帧 GIF 在 PowerPoint 里看起来完全正常，按下放映才发现不动——
这是最容易骗过人的缺陷，所以 G11 数的是文件真实帧数，G3 还要求成品内嵌的那份字节
就是验收过的文件（建完之后再换 GIF 会被拦）。

**闸**：G11（≥10 帧且短边≥640）/ G2（动图提示词 6/6 段）/ G3（内嵌字节绑定）/ G9（技法库必须有这张卡）。

## 卡 13 `3d-model-depth` 3D 模型与立体感

**OOXML 实测**：`sp3d` 170 处、`scene3d` 154 处、`bevelT` 11 处、`reflection` 348 处（171 模板）。
数量不多但集中在封面与"城市生长/微缩场景"页——这正是需求 2 说的"要学的手法"，
之前一直没单独成卡（第五轮复验查出来的缺口）。

**教程原话（逐字稿里 3D 那几条）**：
① 先把文字和其他素材摆放好，**再导入 3D 素材**；
② 公司有产品模型就直接拖进 PPT——**PPT 版本需 2016 及以上**，否则根本没有"3D 模型"入口；
③ 没有模型可以去 Sketchfab 找类似的；
④ 拖动模型中间那个控制点做**三维旋转**，转到合适角度后**复制到其他页面**保持同一机位；
⑤ 立体柱状图不需要真 3D：形状 + 棱台 + 渐变就够了（教程原话"这样具有高级感的立体柱状图就完成了"）。

**参数纪律**：`bevelT` 语料只有 11 处，别到处加棱台；反射压在暗底上，亮底会发灰。

**引擎已经替你写的部分**（`tech_recipe = 3d-model-depth`）：`build` 在这页的标题/大数字**下层**垫一块立体展台
——`a:scene3d`（`perspectiveFront` 相机 + `threePt` 光）+ `a:sp3d`（挤出 114300、轮廓 19050、金色轮廓色、
圆棱台）+ `a:reflection`。板子插在文字之前，所以**正文照旧 100% 可编辑**；上下边缘会自动避开相邻文字行。
`shots.json` 里可选 `extrusion_h` 覆盖厚度。
**必须手工的部分**（不谎称）：导入 `.glb` 真 3D 模型是 PowerPoint 2016+ 的「3D 模型」按钮，
三维旋转的机位也要在那个控制点上拖——引擎不产 3D 模型文件。

**写法硬约束**：`CT_SpPr` 是**有序**序列 `xfrm → 几何 → fill → ln → effectLst → scene3d → sp3d`。
把 `gradFill`/`solidFill` 追加在 `a:ln` 之后（或重复塞一个 `prstGeom`）真 PowerPoint 会弹
"该内容需要修复"，而 WPS 照样能渲染出图——这轮就是靠这条抓到 `add_scrim` 的老 bug。
**闸**：G6（声明了卡却数不到 `a:sp3d` 直接拦）/ G8（`spPr` 顺序与重复、展台边压到别的文字行）/
G4 对比度（棱台与反射会抬亮背景像素）。

## 卡 14 `table-motion` 表格自身的转场与轮播

**实测**：转场 `checker`（棋盘）10 次、`blinds` 35 次；`animMotion` 71 次；
转写分组里"表格动画"30 组。语料里有两张表互相配合的轮播玩法，之前只写了表格"可编辑"，
没写它怎么动。

**教程原话 A（"最神奇的表格"）**：先插入 7×5 的表格 → **给它加专属转场「棋盘」** →
同一张表还能做多图展示。

**教程原话 B（"表格自动循环轮播"）**：
① 先插一个表格；② 再插第二个表格，**这个表格是轮播的主体**，把数据复制过去；
③ 画一个矩形，**矩形的大小和位置就是轮播表格显示的区域**，拖到表格下方定位；
④ 选中表格**添加直线动画**；⑤ "需要几个环形就填几个 1"（重复次数用环形数决定）。

**参数**：位移量 = 行高 × 行数；动画目标指向 `p:graphicFrame` 里的表；
转场选 checker/blinds 这类分块型，morph 用在整页位移上（卡 2）。

**引擎已经替你写的部分**（`tech_recipe = table-motion` 且这页真有表格）：`build` 写
`mc:AlternateContent → mc:Choice(Requires="p14") → p:transition spd="slow" p14:dur="2500" → p:checker`
外加 `mc:Fallback`（照抄语料 `PPT7×5神奇表格.pptx` 的写法）。`shots.json` 里写 `"transition": "blinds"`
可换百叶（语料 `产品需求文档.pptx` 用 `p:blinds dir="vert"`）。
**一页只容得下一个 `p:transition`**，所以表格页不再叠 morph——台账连续分页靠"表头每页重复"衔接，
`styles.py` 也已不给 table 页默认平滑。
**必须手工的部分**（不谎称）：上面教程 B 那种"复制两份表格 + 直线动画"的循环 marquee 是在 PowerPoint
里点的操作；我们的台账按 G13 要求逐格可核对，引擎不做超出画布的滚动。

**闸**：G6（声明 table-motion 却没在这页 XML 数到 checker/blinds 直接拦）/
G13 逐格核对（动画不许改内容）/ G8 版式（轮播框不许越界）/ G7 可编辑（必须是真 `a:tbl`，不是图片）。





## 卡 15 `blip-duotone` 双色调锁色

**OOXML 实测**：`a:duotone` 74 处、其中的 `a:satMod` 196 处、`a:tint` 30 处、`a:biLevel` 3 处（171 模板）。
这一族以前谁都没写卡——是"手法矩阵"逐 token 对账时抓出来的（G9 现在会盯这个矩阵）。

**语料原文**（NO.176 海浪动态封面、NO.72 创赛级科技风，slide1 的 `p:blipFill/a:blip` 里）：

```xml
<a:duotone><a:srgbClr val="016DFF"><a:shade val="45000"/><a:satMod val="135000"/></a:srgbClr>
<a:prstClr val="white"/></a:duotone>
```

**为什么值得学**：整册要用不同来源的实景照片却必须像一整套。语料的做法不是逐张修图，
而是**在图片上挂一层重新着色指令**——原图一个字节没改，在 PowerPoint 里可以单独撤掉，
所以这比把调色烘进 JPG 更符合"成品可编辑"这条硬要求。

**引擎已经替你写的部分**（`tech_recipe = blip-duotone` 且 shots 里 `"duotone": true`）：
`add_duotone()` 给整页实景图挂上面那段 XML。**没有实测样本的风格不做**：
`ink_wash` / `photo_real` 语料里没有对应色值，引擎会照实写进 build_log 并被 G6 拦下，
不会假装做了。要纯黑白用 `biLevel`，要整体压暗用 `tint`。
**闸**：G6（数 `a:duotone`）/ G4 对比度（锁色会改明度，文字压不住要回去调 scrim）/
G3b（锁色不算降分辨率）。

## 卡 16 `emphasis-scale` 放大强调

**OOXML 实测**：`p:animScale` 85 处、`presetClass="emph"` 节点 39 处（171 模板）。

**语料原文**：NO.144 动态数字过渡页把大数字放大 15%（`by x="150000" y="150000"`，dur 1000ms）；
NO.136 花瓣旋转开场把主体放大 150%（`by x="1500000" y="1500000"`，accel/decel=50000），
和 `animRot by="21600000"` 的陀螺旋同时挂在一个 `grpId` 组里。

**纪律**：一册 ≤2 处，多了像弹窗广告。**这是相对量**——形状本体尺寸不变，
所以它不会把越界闸搞坏；真出现越界，说明有人把 `by` 写成了绝对尺寸。

**引擎已经替你写的部分**（`tech_recipe = emphasis-scale`）：`inject_emphasis_scale()` 把这页第一个
文字形状按 `scale_pct`（默认 15%）放大，并与已有的入场淡入合并进同一个 `mainSeq`。
**闸**：G6（这页必须数到 `p:animScale` 本身）。注意只数 `presetClass="emph"` 节点是不够的——
把 `animScale` 抠掉会留下一个空壳 emph 节点照样计数，这条就是自检里这么被抓出来的。

## 常见"电影感"母题（从 171 个模板名与画面归纳）

| 母题 | 代表模板 | 装配要点 |
|---|---|---|
| 城市生长（地图→实景） | NO.148 NO.160 NO.139 NO.122 | 首帧地图 + 尾帧实景，因果写死"点击后才生长" |
| 破浪/航行 | NO.155 NO.167 NO.153 NO.095 | 循环尾浪，机位固定 |
| 升空（火箭/热气球/飞机） | NO.104 NO.137 NO.113 NO.073 | 运动路径 + 尾迹 |
| 旋转（风车/转盘/唱片/花瓣） | NO.138 NO.157 NO.101 NO.149 NO.105 NO.136 | 陀螺旋 + 循环 |
| 卷轴/扇子/折页 | NO.142 NO.109 NO.092 NO.159 | 布尔裁切 + 平滑展开 |
| 水墨/国风 | NO.150 NO.154 NO.071 NO.074 NO.075 | 墨迹晕染到哪画面显到哪 |
| 撕纸/笔刷 | NO.129 NO.145 NO.120 NO.084 NO.086 | custGeom 边缘 + 擦除 |
| 玻璃破碎 | NO.151 NO.076 | 序列帧或视频，音效点睛 |
| 数字滚动 | NO.116 NO.144 NO.085 | 数字列 + 平滑，或视频 |
| 3D 微缩景观 | NO.164 NO.123 NO.173 | 等距轴测 + 固定机位循环 |

## 卡 17 `split-reflow` 图文重排（图片按实测栏位裁切，文字落互补区）

**为什么单独有一张卡**：语料 2224 页里有 504 页是"图居中/偏右、文字在两侧与上下"，母版与版式里
3814 个正文占位符的中位框是 `[0.069, 0.300, 0.425, 0.537]`，图片占位符是 `[0.425, 0.144, 0.506, 0.711]`
——左文右图、两不重叠，这就是语料自己的重排模板。更要紧的是：**语料有 346 页把字压在亮底上糊了字**，
分栏正是这个问题的解法，所以这一条既是手法也是避坑。

- 分镜写法：`"layout": "split-centre-image"`（plan 阶段 data/process/compare 且素材是静帧时会自动写）
- 装配：`add_picture_in_box()` 按栏位放图并用 `a:srcRect` 居中裁切保比例（不拉伸变形）；
  文字落 `[0.069, 0.067, 0.425, 0.77]` 那一栏；**分栏页不压蒙版**，整页铺色板底色
- 内容放不下怎么办：`split_zone_fits()` 先按行高估一遍，放不下就退回满幅原型并在 build_log 里写明——
  不靠压字号、不靠溢出隐藏
- 验收：G17 用**与语料挖掘同一份** `deck_measure.py` 量成品 bbox，声明与实测量出的原型必须一致；
  分栏页压图率超过 25% 就拦（语料该原型实测 0.4%）
- **同一张卡也管多图栅格**：`"layout": "photo-wall"` + `"assets": 6` 时装配走
  `find_gen_all()` 收本页 `gen/pNNN/image_1..6.jpg`，`tile_boxes()` 按语料实测图块尺寸排
  （照片墙 0.140×0.185 三列、卡片 0.225×0.321、时间轴节点共线）。块数不够最低要求
  （照片墙 6、栅格/时间轴 3）就在选原型这一步降级并记进 build_log——
  不是凑够数好看，是 `deck_measure` 的分类器按这些条件判定原型，凑不够 G17 会量成别的原型。
  scaffold 会在该页提示词里逐块列出 N 个主体，要求逐张出图放回文件。
- 好示范页里这个原型只有 49 页（5.9%），不是主力；主力是满幅压字（487 页，58.3%）。
  别为了"看起来会重排"把每页都拆栏

## 卡 18 `wipe-family` 擦入家族与位移入场

presetID 不靠记忆命名，靠 XML 签名自证：`p:animEffect/@filter` 直接写着 `wipe(left)`，
`p:cBhvr/p:attrNameLst/p:attrName` 写着 `ppt_x`，`p:tav/p:val/p:strVal` 写着 `0-#ppt_w/2`。

| preset 名 | 三元组 | 实测签名 | 次数/册数 |
|---|---|---|---|
| 淡入 fade | entr:10:0 | `effect:fade` + `set:visible` | 209 / 46 |
| 擦入（左）wipe-left | entr:22:8 | `effect:wipe(left)` | 109 / 34 |
| 擦入（下）wipe-down | entr:22:4 | `effect:wipe(down)` | 66 / 12 |
| 擦入（右）wipe-right | entr:22:2 | `effect:wipe(right)` | 32 / 10 |
| 擦入（上）wipe-up | entr:22:1 | `effect:wipe(up)` | 35 / 9 |
| 飞入（自左/右/下/上）fly-* | entr:2:8/2/1/4 | `p:anim` 改 `ppt_x`/`ppt_y` | 40/32/25/19 |
| 升起+淡入 rise-fade | entr:53:16 | 2×`p:anim` + `fade` | 74 / 28 |
| 微位移+淡入 drift-fade | entr:42:0 | 2×`p:anim` + `fade` | 32 / 20 |
| 门扇 barn | entr:16:37 | `effect:barn(outVertical)` | 19 / 12 |
| 随机线条 random-bar | entr:14:10 | `effect:randombar(horizontal)` | 14 / 6 |
| 轮子 wheel | entr:21:1 | `effect:wheel(1)` | 7 / 5 |
| 淡出 exit-fade | exit:10:0 | `effect:fade` + `set:hidden` | 61 / 12 |
| 消失 exit-appear | exit:1:0 | `set:hidden` | 27 / 11 |

- 分镜写法：`"motion_preset": "wipe-left"`、`"motion_out": "exit-fade"`（`motion` 字段本身是给
  agent 看的自由文本意图，不当 preset 解析）
- 节奏：delay 阶梯取语料实测 0/500/1000/1500/2000ms；nodeType 用 afterEffect/withEffect 串接
  （语料 898/819 次），clickEffect 1479 次是人工点击点
- 验收：G18 在这页成品 `p:timing` 里数三元组与 filter 字符串，声明了没写就拦

## 卡 19 `video-click-pause` 视频点击暂停/继续

语料 165 册、1042 页的每个内嵌视频都写了**两条** mediacall：`onBegin` 自动播 `playFrom(0.0)`
（1092 次）与 `onClick` 的 `togglePause`（1036 次）。后者挂在
`p:seq[@concurrent="1" @nextAc="seek"] > p:cTn[@nodeType="interactiveSeq"]` 上，带
`restart="whenNotActive"`、`evtFilter="cancelBubble"`、`endSync evt="end"`。

此前引擎只写自动播——放映时点画面不会停，讲者想停就得翻页，这是真缺的功能，不是风格问题。
现在 `attach_video()` 会同时写 `inject_autoplay_timing()` 与 `inject_video_click()`，
G18 对视频页要求 `playFrom`、`togglePause`、`interactiveSeq` 三样齐全。

顺带纠正一条旧说法：`interactiveSeq` 不是"竞赛答题的点选交互"，它就是视频点击播放的内建序列。
答题/轮盘那类交互靠的是超链接 `action="ppaction://hlinksldjump"`（语料 166 册带内嵌视频，
其中确有跳页链接），那属于另一条手工路径，别把两者混为一谈。

## 卡 20 `exemplar-policy` 示范质量分层（坏示范不学，且拦在成品外）

**这张卡是用户点名要的**：语料里有的东西是错误示范，学它就达不到目的。判据写在
`corpus_tables.BAD_RULES`，`grade_exemplars.py` 用它给 2224 页分层，G19 用同一份拦成品。

| 坏示范 | 语料页数 | 为什么不学 | 引擎/闸怎么处理 |
|---|---|---|---|
| 推广页（提取码/网盘/公众号/更多模板） | 504 | 是作者卖货页，不是设计 | 不生成；G19 正则拦 |
| 无字无信息空页 | 560 | 好示范子集里从 33.5% 掉到 22%，剩下的是有媒体/动效的展示页 | 无字页必须带媒体或动效，否则 G19 拦 |
| 字压亮底 | 346 | 缺陷不是手法 | 满幅页必压蒙版 + G4 量渲染像素对比 + G19 复核 |
| 单页 300 字以上文字墙 | 70 | 与"一页一意"相反 | plan 拆页；G19 拦（署名页豁免这一条） |
| 整页最大字号 <14pt | 20 | 好示范页字号中位 66pt | `pick_pt` 抬字号；G19 拦 |
| 模板编号当主视觉 | 16 | 那是商品图 | 标题取 Word；G19 拦 |

结论口径也改了：文档里所有版式/配色占比都按**好示范 835 页**重算（原始数同时列出并标明含污染），
117 册过半是坏页的模板在 `kb/exemplar_study.md` 里点名，做片子时不参考。


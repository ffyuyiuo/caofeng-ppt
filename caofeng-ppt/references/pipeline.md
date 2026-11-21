# 七阶段流水线：每步的输入、输出、判据

工作目录约定（`--work DIR`，可反复续跑，产物都可查）：

```
DIR/
  outline.json              S1 文档结构（页/角色/标题/正文/实体/来源片段）
  shots.json                S2 分镜（镜头意图/动效/技法卡/留白区/素材需求）
  materials/
    manifest.jsonl          S3 每条素材：url 许可 尺寸 sha1 代理 时间
    gaps.json               S3 搜不到的页与原因
    raw/pNNN.*              原始下载件（保留备查，不进成品）
    pNNN.jpg                归一化后可嵌件的（长边 3200）
  promptpack/
    pNNN_image.txt          S4 九段式图片提示词
    pNNN_first.txt pNNN_last.txt
    pNNN_video.txt          S4 六段式视频提示词
    pNNN_negative.txt       S4 负面三类清单
    pack.json               语法校验结果（bad_pages 必须为 0；只是缓存，check 会重读 *.txt 复核）

    delivery_card.md        S5 投放卡（即梦/豆包点击步骤 + 附件路径 + 回收文件名）
    delivery_request.json   S5 投放请求单（237 项产物清单 + 指纹；ack 回执与它对账，G26）
    delivery_ack.json       S5 用户回执（--ready/--no-video/--declined + 原话；没有它 build 停）
  gen/pNNN/{image,first,last}.png  gen/pNNN/video.mp4     S5 回收物
  out/deck.pptx             S6 成品
  out/build_log.json        每页实际用了什么背景/几个文本框/降级原因
  out/bg_fallback/pNNN.jpg  无素材页的矢量色场兜底
  report/验收报告.md         S7 二十九道闸结果
  report/render/pNNN.png    WPS 真渲染图
  report/sheets/sNN.jpg     逐页拼图（人眼复核用）
```

## S1 plan

`python caofeng.py plan <docx> --work DIR --pages 30 --occasion 路演 --audience 投资人 --note 用户原话`

**页数契约（SPEC §21）**：先问五件事（页数/区间、场合、受众、模板风格、留档与否）再 plan；
`--pages` 按契约重新梳理 Word（结构页不动、章内内容页相邻合并精确凑数、正文一句不丢），
`refine` 写压缩台账供 G13/G24 核。没契约且非 `--auto-pages`/`--verbatim`：plan 打印五问、G23 硬拦。

- 按**文档顺序**读 `docx` 的段落与表格（python-docx 把两者分成两个列表，顺序一读就丢，表格会被挂错章
  或整张丢掉）。1 级标题切章发隔页，2/3 级标题作为 kicker 行落在其下第一页；表格挂到所属章，
  装配成可编辑的原生表格（>8 行分页并重复表头）。按 `--per-page`（默认 2 段/页）切页。
- 角色自动判定：含 `%`/金额/计数 → data；含"第一/步骤/流程" → process；含"对比/高于" → compare；
  单段短句 → statement；章首 → divider；首末 → cover/closing；**Word 表格 → table**（一页最多 8 行，
  续页重复表头并标「（续表）」，`asset_kind=none`，装配成原生可编辑表格）。
- 2/3 级标题不再各发一张隔页：它作为 kicker 行落在其下第一页；**自己没有正文、内容在子孙节里的
  父级标题也会一起带下去**，否则那行标题就成了成品里找不到的原文（G13 会报）。
- 输出 `outline.json`（含 `entities` 候选检索词、`source_span` 原文片段、`headline_is_draft`）。
- **判据**：章节数 > 0；`headline` 不越界、不与正文重复；`headline_is_draft=true` 的标题必须由 agent 改写成含数字或判断词的主张句；**G13 要求 Word 每句话都出现在某一页**（防截断丢内容）。
- **agent 必做**：`headline_is_draft` 为 true 的标题要逐条改写成判断句（含数字或结论）。

## S2 分镜（agent 手工，脚本给骨架）

`shots.json` 每页要定：

| 字段 | 含义 | 取值来源 |
|---|---|---|
| `shot_intent` | 镜头语言，不是排版语言 | `references/cinema-recipes.md` |
| `tech_recipe` | 技法卡 id | `kb/deck_tech.json` 的 20 张卡 |
| `motion` | 具体动效参数 | 卡里的参数（morph 2000ms、文字 delay 6000ms…） |
| `asset_kind` | `video_bg` / `image` / `gif` / `field` / `none` / `table` | 角色与"要不要真素材"决定；`gif` 是语料里独立的一类（162 个） |
| `reserved_zone` | 文字落区（0–1 分数） | 分栏原型取实测栏位（`LAYOUT_ZONE`），否则 `zone_for(role)` |
| `assets` | 多图栅格要几张图 | 配 `layout=photo-wall|card-grid|timeline-on-line` 用；装配收 `gen/pNNN/image_1..N.jpg`，照片墙 ≥6、栅格/时间轴 ≥3，不够数引擎自动降级 |
| `layout` | 图文重排原型 | `split-centre-image`/`full-bleed`/`text-block`…；data/process/compare 用静帧素材时 plan 自动写分栏；正文放不进栏位时 build 自动退回满幅并记日志 |
| `motion_preset` | 入场动画 preset 名 | `corpus_tables.ANIM` 里的键（fade/wipe-left/fly-up/rise-fade/barn/wheel/random-bar…），默认 fade；不在表里会写明并按淡入装配 |
| `motion_out` | 出场动画 preset 名 | `exit-fade`/`exit-wipe-left`/`exit-appear`；不写就不出场 |
| `style` | tech_blue / ink_wash / photo_real | 全册一个 |
| `morph_from_prev` | 该页是否与上一页做平滑 | 同主体位移时为 true；table 页不给（一页只有一个 `p:transition`） |
| `transition` | 表格页转场种类 | `checker`（默认）或 `blinds`，只在 `tech_recipe=table-motion` 时生效 |
| `extrusion_h` | 立体展台厚度（EMU） | 只在 `tech_recipe=3d-model-depth` 时生效，默认 114300 |

**判据**：≥1 页 `asset_kind=video_bg`（G11 硬拦）；≥2 页 `morph_from_prev`（G14 回查成品 XML 才算数）；
**成品里**≥30% 页真的带动效（G6 数 slide XML 的 transition/timing，不看这份计划）；
每个 divider 必有 morph/视频/动图（`build` 会给隔页自动补平滑，只淡入不算视差）；
**声明了引擎能产出的卡就必须数到 token**——清单以代码 `caofeng.py` 的 `RECIPE_TOKENS` 为唯一定义点
（当前 12 项，逐名列在 SPEC §7 的 G6 行；键名必须与 `kb/deck_tech.json` 的卡 id 一致，
旧键 `theme-palette-lock` 不是卡 id、曾让这条校验变成死代码），G6 逐页在成品 XML 里核对；
手工类（`bool-shape`/`motion-path`/`softedge-glow`/`sound-cue`）不在这张表里，声明了也不拦，
但也不会自动出现——界线写在 `references/cinema-recipes.md` 开头的"这 20 张卡谁来产出"表里。


## S3 fetch

`python caofeng.py fetch --work DIR --term "3=英文词" ...`（检索词优先读 shots 每页 `search_terms`，逐词重试）
`python caofeng.py fetch-video --work DIR` → 视频先联网找无水印 CC 素材，找不到才豆包/即梦兜底（SPEC §23）
`fetch` 联网优先逐词重试到过闸为止，搜尽才豆包/即梦兜底；source=stock 举证页豁免美学闸（SPEC §24）
`python caofeng.py materials-review --work DIR` → 真看联系表 → `--set "3=keep,4=reject:理由"`（G3 硬要求）

- Commons 检索（国外 → 代理 7897）→ 筛短边 ≥ `--min-edge` → 下载 → **归一化** → 写 manifest。
- 搜不到的写 `gaps.json`，不猜、不硬凑。
- **判据 G3**：许可非空、文件存在、短边≥1920、宽高比在 1.45–1.95、相关度≥50%（`fetch --min-rel` 默认值、G3 复核、装配拒用三处都读 `TH["relevance_min"]` 这一个定义点；`relevance()` 内部还要求主体词块命中率≥50%，`phrase_tokens` 先切掉末尾介词短语，修饰语不算主体）、未命中敏感/离域黑名单。
- **检索词按这一页自己的文字派，别按页号派**（实测教训）：`--per-page` / `--max-chars` 一改，页号全体重排，
  按页号存的词与素材就错配到别的页上。正确做法是关键词 → 词的规则表 + 把已下载的图按词归堆，
  谁对应谁拿走一张（同一张最多 3 页，与 G20 同口径），词下没有余图才真的联网再取。

## S4 scaffold + pack

```
python caofeng.py scaffold --work DIR --style tech_blue --subject "1=主体词"
# agent 逐页改写：把「主体」槽位换成文档里的具体事物，补因果顺序与留白描述
python caofeng.py refine --work DIR          # S3：给每页出 display_lines 提案（必须再人工改写）
python caofeng.py refine --work DIR --force  # 显示契约改档后（条数/字数上限变了）用这个重出提案
python caofeng.py pack --work DIR            # S5：投放卡全文打到 stdout + delivery_request.json
```

- `scaffold` 按实测语法生成草稿，并回填 `shot_intent/tech_recipe/motion`；画不出的主体（章节名/抽象词）
  会按页面实体自动翻成具体画面（`shots.json` 标 `subject_auto=true`，语料直陈句式），agent 仍要逐页改得更贴
  文档实体；`pack` 三条硬判据兜底：主体句出现"与「…」直接相关的核心视觉主体"即拦、两页主体句逐字相同即拦、
  「主题为「X」」/「象征Y」不是完整短语即拦（尺子 `styles.clean_phrase/phrase_ok`，SPEC §25）。
- `pack` 校验九段/六段齐全度与字数（图片 7/9、视频 6/6、动图 6/6），产出 `delivery_card.md`。
- **判据 G2**：`bad_pages == 0`。`check` 会**重新读 `promptpack/*.txt` 打分**，
  所以"改完提示词没重跑 pack"或"删掉 pack.json 装作没检查"都会被发现（两者都是实测复现过的假绿灯）。


## S5 投放与回收

一页一轮：`python caofeng.py card --work DIR`（不给页码取队列里最早待回收页，视频优先；`--page N` 指定），
把 stdout **整段贴进对话回复正文、每段提示词用代码块**（用户要复制粘贴，只留在工具输出里等于没发）。
用户照卡在即梦/豆包网页版生成，下载丢进 `<项目>\PPT素材\收件`，回「好」→
`python caofeng.py pickup --work DIR --page N`（验短边/时长 → 去水印 → 改名进 gen/ → AI产物 留档）。
**命名**：一次只下一页可不改名；同时下好几页必须按页号命名——图 `p011_image.png`、视频
`p011_video.mp4`（中文别名 `p011_图.png`/`p011_视频.mp4` 也认）。pickup 页号命名优先；没命名且同类
候选多于一个就列出文件名停下来问，绝不猜对应关系。
没有专属静帧的页，卡会自动变成两步：先文生图、再拿那张图当首帧出视频（首帧附件唯一口径
`card_first_frame`，公共色场 `out/bg_fallback` 不算）。全部回收完、用户回「是」→ `ack --ready`；
单页用户原话放弃 → `waive --page N --note 原话`；整册不要视频 → `ack --no-video`。
**不许用 Qoder 自带 ImageGen 代劳**（消耗用户模型积分 + 烧水印）；不想手动就只剩两条合法路子：
S3 联网抓 CC 授权照片，或让该页走矢量色场/`make_gif.py`（零模型调用）。


- **判据 G11/G15**：有提示词无产物 → 硬失败；`video_bg` 页必须有 mp4（<64KB 的占位文件不算）；
  动图页的 GIF 必须真有多帧（<10 帧就是静图）；video 提示词必须点名附件且该页有静帧首帧
  （首帧链断了图生视频必跑偏，实测第 0 帧与首帧差的 MAD 要 ≤28/255）。

## S6 build

`python caofeng.py ack --work DIR --ready     # 用户回「已就绪」且文件已放回（G26 才放行）
python caofeng.py ack --work DIR --no-video  # 用户明确不要视频背景（只豁免 video 类）
python caofeng.py fetch --work DIR --adopt <下载的文件> --page N --url <照片页> --artist <作者>
python caofeng.py build --work DIR`

- 逐页：整页视频/大图 → 渐变压暗蒙版 → **动图叠在蒙版之上** → 文本层落进留白区 →
  每个文本框写 `p:timing` 的 `entr` 入场节点（语料 `presetID=10` 淡入，逐条 120ms 递进）→
  需要时加 morph（隔页自动补，保证视差真的在成品里）。
- 无素材页自动走 `field_bg()` 矢量色场（用本页数值画母题），不是黑底。
- **判据 G7/G8**：真文本框数 > 0、16:9、无形状越界（含 width/height 超画布 1.5 倍的荒谬几何）；`add_text` 的 zone 必须是 0-1 分数，传 EMU 直接抛错。

## S7 check + shots

```
python caofeng.py check --work DIR --render     # 29 道闸 + WPS 真渲染
python caofeng.py check --work DIR --render --allow-field   # 接受「色场为主」= 显式降级交付，不是电影级
python caofeng.py shots --work DIR              # 逐页拼图
```

- **G12 逐张绑定**：`render_stamp.json` 记成品 SHA1 + 每张 PNG 的 SHA1；渲染图被换一张、
  凭据是旧版成品、或者只有张数的旧格式凭据，全部拦。
- **G4 必须自证覆盖**：报告要写 `text_shapes / boxes_measured`，量到的框不足文本框数一半就判闸失效——
  只报"0 违规"不报"量了多少"的闸等于没跑。

- **必须打开拼图逐页看**。统计全绿不等于画面成立（本项目实测：G4 曾把满幅蒙版误判、
  标题曾从中间截断，都是看图才发现）。
- 任一闸不过 → 回到对应阶段改，改完**重跑全部闸**（契约变了不能只跑受影响那一道）。

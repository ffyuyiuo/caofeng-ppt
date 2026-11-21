# caofeng-PPT

把一份 Word 文档做成**电影级、可编辑、带真实动态背景**的 PPT。

能力来源：`CORPUS_DIR` 的 171 个模板、2224 页 OOXML、49 段真实 AI 提示词、
253 条去重教程视频（166 个模板内嵌的 1035 条条目按 CRC 去重得 250 个，另有语料根目录 3 个独立教程视频；
253 条全部抽帧看过，252 份逐字稿入库，其中 184 条转出讲解内容、67 条无声或纯配乐、1 条没有音轨）。
这些数与 `kb/coverage.json` 逐条对得上，`check_docs.py` 会当场比，改了语料没改文档就会退出码 1。

## 怎么用（对用户）

直接说：

> 用 caofeng-PPT 把 `某方案.docx` 做成 PPT，封面要科技感的动态背景。

技能会：读 Word → 按内容分镜 → 联网取高清无水印素材 → 写文生图/首尾帧图生视频提示词 →
给出即梦/豆包网页版的点击级投放卡 → 你生成后放回文件 → 装配成可编辑 .pptx → 过 29 道机器闸 →
出验收报告和逐页渲染图。

## 怎么用（对 agent）

```bash
S=~/.qoder-cn/skills/caofeng-PPT/scripts
W=<作业目录>
# 先问五件事（页数/场合/受众/模板风格/留档与否），拿到原话再 plan；没契约 G23 会拦
python $S/caofeng.py plan   "<docx>" --work "$W" --pages 30 --occasion 路演 --note "用户原话"
# 逐页定 shot_intent / tech_recipe / reserved_zone（读 kb/deck_tech.json）
python $S/caofeng.py refine --work "$W"        # 给每页 display_lines 出提案，agent 再逐页改写
python $S/caofeng.py fetch  --work "$W" --term "3=Shanghai Pudong skyline night"
python $S/caofeng.py fetch-video --work "$W"        # 视频先联网找无水印 CC 素材，找不到才豆包/即梦兜底
python $S/caofeng.py materials-review --work "$W"   # 联系表真看后 --set "3=keep,4=reject:理由"（G3 硬要求）
python $S/caofeng.py scaffold --work "$W" --style tech_blue --subject "1=主体词"
# 逐页改写草稿：把主体槽位换成文档里的具体事物（画不出的主体引擎会自动翻成具体画面，仍要再改贴切）
python $S/caofeng.py pack   --work "$W"        # 出 delivery_card.md + delivery_request.json
python $S/caofeng.py card   --work "$W"        # 一页一轮：取最早待回收页，stdout 整段贴进对话正文（代码块）
# 用户回「好」→ 认领这一页；没有专属静帧的页卡会自动变成"先文生图、再拿那张图当首帧出视频"两步
python $S/caofeng.py pickup --work "$W" --page N
# 全部回收完、用户回「是」才登记回执；单页用户原话放弃用 waive --page N --note 原话
python $S/caofeng.py ack    --work "$W" --ready
python $S/caofeng.py build  --work "$W" --palette tech_blue   # 六块实测色板之一，会写进 theme1.xml
python $S/caofeng.py check  --work "$W" --render
python $S/caofeng.py shots  --work "$W"        # 必须逐页看图，别只看统计
```

**没有 ack 回执 build 会退出码 1 停住**（G26）：卡片改过指纹就失效，必须重新发卡、重新等用户回答。
口令分两层：一页一轮回「好」走 pickup；整册就绪回「是」走 ack --ready。豁免必须带用户原话，
agent 不能替用户放弃。提示词卡必须用代码块贴进对话回复正文，不能只留在工具输出里。

分镜里可以点名的三样（都按语料实测值装配，并被闸逐页数）：
`"layout": "split-centre-image"`（图文分栏，图上不压字）、
`"motion_preset": "wipe-left|fly-up|rise-fade|barn|wheel"`（擦入/飞入/升字，不再只有淡入）、
`"motion_out": "exit-fade"`（出场）。整页视频自动带点击暂停。

**学的是好示范，不是全部**：语料 2224 页逐页分过层，只有 835 页当范本（其余 1389 页是作者推广页、
无字空页、字压亮底、文字墙、无层级小字、模板编号当主视觉）。判据集中在一份，
分层脚本与成品闸 G19 共用，详见 `kb/exemplar_study.md`。

`check` 退出码非 0 就是不合格，回去改，改完**重跑全部闸**。

退出码分三种，别混：**1 = 闸拦下了**（报告 `report/验收报告.md` 列明硬伤）；
**2 = 输入不对**（路径、类别名、工作目录不存在这类）；**3 = 脚本自己崩了**，
此时 `report/崩溃.md` 会写明"本次结果不可信"——崩了不等于拦住了，这是复验抓过的真事故。

闸量的是**交付物**：成品 pptx 里的每一个文本框（正文短句也不豁免）、素材文件与 manifest 的 SHA1 复算、
内嵌视频/动图的字节是否就是验收过的那一份、内嵌图与视频的真实分辨率、slide XML 里真的有多少页带动效、
渲染后的像素、渲染截图逐张的指纹、视频第 0 帧与首帧参考图的实测差值。
中间计划（outline/shots/pack.json）只作为对照，不作为通过依据——`check` 会自己重读提示词文件打分。
所有阈值只有一个来源：`caofeng.py` 的 `TH` 表，SPEC §7.1 抄了一遍并由 `check_docs.py` 逐键对账
（文档里写 1280、代码里跑 1080 这种事现在当场就会红）。

用户明确不投放视频时，只有 `check --allow-still` 这一条口子，它会把视频相关硬伤转成报告里
`降级交付_无视频背景` 的显式声明——那一版不是电影级交付，报告必须这样写。

## 目录

```
SKILL.md                  入口：七条硬约束 + 人工投放等待续接协议 + 七阶段 + 决策顺序
SPEC.md                   契约：输入输出、语法、技法、闸、缺口
references/
  pipeline.md             七阶段每步的输入输出与判据
  prompt-grammar.md       实测语法（图片九段式/视频六段式/动图六段式/负面三类）+ 槽位覆盖率
  cinema-recipes.md       20 张技法卡 + 教程原话里的点击步骤与参数

  visual-patterns.md      逐页看图归纳：封面公式、引导线承载数据、首尾帧三页式
  material-sourcing.md    取图渠道、代理规则、许可、质检
  model-routing.md        豆包/即梦投放手册、能力矩阵、秒数约定
  deck-craft.md           字号阶梯、配色闭集、蒙版、留白纪律
kb/
  deck_tech.json          技法卡（带实测次数与 OOXML 写法）
  color_system.json       配色实测：按角色的色值、渐变停点/角度、lumMod/lumOff/alpha 派生档、主题家族
  layout_archetypes.json  版式实测：2224 页逐页图文原型 + 母版/版式占位符几何
  animation_mechanics.json 动画实测：preset 三元组带 filter/attrName 签名、点击暂停链、cmd 计数
  exemplar_grades.json    2224 页好示范/坏示范逐页判定（835 vs 1389）与判据
  exemplar_study.md       分层结论：哪些语料做法被否决、哪些数字改成好示范口径
  prompt_corpus.jsonl     49 段真实提示词 + 槽位标注
  prompt_stats.json       槽位覆盖率统计
  transcript_study.md     讲解语料挖掘结论（按主题分组要点 + 带数值指令；逐字稿原文未在公开版分发）
  coverage.json           研读覆盖与缺口（可机器核对）
scripts/
  caofeng.py              主引擎：plan/fetch/scaffold/pack/build/check/shots
  safety.py               取图内容安全：8 类敏感黑名单（词边界，不吃 software/March）+ 离域清单 +
                          按文档主题放行（--allow-sensitive medical-gore）
  delivery.py             投放卡渲染（点击级步骤 + 绝对路径 + 回收文件名）
  styles.py               三套风格库 + 九段/六段提示词生成（含动图六段式）
  make_gif.py             本机生成**透明底循环动图**（arc/sweep/bloom/spin × gold/ink/cyan），
                          给没有即梦账号或需要一定不露框的页兜底

  build_kb             从研习产物重建 kb/
  corpus_path.py          语料目录单点解析：CAOFENG_SRC → kb/corpus_src.json → 历史默认 →
                          自动定位（用户目录下探两层找 .pptx 最多且 ≥100 的目录）。
                          语料文件夹改名或挪盘后不用再改代码
  montage.py              逐页拼图（人眼复核用）
  test_gates.py           闸的反向自证：每道闸都要有反例被拦、正例放行（含真跑 check 的差分测试）
  test_safety.py          标题分类表：敏感/离域/误伤三个方向逐条核对
  check_docs.py           文档与代码口径对账（闸数、闸编号、脚本存在性、自证项数）
```

## 三条最容易踩的坑

1. **AI 图里不许有字**。一旦模型自己写字，成品就不可编辑了——这是整套语料的头号纪律。
2. **素材必须先归一化再嵌**（长边 3200）。这套语料自己就是因为直嵌素材才膨胀到 106GB。
3. **验收要真渲染真看图**。统计全绿时，实测出现过"离题政治照片被配上收入页"和
   "标题从句子中间被截断"两种硬伤，只有看 PNG 才发现。

## 已知限制

- 本机没有可白用的图像/视频生成模型：出图、出视频、出动图一律走即梦/豆包网页版（用户已确认走免费额度）。
- **全绿不等于电影级**：G20 查的就是「整册有没有真素材打底」。实测有片子 50/58 页用引擎自画的
  矢量色场、素材数 0，其余 G1–G19 照样全绿——那种片子放映出来就是满屏同一套渐变。
- **禁止用 Qoder 自带 ImageGen 出图**：消耗用户的模型积分，且右下角烧「AI生成」水印。
  要全自动就只能联网抓 CC 授权照片或用矢量色场（零模型调用）。

- 92 个模板的教程只有夸克网盘/抖音外链，本地读不到（清单在 `kb/coverage.json`）。
- presetID→中文动画名的官方对照表未取到，技法卡只写原始 ID 与实测次数，不挂中文名。

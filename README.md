# caofeng-ppt — 可移植分发包

把 Word 文档做成**电影级、可编辑、带真实动态背景**的 PPT 的技能（标准 Agent Skills 包）。
当前版本见 `caofeng-ppt/VERSION` 与 `MANIFEST.json`。

知识来源：171 个动态 PPT 模板 / 2224 页 OOXML / 49 段模板内嵌真实 AI 提示词 / 教程视频逐帧逐句转写。
2224 页先按 9 条「坏示范规则」分成 **835 页好示范 vs 1389 页坏示范**（54 套好示范册 / 117 套 junk 册）；
**所有沉淀参数与同级基线只取好示范**——广告页、空页、文字墙、字压亮底、无层级小字、模板编号主视觉这类
非模版元素不学习、不沉淀。

## 三步装到任意 agent

```bash
python -m pip install -r requirements.txt
python install.py --agent qoder-cn --force --verify    # 或 --agent workbuddy / qoder / --target <skills目录>
# 重启 agent，在技能列表里确认 caofeng-PPT 的版本与 caofeng-ppt/VERSION 一致
```

依赖：`python-pptx` `lxml` `Pillow` `python-docx` `numpy` `imageio-ffmpeg`；`pywin32` 只在 Windows 且要跑真渲染验收时需要。
完整路径映射、离线降级、故障排查见 `INSTALL.md`。

## 用起来

直接说：「把这份 Word 做成电影级汇报 PPT」。流程是
`plan → fetch / fetch-video → scaffold → refine → pack → card/pickup/ack → build → check --render → shots`，
由 `scripts/caofeng.py` 一个入口驱动：读 Word 正文按内容分镜（可给页数契约）→ 联网取高清无水印素材 →
按实测语法出提示词包（文生图 / 图生视频分组，逐文件给绝对路径）→ **出图出视频一律走网页版豆包 / 即梦**
（禁用 ImageGen，不耗模型积分；提示词卡全文打到对话里等人工投放）→ 回收产物自动去平台角标 →
装配成可编辑 .pptx（整页视频背景 + morph 平滑 + 压暗蒙版 + Word 表格转原生可编辑表格 +
preset 表驱动的入场/出场/强调/点击暂停）→ 过 **29 道机器闸** + WPS 真渲染逐页看图，全绿才交付。

## 仓库结构

- `caofeng-ppt/` — 标准 skill 目录：`SKILL.md` 入口 + `SPEC.md` 契约 + 37 个脚本 + 7 份技法文档 + `kb/` 实测数据
- `install.py` / `verify_install.py` — 一键安装与安装后自检（含端到端出片 + 闸门必须咬住空产物）
- `INSTALL.md` / `MANIFEST.json` / `sha256sums.txt` — 安装说明、清单、校验和
- `LICENSE`（MIT）/ `SECURITY.md` / `CHANGELOG.md` / `CONTRIBUTING.md` — 授权、合规与协作说明

发布的 zip 包由 release 流程从本仓库生成，不作为源码提交。

## 自检

```bash
python caofeng-ppt/scripts/check_docs.py     # 文档↔代码数字对账
python caofeng-ppt/scripts/test_gates.py     # 167 项闸门反向自证（约数分钟）
python verify_install.py --skip-e2e           # 结构 / 依赖 / 版本自检
python verify_install.py                     # 含端到端出片（需已安装依赖）
```

四项均退出码 0 即为全绿。`--full-selftest` 会在安装自检中附带跑完整的闸门反向自证。

## 许可与素材说明

本仓库代码与原创技法数据以 MIT 授权。`caofeng-ppt/kb/` 中的版式、配色、动画等
参数为对第三方模板与教程视频的抽象提炼，原始作品版权归各自权利人所有，
不在本仓库分发。公开版本已清除其中的下载链接、提取码与创作者引流话术。
详见 `SECURITY.md`。

## 关键事实

- **闸量的是交付物，不是中间计划**：成品 pptx 的每个文本框、内嵌图与视频的真实分辨率、渲染后的像素、
  视频第 0 帧与首帧参考图的实测平均绝对差。`check` 必须带 `--render`，不重渲染就改 deck 会被判硬伤。
- **同级打分与语料同口径**：`deck_parity.py` 用与语料挖掘**同一份度量**量成品，按六维对照
  `kb/parity_baseline.json`（每页字数 p50/p90、大数字率、有图率、图形化率、版式与表格匹配）。
  基线**只用全量好示范页**校准；收口判据是六维 attain 全部 = 1.0（score 100），78 只是及格线。
- **数据页一屏一断言**：数据页先立一个 ≥60pt 主视觉大数，装不下的事实整条进演讲者备注——
  屏幕不堆字，事实一条不丢（G24 按含备注的全簿文本核）。
- **三层实测全部落进引擎**：配色（6 套实测色板 + theme accent 写入，G16）、图文重排（9 类版式原型实测坐标 +
  多图栅格 + 自动降级，G17）、高级动画（24 组 preset 三元组含真实 XML 签名 + 出场 + 视频点击暂停，G18）、
  示范质量（G19 拦广告/占位/模板号页）、溯源（G21 认磁盘原件，防拿旧成品抽字当基准）、
  素材覆盖（G20）、投放回执（G26）、开场与封面契约（G29）。
- **AI 画面里不许有字**（留白占位强制），所有文字由 PPT 文本层叠加，否则成品不可编辑。
- **平台角标可以真删**：回执素材走 `dewatermark.py` / `lama_cleaner.py`——漂移水印用裁角 + 几何证明 +
  高阈值模板匹配清除，**不许打码遮掩**；G28 拿 AI 产物原件与成品逐帧比对复核。
- **退出码三种**：1=闸拒绝（`report/验收报告.md` 列硬伤）；2=输入不对；3=脚本自己崩了（写 `report/崩溃.md`，本次结果不可信）。
- **不投放视频的合法出口只有** `check --allow-still`，它把硬伤转成报告里 `降级交付_无视频背景` 的显式声明——那一版不算电影级交付。
- **本机硬限制**：ImageGen 最大 1792×1024、短边不足 1080，不能当整页背景，所以本技能禁用它，出图出视频只走豆包/即梦网页版。

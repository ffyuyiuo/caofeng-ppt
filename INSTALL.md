# caofeng-ppt — 跨 Agent 安装与依赖说明

标准 Agent Skills 包（`SKILL.md` 入口 + 渐进式披露的 `SPEC.md` / `references/` / `kb/` / `scripts/`）。
当前版本见 `caofeng-ppt/VERSION` 与 `MANIFEST.json` 的 `version` 字段。
不绑定厂商：**装配与验收链不含任何本机绝对路径**——`--work` 是必填参数，工作目录由调用方给。
唯一的环境耦合在「研习脚本」上（它们要读原始模板库），见 §5；**出片不依赖原始模板库**，成果已沉淀在 `kb/`。

> 本文数字（闸数 / 文件数 / 子命令 / 自证项数）以包内实际为准。改代码时请同步这里，别凭记忆抄——
> `caofeng-ppt/scripts/check_docs.py` 会对账 skill 内的 SKILL.md / SPEC.md，但**不会**读这份 dist 侧 INSTALL.md。

---

## 1. 包内文件清单（分发包根目录）

| 路径 | 用途 |
|---|---|
| `caofeng-ppt/` | **标准 skill 目录**，可直接整体复制；内容见 §1.1 |
| `install.py` | 一键安装：解析目标 agent 的 skills 目录，纯标准库 |
| `verify_install.py` | 安装后自检 V1–V13（**15 项断言**）：结构、版本、依赖导入、**引擎是否真写动画/配色 XML**、闸编号 1–29 连续、文档对账、好/坏示范分层数据、语料目录单点解析、端到端出片 + **闸门必须咬住空产物** + 投放回执闸、成品动画与主题写入、校验和 |
| `requirements.txt` | 依赖清单（与 skill 内那份一致） |
| `README.md` | 快速开始 |
| `MANIFEST.json` | 版本、生成时间、逐文件 SHA256、被替换版本说明 |
| `sha256sums.txt` | 逐文件校验和（`sha256sum -c` 可校验） |
| `LICENSE` / `SECURITY.md` / `CHANGELOG.md` / `CONTRIBUTING.md` | 授权、合规与协作说明 |

> 压缩分发包（`*.skill.zip`、`*-dist.zip`）由 release 流程从本仓库生成，不作为源码提交。
> `VERIFICATION.json` 由 `verify_install.py` 在本地运行时写出，不参与版本控制。

### 1.1 `caofeng-ppt/` 内容（377 个文件）

```
caofeng-ppt/
├── SKILL.md                 # 唯一入口：硬约束 + 决策顺序 + 子命令用法 + 29 道闸清单
├── SPEC.md                  # 契约：输入输出、29 道闸（§7）、阈值表（§7.1）、
│                            #   完全同等级停止判据（§16）、开场/封面契约（§17）、
│                            #   真视频动态开场与数字过渡页（§18）、去水印链（§19）、首帧唯一性（§20）、
│                            #   页数契约（§21）、取图在线优先（§22）、fetch-video（§23）、
│                            #   美学闸（§24）、提示词句子完整性（§25）
├── README.md                # 设计说明与实测来源
├── VERSION / skill.json / requirements.txt
├── scripts/  (37 个 .py)
│   ├── caofeng.py           # 唯一运行入口，14 个子命令（见 §5）
│   ├── styles.py            # 分镜→素材→提示词的版式与槽位装配
│   ├── corpus_tables.py     # 引擎默认值真源：6 套实测色板、9 类版式原型、24 组动画 preset、坏示范规则
│   ├── deck_measure.py      # 页面几何与版式分类（装配与闸 G17 共用同一实现）
│   ├── deck_parity.py       # 与语料**好示范册**同口径的六维同级打分（闸 G22；--calibrate 出基线）
│   ├── grade_exemplars.py   # 好/坏示范分层（非模版元素不进学习、不进基线）
│   ├── safety.py            # 素材 8 类词边界黑名单 + 画种/来源筛（fetch 与闸 G3 两处复检）
│   ├── visual_metrics.py    # 渲染像素 / 内嵌字节质量量测
│   ├── dewatermark.py / lama_cleaner.py  # 回执素材去平台角标（裁角优先 + 几何证明 + 模板匹配）
│   ├── divider_art.py       # 数字过渡页（巨型斜体编号 + 取色渐变块 + 同帧飞入）
│   ├── delivery.py / delivery_gate.py    # 投放卡、回执台账与回执闸（G26）
│   ├── display_refine.py / data_layout.py # 显示契约提案与数据版式（hero/条形/数字排/筹码/环图）
│   ├── check_docs.py        # 文档↔代码↔kb 三方口径对账（闸数、阈值、卡片数、分层数字、基线中位）
│   ├── test_gates.py        # 每道闸的反向自证（每道闸必须咬得住坏输入；项数见 §4C）
│   ├── test_safety.py / verify_dual_viewer.py
│   └── analyze_ooxml.py / aggregate_ledger.py / build_kb.py / study_style_anim.py /
│       study_data_design.py / study_table_design.py / study_tutorials.py /
│       extract_prompt_grammar.py / extract_text_geom.py / transcribe_videos.py /
│       mine_transcripts.py / regen_slot_tables.py / deck_render.py / make_gif.py /
│       montage.py / corpus_path.py        # 研习与出图脚本，换机器可选重跑
├── references/  (7)
│   ├── pipeline.md          # 子命令的数据契约与字段
│   ├── cinema-recipes.md    # 技法卡（数值全部实测，含「谁来产出」）
│   ├── deck-craft.md        # 配色/版式/蒙版/表格的工程做法与实测坐标
│   ├── prompt-grammar.md    # 图片九段式 / 视频六段式（实测均值 809 / 1011 字）
│   ├── visual-patterns.md   # 逐页看图范式与好/坏示范构成
│   ├── material-sourcing.md # 高清无水印素材通道与许可
│   └── model-routing.md     # 豆包 / 即梦网页版渠道路由（禁用 ImageGen）
└── kb/                      # 实测数据——不是文档附录，是闸的判据来源
    ├── color_system.json  layout_archetypes.json  animation_mechanics.json  table_design.json
    ├── exemplar_grades.json（2224 页 → 835 好 / 1389 坏；54 好示范册 / 117 junk 册）
    ├── parity_baseline.json（G22 同级基线：**只用全量好示范页**校准，非模版元素不学习沉淀）
    ├── deck_tech.json（技法卡 + 教程引用）  prompt_corpus.jsonl / prompt_blocks.json / prompt_stats.json
    ├── coverage.json  corpus_stats.json  technique_report.md  visual_study.md
    ├── tutorials.json  tutorial_study.md  transcript_study.md  video_index.json  visual_metrics.jsonl
    ├── video_logic/         （逐帧逐句提炼的教程动作逻辑）
    └── transcripts/         （教程视频逐字稿）
```

---

## 2. 依赖说明

| 类别 | 要求 | 说明 |
|---|---|---|
| Python | **≥ 3.9**（3.13 已验证） | — |
| 装配必需 | `python-pptx` `lxml` `Pillow` `python-docx` `numpy` | `build` 出 .pptx、`check` 量内嵌字节与像素 |
| 造视频片段 | `imageio-ffmpeg` | 自带 ffmpeg 二进制，不需另装 |
| 真渲染验收 | `pywin32` + **Windows + WPS Office** | 走 `KWPP.Application` COM。PowerPoint COM 打不开 WPS 生成的 pptx（0x80070570），KWPP 是唯一可用渲染通道 |
| 可选（仅重跑研习） | `faster-whisper` | 只有 `transcribe_videos.py` 需要，正常出片用不到 |
| 安装命令 | `python -m pip install -r requirements.txt` | 在目标 agent 实际使用的解释器下执行 |
| 网络 | 取图必需 | `fetch` 走 Wikimedia Commons 等公开通道；无网络时可跳过取图，但**不能**当作电影级交付 |
| 图像/视频生成 | **只用网页版豆包 / 即梦（人工投放）** | 不用 ImageGen、不用任何 API Key：提示词整段可复制，投放后回答就绪才装配（G26） |
| API Key / MCP / 私有配置 | 不需要 | 运行时状态只落在 `--work` 目录 |

> 没有 WPS 的机器上 `check --render` 跑不了，闸 G12 会按「未渲染」判**不合格**——这是有意的，
> 不允许拿静态闸冒充渲染验收。此时最多交付到 `--allow-still` 的降级声明版。

---

## 3. 各 Agent 安装路径映射

| Agent | 用户级 skills 根 | 项目级 skills 根 |
|---|---|---|
| **Qoder IDE / Qoder CLI** | `~/.qoder/skills/` | `<workspace>/.qoder/skills/` |
| **Qoder CN（国内版）** | `~/.qoder-cn/skills/` | `<workspace>/.qoder/skills/` |
| **WorkBuddy** | `~/.workbuddy/skills/` | `<workspace>/.workbuddy/skills/` |
| Claude Code | `~/.claude/skills/` | `<workspace>/.claude/skills/` |
| Cursor | `~/.cursor/skills/` | `<workspace>/.cursor/skills/` |
| Codex CLI | `~/.codex/skills/` | `<workspace>/.codex/skills/` |
| **任意其他 agent** | 用 `--target <它的 skills 目录>` | 同上 |

技能名以 `SKILL.md` frontmatter 的 `name: caofeng-PPT` 为准（目录名 `caofeng-ppt`，大小写差异属正常）。

---

## 4. 安装方式

### A. 一键安装（推荐，发给同事就用这个）

```bash
cd <分发包目录>
python install.py --list                                  # 查看本机识别到的 agent skills 根
python install.py --agent qoder-cn --force --verify        # 装进 Qoder 国内版并跑 V1–V13
python install.py --agent workbuddy --force --verify       # 装进 WorkBuddy
python install.py --agent qoder --project <workspace>      # 装成项目级
python install.py --target <任意 skills 根> --force         # 装进别的 agent
```

`install.py` 只做一件事：把 `caofeng-ppt/` 复制到目标 skills 根（忽略 `__pycache__`），不写注册表、不改全局配置。
`--force` 先把旧副本**改名**成 `.backup-<时间戳>` 再装（不删除）；
遇到只差大小写的同名旧技能会先报冲突并跳过，确认要替换时加 `--replace-conflict`。
同目录只剩 `.skill.zip` 时会自动解压取用。

### B. 手动安装

解压 `caofeng-ppt-v<版本>.skill.zip`，把整个 `caofeng-ppt/` 放到上表的 skills 根，重启 agent，
在技能列表里应看到 `caofeng-PPT`。

### C. 安装后自检（装完务必跑一次）

```bash
python -m pip install -r requirements.txt
python verify_install.py                                   # 校验包内 caofeng-ppt/
python verify_install.py --skill ~/.qoder-cn/skills/caofeng-PPT
python verify_install.py --skill ~/.workbuddy/skills/caofeng-PPT
python verify_install.py --skip-e2e                        # 只做静态与对账（秒级）
python verify_install.py --full-selftest                   # 再加跑全量闸反向自证（约 10 分钟）
```

退出码 **0=全绿；1=有项不过；2=参数/路径不对**（不拿"没跑成"冒充"跑过了"）。
闸的反向自证项数以 `python caofeng-ppt/scripts/test_gates.py` 的实跑输出为准（当前 167 项），
`skill.json` 的 `contents.selftests` 与 `SKILL.md` / `SPEC.md` 里的下限由 `check_docs.py` 与代码条数对账。

`V10a/V10b/V11` 是端到端：用一份合成 Word 走 `plan→scaffold→refine→fetch→pack→ack→build`
（取图指向一个必定拒绝的代理，验的是**离线降级路径**：缺口写进 `materials/gaps.json`，缺页转矢量色场，
不放假图），再解开成品确认里面有 `p:timing` + `presetClass` + 主题 accent 写入——**只看文档写了动画不算能力**。
`V10b` 反向断言 `check --allow-still` **必须拒绝**这只没有投放产物的片子并命中 G11：
闸门放过空产物才是事故；`--allow-still` 只豁免「没投放视频」，**不豁免**「写了提示词却没产物」。
`V10c` 断言投放卡全文必须打到 stdout、没有用户回执时 `build` 必须停住（G26）。
真实素材的全绿交付要在装有 WPS 且已有 `gen/` 产物的机器上跑 `check --work DIR --render`。

---

## 5. 运行时、生效范围与本机耦合

- **触发**：给一份 Word 要求做 PPT / 要求电影级·高级感·会动 / 索要文生图·图生视频提示词 / 提到豆包·即梦网页版。
- **14 个子命令**：`plan`（分镜，可 `--pages N` 或 `N-M` 页数契约）→ `fetch`（联网取图）/
  `fetch-video`（联网取真视频）→ `scaffold`（提示词包骨架）→ `refine`（显示契约提案）→
  `pack`（投放卡全文打印 + 回执请求单）→ `card`（一页一轮，取最早待回收页）→
  `pickup`（回收产物，自动去水印）→ `ack`（回执：`--ready` / `--no-video` / `--declined`）→
  `waive`（单页豁免，**必须带用户原话**）→ `materials-review`（联系表 + keep/reject 台账）→
  `build`（装配可编辑 .pptx）→ `check`（29 道闸，可 `--render`）→ `shots`（逐页出图/拼图供目检）。
- **工作目录**：每个子命令都要 `--work DIR`，脚本自己 `abspath` 并在其下建
  `materials/ promptpack/ gen/ out/ report/`。换目录跑不会失效（相对路径写进 manifest 的老坑已修）。
- **素材落点**：由调用方的 `--work` 决定，包内不写死任何项目路径；**不要**把素材放进 skill 目录。
- **研习脚本的耦合**：`analyze_ooxml.py` `build_kb.py` `study_*.py` `grade_exemplars.py` `deck_render.py`
  `extract_*.py` `mine_transcripts.py` `aggregate_ledger.py` `transcribe_videos.py` `regen_slot_tables.py`
  默认读 `CAOFENG_SRC`（原始模板库）与 `CAOFENG_WORK`，目录不存在直接**退出码 2**，不会凭空建盘。
  **这些脚本不是出片必需**——成果已沉淀在 `kb/`，换机器直接用。
- **退出码三种**：1=闸拒绝；2=输入不对；3=脚本崩溃（写 `report/崩溃.md` 并声明本次结果不可信）。
- **升级**：整个目录替换；工作目录留在调用方，不受影响。
- **卸载**：删掉 skills 根下的 `caofeng-ppt/`（或 `caofeng-PPT/`），再按需删工作目录。

---

## 6. 校验与故障排查

```bash
sha256sum -c sha256sums.txt
python -c "import json;print(json.load(open('MANIFEST.json'))['version'])"
```

| 现象 | 处理 |
|---|---|
| 技能列表里没有 | 确认 `SKILL.md` 在目录**第一层**；重启 agent |
| `ModuleNotFoundError: pptx/lxml/PIL` | 在 agent 实际使用的解释器下 `python -m pip install -r requirements.txt` |
| `check` 报「未渲染/渲染陈旧」（G12） | 必须用 `check --work DIR --render`；改过 deck 不重渲染就是硬伤 |
| WPS 渲染起不来 | 需 Windows + 装有 WPS Office；`KWPP.Application` 是唯一通道，PowerPoint COM 会 0x80070570 |
| 没有素材投放渠道 | `check --allow-still` 是**唯一**合法出口，报告会显式写 `降级交付_无视频背景`，且不算电影级 |
| 取图被安全词拦 | 按文档主题放行：`fetch --allow-sensitive <类别>`，类别名写错直接停（退出码 2） |
| 找不到对题高清图 | 别塞假图、也别降色场凑数：走投放卡兜底，或按 sanctioned 路径改矢量色场并在报告交底 |
| 找不到 `CAOFENG_WORK` 报退出码 2 | 那是研习脚本，出片不需要；要重跑研习就设这两个环境变量 |

---

## 7. 与最初 1.0.0 的差异（一句话）

1.0.0 的装配器只调 `slide.shapes.add_movie(...)`，全仓 grep `p:timing|p159:morph|animEffect|duotone`
在写盘脚本里零命中——**文档里写了动画、代码里一行都不写**；自检也只是流程冒烟测试，不渲染、不量像素、不核溯源。
现在把配色、图文重排、高级动画、表格设计、真实动态背景、素材质量与去水印、同级打分全部落进引擎，
每道能力各配机器闸（G1–G29）与反向自证，并且只从语料**好示范**页学习取样——非模版元素（广告页、空页、
文字墙、字压亮底、无层级小字、模板编号主视觉）不进学习、也不进同级基线。

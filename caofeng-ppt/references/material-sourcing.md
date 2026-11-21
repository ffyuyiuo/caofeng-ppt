# 素材获取：渠道、代理、许可、质检
> 2026-11-07 起共三条通道：Commons（默认）· Unsplash（用户指定，有 UNSPLASH_ACCESS_KEY 才自动下载，否则给中文站检索 URL + `fetch --adopt` 登记）· Openverse（`--source openverse` 或 `auto`，匿名可调、聚合 Flickr/Wikimedia/Stocksnap，许可与作者字段机器可读）。三条共用同一套判据：短边≥1080 起筛、相关度≥阈值、安全黑名单复筛、同一 URL 最多铺 3 页。


## 实测连通性（2026-11-05，本机）

| 渠道 | 直连 | 代理 7897 | 结论 |
|---|---|---|---|
| commons.wikimedia.org API | **超时** | **200，2.1s** | 主渠道，必须走代理 |
| upload.wikimedia.org 原图 | — | 可下 | 单图可达 11–37MB，必须归一化 |
| pexels.com | — | **403 Forbidden** | 反爬，不用 |
| 国内站点（baidu 等） | 200，0.2s | — | 直连即可 |

**规则**：国内资源直连，国外资源走本地代理（默认 `http://127.0.0.1:7897`，可用 `--proxy` 或环境变量 `CAOFENG_PROXY` 覆盖为你本机端口）。`caofeng.py fetch` 已按域名自动判断
（`FOREIGN_HINT`），要覆盖用 `--proxy`。

## 为什么首选 Wikimedia Commons

1. **许可机器可读**：API 的 `extmetadata.LicenseShortName` 直接给出 `CC BY-SA 4.0` 这类结论，
   能写进 manifest 举证；图库站的"免费"往往只在页面文案里。
2. **无水印**：官方原图不带水印，从根上满足"高清无水印"。
3. **分辨率高**：实测取到 10200×5100、8064×6048。
4. 有 `filetype:bitmap` 检索与 `width/height` 元数据，能程序化筛。

代价：**英文索引**。中文实体词经常搜不到——实测「大盘与份额」这类标题词返回 0 结果，
换成 `Shanghai Pudong skyline` 立刻出图。所以：

- `plan` 产出的 `entities` 只是**候选词**；
- 真正检索必须 agent 把它翻成**具体的英文实物名 + 场景词**（地点、物体、年代、行业）；
- 用 `--term "页码=英文词"` 显式传入。

## 检索词怎么写

| 页面在讲 | 别搜 | 要搜 |
|---|---|---|
| 华东 12 城签约 | 大盘与份额 | `Shanghai skyline night` / `Hangzhou CBD` |
| 门店转化 | 潜在池 | `medical clinic interior` / `storefront china` |
| 巡检合规 | 巡检表 | `quality inspection checklist` |
| 2027 目标 | 下一步 | `road ahead mountain highway` |

原则：**搜画面里能看见的东西**，不搜抽象概念。抽象概念页走"矢量色场"或 AI 文生图。

## 质检硬标准（G3 拦）

- 短边 ≥1920（`--min-edge`）；
- 许可字段非空，`license` 与 `artist` 一起存；
- 存 `url`、`source_page`、`sha1`、`bytes`、`fetched_at`、`proxy_used`；
- 下载后**必须归一化**：长边压到 3200、JPEG q88。实测 11MB→0.62MB。
  不压的代价就在本次语料自己身上——171 个模板 106GB，平均一个 600MB，根本没法发给别人。

## 许可使用纪律

- `CC BY-SA`：可用于对外路演，但要在**附录或备注页**列出处（标题、作者、许可、链接）。
- `CC0 / Public Domain`：无署名要求，优先选。
- 拿不准许可的图：不用。改走 AI 文生图或矢量色场。
- 绝不做的事：扒带水印的图库预览图、裁掉**图库水印**再用、把别人模板里的图当自己素材。

## CC 作品自带的作者签名（实测坑，2026-11-05）

CC 许可的照片里，摄影师常把「2018© 作者名」这类签名直接烧在画面里。这**不是图库水印**：
许可合法、G3 也不会报错，但铺成整页背景时就是一块杂物。处理方式：

1. 在 `shots` 拼图上用人眼发现（G12 强制真渲染就是为这一步）；
2. 裁掉带签名的那条边（顶部/底部 5–6%），再按 16:9 居中裁宽；
3. **同步 manifest**：更新 `sha1`、`bytes`、`stored_w/stored_h`；`w/h` 保持"抓到的原图尺寸"不动
   （SPEC §5 的短边 ≥1920 判据看的是原图，不是裁后图），并在条目里写一句 `note` 说明裁了什么。
   不更新 sha1 → G3 的字节复算会判"素材被偷换"；把 w/h 改成裁后值 → G3 会误判"原图短边不足"。
   这两条都是实测踩到的（e4c 封面）。
4. 换图也可以，但 Commons 同类夜景照片多数都有签名，裁切通常比反复检索更快落地。


## 缺口处理

`fetch` 会把搜不到的页写进 `materials/gaps.json`（含检索词与原因）。这些页在 `build` 时
自动降级为**按本页数值绘制的矢量色场**（`field_bg`），并在 `build_log.json` 标注原因；
`check` 的 G11 会把"有提示词但没有产物"的页列成待补清单。**不许**为了看起来齐全塞占位图。
